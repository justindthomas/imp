#!/bin/bash
#
# build-image.sh - Build a deployable ZFS boot environment image
#
# Usage: build-image.sh <version>
# Example: build-image.sh debian-v1.0.0
#
# Output: /var/lib/images/<version>.zfs.zst
#
# This script builds a Debian Bookworm image with:
#   - ZFS root filesystem support
#   - VPP (Vector Packet Processing) from fd.io
#   - FRR (Free Range Routing)
#   - Incus container runtime (from backports)
#   - Dataplane namespace isolation
#

set -euo pipefail

VERSION="${1:?Usage: build-image.sh <version>}"
DATASET="buildpool/workspace/${VERSION}"
SNAPSHOT="${DATASET}@release"
OUTPUT_DIR="/var/lib/images"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/../config"

mkdir -p "$OUTPUT_DIR"

# Clean any previous build
zfs destroy -r "$DATASET" 2>/dev/null || true
zfs create "$DATASET"

MOUNTPOINT=$(zfs get -H -o value mountpoint "$DATASET")
echo "Building in: $MOUNTPOINT"

# Bootstrap minimal Debian Bookworm
# Using bookworm because fd.io doesn't have trixie packages yet
mmdebstrap \
    --variant=minbase \
    --components=main,contrib \
    --include=systemd,systemd-sysv,dbus,linux-image-amd64,linux-headers-amd64,zfsutils-linux,zfs-initramfs,openssh-server,systemd-resolved,iproute2,dhcpcd-base,locales,console-setup,zstd,curl,gnupg \
    bookworm \
    "$MOUNTPOINT" \
    "deb http://deb.debian.org/debian bookworm main contrib" \
    "deb http://deb.debian.org/debian bookworm-backports main contrib"

# Basic configuration
echo "appliance" > "${MOUNTPOINT}/etc/hostname"
echo "127.0.1.1 appliance" >> "${MOUNTPOINT}/etc/hosts"

# Temporary DNS for chroot (systemd-resolved will take over after boot)
rm -f "${MOUNTPOINT}/etc/resolv.conf"
echo "nameserver 1.1.1.1" > "${MOUNTPOINT}/etc/resolv.conf"

# Configure locale
sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' "${MOUNTPOINT}/etc/locale.gen"
chroot "$MOUNTPOINT" locale-gen
echo 'LANG=en_US.UTF-8' > "${MOUNTPOINT}/etc/default/locale"

# Network config (DHCP on management interface only)
# Dataplane interfaces are managed by VPP
mkdir -p "${MOUNTPOINT}/etc/systemd/network"
cat > "${MOUNTPOINT}/etc/systemd/network/20-wired.network" << 'EOF'
[Match]
Name=en*

[Network]
DHCP=yes
EOF

# Set root password (change this!)
echo "root:appliance" | chroot "$MOUNTPOINT" chpasswd

# =============================================================================
# Pre-seed debconf for non-interactive installation
# =============================================================================
cat > "${MOUNTPOINT}/tmp/debconf-selections" << 'EOF'
# Console/keyboard configuration
console-setup console-setup/charmap47 select UTF-8

# ZFS license acknowledgment
zfs-dkms zfs-dkms/note-incompatible-licenses note
EOF
chroot "$MOUNTPOINT" debconf-set-selections /tmp/debconf-selections
rm "${MOUNTPOINT}/tmp/debconf-selections"

# =============================================================================
# fd.io VPP Repository Setup
# =============================================================================
echo "Adding fd.io repository..."

# Ensure DNS is working (mmdebstrap may have recreated resolv.conf symlink)
rm -f "${MOUNTPOINT}/etc/resolv.conf"
echo "nameserver 1.1.1.1" > "${MOUNTPOINT}/etc/resolv.conf"

mkdir -p "${MOUNTPOINT}/etc/apt/keyrings"
# Run gpg inside the chroot where gnupg is installed
curl -fsSL https://packagecloud.io/fdio/release/gpgkey | \
    chroot "$MOUNTPOINT" gpg --dearmor -o /etc/apt/keyrings/fdio_release-archive-keyring.gpg

# Copy fd.io sources list from config
cp "${CONFIG_DIR}/etc/apt/sources.list.d/fdio_release.list" \
   "${MOUNTPOINT}/etc/apt/sources.list.d/"

# Update package lists with new repos
chroot "$MOUNTPOINT" apt-get update

# =============================================================================
# Install VPP packages
# =============================================================================
echo "Installing VPP..."
DEBIAN_FRONTEND=noninteractive chroot "$MOUNTPOINT" apt-get install -y \
    vpp \
    vpp-plugin-core \
    vpp-plugin-dpdk

# =============================================================================
# Install FRR (Free Range Routing)
# =============================================================================
echo "Installing FRR..."
DEBIAN_FRONTEND=noninteractive chroot "$MOUNTPOINT" apt-get install -y frr frr-pythontools

# =============================================================================
# Install Incus from backports
# =============================================================================
echo "Installing Incus from backports..."
DEBIAN_FRONTEND=noninteractive chroot "$MOUNTPOINT" apt-get install -y -t bookworm-backports incus

