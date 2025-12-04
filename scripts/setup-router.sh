#!/bin/bash
#
# setup-router.sh - Complete IMP router setup from Live CD
#
# This script:
#   1. Partitions a disk and creates a ZFS pool
#   2. Bootstraps Debian Bookworm
#   3. Installs VPP (from fd.io), FRR, and Incus
#   4. Installs configuration templates and scripts
#   5. Sets up zfsbootmenu for boot environment management
#
# Usage: setup-router.sh <disk>
# Example: setup-router.sh /dev/sda
#
# Run from the IMP Installer ISO or a Debian Bookworm Live CD with ZFS support.
#

set -euo pipefail

DISK="${1:-}"
POOL_NAME="tank"
ROOTFS="/mnt/root"
HOSTNAME="router"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${SCRIPT_DIR}/../config"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

usage() {
    echo "Usage: $0 <disk>"
    echo ""
    echo "Arguments:"
    echo "  disk    Target disk (e.g., /dev/sda, /dev/nvme0n1)"
    echo ""
    echo "This script will DESTROY all data on the target disk."
    echo ""
    echo "Run from the IMP Installer ISO or a Debian Live CD with ZFS:"
    echo "  sudo -i"
    echo "  ./setup-router.sh /dev/sda"
    exit 1
}

# Validate arguments
[[ -z "$DISK" ]] && usage
[[ ! -b "$DISK" ]] && error "Disk $DISK does not exist or is not a block device"

# Check we're running as root
[[ $EUID -ne 0 ]] && error "This script must be run as root"

# Check ZFS is available
if ! command -v zpool &>/dev/null; then
    error "ZFS not available. Install with: apt install -y zfsutils-linux zfs-dkms && modprobe zfs"
fi

# Check if config directory exists (running from repo)
if [[ ! -d "$CONFIG_DIR" ]]; then
    # Try alternate location (installed on ISO)
    if [[ -d "/root/imp-build/config" ]]; then
        CONFIG_DIR="/root/imp-build/config"
        SCRIPT_DIR="/root/imp-build/scripts"
    else
        error "Cannot find config directory. Run from imp-build repository."
    fi
fi

# Confirm destruction
echo ""
echo "=========================================="
echo "  IMP Router Setup"
echo "=========================================="
echo ""
echo "Target disk: $DISK"
echo "Pool name:   $POOL_NAME"
echo "Hostname:    $HOSTNAME"
echo ""
echo "This will install:"
echo "  - Debian Bookworm with ZFS root"
echo "  - VPP (Vector Packet Processing)"
echo "  - FRR (Free Range Routing)"
echo "  - Incus (container runtime)"
echo "  - IMP configuration tools"
echo ""
warn "This will DESTROY ALL DATA on $DISK"
echo ""
read -p "Type 'yes' to continue: " CONFIRM
[[ "$CONFIRM" != "yes" ]] && error "Aborted"

# =============================================================================
# Partition the disk
# =============================================================================
log "Partitioning $DISK..."

# Determine partition suffix (nvme uses 'p', sata/virtio don't)
if [[ "$DISK" == *"nvme"* ]] || [[ "$DISK" == *"loop"* ]]; then
    PART_PREFIX="${DISK}p"
else
    PART_PREFIX="${DISK}"
fi

sgdisk --zap-all "$DISK"

# Partition layout:
# 1: 1MB BIOS boot (legacy compatibility)
# 2: 512MB EFI System Partition
# 3: Remainder for ZFS
sgdisk -n1:1M:+1M -t1:EF02 "$DISK"
sgdisk -n2:0:+512M -t2:EF00 "$DISK"
sgdisk -n3:0:0 -t3:BF00 "$DISK"

# Wait for partitions to appear
sleep 2
partprobe "$DISK" 2>/dev/null || true
sleep 1

# Format ESP
log "Formatting EFI System Partition..."
mkfs.fat -F32 "${PART_PREFIX}2"

# =============================================================================
# Create ZFS pool
# =============================================================================
log "Creating ZFS pool..."

zpool create -f \
    -o ashift=12 \
    -o autotrim=on \
    -O acltype=posixacl \
    -O canmount=off \
    -O compression=zstd \
    -O dnodesize=auto \
    -O normalization=formD \
    -O relatime=on \
    -O xattr=sa \
    -O mountpoint=none \
    "$POOL_NAME" "${PART_PREFIX}3"

