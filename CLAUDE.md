# CLAUDE.md - IMP Build System

## Project Overview

IMP (Infrastructure Management Platform) is a ZFS-based appliance build system for network infrastructure. The target use case is a custom routing/services platform for a small ISP, replacing commercial platforms like Juniper MX480 and SONiC-based systems.

### Design Goals

- **Atomic upgrades with instant rollback** — Critical for network infrastructure where a bad update can break routing
- **Externally-built images** — Build once, deploy to many appliances
- **Boot environment management** — Multiple OS versions coexist, switch between them with a reboot
- **High-performance dataplane** — VPP (Vector Packet Processing) for wire-speed forwarding
- **Container-ready** — Incus containers for services (DNS, DHCP, IDS, etc.)

### Architecture Decisions

- **ZFS root with zfsbootmenu** — Provides boot environment selection, snapshots, send/receive for image deployment
- **Debian Bookworm** — Target distribution (fd.io doesn't have Trixie packages yet)
- **VPP for dataplane** — Userspace packet processing with DPDK, memif for inter-process communication
- **Dedicated dataplane namespace** — Network namespace isolation between management and dataplane
- **FRR for routing** — BGP, OSPF running in the dataplane namespace, synced to VPP via linux_cp plugin
- **Incus for services** — Containers bridged into VPP via veth + host-interface
- **Separate build VM** — Images are built externally and deployed via `zfs send | zfs receive`

## Repository Structure

```
imp-build/
├── CLAUDE.md               # This file
├── INSTALL.md              # Manual installation procedures
├── diagrams/
│   └── vpp.pdf             # Architecture diagram
├── config/                 # Configuration files copied into images
│   ├── etc/
│   │   ├── apt/sources.list.d/
│   │   │   └── fdio_release.list
│   │   ├── frr/
│   │   │   ├── daemons
│   │   │   ├── frr.conf
│   │   │   └── vtysh.conf
│   │   ├── systemd/system/
│   │   │   ├── netns-dataplane.service
│   │   │   ├── netns-move-interfaces.service
│   │   │   ├── vpp-core.service
│   │   │   ├── vpp-core-config.service
│   │   │   ├── vpp-nat.service
│   │   │   └── incus-dataplane.service
│   │   └── vpp/
│   │       ├── startup-core.conf
│   │       ├── startup-nat.conf
│   │       ├── commands-core.txt
│   │       └── commands-nat.txt
│   └── usr/local/bin/
│       ├── vpp-core-config.sh
│       ├── incus-networking.sh
│       ├── incus-init.sh
│       └── wait-for-iface-load
└── scripts/
    ├── build-installer-iso.sh  # Build custom Live ISO with ZFS pre-compiled
    ├── bootstrap-livecd.sh     # Add ZFS support to stock Debian Live CD
    ├── setup-appliance.sh      # Initial ZFS setup from Live CD
    ├── setup-build-vm.sh       # Build VM initialization
    ├── build-image.sh          # Builds a deployable ZFS image
    └── deploy-image.sh         # Deploys an image to an appliance
```

## Dataplane Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Default Namespace                         │
│  ┌──────────┐  ┌─────────────────────────────────────────────┐  │
│  │   SSH    │  │              Incus Containers               │  │
│  │  Server  │  │  ┌──────┐  ┌──────┐  ┌──────────┐          │  │
│  └────┬─────┘  │  │ DHCP │  │ DNS  │  │ Suricata │  ...     │  │
│       │        │  └──┬───┘  └──┬───┘  └────┬─────┘          │  │
│  management    │     └─────────┴───────────┘                 │  │
│                │              incusbr0                        │  │
│                └───────────────┬─────────────────────────────┘  │
│                                │ veth                            │
├────────────────────────────────┼────────────────────────────────┤
│                        Dataplane Namespace                       │
│                                │                                 │
│  ┌─────────────────────────────┴─────────────────────────────┐  │
│  │                         VPP Core                           │  │
│  │  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌────────────┐   │  │
│  │  │ external │  │ internal │  │ memif  │  │ host-if    │   │  │
│  │  │  (DPDK)  │  │  (DPDK)  │  │  1/0   │  │ incus-dp   │   │  │
│  │  └────┬─────┘  └────┬─────┘  └───┬────┘  └─────┬──────┘   │  │
│  │       │             │            │             │           │  │
│  │  linux_cp: TAP interfaces for FRR visibility              │  │
│  └───────┼─────────────┼────────────┼─────────────┼──────────┘  │
│          │             │            │             │              │
│  ┌───────┴─────────────┴────┐      │             │              │
│  │          FRR             │      │             │              │
│  │  (BGP, OSPF, OSPF6)      │   ┌──┴──────────┐  │              │
│  │  watchfrr --netns=dp     │   │   VPP NAT   │  │              │
│  └──────────────────────────┘   │   (det44)   │  │              │
│                                 │   memif     │  │              │
│                                 └─────────────┘  │              │
└──────────────────────────────────────────────────┴──────────────┘
                    │                               │
                    ▼                               ▼
              ┌──────────┐                    ┌──────────┐
              │ Internet │                    │ Internal │
              │ VLAN 25  │                    │ VLAN 30  │
              └──────────┘                    └──────────┘
```

### Service Startup Chain

```
netns-dataplane.service          # Create isolated namespace (PrivateNetwork=yes trick)
        │
        ▼
netns-move-interfaces.service    # Move physical NICs into namespace
        │
        ▼
vpp-core.service                 # DPDK-based VPP, linux_cp for TAPs
        │
        ├──► vpp-core-config.service   # IPv6 RA configuration
        │
        ▼
vpp-nat.service                  # memif-connected NAT instance (det44)
        │
        ▼
frr.service                      # Runs in dataplane namespace
        │
        ▼
incus-dataplane.service          # Bridge Incus containers to VPP
```

### Key Integration Points

- **VPP ↔ Linux**: `linux_cp_plugin` creates TAP interfaces mirroring VPP interfaces, visible to FRR
- **VPP Core ↔ VPP NAT**: memif sockets (`/run/vpp/memif-nat-{int,ext}.sock`)
- **VPP ↔ Incus**: veth pair bridges incusbr0 to VPP via `create host-interface`
- **FRR ↔ Namespace**: `watchfrr_options="--netns=dataplane"` in `/etc/frr/daemons`
- **Policy routing**: ACL-based forwarding (ABF) steers private traffic to NAT instance

### Incus Container Networking

Incus containers connect to the network through VPP:

```
Container → incusbr0 → veth → host-incus-dataplane (VPP) → NAT/Internet
```

**incusbr0 configuration** (set by `incus-init.sh`):
- `ipv4.address`: 10.234.116.1/24 (bridge IP)
- `ipv4.nat`: false (VPP handles NAT, not Incus)
- `ipv4.dhcp.gateway`: 10.234.116.5 (VPP's host-interface IP)
- `ipv4.dhcp.ranges`: 10.234.116.100-254
- `ipv6.address`: none (VPP handles RA on host-incus-dataplane)

**Traffic flow**:
1. Container gets DHCP lease from incusbr0 (10.234.116.x)
2. Default gateway points to 10.234.116.5 (VPP)
3. VPP applies ACL/ABF policy, forwards to NAT instance
4. NAT instance translates to public IP

## Current State

### What's Working

- Base appliance boots from ZFS root with zfsbootmenu
- Boot environment switching via `zpool set bootfs=tank/ROOT/<env> tank`
- Image build script produces compressed ZFS send streams (`.zfs.zst` files)
- Images can be received and booted on target appliances
- Persistent datasets at `/persistent/config` and `/persistent/data` survive across deployments
- VPP, FRR, and Incus installed in built images
- Dataplane namespace and service chain configured

### What Needs Work

- **Machine-specific configuration** — PCI addresses, interface names, IP addresses are hardcoded for the test platform
- **Persistent state integration** — Machine config should load from `/persistent/config` at boot
- **First-boot initialization** — Incus requires `incus admin init` before use
- **Build time optimization** — ZFS DKMS compilation takes 30+ minutes per build
- **Image versioning/metadata** — No systematic way to track what's in an image
- **Automated testing** — No validation that a built image actually boots

### Machine-Specific Files

These files in `config/` contain test-platform-specific values that need customization per appliance:

| File | Contains |
|------|----------|
| `etc/vpp/startup-core.conf` | PCI device addresses for DPDK |
| `etc/vpp/commands-core.txt` | IP addresses, prefixes, ACLs |
| `etc/vpp/commands-nat.txt` | NAT pool mappings |
| `etc/systemd/system/netns-move-interfaces.service` | Physical interface names |
| `etc/frr/frr.conf` | BGP peers, router-id, announced prefixes |

## Key Commands

### On the Build VM

```bash
# Import build pool (required after reboot)
zpool import -d /var/lib buildpool

# Build an image
./scripts/build-image.sh imp-v1.0.0

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
zstd -d < /tmp/imp-v1.0.0.zfs.zst | zfs receive tank/ROOT/imp-v1.0.0
zfs set mountpoint=/ tank/ROOT/imp-v1.0.0

# Switch boot environment
zpool set bootfs=tank/ROOT/imp-v1.0.0 tank
reboot

# Rollback to previous
zpool set bootfs=tank/ROOT/imp-v0.9.0 tank
reboot
```

### VPP Commands

```bash
# Connect to VPP CLI (core instance)
vppctl -s /run/vpp/core-cli.sock

# Inside vppctl:
show interface              # List interfaces
show interface address      # Show IP addresses
show ip fib                 # IPv4 routing table
show ip6 fib                # IPv6 routing table
show acl-plugin acl         # Show ACLs
show abf policy             # Show ACL-based forwarding policies

# Connect to NAT instance
vppctl -s /run/vpp/nat-cli.sock
show det44 sessions         # Show NAT sessions
```

### FRR Commands

```bash
# Enter FRR shell (runs in dataplane namespace)
sudo ip netns exec dataplane vtysh

# Inside vtysh:
show ip bgp summary
show ip route
show ipv6 route
```

## ZFS Dataset Layout

```
tank/
├── ROOT/                      # Boot environments (one per deployment)
│   ├── debian-initial/        # First manually-installed system
│   ├── imp-v1.0.0/            # Deployed image
│   └── imp-v1.0.1/            # Another deployed image
└── persistent/                # Survives across deployments
    ├── config/                # Machine-specific configuration
    └── data/                  # Application data (container volumes, logs)
```

## Build Script Details

`scripts/build-image.sh` uses mmdebstrap to create a Debian Bookworm system with:

- systemd, dbus
- Linux kernel and headers
- ZFS utilities and initramfs support
- SSH server
- VPP and plugins (from fd.io repository)
- FRR (Free Range Routing)
- Incus (from bookworm-backports)
- Dataplane namespace services and configuration

The script:
1. Creates a fresh dataset in the build pool
2. Bootstraps Debian Bookworm into it
3. Adds fd.io repository and installs VPP
4. Installs FRR and Incus
5. Copies configuration from `config/` directory
6. Enables all services
7. Sets ZFS properties for bootability
8. Snapshots and sends to a compressed file

## Environment Details

### Build VM

- Debian Bookworm
- File-backed ZFS pool at `/var/lib/build-pool.img`
- Build output in `/var/lib/images/`
- Pool must be manually imported after reboot: `zpool import -d /var/lib buildpool`

### Appliance

- Debian Bookworm on ZFS root
- Pool name: `tank`
- ESP mounted at `/boot/efi`
- zfsbootmenu EFI binary at `/boot/efi/EFI/ZBM/zfsbootmenu.efi`

## Next Steps

Suggested development priorities:

1. **Machine config templating** — Load interface names, IPs, PCI addresses from `/persistent/config`
2. **First-boot service** — Initialize Incus, apply machine-specific config
3. **Base service containers** — Unbound for DNS, Kea for DHCP, Suricata for IDS
4. **NAT64 integration** — Add VPP NAT64 instance for IPv6-only client support
5. **Image metadata** — Embed version info, build date, package manifest in images
6. **Build caching** — Speed up builds by reusing base layers or pre-compiled DKMS modules

## Testing

### Manual Boot Test

1. Build image on build VM
2. Transfer to appliance
3. Deploy with `zfs receive`
4. Set as bootfs and reboot
5. Verify correct environment is mounted at `/`
6. Verify dataplane namespace exists: `ip netns list`
7. Verify VPP is running: `vppctl -s /run/vpp/core-cli.sock show version`
8. Verify FRR is running: `ip netns exec dataplane vtysh -c "show ip bgp summary"`
9. Verify rollback to previous environment works

### Validation Checklist

- [ ] Image boots successfully
- [ ] ZFS pool imports automatically
- [ ] Dataplane namespace created
- [ ] VPP core instance running
- [ ] VPP NAT instance running
- [ ] FRR daemons running in dataplane namespace
- [ ] BGP sessions established
- [ ] Traffic forwarding through VPP
- [ ] NAT working for private clients
- [ ] SSH accessible on management interface
- [ ] Persistent datasets mounted
- [ ] Can switch to different boot environment
