#!/bin/bash
#
# setup-build-vm.sh - Set up a build VM for IMP image creation
#
# This script creates a file-backed ZFS pool and installs the tools
# needed to build IMP appliance images.
#
# Usage: setup-build-vm.sh [pool-size]
# Example: setup-build-vm.sh 20G
#
# Run on a Debian Bookworm system.
#

set -euo pipefail

POOL_SIZE="${1:-10G}"
POOL_FILE="/var/lib/build-pool.img"
POOL_NAME="buildpool"
OUTPUT_DIR="/var/lib/images"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[+]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Check we're running as root
[[ $EUID -ne 0 ]] && error "This script must be run as root"

echo ""
echo "=========================================="
echo "  IMP Build VM Setup"
echo "=========================================="
echo ""
echo "Pool file: $POOL_FILE"
echo "Pool size: $POOL_SIZE"
echo "Pool name: $POOL_NAME"
echo ""

# =============================================================================
# Install dependencies
# =============================================================================
log "Installing build dependencies..."

apt-get update
apt-get install -y \
    zfsutils-linux \
    mmdebstrap \
    squashfs-tools \
    zstd \
    curl \
    gnupg \
    debootstrap

# Load ZFS module if needed
if ! lsmod | grep -q "^zfs "; then
    log "Loading ZFS kernel module..."
    modprobe zfs
fi

# =============================================================================
# Create file-backed pool
# =============================================================================
if [[ -f "$POOL_FILE" ]]; then
    warn "Pool file $POOL_FILE already exists"

    # Check if pool is already imported
    if zpool list "$POOL_NAME" &>/dev/null; then
        log "Pool $POOL_NAME is already imported"
    else
        log "Importing existing pool..."
        zpool import -d /var/lib "$POOL_NAME" || error "Failed to import pool"
    fi
else
    log "Creating ${POOL_SIZE} file-backed pool..."

    truncate -s "$POOL_SIZE" "$POOL_FILE"
    zpool create "$POOL_NAME" "$POOL_FILE"

    log "Creating workspace dataset..."
    zfs create "${POOL_NAME}/workspace"
fi

# =============================================================================
# Create output directory
# =============================================================================
log "Creating image output directory..."
mkdir -p "$OUTPUT_DIR"

# =============================================================================
# Install build script
# =============================================================================
log "Installing build script..."

if [[ -f "${SCRIPT_DIR}/build-image.sh" ]]; then
    cp "${SCRIPT_DIR}/build-image.sh" /usr/local/bin/build-image.sh
    chmod +x /usr/local/bin/build-image.sh
    log "Installed build-image.sh to /usr/local/bin/"
else
    warn "build-image.sh not found in ${SCRIPT_DIR}"
    warn "You'll need to copy it manually"
fi

# =============================================================================
# Summary
# =============================================================================
echo ""
echo "=========================================="
log "Build VM setup complete!"
echo "=========================================="
echo ""
echo "Pool status:"
zpool status "$POOL_NAME"
echo ""
echo "Workspace:"
zfs list -r "$POOL_NAME"
echo ""
echo "Usage:"
echo "  build-image.sh imp-v1.0.0"
echo ""
echo "Output will be in: $OUTPUT_DIR"
echo ""
warn "Note: The pool won't auto-import after reboot."
echo "Re-import with: zpool import -d /var/lib $POOL_NAME"
echo ""
echo "To make it persistent, add to /etc/rc.local or create a systemd unit:"
echo "  zpool import -d /var/lib $POOL_NAME"
echo ""
