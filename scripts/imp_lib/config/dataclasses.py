"""
Configuration dataclasses for IMP router configuration.

These define the structure of router configuration as stored in router.json.
"""

import ipaddress
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InterfaceInfo:
    """Information about a physical network interface."""
    name: str
    mac: str
    pci: Optional[str]
    driver: str


@dataclass
class SubInterface:
    """A VLAN sub-interface with L3 termination."""
    vlan_id: int
    ipv4: Optional[str] = None
    ipv4_prefix: Optional[int] = None
    ipv6: Optional[str] = None
    ipv6_prefix: Optional[int] = None
    create_lcp: bool = True  # Create linux_cp TAP for FRR visibility
    # OSPF settings
    ospf_area: Optional[int] = None  # OSPF area (None = not participating)
    ospf_passive: bool = False  # Passive interface (no hellos)
    ospf6_area: Optional[int] = None  # OSPFv3 area (None = not participating)
    ospf6_passive: bool = False  # Passive for OSPFv3
    # IPv6 Router Advertisement settings
    ipv6_ra_enabled: bool = True  # Send RAs (if IPv6 configured and LCP created)
    ipv6_ra_interval_max: int = 30  # Max RA interval in seconds
    ipv6_ra_interval_min: int = 15  # Min RA interval in seconds
    ipv6_ra_suppress: bool = False  # Suppress RAs (keep config but don't send)
    ipv6_ra_prefixes: list[str] = field(default_factory=list)  # Custom prefixes (empty = auto from IPv6)


@dataclass
class LoopbackInterface:
    """A loopback interface for service addresses, router-id, etc."""
    instance: int  # VPP loopback instance number (creates loopX)
    name: str  # Friendly name for reference
    ipv4: Optional[str] = None
    ipv4_prefix: Optional[int] = None
    ipv6: Optional[str] = None
    ipv6_prefix: Optional[int] = None
    create_lcp: bool = True  # Create linux_cp TAP for FRR visibility
    # OSPF settings
    ospf_area: Optional[int] = None  # OSPF area (None = not participating)
    ospf_passive: bool = False  # Passive interface (no hellos)
    ospf6_area: Optional[int] = None  # OSPFv3 area (None = not participating)
    ospf6_passive: bool = False  # Passive for OSPFv3
    # IPv6 Router Advertisement settings
    ipv6_ra_enabled: bool = True  # Send RAs (if IPv6 configured and LCP created)
    ipv6_ra_interval_max: int = 30  # Max RA interval in seconds
    ipv6_ra_interval_min: int = 15  # Min RA interval in seconds
    ipv6_ra_suppress: bool = False  # Suppress RAs (keep config but don't send)
    ipv6_ra_prefixes: list[str] = field(default_factory=list)  # Custom prefixes (empty = auto from IPv6)


@dataclass
class BridgeDomainMember:
    """An interface or sub-interface that's a member of a bridge domain."""
    interface: str  # VPP interface name (e.g., "external", "internal0")
    vlan_id: Optional[int] = None  # If set, uses/creates a sub-interface


@dataclass
class BVIConfig:
    """Bridge domain with BVI (like a switch VLAN interface / SVI)."""
    bridge_id: int  # Bridge domain ID (also used as loopback instance)
    name: str  # Friendly name (e.g., "vlan100", "customer-lan")
    members: list[BridgeDomainMember] = field(default_factory=list)
    ipv4: Optional[str] = None
    ipv4_prefix: Optional[int] = None
    ipv6: Optional[str] = None
    ipv6_prefix: Optional[int] = None
    create_lcp: bool = True  # Create linux_cp TAP for FRR visibility
    # OSPF settings
    ospf_area: Optional[int] = None  # OSPF area (None = not participating)
    ospf_passive: bool = False  # Passive interface (no hellos)
    ospf6_area: Optional[int] = None  # OSPFv3 area (None = not participating)
    ospf6_passive: bool = False  # Passive for OSPFv3
    # IPv6 Router Advertisement settings
    ipv6_ra_enabled: bool = True  # Send RAs (if IPv6 configured and LCP created)
    ipv6_ra_interval_max: int = 30  # Max RA interval in seconds
    ipv6_ra_interval_min: int = 15  # Min RA interval in seconds
    ipv6_ra_suppress: bool = False  # Suppress RAs (keep config but don't send)
    ipv6_ra_prefixes: list[str] = field(default_factory=list)  # Custom prefixes (empty = auto from IPv6)