# Boot environment structure
zfs create -o canmount=off -o mountpoint=none "${POOL_NAME}/ROOT"
zfs create -o canmount=noauto -o mountpoint=/ "${POOL_NAME}/ROOT/debian-initial"

# Persistent data (survives across deployments)
zfs create -o canmount=off -o mountpoint=/persistent "${POOL_NAME}/persistent"
zfs create "${POOL_NAME}/persistent/config"
zfs create "${POOL_NAME}/persistent/data"

# Mount for installation
zfs set mountpoint="$ROOTFS" "${POOL_NAME}/ROOT/debian-initial"
zfs mount "${POOL_NAME}/ROOT/debian-initial"

# =============================================================================
# Bootstrap Debian
# =============================================================================
log "Bootstrapping Debian Bookworm (this takes a few minutes)..."

debootstrap \
    --include=linux-image-amd64,linux-headers-amd64,systemd,systemd-sysv,dbus,locales,keyboard-configuration,curl,gnupg,ca-certificates \
    bookworm \
    "$ROOTFS" \
    https://deb.debian.org/debian

# =============================================================================
# Configure the system
# =============================================================================
log "Configuring system..."

# Mount virtual filesystems
mount --rbind /dev  "$ROOTFS/dev"
mount --rbind /proc "$ROOTFS/proc"
mount --rbind /sys  "$ROOTFS/sys"

# Temporary DNS for chroot (systemd-resolved will take over after boot)
rm -f "$ROOTFS/etc/resolv.conf"
echo "nameserver 1.1.1.1" > "$ROOTFS/etc/resolv.conf"

# Mount ESP
mkdir -p "$ROOTFS/boot/efi"
mount "${PART_PREFIX}2" "$ROOTFS/boot/efi"

# Hostname
echo "$HOSTNAME" > "$ROOTFS/etc/hostname"
cat > "$ROOTFS/etc/hosts" << EOF
127.0.0.1   localhost
127.0.1.1   $HOSTNAME

::1         localhost ip6-localhost ip6-loopback
ff02::1     ip6-allnodes
ff02::2     ip6-allrouters
EOF

# Apt sources (including backports for Incus)
cat > "$ROOTFS/etc/apt/sources.list" << 'EOF'
deb http://deb.debian.org/debian bookworm main contrib
deb http://deb.debian.org/debian bookworm-updates main contrib
deb http://deb.debian.org/debian bookworm-backports main contrib
deb http://security.debian.org/debian-security bookworm-security main contrib
EOF

# Locale
sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' "$ROOTFS/etc/locale.gen"
chroot "$ROOTFS" locale-gen
echo 'LANG=en_US.UTF-8' > "$ROOTFS/etc/default/locale"

# Initial network config (DHCP on all ethernet - will be replaced by configure-router.py)
mkdir -p "$ROOTFS/etc/systemd/network"
cat > "$ROOTFS/etc/systemd/network/20-wired.network" << 'EOF'
[Match]
Name=en*

[Network]
DHCP=yes
EOF

# =============================================================================
# Pre-seed debconf for non-interactive installation
# =============================================================================
log "Configuring non-interactive installation..."

# Accept ZFS license
cat > "$ROOTFS/tmp/debconf-selections" << 'EOF'
# Keyboard configuration - use US layout
keyboard-configuration keyboard-configuration/layoutcode string us
keyboard-configuration keyboard-configuration/model select Generic 105-key PC
keyboard-configuration keyboard-configuration/variant select English (US)
console-setup console-setup/charmap47 select UTF-8

# ZFS license acknowledgment
zfs-dkms zfs-dkms/note-incompatible-licenses note
EOF
chroot "$ROOTFS" debconf-set-selections /tmp/debconf-selections
rm "$ROOTFS/tmp/debconf-selections"

# =============================================================================
# Install base packages
# =============================================================================
log "Installing base packages..."
chroot "$ROOTFS" apt-get update
DEBIAN_FRONTEND=noninteractive chroot "$ROOTFS" apt-get install -y \
    zfsutils-linux \
    zfs-initramfs \
    systemd-resolved \
    iproute2 \
    dhcpcd-base \
    openssh-server \
    efibootmgr \
    zstd \
    console-setup \
    python3 \
    python3-jinja2

# =============================================================================
# Setup fd.io repository and install VPP
# =============================================================================
log "Setting up fd.io repository..."

