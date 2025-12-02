# IMP Build System Installation Guide

This document captures the procedures for building and deploying ZFS-based appliance images for the Emerald IMP platform.

## Overview

The IMP build system creates bootable Debian images that can be deployed as ZFS boot environments. This enables atomic upgrades and instant rollback—critical for network infrastructure where a bad update can take down routing.

### Architecture

- **Boot Environment Management**: zfsbootmenu provides boot selection and rollback
- **Image Format**: ZFS send streams compressed with zstd
- **Target Platform**: Debian Trixie (testing) on x86_64
- **Container Runtime**: Incus (planned)

## Part 1: Appliance Setup

These steps create the initial appliance with ZFS root and zfsbootmenu.

### Prerequisites

- UEFI-capable system (physical or VM)
- Boot media: Debian Live ISO (Trixie)
- Target disk: 40GB+ recommended

### Boot Live Environment and Install ZFS

```bash
sudo -i

# Add contrib repository for ZFS
cat > /etc/apt/sources.list << 'EOF'
deb http://deb.debian.org/debian trixie main contrib
deb http://deb.debian.org/debian trixie-updates main contrib
EOF

apt update
apt install -y linux-headers-amd64 zfsutils-linux zfs-dkms dkms debootstrap gdisk

# Build DKMS modules if needed
dkms autoinstall
modprobe zfs
```

### Partition the Target Disk

Identify your disk with `lsblk`. Adjust `DISK` variable accordingly.

```bash
DISK=/dev/sda  # or /dev/vda in some VMs

sgdisk --zap-all $DISK

# Partition layout:
# 1: 1MB BIOS boot (legacy compatibility)
# 2: 512MB EFI System Partition
# 3: Remainder for ZFS
sgdisk -n1:1M:+1M -t1:EF02 $DISK
sgdisk -n2:0:+512M -t2:EF00 $DISK
sgdisk -n3:0:0 -t3:BF00 $DISK

# Format ESP
mkfs.fat -F32 ${DISK}2
```

### Create ZFS Pool and Datasets

```bash
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
    tank ${DISK}3

# Boot environment structure
zfs create -o canmount=off -o mountpoint=none tank/ROOT
zfs create -o canmount=noauto -o mountpoint=/ tank/ROOT/debian-initial

# Persistent data (survives across deployments)
zfs create -o canmount=off -o mountpoint=/persistent tank/persistent
zfs create tank/persistent/config
zfs create tank/persistent/data

# Mount for installation (use alternate mountpoint to avoid overlaying live root)
zfs set mountpoint=/mnt/root tank/ROOT/debian-initial
zfs mount tank/ROOT/debian-initial
```

### Bootstrap Debian

```bash
ROOTFS=/mnt/root

debootstrap --include=linux-image-amd64,linux-headers-amd64,systemd,systemd-sysv,dbus,locales,keyboard-configuration \
    trixie "$ROOTFS" https://deb.debian.org/debian
```

### Configure the System

```bash
# Mount virtual filesystems
mount --rbind /dev  "$ROOTFS/dev"
mount --rbind /proc "$ROOTFS/proc"
mount --rbind /sys  "$ROOTFS/sys"

# Mount ESP
mkdir -p "$ROOTFS/boot/efi"
mount ${DISK}2 "$ROOTFS/boot/efi"

# Enter chroot
chroot "$ROOTFS" /bin/bash
```

Inside the chroot:

```bash
# Basic config
echo "appliance" > /etc/hostname
echo "127.0.1.1 appliance" >> /etc/hosts

# Set root password
passwd

# Configure apt with contrib
cat > /etc/apt/sources.list << 'EOF'
deb http://deb.debian.org/debian trixie main contrib
deb http://deb.debian.org/debian trixie-updates main contrib
deb http://security.debian.org/debian-security trixie-security main contrib
EOF

apt update

# Install ZFS and boot support
apt install -y zfsutils-linux zfs-initramfs

# Install networking
apt install -y systemd-resolved iproute2 dhcpcd-base openssh-server

# Enable networking
cat > /etc/systemd/network/20-wired.network << 'EOF'
[Match]
Name=en*

[Network]
DHCP=yes
EOF

systemctl enable systemd-networkd systemd-resolved ssh
```

### Install zfsbootmenu

