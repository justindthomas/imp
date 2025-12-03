# IMP Build System Installation Guide

This document captures the procedures for building and deploying ZFS-based appliance images for the Emerald IMP platform.

## Overview

The IMP build system creates bootable Debian images that can be deployed as ZFS boot environments. This enables atomic upgrades and instant rollback—critical for network infrastructure where a bad update can take down routing.

### Architecture

- **Boot Environment Management**: zfsbootmenu provides boot selection and rollback
- **Image Format**: ZFS send streams compressed with zstd
- **Target Platform**: Debian Bookworm on x86_64 (Bookworm required for fd.io VPP packages)
- **Dataplane**: VPP (Vector Packet Processing) with DPDK
- **Routing**: FRR (Free Range Routing) in dedicated network namespace
- **Container Runtime**: Incus (from bookworm-backports)

## Part 1: Appliance Setup

These steps create the initial appliance with ZFS root and zfsbootmenu.

### Prerequisites

- UEFI-capable system (physical or VM)
- Boot media: IMP Installer ISO (recommended) or Debian Live ISO (Bookworm)
- Target disk: 40GB+ recommended

### Option A: IMP Installer ISO (Recommended)

Use the pre-built IMP Installer ISO which includes ZFS modules already compiled (saves 30-45 minutes):

```bash
# Write ISO to USB
sudo dd if=imp-installer-YYYYMMDD.iso of=/dev/sdX bs=4M status=progress

# Boot from USB, then simply run:
setup-appliance.sh /dev/sda
```

To build the installer ISO yourself (requires a Debian Bookworm system):

```bash
apt install live-build
./scripts/build-installer-iso.sh /var/lib/images
# Output: /var/lib/images/imp-installer-YYYYMMDD.iso
```

### Option B: Stock Debian Live ISO

If using the stock Debian Live ISO, you'll need to compile ZFS modules first (30-45 minutes).

Two scripts automate the process - first bootstrap ZFS on the Live CD, then run the appliance setup:

```bash
sudo -i

# Step 1: Bootstrap ZFS on the Live CD
curl -sL https://raw.githubusercontent.com/your-org/imp-build/main/scripts/bootstrap-livecd.sh | bash

# Step 2: Run appliance setup
curl -LO https://raw.githubusercontent.com/your-org/imp-build/main/scripts/setup-appliance.sh
chmod +x setup-appliance.sh
./setup-appliance.sh /dev/sda
```

Or clone the repo:

```bash
sudo -i
apt update && apt install -y git curl

git clone https://github.com/your-org/imp-build.git
cd imp-build
./scripts/bootstrap-livecd.sh
./scripts/setup-appliance.sh /dev/sda
```

The script will:
1. Partition the disk (BIOS boot, ESP, ZFS)
2. Create ZFS pool with boot environments and persistent datasets
3. Bootstrap Debian Bookworm
4. Install zfsbootmenu
5. Configure the system for first boot

### Manual Setup

If you prefer to run steps manually, see below.

#### Boot Live Environment and Install ZFS

```bash
sudo -i

# Add contrib repository for ZFS
cat > /etc/apt/sources.list << 'EOF'
deb http://deb.debian.org/debian bookworm main contrib
deb http://deb.debian.org/debian bookworm-updates main contrib
deb http://deb.debian.org/debian bookworm-backports main contrib
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
    bookworm "$ROOTFS" https://deb.debian.org/debian
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

# Configure apt with contrib and backports
cat > /etc/apt/sources.list << 'EOF'
deb http://deb.debian.org/debian bookworm main contrib
deb http://deb.debian.org/debian bookworm-updates main contrib
deb http://deb.debian.org/debian bookworm-backports main contrib
deb http://security.debian.org/debian-security bookworm-security main contrib
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

A separate VM for building images keeps the build environment isolated. The build VM should run Debian Bookworm.

### Automated Setup

The `setup-build-vm.sh` script automates build VM initialization:

```bash
sudo -i

# Clone the repo or download the scripts
git clone https://github.com/your-org/imp-build.git
cd imp-build

# Run setup (default 10G pool, or specify size)
./scripts/setup-build-vm.sh 20G
```

The script will:
1. Install ZFS and build dependencies
2. Create a file-backed ZFS pool
3. Install the build script to `/usr/local/bin/`

### Manual Setup

If you prefer to run steps manually:

```bash
# File-backed pool for builds
truncate -s 10G /var/lib/build-pool.img
zpool create buildpool /var/lib/build-pool.img

zfs create buildpool/workspace

