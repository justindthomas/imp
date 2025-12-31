# CLAUDE.md - IMP Build System

## Project Overview

IMP (Internet Management Platform) is a ZFS-based appliance build system for network infrastructure. The target use case is a custom routing/services platform for a small ISP, replacing commercial platforms like Juniper MX480 and SONiC-based systems.

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
├── TEMPLATE_PLAN.md        # Design doc for router configuration
├── diagrams/
│   └── vpp.pdf             # Architecture diagram
├── config/                 # Static configuration files copied into images
│   ├── etc/
│   │   ├── apt/sources.list.d/
│   │   │   └── fdio_release.list
│   │   ├── frr/
│   │   │   ├── daemons           # Static: FRR daemon enable flags
│   │   │   └── vtysh.conf        # Static: vtysh settings
│   │   ├── systemd/system/
│   │   │   ├── netns-dataplane.service
│   │   │   ├── vpp-core.service
│   │   │   ├── vpp-core-config.service
│   │   │   ├── vpp-nat.service
│   │   │   └── incus-dataplane.service
│   │   └── vpp/
│   │       └── startup-nat.conf  # Static: NAT instance startup
│   ├── templates/                # Jinja2 templates for configure-router.py
│   │   ├── vpp/
│   │   │   ├── startup-core.conf.j2
│   │   │   ├── commands-core.txt.j2
│   │   │   └── commands-nat.txt.j2
│   │   ├── frr/
│   │   │   └── frr.conf.j2
│   │   ├── systemd/
│   │   │   ├── netns-move-interfaces.service.j2
│   │   │   └── management.network.j2
│   │   └── scripts/
│   │       ├── vpp-core-config.sh.j2
│   │       └── incus-networking.sh.j2
│   └── usr/local/bin/
│       ├── incus-init.sh         # Static: Incus initialization
│       └── wait-for-iface-load   # Static: Interface wait helper
└── scripts/
    ├── build-installer-iso.sh  # Build custom Live ISO with ZFS pre-compiled
    ├── bootstrap-livecd.sh     # Add ZFS support to stock Debian Live CD
    ├── install-imp             # Complete router install from Live CD
    ├── setup-build-vm.sh       # Build VM initialization
    ├── build-image.sh          # Builds a deployable ZFS image
    ├── configure-router.py     # Interactive router configuration (Python/Jinja2)
    └── imp                     # CLI management utility
```

## Dataplane Architecture

### Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                       Default Namespace                         │
│  ┌──────────┐  ┌─────────────────────────────────────────────┐  │
│  │   SSH    │  │              Incus Containers               │  │
│  │  Server  │  │  ┌──────┐  ┌──────┐  ┌──────────┐           │  │
│  └────┬─────┘  │  │ DHCP │  │ DNS  │  │ Suricata │  ...      │  │
│       │        │  └──┬───┘  └──┬───┘  └────┬─────┘           │  │
│  management    │     └─────────┴───────────┘                 │  │
│                │              incusbr0                       │  │
│                └───────────────┬─────────────────────────────┘  │
│                                │ veth                           │
├────────────────────────────────┼────────────────────────────────┤
│                        Dataplane Namespace                      │
│                                │                                │
│  ┌─────────────────────────────┴─────────────────────────────┐  │
│  │                         VPP Core                          │  │
│  │  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌────────────┐   │  │
│  │  │ external │  │ internal │  │ memif  │  │ host-if    │   │  │
│  │  │  (DPDK)  │  │  (DPDK)  │  │  1/0   │  │ incus-dp   │   │  │
│  │  └────┬─────┘  └────┬─────┘  └───┬────┘  └─────┬──────┘   │  │
│  │       │             │            │             │          │  │
│  │  linux_cp: TAP interfaces for FRR visibility   │          │  │
│  └───────┼─────────────┼────────────┼─────────────┼──────────┘  │
│          │             │            │             │             │
│  ┌───────┴─────────────┴────┐       │             │             │
│  │          FRR             │       │             │             │
│  │  (BGP, OSPF, OSPF6)      │    ┌──┴──────────┐  │             │
│  │  watchfrr --netns=dp     │    │   VPP NAT   │  │             │
│  └──────────────────────────┘    │   (det44)   │  │             │
│                                  │   memif     │  │             │
│                                  └─────────────┘  │             │
└───────────────────────────────────────────────────┴─────────────┘
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

## Configuration System

The router configuration system uses Python with Jinja2 templates to generate machine-specific configuration files from user input.

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         configure-router.py                              │
├─────────────────────────────────────────────────────────────────────────┤
│  1. Interface Detection    │  Scans /sys/class/net for physical NICs    │
│  2. User Input (phases)    │  Interactive prompts for roles, IPs, BGP   │
│  3. RouterConfig object    │  Python dataclasses store all settings     │
│  4. Save to JSON           │  /persistent/config/router.json            │
│  5. Render templates       │  Jinja2 templates → config files           │
│  6. Enable services        │  systemctl enable for dataplane services   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User Input → RouterConfig (dataclasses) → JSON file → Jinja2 Templates → Config Files
                                              ↓
                              /persistent/config/router.json
                              (survives image upgrades)
```