@dataclass
class InterfaceAddress:
    """A single IP address on an interface."""
    address: str           # IP address (e.g., "192.168.1.1")
    prefix: int            # Prefix length (e.g., 24)


@dataclass
class Interface:
    """A dataplane interface with user-defined name."""
    name: str              # User-defined name (e.g., "wan", "lan", "transit")
    iface: str             # Physical interface name (e.g., "eth0")
    pci: str               # PCI address for DPDK binding

    # IP configuration (multiple addresses supported)
    ipv4: list[InterfaceAddress] = field(default_factory=list)
    ipv6: list[InterfaceAddress] = field(default_factory=list)

    # Link configuration
    mtu: int = 1500

    # VLAN sub-interfaces
    subinterfaces: list[SubInterface] = field(default_factory=list)

    # OSPF (optional)
    ospf_area: Optional[int] = None
    ospf_passive: bool = False
    ospf6_area: Optional[int] = None
    ospf6_passive: bool = False

    # IPv6 Router Advertisement settings
    ipv6_ra_enabled: bool = True  # Send RAs (if IPv6 configured)
    ipv6_ra_interval_max: int = 30  # Max RA interval in seconds
    ipv6_ra_interval_min: int = 15  # Min RA interval in seconds
    ipv6_ra_suppress: bool = False  # Suppress RAs (keep config but don't send)
    ipv6_ra_prefixes: list[str] = field(default_factory=list)  # Custom prefixes (empty = auto from IPv6)

    @property
    def vpp_name(self) -> str:
        """VPP interface name is the user-defined name."""
        return self.name

    @property
    def networks(self) -> list[str]:
        """Compute networks from IPv4 addresses (for ACL/NAT rules)."""
        result = []
        for addr in self.ipv4:
            net = ipaddress.IPv4Network(f"{addr.address}/{addr.prefix}", strict=False)
            result.append(str(net))
        return result

    @property
    def ipv6_networks(self) -> list[str]:
        """Compute networks from IPv6 addresses."""
        result = []
        for addr in self.ipv6:
            net = ipaddress.IPv6Network(f"{addr.address}/{addr.prefix}", strict=False)
            result.append(str(net))
        return result

    @property
    def ipv6_ra_prefixes_auto(self) -> list[str]:
        """Compute /64 RA prefixes from IPv6 addresses (used when ipv6_ra_prefixes is empty)."""
        result = []
        for addr in self.ipv6:
            addr6 = ipaddress.IPv6Address(addr.address)
            net64 = ipaddress.IPv6Network(f"{addr6}/64", strict=False)
            result.append(str(net64))
        return result

    @property
    def ipv6_ra_prefixes_effective(self) -> list[str]:
        """Get effective RA prefixes (custom if set, otherwise auto-computed)."""
        return self.ipv6_ra_prefixes if self.ipv6_ra_prefixes else self.ipv6_ra_prefixes_auto


@dataclass
class Route:
    """A static route."""
    destination: str       # CIDR (e.g., "0.0.0.0/0" or "10.0.0.0/8")
    via: str               # Next-hop IP address
    interface: Optional[str] = None  # Optional: force via specific interface


@dataclass
class ManagementInterface:
    """Management interface configuration."""
    iface: str
    mode: str = "dhcp"  # "dhcp" or "static"
    ipv4: Optional[str] = None
    ipv4_prefix: Optional[int] = None
    ipv4_gateway: Optional[str] = None


