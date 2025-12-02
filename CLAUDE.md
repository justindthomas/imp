# CLAUDE.md - IMP Build System

## Project Overview

IMP (Infrastructure Management Platform) is a ZFS-based appliance build system for network infrastructure. The target use case is a custom routing/services platform for a small ISP, replacing commercial platforms like Juniper MX480 and SONiC-based systems.

### Design Goals

- **Atomic upgrades with instant rollback** — Critical for network infrastructure where a bad update can break routing
- **Externally-built images** — Build once, deploy to many appliances
- **Boot environment management** — Multiple OS versions coexist, switch between them with a reboot
- **Container-ready** — Platform will run Incus containers for services (FRR, DNS, DHCP, IDS, etc.)

### Architecture Decisions

- **ZFS root with zfsbootmenu** — Provides boot environment selection, snapshots, send/receive for image deployment
- **Debian Trixie (testing)** — Target distribution, provides recent packages
- **Separate build VM** — Images are built externally and deployed via `zfs send | zfs receive`
- **File-backed build pool** — Build VM uses a file-backed ZFS pool (`/var/lib/build-pool.img`) for image creation

## Repository Structure

```
imp-build/
├── CLAUDE.md           # This file
├── INSTALL.md          # Manual installation procedures
└── scripts/
    ├── build-image.sh  # Builds a deployable ZFS image on the build VM
    └── deploy-image.sh # Deploys an image to an appliance
```

## Current State

### What's Working

- Base appliance boots from ZFS root with zfsbootmenu
- Boot environment switching via `zpool set bootfs=tank/ROOT/<env> tank`
- Image build script produces compressed ZFS send streams (`.zfs.zst` files)
- Images can be received and booted on target appliances
- Persistent datasets at `/persistent/config` and `/persistent/data` survive across deployments

### What Needs Work

- **Build time optimization** — ZFS DKMS compilation takes 30+ minutes per build. Consider pre-compiled modules or a base image with ZFS already built.
- **Persistent state integration** — Symlinks or bind mounts from `/persistent/` into the root filesystem for machine-specific config
- **Incus integration** — Container runtime not yet installed or configured
- **Service configuration** — No routing daemons, DNS, DHCP, or other services yet
- **Image versioning/metadata** — No systematic way to track what's in an image
- **Automated testing** — No validation that a built image actually boots

## Key Commands

### On the Build VM

```bash
# Import build pool (required after reboot)
zpool import -d /var/lib buildpool

# Build an image
/usr/local/bin/build-image.sh debian-v1.0.1

# Output location
ls -lh /var/lib/images/
```

### On the Appliance

```bash
# List boot environments
zfs list -r tank/ROOT

# Check current boot environment
zpool get bootfs tank
mount | grep "on / "

# Deploy a new image
zstd -d < /tmp/debian-v1.0.1.zfs.zst | zfs receive tank/ROOT/debian-v1.0.1
zfs set mountpoint=/ tank/ROOT/debian-v1.0.1

# Switch boot environment
zpool set bootfs=tank/ROOT/debian-v1.0.1 tank
reboot

# Rollback to previous
zpool set bootfs=tank/ROOT/debian-v1.0.0 tank
reboot
```

## ZFS Dataset Layout

```
tank/
├── ROOT/                      # Boot environments (one per deployment)
│   ├── debian-initial/        # First manually-installed system
│   ├── debian-v1.0.0/         # Deployed image
│   └── debian-v1.0.1/         # Another deployed image
└── persistent/                # Survives across deployments
    ├── config/                # Machine-specific configuration
    └── data/                  # Application data
```

## Build Script Details

`scripts/build-image.sh` uses mmdebstrap to create a minimal Debian system with:

- systemd, dbus
- Linux kernel and headers
- ZFS utilities and initramfs support
- SSH server
- Basic networking (systemd-networkd, dhcpcd-base)
- Locale and console setup

The script:
1. Creates a fresh dataset in the build pool
2. Bootstraps Debian into it
3. Configures basic system settings
4. Sets ZFS properties for bootability (`canmount=noauto`, `org.zfsbootmenu:commandline`)
5. Snapshots and sends to a compressed file

### Customization Points

To add packages or configuration to built images, modify `build-image.sh`:

- Add packages to the `--include=` list in the mmdebstrap command
- Add configuration steps after the bootstrap (before `update-initramfs`)
- For complex customization, consider a hook script system

## Environment Details

### Build VM

- Debian Trixie
- File-backed ZFS pool at `/var/lib/build-pool.img`
- Build output in `/var/lib/images/`
- Pool must be manually imported after reboot: `zpool import -d /var/lib buildpool`

### Appliance

- Debian Trixie on ZFS root
- Pool name: `tank`
- ESP mounted at `/boot/efi`
- zfsbootmenu EFI binary at `/boot/efi/EFI/ZBM/zfsbootmenu.efi`

## Next Steps

Suggested development priorities:

1. **Add Incus to build script** — Install and configure Incus in built images
2. **Create base service containers** — FRR for routing, Unbound for DNS, Kea for DHCP
3. **Persistent state design** — Define what persists across upgrades and how
4. **Image metadata** — Embed version info, build date, package manifest in images
5. **Network configuration** — Move beyond DHCP to static/configured networking appropriate for a router
6. **Build caching** — Speed up builds by reusing base layers or pre-compiled DKMS modules

## Testing

### Manual Boot Test

1. Build image on build VM
2. Transfer to appliance
3. Deploy with `zfs receive`
4. Set as bootfs and reboot
5. Verify correct environment is mounted at `/`
6. Verify networking works
7. Verify rollback to previous environment works

### Validation Checklist

- [ ] Image boots successfully
- [ ] ZFS pool imports automatically
- [ ] Network comes up
- [ ] SSH accessible
- [ ] Persistent datasets mounted
- [ ] Can switch to different boot environment