### Configuration Phases

The interactive configuration runs through 7 phases:

1. **Interface Detection** — Discovers NICs, shows name/MAC/PCI/driver
2. **Role Assignment** — User selects management, external, internal interfaces
3. **Management Config** — DHCP or static IP for out-of-band management
4. **External Config** — WAN IPv4/IPv6 addresses and gateways
5. **Internal Config** — LAN IPv4/IPv6 addresses (supports multiple interfaces)
6. **BGP Config** — Optional BGP peering (ASN, peer addresses)
7. **NAT Config** — Public IP pool and internal networks to NAT

### Static vs Generated Files

**Static files** (copied during install, never change):
- `/etc/vpp/startup-nat.conf` — NAT instance startup config
- `/etc/frr/daemons` — Which FRR daemons to run
- `/etc/frr/vtysh.conf` — vtysh shell settings
- `/etc/systemd/system/*.service` — Service unit files (except netns-move-interfaces)

**Generated files** (created by configure-router.py):

| File | Template | Purpose |
|------|----------|---------|
| `/etc/vpp/startup-core.conf` | `startup-core.conf.j2` | VPP core startup, PCI addresses |
| `/etc/vpp/commands-core.txt` | `commands-core.txt.j2` | VPP interfaces, IPs, routes, ACLs |
| `/etc/vpp/commands-nat.txt` | `commands-nat.txt.j2` | NAT pool mappings |
| `/etc/frr/frr.conf` | `frr.conf.j2` | BGP configuration |
| `/etc/systemd/system/netns-move-interfaces.service` | `netns-move-interfaces.service.j2` | Interface names to move |
| `/etc/systemd/network/10-management.network` | `management.network.j2` | Management interface config |
| `/usr/local/bin/vpp-core-config.sh` | `vpp-core-config.sh.j2` | IPv6 RA configuration |
| `/usr/local/bin/incus-networking.sh` | `incus-networking.sh.j2` | Container bridge setup |

### Service Enable Flow

On fresh install, only basic services are enabled:
- `systemd-networkd`, `systemd-resolved`, `ssh`

After running `configure-router.py`, dataplane services are enabled:
- `netns-dataplane`, `netns-move-interfaces`
- `vpp-core`, `vpp-core-config`, `vpp-nat`
- `frr`, `incus-dataplane`

This ensures the system boots to a usable state (SSH accessible) before configuration.

### Re-applying Configuration

After deploying a new image, existing configuration can be re-applied:

```bash
configure-router.py --apply-only
```

This reads `/persistent/config/router.json`, regenerates all config files, and enables services.

## Current State

### What's Working

- Base appliance boots from ZFS root with zfsbootmenu
- Boot environment switching via `zpool set bootfs=tank/ROOT/<env> tank`
- Image build script produces compressed ZFS send streams (`.zfs.zst` files)
- Images can be received and booted on target appliances
- Persistent datasets at `/persistent/config` and `/persistent/data` survive across deployments
- VPP, FRR, and Incus installed in built images
- Dataplane services installed but not enabled (require configuration first)
- **Interactive router configuration** via `configure-router.py`
- **Configuration persistence** — Config saved to `/persistent/config/router.json`

### What Needs Work

- **First-boot service** — Auto-run `configure-router.py` on first boot if no config exists
- **First-boot initialization** — Incus requires `incus admin init` before use
- **Build time optimization** — ZFS DKMS compilation takes 30+ minutes per build
- **Image versioning/metadata** — No systematic way to track what's in an image
- **Automated testing** — No validation that a built image actually boots

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

### Router Configuration

```bash
# Interactive configuration (first boot or reconfiguration)
configure-router.py

# Re-apply existing configuration (e.g., after image upgrade)
configure-router.py --apply-only

# Configuration is stored as JSON at:
cat /persistent/config/router.json
```

The configuration script (Python/Jinja2):
1. Detects physical network interfaces (name, MAC, PCI address)
2. Prompts for role assignment (management, external, internal)
3. Collects IP configuration (IPv4/IPv6 for each interface)
4. Optionally configures BGP peering
5. Configures NAT pool and internal networks
6. Generates config files from templates
7. Enables and starts services

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
- Jinja2 templates and configure-router.py

The script:
1. Creates a fresh dataset in the build pool
2. Bootstraps Debian Bookworm into it
3. Adds fd.io repository and installs VPP
4. Installs FRR and Incus
5. Copies static configs and Jinja2 templates from `config/` directory
6. Enables only basic services (ssh, networkd, resolved)
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

1. **First-boot service** — Auto-run `configure-router.py` on first boot, initialize Incus
2. **Base service containers** — Unbound for DNS, Kea for DHCP, Suricata for IDS
3. **NAT64 integration** — Add VPP NAT64 instance for IPv6-only client support
4. **Image metadata** — Embed version info, build date, package manifest in images
5. **Build caching** — Speed up builds by reusing base layers or pre-compiled DKMS modules
6. **Web UI** — Optional web interface for router configuration

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
