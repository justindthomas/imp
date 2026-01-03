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
pciutils
net-tools

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

# Python (for IMP tools and agent)
python3
python3-jinja2
python3-prompt-toolkit
python3-requests
python3-rich
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
    cp "${SCRIPT_DIR}/install-imp" config/includes.chroot/usr/local/bin/ 2>/dev/null || true
    cp "${SCRIPT_DIR}/setup-build-vm.sh" config/includes.chroot/usr/local/bin/ 2>/dev/null || true

    # Also copy full scripts and config directory
    cp -r "${SCRIPT_DIR}"/../* config/includes.chroot/root/imp-build/ 2>/dev/null || true

    chmod +x config/includes.chroot/usr/local/bin/*.sh 2>/dev/null || true
fi

# =============================================================================
# Boot menu customization (ISOLINUX for legacy BIOS)
# =============================================================================
log "Customizing ISOLINUX boot menu..."

mkdir -p config/bootloaders/isolinux

# Copy default bootloader files first, then customize
if [[ -d /usr/share/live/build/bootloaders/isolinux ]]; then
    cp -r /usr/share/live/build/bootloaders/isolinux/* config/bootloaders/isolinux/
    # Remove default configs that we'll replace with our own
    rm -f config/bootloaders/isolinux/live.cfg
    rm -f config/bootloaders/isolinux/isolinux.cfg
    rm -f config/bootloaders/isolinux/menu.cfg
    rm -f config/bootloaders/isolinux/stdmenu.cfg
fi

# Create isolinux.cfg - the main entry point
cat > config/bootloaders/isolinux/isolinux.cfg << 'ISOLINUXCFGEOF'
# IMP Router Installer - ISOLINUX config
path
include menu.cfg
include live.cfg
default vesamenu.c32
prompt 0
timeout 50
ISOLINUXCFGEOF

# Override menu.cfg with custom styling (text-only, no graphics)
cat > config/bootloaders/isolinux/menu.cfg << 'MENUEOF'
menu hshift 0
menu width 82
menu title IMP Router Installer
menu color title	1;36;44  #c0ffffff #00000000 std
menu color sel		7;37;40  #e0ffffff #20ffffff all
menu color hotsel	1;7;37;40 #e0ffffff #20ffffff all
menu color tabmsg	31;40    #90ffff00 #00000000 std
menu vshift 8
menu rows 10
menu tabmsgrow 18
menu tabmsg Press ENTER to boot or TAB to edit a menu entry
MENUEOF

# Create live.cfg with the actual boot entries
cat > config/bootloaders/isolinux/live.cfg << 'LIVECFGEOF'
label live-amd64
    menu label ^IMP Router Installer
    menu default
    linux /live/vmlinuz
    initrd /live/initrd.img
    append boot=live components quiet splash

label live-amd64-failsafe
    menu label IMP Router Installer (^fail-safe mode)
    linux /live/vmlinuz
    initrd /live/initrd.img
    append boot=live components memtest noapic noapm nodma nomce nolapic nomodeset nosmp nosplash vga=normal
LIVECFGEOF

# =============================================================================
# Boot menu customization (GRUB EFI)
# =============================================================================
log "Customizing GRUB EFI boot menu..."

# Copy default grub-pc bootloader files (used for both BIOS and EFI in live-build)
if [[ -d /usr/share/live/build/bootloaders/grub-pc ]]; then
    cp -r /usr/share/live/build/bootloaders/grub-pc config/bootloaders/
    # Remove default configs we're replacing
    rm -f config/bootloaders/grub-pc/grub.cfg
    rm -f config/bootloaders/grub-pc/config.cfg
    rm -f config/bootloaders/grub-pc/splash.cfg
fi

mkdir -p config/bootloaders/grub-pc

# Override config.cfg with simple settings
cat > config/bootloaders/grub-pc/config.cfg << 'CONFIGEOF'
set timeout=5
set default=0
set menu_color_normal=light-gray/black
set menu_color_highlight=white/blue
CONFIGEOF

# Empty splash.cfg to remove Debian branding
cat > config/bootloaders/grub-pc/splash.cfg << 'SPLASHEOF'
# No splash screen
SPLASHEOF

# Simple grub.cfg with just menu entries
cat > config/bootloaders/grub-pc/grub.cfg << 'GRUBEOF'
set default=0
set timeout=5
set menu_color_normal=light-gray/black
set menu_color_highlight=white/blue

menuentry "IMP Router Installer" {
    linux /live/vmlinuz boot=live components quiet splash
    initrd /live/initrd.img
}

menuentry "IMP Router Installer (fail-safe mode)" {
    linux /live/vmlinuz boot=live components memtest noapic noapm nodma nomce nolapic nomodeset nosmp nosplash vga=normal
    initrd /live/initrd.img
}
GRUBEOF

# Also customize grub-efi if it exists (some live-build versions use separate dirs)
if [[ -d /usr/share/live/build/bootloaders/grub-efi ]]; then
    cp -r /usr/share/live/build/bootloaders/grub-efi config/bootloaders/
    # Remove default configs we're replacing
    rm -f config/bootloaders/grub-efi/grub.cfg
    rm -f config/bootloaders/grub-efi/config.cfg
    rm -f config/bootloaders/grub-efi/splash.cfg
    # Copy our customizations to grub-efi as well
    cp config/bootloaders/grub-pc/grub.cfg config/bootloaders/grub-efi/
    cp config/bootloaders/grub-pc/config.cfg config/bootloaders/grub-efi/
    cp config/bootloaders/grub-pc/splash.cfg config/bootloaders/grub-efi/
fi

# =============================================================================
# MOTD / Welcome message
# =============================================================================
mkdir -p config/includes.chroot/etc

cat > config/includes.chroot/etc/motd << 'EOF'

 ___ __  __ ____    ____             _
|_ _|  \/  |  _ \  |  _ \ ___  _   _| |_ ___ _ __
 | || |\/| | |_) | | |_) / _ \| | | | __/ _ \ '__|
 | || |  | |  __/  |  _ < (_) | |_| | ||  __/ |
|___|_|  |_|_|     |_| \_\___/ \__,_|\__\___|_|

ZFS modules are pre-compiled and ready to use.

Quick start:
  install-imp /dev/sda                          # Bootstrap from internet
  install-imp --image system.zfs.zst /dev/sda   # Install from snapshot

Or manually:
  modprobe zfs                  # Load ZFS (should be instant)
  lsblk                         # List disks

After install, run:
  imp                           # Interactive configuration REPL
  imp config edit               # Interactive network configuration
  imp status                    # Check service status

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