@dataclass
class BGPPeer:
    """A single BGP peer."""
    name: str  # Friendly name (e.g., "upstream", "ix-peer-1")
    peer_ip: str  # IPv4 or IPv6 address
    peer_asn: int  # Remote AS number
    description: Optional[str] = None  # FRR neighbor description
    update_source: Optional[str] = None  # Source IP (defaults to external.ipv4 or .ipv6)


@dataclass
class BGPConfig:
    """BGP configuration with multiple peers."""
    enabled: bool = False
    asn: Optional[int] = None  # Local AS number
    router_id: Optional[str] = None  # BGP router-id
    peers: list[BGPPeer] = field(default_factory=list)  # Multiple peers


@dataclass
class OSPFConfig:
    """OSPF (IPv4) configuration."""
    enabled: bool = False
    router_id: Optional[str] = None  # Falls back to BGP router-id if None
    default_originate: bool = False  # Inject default route


@dataclass
class OSPF6Config:
    """OSPFv3 (IPv6) configuration."""
    enabled: bool = False
    router_id: Optional[str] = None  # Falls back to OSPF/BGP router-id if None
    default_originate: bool = False  # Inject default route


@dataclass
class NATMapping:
    """A single det44 NAT mapping."""
    source_network: str  # e.g., "192.168.20.0/24"
    nat_pool: str  # e.g., "23.177.24.96/30"


@dataclass
class ACLBypassPair:
    """A source/destination pair that should skip NAT (allow direct routing)."""
    source: str  # e.g., "192.168.20.0/24"
    destination: str  # e.g., "192.168.37.0/24"


@dataclass
class VLANPassthrough:
    """A VLAN to pass through (L2 xconnect) between two interfaces."""
    vlan_id: int  # VLAN ID (same on both sides)
    from_interface: str  # Source interface name (e.g., "wan")
    to_interface: str  # Destination interface name (e.g., "lan")
    vlan_type: str = "dot1q"  # "dot1q" (802.1Q) or "dot1ad" (QinQ S-tag)
    inner_vlan: Optional[int] = None  # For QinQ: the inner C-tag (if specific)


@dataclass
class NATConfig:
    """NAT configuration."""
    bgp_prefix: str = ""  # The prefix to announce via BGP (e.g., "23.177.24.96/29")
    mappings: list[NATMapping] = field(default_factory=list)  # det44 source -> pool mappings
    bypass_pairs: list[ACLBypassPair] = field(default_factory=list)  # ACL bypass rules


@dataclass
class ContainerConfig:
    """Container network configuration."""
    network: str = "10.234.116.0/24"
    gateway: str = "10.234.116.5"
    prefix: int = 24
    bridge_ip: str = "10.234.116.1"      # Incus bridge IP
    dhcp_start: str = "10.234.116.100"   # DHCP range start
    dhcp_end: str = "10.234.116.254"     # DHCP range end
    ipv6: Optional[str] = None           # VPP's IPv6 on host-interface
    ipv6_prefix: Optional[int] = None
    ipv6_ra_prefix: Optional[str] = None
    bridge_ipv6: Optional[str] = None    # Incus bridge IPv6

    @classmethod
    def from_network(cls, network: str, gateway: str) -> 'ContainerConfig':
        """Create ContainerConfig with computed values from network."""
        net = ipaddress.ip_network(network, strict=False)
        hosts = list(net.hosts())

        # Bridge IP is .1, gateway (VPP) is .5, DHCP is .100-.254
        bridge_ip = str(hosts[0])       # .1
        dhcp_start = str(hosts[99])     # .100
        dhcp_end = str(hosts[-1])       # .254 (or last usable)

        return cls(
            network=network,
            gateway=gateway,
            prefix=net.prefixlen,
            bridge_ip=bridge_ip,
            dhcp_start=dhcp_start,
            dhcp_end=dhcp_end,
        )