Still inside the chroot:

```bash
apt install -y curl efibootmgr

mkdir -p /boot/efi/EFI/ZBM
curl -L https://get.zfsbootmenu.org/efi -o /boot/efi/EFI/ZBM/zfsbootmenu.efi

# Create EFI boot entry (adjust disk path if needed)
efibootmgr -c -d /dev/sda -p 2 -L "ZFSBootMenu" -l '\EFI\ZBM\zfsbootmenu.efi'
```

### Set ZFS Boot Properties

```bash
# Set boot environment properties
zpool set bootfs=tank/ROOT/debian-initial tank
zfs set org.zfsbootmenu:commandline="quiet" tank/ROOT/debian-initial

# Regenerate initramfs
update-initramfs -u -k all

# Exit chroot
exit
```

### Finalize and Reboot

```bash
# Set mountpoint back to / for booting
zfs set mountpoint=/ tank/ROOT/debian-initial
zfs set mountpoint=/persistent/config tank/persistent/config
zfs set mountpoint=/persistent/data tank/persistent/data

# Unmount everything
umount "$ROOTFS/boot/efi"
umount -R "$ROOTFS/dev"
umount -R "$ROOTFS/proc"
umount -R "$ROOTFS/sys"
zfs unmount -a
zpool export tank

# Set boot order (ZFSBootMenu first)
efibootmgr -o 0004,0002,0001,0000,0003  # Adjust based on your efibootmgr -v output
```

Remove the live ISO and reboot. zfsbootmenu should present your boot environment.

## Part 2: Build VM Setup

A separate VM for building images keeps the build environment isolated.

### Create Build Pool

```bash
# File-backed pool for builds
truncate -s 10G /var/lib/build-pool.img
zpool create buildpool /var/lib/build-pool.img

zfs create buildpool/workspace

# Install build tools
apt install -y mmdebstrap squashfs-tools zstd
```

**Note**: The file-backed pool won't auto-import on reboot. Re-import with:

```bash
zpool import -d /var/lib buildpool
```

### Build Script

See `scripts/build-image.sh` for the image build script.

Usage:

```bash
build-image.sh debian-v1.0.0
```

This produces `/var/lib/images/debian-v1.0.0.zfs.zst`.

**Note**: Initial builds take 30+ minutes due to ZFS DKMS compilation in the chroot.

## Part 3: Deploying Images

### Transfer Image to Appliance

Use scp, shared storage, or any preferred method:

```bash
scp /var/lib/images/debian-v1.0.0.zfs.zst root@appliance:/tmp/
```

### Receive and Activate

On the appliance:

```bash
# Receive the image as a new boot environment
zstd -d < /tmp/debian-v1.0.0.zfs.zst | zfs receive tank/ROOT/debian-v1.0.0

# Set mountpoint for booting
zfs set mountpoint=/ tank/ROOT/debian-v1.0.0

# Activate as default boot environment
zpool set bootfs=tank/ROOT/debian-v1.0.0 tank

# Reboot into new image
reboot
```

### Rollback

To switch to a previous boot environment:

```bash
zpool set bootfs=tank/ROOT/debian-initial tank
reboot
```

### Managing Boot Environments

```bash
# List all boot environments
zfs list -r tank/ROOT

# Check current default
zpool get bootfs tank

# Check what's currently mounted
mount | grep tank
```

## zfsbootmenu Keyboard Shortcuts

At the zfsbootmenu screen:

- `Enter` — Boot selected environment
- `e` — Edit kernel command line
- `p` — Set selected as default (changes bootfs)
- `d` — Duplicate/clone environment
- `s` — Create snapshot
- `Escape` — Back/cancel
- `Ctrl+H` — Help

## Troubleshooting

### zfsbootmenu shows blank screen

Add console parameters:

```bash
zfs set org.zfsbootmenu:commandline="console=tty0 loglevel=7" tank/ROOT/your-environment
```

### Pool won't import (was in use by another system)

```bash
zpool import -f tank
```

### Build dataset stuck as "busy"

Export and reimport the build pool:

```bash
zpool export buildpool
zpool import -d /var/lib buildpool
```

### ZFS modules not loading in live environment

```bash
apt install -y linux-headers-amd64 zfs-dkms
dkms autoinstall
modprobe zfs
```
