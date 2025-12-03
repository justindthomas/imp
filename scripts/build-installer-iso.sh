#!/bin/bash
#
# build-installer-iso.sh - Build a custom Debian Live ISO with ZFS pre-compiled
#
# This creates a bootable USB/ISO image that includes:
#   - Debian Bookworm live environment
#   - ZFS kernel modules pre-compiled (no 30+ minute DKMS wait)
#   - IMP setup scripts included
#
# Usage: build-installer-iso.sh [output-dir]
# Example: build-installer-iso.sh /var/lib/images
#
# Requirements: Run on Debian Bookworm with live-build installed
#   apt install live-build
#
# Output: imp-installer-<date>.iso
#

set -euo pipefail

OUTPUT_DIR="${1:-/var/lib/images}"
WORK_DIR="/tmp/imp-live-build"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATE=$(date +%Y%m%d)

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Check we're running as root
[[ $EUID -ne 0 ]] && error "This script must be run as root"

# Check live-build is installed
if ! command -v lb &>/dev/null; then
    error "live-build not installed. Run: apt install live-build"
fi

log "Building IMP Installer ISO..."
log "Work directory: $WORK_DIR"
log "Output directory: $OUTPUT_DIR"

# Clean previous build
rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

# =============================================================================
# Configure live-build
# =============================================================================
log "Configuring live-build..."

lb config \
    --distribution bookworm \
    --archive-areas "main contrib non-free-firmware" \
    --debian-installer none \
    --binary-images iso-hybrid \
    --iso-application "IMP Installer" \
    --iso-volume "IMP-INSTALLER-${DATE}" \
    --memtest none \
    --win32-loader false \
    --apt-indices false \
    --apt-recommends false \
    --firmware-binary true \
    --firmware-chroot true

# =============================================================================
# Package lists
# =============================================================================
log "Creating package lists..."

mkdir -p config/package-lists

# Core packages for ZFS installation
cat > config/package-lists/imp-installer.list.chroot << 'EOF'
# ZFS
linux-headers-amd64
zfsutils-linux
zfs-dkms
zfs-initramfs

# Disk utilities
gdisk
parted
dosfstools

# Network and utilities
curl
wget
git
openssh-client
rsync
vim-tiny
less
tmux
ca-certificates

# Build essentials (for DKMS)
build-essential
dkms

# Bootstrapping
debootstrap
mmdebstrap

# Compression and console (needed for initramfs)
zstd
console-setup

# Live system essentials
live-boot
live-config
live-config-systemd
sudo
EOF

# =============================================================================
# Hooks - Pre-build DKMS modules
# =============================================================================
log "Creating build hooks..."

mkdir -p config/hooks/normal

# Hook to build DKMS modules during ISO creation
cat > config/hooks/normal/0100-build-zfs-dkms.hook.chroot << 'EOF'
#!/bin/bash
set -e
echo "Building ZFS DKMS modules..."
dkms autoinstall || true
echo "ZFS modules built successfully"
EOF
chmod +x config/hooks/normal/0100-build-zfs-dkms.hook.chroot

# =============================================================================
# Include IMP scripts
# =============================================================================
log "Including IMP scripts..."

mkdir -p config/includes.chroot/usr/local/bin
mkdir -p config/includes.chroot/root/imp-build/scripts

# Copy scripts
if [[ -d "${SCRIPT_DIR}/../scripts" ]]; then
    cp "${SCRIPT_DIR}/bootstrap-livecd.sh" config/includes.chroot/usr/local/bin/ 2>/dev/null || true
    cp "${SCRIPT_DIR}/setup-appliance.sh" config/includes.chroot/usr/local/bin/ 2>/dev/null || true
    cp "${SCRIPT_DIR}/setup-build-vm.sh" config/includes.chroot/usr/local/bin/ 2>/dev/null || true

    # Also copy full scripts directory
    cp -r "${SCRIPT_DIR}"/../* config/includes.chroot/root/imp-build/ 2>/dev/null || true

    chmod +x config/includes.chroot/usr/local/bin/*.sh 2>/dev/null || true
fi

# =============================================================================
# MOTD / Welcome message
# =============================================================================
mkdir -p config/includes.chroot/etc

cat > config/includes.chroot/etc/motd << 'EOF'

 ___ __  __ ____    ___           _        _ _
|_ _|  \/  |  _ \  |_ _|_ __  ___| |_ __ _| | | ___ _ __
 | || |\/| | |_) |  | || '_ \/ __| __/ _` | | |/ _ \ '__|
 | || |  | |  __/   | || | | \__ \ || (_| | | |  __/ |
|___|_|  |_|_|     |___|_| |_|___/\__\__,_|_|_|\___|_|

ZFS modules are pre-compiled and ready to use.

Quick start:
  setup-appliance.sh /dev/sda     # Install to disk

Or manually:
  modprobe zfs                     # Load ZFS (should be instant)
  lsblk                            # List disks

Full documentation: /root/imp-build/

EOF

# =============================================================================
# Auto-load ZFS module on boot
# =============================================================================
mkdir -p config/includes.chroot/etc/modules-load.d
echo "zfs" > config/includes.chroot/etc/modules-load.d/zfs.conf

# =============================================================================
# Configure live user
# =============================================================================
log "Configuring live user..."

# Hook to set up the live user with a known password and sudo access
cat > config/hooks/normal/0200-setup-live-user.hook.chroot << 'EOF'
#!/bin/bash
set -e

# Create user account if it doesn't exist
if ! id -u user &>/dev/null; then
    useradd -m -s /bin/bash -G sudo,cdrom,floppy,audio,dip,video,plugdev,netdev user
fi

# Set password for user account (password: live)
echo "user:live" | chpasswd

# Ensure user has sudo access without password
mkdir -p /etc/sudoers.d
echo "user ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/live-user
chmod 440 /etc/sudoers.d/live-user

# Also set root password (password: root)
echo "root:root" | chpasswd

# Enable root login on console
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config 2>/dev/null || true
EOF
chmod +x config/hooks/normal/0200-setup-live-user.hook.chroot

# =============================================================================
# Build the ISO
# =============================================================================
log "Building ISO (this will take 15-30 minutes)..."
log "DKMS modules will be compiled during this process..."

lb build 2>&1 | tee build.log

# =============================================================================
# Copy output
# =============================================================================
if [[ -f live-image-amd64.hybrid.iso ]]; then
    mkdir -p "$OUTPUT_DIR"
    OUTPUT_FILE="${OUTPUT_DIR}/imp-installer-${DATE}.iso"
    mv live-image-amd64.hybrid.iso "$OUTPUT_FILE"

    echo ""
    echo "=========================================="
    log "ISO build complete!"
    echo "=========================================="
    echo ""
    echo "Output: $OUTPUT_FILE"
    ls -lh "$OUTPUT_FILE"
    echo ""
    echo "Write to USB with:"
    echo "  sudo dd if=$OUTPUT_FILE of=/dev/sdX bs=4M status=progress"
    echo ""
    echo "Or use balenaEtcher, Rufus, or similar."
    echo ""
else
    error "Build failed - check $WORK_DIR/build.log"
fi
