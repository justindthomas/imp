#!/bin/bash
#
# deploy-image.sh - Deploy a ZFS boot environment image
#
# Usage: deploy-image.sh <image.zfs.zst> [--activate]
# Example: deploy-image.sh /tmp/debian-v1.0.0.zfs.zst --activate
#
# Options:
#   --activate    Set the new environment as default and reboot
#

set -euo pipefail

IMAGE="${1:?Usage: deploy-image.sh <image.zfs.zst> [--activate]}"
ACTIVATE="${2:-}"
POOL="tank"

# Extract version from filename
VERSION=$(basename "$IMAGE" .zfs.zst)
TARGET_DATASET="${POOL}/ROOT/${VERSION}"

# Check if already exists
if zfs list "$TARGET_DATASET" &>/dev/null; then
    echo "Error: Dataset $TARGET_DATASET already exists"
    echo "To replace, first destroy it: zfs destroy -r $TARGET_DATASET"
    exit 1
fi

# Receive the image
echo "Receiving image into $TARGET_DATASET..."
zstd -d < "$IMAGE" | zfs receive "$TARGET_DATASET"

# Set mountpoint for booting
zfs set mountpoint=/ "$TARGET_DATASET"

echo "Deployed: $TARGET_DATASET"
echo ""

# List all boot environments
echo "Available boot environments:"
zfs list -r "${POOL}/ROOT"
echo ""

CURRENT_BOOTFS=$(zpool get -H -o value bootfs "$POOL")
echo "Current default: $CURRENT_BOOTFS"

if [[ "$ACTIVATE" == "--activate" ]]; then
    echo ""
    echo "Activating $TARGET_DATASET..."
    zpool set bootfs="$TARGET_DATASET" "$POOL"
    echo "Rebooting in 5 seconds... (Ctrl+C to cancel)"
    sleep 5
    reboot
else
    echo ""
    echo "To activate this environment:"
    echo "  zpool set bootfs=$TARGET_DATASET $POOL"
    echo "  reboot"
fi