# Restore DNS (systemd-resolved install recreates the symlink)
rm -f "$ROOTFS/etc/resolv.conf"
echo "nameserver 1.1.1.1" > "$ROOTFS/etc/resolv.conf"

mkdir -p "$ROOTFS/etc/apt/keyrings"
# Run gpg inside the chroot where gnupg is installed
curl -fsSL https://packagecloud.io/fdio/release/gpgkey | \
    chroot "$ROOTFS" gpg --dearmor -o /etc/apt/keyrings/fdio_release-archive-keyring.gpg

# Copy fd.io sources list
cp "$CONFIG_DIR/etc/apt/sources.list.d/fdio_release.list" "$ROOTFS/etc/apt/sources.list.d/"

chroot "$ROOTFS" apt-get update

log "Installing VPP..."
DEBIAN_FRONTEND=noninteractive chroot "$ROOTFS" apt-get install -y \
    vpp \
    vpp-plugin-core \
    vpp-plugin-dpdk

# =============================================================================
# Install FRR
# =============================================================================
log "Installing FRR..."
DEBIAN_FRONTEND=noninteractive chroot "$ROOTFS" apt-get install -y frr frr-pythontools

# =============================================================================
# Install Incus from backports
# =============================================================================
log "Installing Incus from backports..."
DEBIAN_FRONTEND=noninteractive chroot "$ROOTFS" apt-get install -y -t bookworm-backports incus

# =============================================================================
# Copy static configuration files (templates are used for machine-specific config)
# =============================================================================
log "Installing static configuration files..."

# VPP directories
mkdir -p "$ROOTFS/etc/vpp"
mkdir -p "$ROOTFS/var/log/vpp"

# Static VPP config (NAT startup doesn't need templating)
cp "$CONFIG_DIR/etc/vpp/startup-nat.conf" "$ROOTFS/etc/vpp/"

# FRR static configuration (daemons list, vtysh settings)
cp "$CONFIG_DIR/etc/frr/daemons" "$ROOTFS/etc/frr/"
cp "$CONFIG_DIR/etc/frr/vtysh.conf" "$ROOTFS/etc/frr/"
chroot "$ROOTFS" chown -R frr:frr /etc/frr

# Static systemd service units (netns-move-interfaces is templated)
cp "$CONFIG_DIR/etc/systemd/system/netns-dataplane.service" "$ROOTFS/etc/systemd/system/"
cp "$CONFIG_DIR/etc/systemd/system/vpp-core.service" "$ROOTFS/etc/systemd/system/"
cp "$CONFIG_DIR/etc/systemd/system/vpp-core-config.service" "$ROOTFS/etc/systemd/system/"
cp "$CONFIG_DIR/etc/systemd/system/vpp-nat.service" "$ROOTFS/etc/systemd/system/"
cp "$CONFIG_DIR/etc/systemd/system/incus-dataplane.service" "$ROOTFS/etc/systemd/system/"

# Static helper scripts (vpp-core-config.sh and incus-networking.sh are templated)
mkdir -p "$ROOTFS/usr/local/bin"
cp "$CONFIG_DIR/usr/local/bin/incus-init.sh" "$ROOTFS/usr/local/bin/"
cp "$CONFIG_DIR/usr/local/bin/wait-for-iface-load" "$ROOTFS/usr/local/bin/"
chmod +x "$ROOTFS/usr/local/bin/"*

# Create netns directory
mkdir -p "$ROOTFS/etc/netns/dataplane"

# =============================================================================
# Install Jinja2 templates for configure-router.py
# =============================================================================
log "Installing configuration templates..."

mkdir -p "$ROOTFS/etc/imp/templates/vpp"
mkdir -p "$ROOTFS/etc/imp/templates/frr"
mkdir -p "$ROOTFS/etc/imp/templates/systemd"
mkdir -p "$ROOTFS/etc/imp/templates/scripts"

cp "$CONFIG_DIR/templates/vpp/"*.j2 "$ROOTFS/etc/imp/templates/vpp/"
cp "$CONFIG_DIR/templates/frr/"*.j2 "$ROOTFS/etc/imp/templates/frr/"
cp "$CONFIG_DIR/templates/systemd/"*.j2 "$ROOTFS/etc/imp/templates/systemd/"
cp "$CONFIG_DIR/templates/scripts/"*.j2 "$ROOTFS/etc/imp/templates/scripts/"

