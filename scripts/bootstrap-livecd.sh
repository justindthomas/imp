#!/bin/bash
#
# bootstrap-livecd.sh - Prepare Debian Live CD for ZFS operations
#
# This script adds ZFS support to a Debian Live environment.
# Run this first, then use setup-appliance.sh.
#
# Usage: curl -L <url>/bootstrap-livecd.sh | sudo bash
#    or: sudo ./bootstrap-livecd.sh
#

set -euo pipefail

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Check we're running as root
[[ $EUID -ne 0 ]] && error "This script must be run as root (use sudo)"

echo ""
echo "=========================================="
echo "  IMP Live CD Bootstrap"
echo "=========================================="
echo ""

# =============================================================================
# Configure apt with contrib
# =============================================================================
log "Configuring apt repositories..."

cat > /etc/apt/sources.list << 'EOF'
deb http://deb.debian.org/debian bookworm main contrib
deb http://deb.debian.org/debian bookworm-updates main contrib
deb http://deb.debian.org/debian bookworm-backports main contrib
EOF

# =============================================================================
# Install packages
# =============================================================================
log "Updating package lists..."
apt-get update

log "Installing ZFS and utilities (this may take a few minutes)..."
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    linux-headers-amd64 \
    zfsutils-linux \
    zfs-dkms \
    dkms \
    debootstrap \
    gdisk \
    curl \
    git

# =============================================================================
# Build and load ZFS module
# =============================================================================
log "Building ZFS kernel modules (if needed)..."
dkms autoinstall || true  # May return non-zero if modules already exist

log "Loading ZFS module..."
modprobe zfs

# Give it a moment to fully load
sleep 1

# Verify
if lsmod | grep -q "^zfs "; then
    log "ZFS module loaded successfully"
elif command -v zpool &>/dev/null && zpool --version &>/dev/null; then
    log "ZFS commands available"
else
    warn "Could not verify ZFS module, but it may still work"
    warn "Try running: modprobe zfs && zpool --version"
fi

# =============================================================================
# Show status
# =============================================================================
echo ""
echo "=========================================="
log "Live CD bootstrap complete!"
echo "=========================================="
echo ""
echo "ZFS version:"
zfs --version
echo ""
echo "You can now run the appliance setup:"
echo ""
echo "  # Option 1: Download and run"
echo "  curl -LO https://raw.githubusercontent.com/your-org/imp-build/main/scripts/setup-appliance.sh"
echo "  chmod +x setup-appliance.sh"
echo "  ./setup-appliance.sh /dev/sda"
echo ""
echo "  # Option 2: Clone repo and run"
echo "  git clone https://github.com/your-org/imp-build.git"
echo "  cd imp-build"
echo "  ./scripts/setup-appliance.sh /dev/sda"
echo ""
echo "List available disks with: lsblk"
echo ""