# =============================================================================
# Install Python/Jinja2 for configuration script
# =============================================================================
echo "Installing Python/Jinja2..."
DEBIAN_FRONTEND=noninteractive chroot "$MOUNTPOINT" apt-get install -y python3 python3-jinja2

# =============================================================================
# Copy configuration templates and scripts
# =============================================================================
echo "Copying configuration templates and scripts..."

# VPP configuration directory and log directory
mkdir -p "${MOUNTPOINT}/etc/vpp"
mkdir -p "${MOUNTPOINT}/var/log/vpp"

# Copy static VPP startup configs (nat doesn't need templating)
cp "${CONFIG_DIR}/etc/vpp/startup-nat.conf" "${MOUNTPOINT}/etc/vpp/"

# FRR configuration (daemons and vtysh.conf are static)
cp "${CONFIG_DIR}/etc/frr/daemons" "${MOUNTPOINT}/etc/frr/"
cp "${CONFIG_DIR}/etc/frr/vtysh.conf" "${MOUNTPOINT}/etc/frr/"
chroot "$MOUNTPOINT" chown -R frr:frr /etc/frr

# Systemd service units for dataplane (static services)
cp "${CONFIG_DIR}/etc/systemd/system/netns-dataplane.service" "${MOUNTPOINT}/etc/systemd/system/"
cp "${CONFIG_DIR}/etc/systemd/system/vpp-core.service" "${MOUNTPOINT}/etc/systemd/system/"
cp "${CONFIG_DIR}/etc/systemd/system/vpp-core-config.service" "${MOUNTPOINT}/etc/systemd/system/"
cp "${CONFIG_DIR}/etc/systemd/system/vpp-nat.service" "${MOUNTPOINT}/etc/systemd/system/"
cp "${CONFIG_DIR}/etc/systemd/system/incus-dataplane.service" "${MOUNTPOINT}/etc/systemd/system/"

# Helper scripts (static scripts)
mkdir -p "${MOUNTPOINT}/usr/local/bin"
cp "${CONFIG_DIR}/usr/local/bin/incus-init.sh" "${MOUNTPOINT}/usr/local/bin/"
cp "${CONFIG_DIR}/usr/local/bin/wait-for-iface-load" "${MOUNTPOINT}/usr/local/bin/"
chmod +x "${MOUNTPOINT}/usr/local/bin/"*

# Create netns directory for dataplane namespace config
mkdir -p "${MOUNTPOINT}/etc/netns/dataplane"

# =============================================================================
# Copy configuration templates for configure-router.py
# =============================================================================
echo "Copying Jinja2 templates..."

# Templates directory
mkdir -p "${MOUNTPOINT}/etc/imp/templates/vpp"
mkdir -p "${MOUNTPOINT}/etc/imp/templates/frr"
mkdir -p "${MOUNTPOINT}/etc/imp/templates/systemd"
mkdir -p "${MOUNTPOINT}/etc/imp/templates/scripts"

cp "${CONFIG_DIR}/templates/vpp/"*.j2 "${MOUNTPOINT}/etc/imp/templates/vpp/"
cp "${CONFIG_DIR}/templates/frr/"*.j2 "${MOUNTPOINT}/etc/imp/templates/frr/"
cp "${CONFIG_DIR}/templates/systemd/"*.j2 "${MOUNTPOINT}/etc/imp/templates/systemd/"
cp "${CONFIG_DIR}/templates/scripts/"*.j2 "${MOUNTPOINT}/etc/imp/templates/scripts/"

# Router configuration script (Python)
cp "${SCRIPT_DIR}/configure-router.py" "${MOUNTPOINT}/usr/local/bin/"
chmod +x "${MOUNTPOINT}/usr/local/bin/configure-router.py"

# Create symlink for convenience
ln -sf configure-router.py "${MOUNTPOINT}/usr/local/bin/configure-router"

# =============================================================================
# Enable services
# =============================================================================
echo "Enabling services..."
# Only enable basic services - dataplane services are enabled by configure-router.py
chroot "$MOUNTPOINT" systemctl enable \
    systemd-networkd \
    systemd-resolved \
    ssh

# =============================================================================
# Finalize
# =============================================================================

# Regenerate initramfs with ZFS
chroot "$MOUNTPOINT" update-initramfs -u -k all

# Clean up
rm -rf "${MOUNTPOINT}/var/cache/apt/archives"/*.deb
rm -rf "${MOUNTPOINT}/var/lib/apt/lists"/*

# Set ZFS properties for bootability
zfs set canmount=noauto "$DATASET"
zfs set org.zfsbootmenu:commandline="quiet" "$DATASET"

# Snapshot and send
zfs snapshot "$SNAPSHOT"
zfs send "$SNAPSHOT" | zstd -T0 -10 > "${OUTPUT_DIR}/${VERSION}.zfs.zst"

echo ""
echo "Build complete: ${OUTPUT_DIR}/${VERSION}.zfs.zst"
ls -lh "${OUTPUT_DIR}/${VERSION}.zfs.zst"