# Install build tools
apt install -y mmdebstrap squashfs-tools zstd curl gnupg zfsutils-linux
```

**Note**: The file-backed pool won't auto-import on reboot. Re-import with:

```bash
zpool import -d /var/lib buildpool
```

### Build Script

See `scripts/build-image.sh` for the image build script. The script builds images with:

- Debian Bookworm base system
- ZFS root filesystem support
- VPP (from fd.io repository)
- FRR (Free Range Routing)
- Incus (from bookworm-backports)
- Dataplane namespace configuration

Usage:

```bash
./scripts/build-image.sh imp-v1.0.0
```

This produces `/var/lib/images/imp-v1.0.0.zfs.zst`.

**Note**: Initial builds take 30+ minutes due to ZFS DKMS compilation in the chroot.

## Part 3: Deploying Images

### Transfer Image to Appliance

Use scp, shared storage, or any preferred method:

```bash
scp /var/lib/images/imp-v1.0.0.zfs.zst root@appliance:/tmp/
```

### Receive and Activate

On the appliance:

```bash
# Receive the image as a new boot environment
zstd -d < /tmp/imp-v1.0.0.zfs.zst | zfs receive tank/ROOT/imp-v1.0.0

# Set mountpoint for booting
zfs set mountpoint=/ tank/ROOT/imp-v1.0.0

# Activate as default boot environment
zpool set bootfs=tank/ROOT/imp-v1.0.0 tank

# Reboot into new image
reboot
```

### Post-Deployment Configuration

After deploying an image and rebooting, run the interactive configuration script:

```bash
# Interactive configuration
configure-router.py

# Or use the symlink
configure-router
```

The script will guide you through:

1. **Interface Discovery** — Detects physical NICs and shows name, MAC, PCI address
2. **Role Assignment** — Select which interface is management, external (WAN), and internal (LAN)
3. **IP Configuration** — Enter IPv4/IPv6 addresses for external and internal interfaces
4. **Management Configuration** — Choose DHCP or static IP for management interface
5. **BGP Configuration** — Optionally configure BGP peering
6. **NAT Configuration** — Set NAT pool and internal networks to NAT

Configuration is saved to `/persistent/config/router.json` and survives image upgrades.

#### Re-applying Configuration

After deploying a new image, re-apply existing configuration:

```bash
configure-router.py --apply-only
```

#### Initialize Incus

After router configuration, initialize Incus (first boot only):
```bash
# Interactive setup
incus-init.sh

# Or non-interactive with defaults
incus-init.sh --non-interactive
```

This script:
- Initializes Incus with a default storage pool
- Creates incusbr0 bridge with 10.234.116.0/24
- Disables Incus NAT (VPP handles NAT)
- Sets gateway to VPP's host-interface (10.234.116.5)
- Configures DHCP range for containers

#### Manual Configuration (Advanced)

If you need to manually edit configuration files:

| File | Purpose |
|------|---------|
| `/etc/vpp/startup-core.conf` | VPP startup config, PCI addresses |
| `/etc/vpp/commands-core.txt` | VPP runtime config, IPs, routes, ACLs |
| `/etc/vpp/commands-nat.txt` | NAT pool mappings |
| `/etc/frr/frr.conf` | BGP configuration |
| `/etc/systemd/system/netns-move-interfaces.service` | Interface names to move |

After making manual changes, restart services:
```bash
systemctl restart vpp-core vpp-nat frr
```

### Rollback

To switch to a previous boot environment:

```bash
zpool set bootfs=tank/ROOT/imp-v0.9.0 tank
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

## Part 4: Verifying the Dataplane

After deploying an image and rebooting, verify the dataplane is operational:

### Check Service Status

```bash
# All dataplane services should be active
systemctl status netns-dataplane
systemctl status vpp-core
systemctl status vpp-nat
systemctl status frr
```

### Verify Namespace

```bash
# Should show "dataplane"
ip netns list

# Check interfaces in dataplane namespace
ip netns exec dataplane ip link
```

### Verify VPP

```bash
# Connect to VPP CLI
vppctl -s /run/vpp/core-cli.sock

# Inside vppctl:
show version
show interface
show interface address
show ip fib
```

### Verify FRR

```bash
# Run vtysh in the dataplane namespace
ip netns exec dataplane vtysh

# Inside vtysh:
show ip bgp summary
show ip route
```

### Verify NAT

```bash
# Connect to NAT instance
vppctl -s /run/vpp/nat-cli.sock

# Inside vppctl:
show det44 sessions
```

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

### VPP fails to start

Check logs:
```bash
journalctl -u vpp-core
cat /var/log/vpp/core.log
```

Common issues:
- **DPDK can't bind to NIC**: Check PCI addresses in `startup-core.conf`
- **Permission denied on socket**: Ensure `/run/vpp/` directory exists
- **Interface not found**: Ensure `netns-move-interfaces` ran before VPP

### FRR not establishing BGP sessions

```bash
# Check FRR is running in correct namespace
ip netns exec dataplane pgrep -a bgpd

# Check connectivity to peer
ip netns exec dataplane ping <peer-address>

# Check VPP created TAP interfaces
vppctl -s /run/vpp/core-cli.sock show lcp
```

### Dataplane namespace not created

```bash
# Check service status
systemctl status netns-dataplane

# Manually create if needed (for debugging)
ip netns add dataplane
```
