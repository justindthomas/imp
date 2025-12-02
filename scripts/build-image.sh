#!/bin/bash
#
# build-image.sh - Build a deployable ZFS boot environment image
#
# Usage: build-image.sh <version>
# Example: build-image.sh debian-v1.0.0
#
# Output: /var/lib/images/<version>.zfs.zst
#

set -euo pipefail

VERSION="${1:?Usage: build-image.sh <version>}"
DATASET="buildpool/workspace/${VERSION}"
SNAPSHOT="${DATASET}@release"
OUTPUT_DIR="/var/lib/images"

mkdir -p "$OUTPUT_DIR"

# Clean any previous build
zfs destroy -r "$DATASET" 2>/dev/null || true
zfs create "$DATASET"

MOUNTPOINT=$(zfs get -H -o value mountpoint "$DATASET")
echo "Building in: $MOUNTPOINT"

# Bootstrap minimal Debian with contrib for ZFS
mmdebstrap \
    --variant=minbase \
    --components=main,contrib \
    --include=systemd,systemd-sysv,dbus,linux-image-amd64,linux-headers-amd64,zfsutils-linux,zfs-initramfs,openssh-server,systemd-resolved,iproute2,dhcpcd-base,locales,console-setup,zstd \
    trixie \
    "$MOUNTPOINT" \
    "deb http://deb.debian.org/debian trixie main contrib"

# Basic configuration
echo "appliance" > "${MOUNTPOINT}/etc/hostname"
echo "127.0.1.1 appliance" >> "${MOUNTPOINT}/etc/hosts"

# Configure locale
sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' "${MOUNTPOINT}/etc/locale.gen"
chroot "$MOUNTPOINT" locale-gen
echo 'LANG=en_US.UTF-8' > "${MOUNTPOINT}/etc/default/locale"

# Network config (DHCP on all ethernet)
mkdir -p "${MOUNTPOINT}/etc/systemd/network"
cat > "${MOUNTPOINT}/etc/systemd/network/20-wired.network" << 'EOF'
[Match]
Name=en*

[Network]
DHCP=yes
EOF

# Enable services
chroot "$MOUNTPOINT" systemctl enable systemd-networkd systemd-resolved ssh

# Set root password (change this!)
echo "root:appliance" | chroot "$MOUNTPOINT" chpasswd

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