@dataclass
class CPUConfig:
    """CPU allocation for VPP instances."""
    total_cores: int = 4
    # VPP Core instance
    core_main: int = 1
    core_workers: str = "2-3"       # corelist format
    core_worker_count: int = 2
    # VPP NAT instance
    nat_main: int = 0               # 0 means no dedicated main core (shares with workers)
    nat_workers: str = ""           # corelist format (empty if no dedicated workers)
    nat_worker_count: int = 0

    @classmethod
    def detect_and_allocate(cls) -> 'CPUConfig':
        """Detect CPU count and allocate cores optimally."""
        total_cores = os.cpu_count() or 4

        # Allocation strategy:
        # - Core 0: Reserved for Linux kernel
        # - Remaining cores split between VPP core and NAT instances
        #
        # Minimum: 2 cores - VPP core gets main+worker on core 1, NAT disabled
        # 4 cores: Core 1 = core main, 2-3 = core workers, NAT uses software threads
        # 8 cores: Core 1 = core main, 2-5 = core workers, 6 = NAT main, 7 = NAT worker
        # 16+ cores: Core 1 = core main, 2-9 = core workers, 10 = NAT main, 11-15 = NAT workers

        if total_cores <= 2:
            # Minimal setup - single core for VPP
            return cls(
                total_cores=total_cores,
                core_main=1,
                core_workers="",
                core_worker_count=0,
                nat_main=0,
                nat_workers="",
                nat_worker_count=0,
            )
        elif total_cores <= 4:
            # Small system - VPP core gets most, NAT runs without dedicated cores
            return cls(
                total_cores=total_cores,
                core_main=1,
                core_workers="2" if total_cores == 3 else "2-3",
                core_worker_count=1 if total_cores == 3 else 2,
                nat_main=0,
                nat_workers="",
                nat_worker_count=0,
            )
        elif total_cores <= 8:
            # Medium system - split cores between VPP and NAT
            core_workers_end = total_cores - 3  # Leave 2 for NAT
            nat_main = core_workers_end + 1
            nat_worker = nat_main + 1
            return cls(
                total_cores=total_cores,
                core_main=1,
                core_workers=f"2-{core_workers_end}",
                core_worker_count=core_workers_end - 1,
                nat_main=nat_main,
                nat_workers=str(nat_worker),
                nat_worker_count=1,
            )
        else:
            # Large system - more workers for both
            # Give 60% to core, 40% to NAT (excluding core 0)
            available = total_cores - 1
            core_count = int(available * 0.6)
            nat_count = available - core_count

            core_workers_end = core_count  # cores 1 to core_count for VPP core
            nat_main = core_workers_end + 1
            nat_workers_end = total_cores - 1

            return cls(
                total_cores=total_cores,
                core_main=1,
                core_workers=f"2-{core_workers_end}",
                core_worker_count=core_workers_end - 1,
                nat_main=nat_main,
                nat_workers=f"{nat_main + 1}-{nat_workers_end}" if nat_workers_end > nat_main + 1 else str(nat_main + 1),
                nat_worker_count=nat_workers_end - nat_main,
            )


@dataclass
class RouterConfig:
    """Complete router configuration."""
    hostname: str = "appliance"
    management: Optional[ManagementInterface] = None
    interfaces: list[Interface] = field(default_factory=list)  # All dataplane interfaces
    routes: list[Route] = field(default_factory=list)  # Static routes (including defaults)
    bgp: BGPConfig = field(default_factory=BGPConfig)
    ospf: OSPFConfig = field(default_factory=OSPFConfig)
    ospf6: OSPF6Config = field(default_factory=OSPF6Config)
    nat: NATConfig = field(default_factory=NATConfig)
    container: ContainerConfig = field(default_factory=ContainerConfig)
    cpu: CPUConfig = field(default_factory=CPUConfig)
    vlan_passthrough: list[VLANPassthrough] = field(default_factory=list)
    loopbacks: list[LoopbackInterface] = field(default_factory=list)
    bvi_domains: list[BVIConfig] = field(default_factory=list)
    modules: list[dict] = field(default_factory=list)  # Module configs from router.json