# Install configure-router.py
cp "$SCRIPT_DIR/configure-router.py" "$ROOTFS/usr/local/bin/"
chmod +x "$ROOTFS/usr/local/bin/configure-router.py"
ln -sf configure-router.py "$ROOTFS/usr/local/bin/configure-router"

# =============================================================================
# Enable services
# =============================================================================
log "Enabling services..."
# Only enable basic services - dataplane services are enabled by configure-router.py
chroot "$ROOTFS" systemctl enable \
    systemd-networkd \
    systemd-resolved \
    ssh

# =============================================================================
# Set root password
# =============================================================================
log "Setting root password..."
echo "root:router" | chroot "$ROOTFS" chpasswd
warn "Default root password is 'router' - change it after first boot!"

# Generate hostid (required for ZFS pool import)
log "Generating hostid..."
zgenhostid -f -o "${ROOTFS}/etc/hostid"

# =============================================================================
# Install zfsbootmenu
# =============================================================================
log "Installing zfsbootmenu..."

mkdir -p "$ROOTFS/boot/efi/EFI/ZBM"
curl -L https://get.zfsbootmenu.org/efi -o "$ROOTFS/boot/efi/EFI/ZBM/zfsbootmenu.efi"

# Remove any existing ZFSBootMenu entries to avoid duplicates
log "Cleaning up existing ZFSBootMenu EFI entries..."
for entry in $(efibootmgr | grep -i "ZFSBootMenu" | grep -oP 'Boot\K[0-9A-Fa-f]{4}'); do
    efibootmgr -b "$entry" -B 2>/dev/null || true
done

# Create EFI boot entry
chroot "$ROOTFS" efibootmgr -c -d "$DISK" -p 2 -L "ZFSBootMenu" -l '\EFI\ZBM\zfsbootmenu.efi'

# =============================================================================
# Set ZFS boot properties
# =============================================================================
log "Setting ZFS boot properties..."

zpool set bootfs="${POOL_NAME}/ROOT/debian-initial" "$POOL_NAME"
zfs set org.zfsbootmenu:commandline="quiet" "${POOL_NAME}/ROOT/debian-initial"

# Regenerate initramfs
chroot "$ROOTFS" update-initramfs -u -k all

# =============================================================================
# Finalize
# =============================================================================
log "Finalizing installation..."

# Unmount bind mounts first
log "Unmounting filesystems..."
umount "$ROOTFS/boot/efi" || warn "ESP already unmounted"
umount -l "$ROOTFS/dev" 2>/dev/null || true
umount -l "$ROOTFS/proc" 2>/dev/null || true
umount -l "$ROOTFS/sys" 2>/dev/null || true

# Give things a moment to settle
sync
sleep 2

# Unmount ZFS filesystem before changing mountpoint
log "Setting ZFS mountpoints for booting..."
zfs unmount "${POOL_NAME}/ROOT/debian-initial" 2>/dev/null || true

# Set mountpoints for booting (must be done while unmounted)
zfs set mountpoint=/ "${POOL_NAME}/ROOT/debian-initial"
zfs set mountpoint=/persistent/config "${POOL_NAME}/persistent/config"
zfs set mountpoint=/persistent/data "${POOL_NAME}/persistent/data"

# Verify mountpoint is correct
VERIFY_MP=$(zfs get -H -o value mountpoint "${POOL_NAME}/ROOT/debian-initial")
if [[ "$VERIFY_MP" != "/" ]]; then
    error "Mountpoint not set correctly (got: $VERIFY_MP, expected: /)"
fi
log "Mountpoint verified: $VERIFY_MP"

# Export pool
log "Exporting ZFS pool..."
zpool export "$POOL_NAME" || {
    warn "Pool busy, trying force export..."
    sleep 2
    zpool export -f "$POOL_NAME" || warn "Could not export pool - reboot will handle it"
}

echo ""
echo "=========================================="
log "IMP Router setup complete!"
echo "=========================================="
echo ""
echo "Installed components:"
echo "  - Debian Bookworm with ZFS root"
echo "  - VPP (Vector Packet Processing) from fd.io"
echo "  - FRR (Free Range Routing)"
echo "  - Incus (container runtime)"
echo "  - ZFSBootMenu"
echo ""
echo "Next steps:"
echo "  1. Remove the Live CD/USB"
echo "  2. Reboot"
echo "  3. Login as root (password: router)"
echo "  4. Run 'configure-router' to set up networking"
echo "  5. Run 'incus-init.sh' to initialize containers"
echo "  6. Change the root password!"
echo ""
