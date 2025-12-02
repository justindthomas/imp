#!/bin/bash
#
# setup-appliance.sh - Initial ZFS appliance setup from Live CD
#
# This script partitions a disk, creates a ZFS pool, bootstraps Debian,
# and installs zfsbootmenu for boot environment management.
#
# Usage: setup-appliance.sh <disk>
# Example: setup-appliance.sh /dev/sda
#
# Run from a Debian Bookworm Live CD with ZFS support.
#

set -euo pipefail

DISK="${1:-}"
POOL_NAME="tank"
ROOTFS="/mnt/root"
HOSTNAME="appliance"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

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
    echo "Run from a Debian Bookworm Live CD after installing ZFS:"
    echo "  sudo -i"
    echo "  apt update && apt install -y zfsutils-linux zfs-dkms debootstrap gdisk"
    echo "  modprobe zfs"
    echo "  ./setup-appliance.sh /dev/sda"
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

# Confirm destruction
echo ""
echo "=========================================="
echo "  IMP Appliance Setup"
echo "=========================================="
echo ""
echo "Target disk: $DISK"
echo "Pool name:   $POOL_NAME"
echo "Hostname:    $HOSTNAME"
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
    --include=linux-image-amd64,linux-headers-amd64,systemd,systemd-sysv,dbus,locales,keyboard-configuration,curl,gnupg \
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

# Apt sources
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

# Network config (DHCP on all ethernet)
mkdir -p "$ROOTFS/etc/systemd/network"
cat > "$ROOTFS/etc/systemd/network/20-wired.network" << 'EOF'
[Match]
Name=en*

[Network]
DHCP=yes
EOF

# Install packages in chroot
log "Installing packages in chroot..."
chroot "$ROOTFS" apt-get update
chroot "$ROOTFS" apt-get install -y \
    zfsutils-linux \
    zfs-initramfs \
    systemd-resolved \
    iproute2 \
    dhcpcd-base \
    openssh-server \
    efibootmgr \
    zstd \
    console-setup

# Enable services
chroot "$ROOTFS" systemctl enable systemd-networkd systemd-resolved ssh

# Set root password
log "Setting root password..."
echo "root:appliance" | chroot "$ROOTFS" chpasswd
warn "Default root password is 'appliance' - change it after first boot!"

# Generate hostid (required for ZFS pool import)
log "Generating hostid..."
zgenhostid -f -o "${ROOTFS}/etc/hostid"

# =============================================================================
# Install zfsbootmenu
# =============================================================================
log "Installing zfsbootmenu..."

mkdir -p "$ROOTFS/boot/efi/EFI/ZBM"
curl -L https://get.zfsbootmenu.org/efi -o "$ROOTFS/boot/efi/EFI/ZBM/zfsbootmenu.efi"

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
log "Appliance setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Remove the Live CD"
echo "  2. Reboot"
echo "  3. zfsbootmenu should present your boot environment"
echo "  4. Login as root (password: appliance)"
echo "  5. Change the root password!"
echo ""
echo "To deploy IMP images to this appliance:"
echo "  zstd -d < imp-v1.0.0.zfs.zst | zfs receive tank/ROOT/imp-v1.0.0"
echo "  zfs set mountpoint=/ tank/ROOT/imp-v1.0.0"
echo "  zpool set bootfs=tank/ROOT/imp-v1.0.0 tank"
echo "  reboot"
echo ""
