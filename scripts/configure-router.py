#!/usr/bin/env python3
"""
configure-router.py - Interactive router configuration script

This script configures VPP, FRR, and network interfaces for the IMP platform.
It can be run on first boot or from the installer ISO.

Usage:
    configure-router.py              # Interactive configuration
    configure-router.py --apply-only # Apply existing config from /persistent/config/router.json
"""

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("ERROR: python3-jinja2 is required. Install with: apt install python3-jinja2")
    sys.exit(1)


# =============================================================================
# Configuration
# =============================================================================

TEMPLATE_DIR = Path("/etc/imp/templates")
CONFIG_FILE = Path("/persistent/config/router.json")
GENERATED_DIR = Path("/tmp/imp-generated-config")

# Colors for terminal output
class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    BOLD = "\033[1m"
    NC = "\033[0m"  # No Color


def log(msg: str) -> None:
    print(f"{Colors.GREEN}[+]{Colors.NC} {msg}")


def warn(msg: str) -> None:
    print(f"{Colors.YELLOW}[!]{Colors.NC} {msg}")


def error(msg: str) -> None:
    """Print an error message."""
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")


def fatal(msg: str) -> None:
    """Print an error message and exit."""
    error(msg)
    sys.exit(1)


def info(msg: str) -> None:
    print(f"{Colors.CYAN}[i]{Colors.NC} {msg}")


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class InterfaceInfo:
    """Information about a physical network interface."""
    name: str
    mac: str
    pci: Optional[str]
    driver: str


@dataclass
class ExternalInterface:
    """External (WAN) interface configuration."""
    iface: str
    pci: str
    ipv4: str
    ipv4_prefix: int
    ipv4_gateway: str
    ipv6: Optional[str] = None
    ipv6_prefix: Optional[int] = None
    ipv6_gateway: Optional[str] = None


@dataclass
class InternalInterface:
    """Internal (LAN) interface configuration."""
    iface: str
    pci: str
    vpp_name: str
    ipv4: str
    ipv4_prefix: int
    network: str = ""  # Computed: network address
    ipv6: Optional[str] = None
    ipv6_prefix: Optional[int] = None
    ipv6_network: Optional[str] = None  # Computed: network for BGP announcement
    ipv6_ra_prefix: Optional[str] = None  # Computed: RA prefix (typically /64)

    def __post_init__(self):
        # Compute network address from IP
        net = ipaddress.IPv4Network(f"{self.ipv4}/{self.ipv4_prefix}", strict=False)
        self.network = str(net)

        if self.ipv6 and self.ipv6_prefix:
            net6 = ipaddress.IPv6Network(f"{self.ipv6}/{self.ipv6_prefix}", strict=False)
            self.ipv6_network = str(net6)
            # RA prefix - use /64 from the address
            addr6 = ipaddress.IPv6Address(self.ipv6)
            # Get the /64 network
            net64 = ipaddress.IPv6Network(f"{addr6}/64", strict=False)
            self.ipv6_ra_prefix = str(net64)


@dataclass
class ManagementInterface:
    """Management interface configuration."""
    iface: str
    mode: str = "dhcp"  # "dhcp" or "static"
    ipv4: Optional[str] = None
    ipv4_prefix: Optional[int] = None
    ipv4_gateway: Optional[str] = None


@dataclass
class BGPConfig:
    """BGP configuration."""
    enabled: bool = False
    asn: Optional[int] = None
    router_id: Optional[str] = None
    peer_ipv4: Optional[str] = None
    peer_ipv6: Optional[str] = None
    peer_asn: Optional[int] = None


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
    """A VLAN to pass through (L2 xconnect) between external and an internal interface."""
    vlan_id: int  # VLAN ID (same on both sides)
    internal_interface: str  # VPP interface name, e.g., "internal0"
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
        import ipaddress
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
        import os
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
    external: Optional[ExternalInterface] = None
    internal: list[InternalInterface] = field(default_factory=list)
    bgp: BGPConfig = field(default_factory=BGPConfig)
    nat: NATConfig = field(default_factory=NATConfig)
    container: ContainerConfig = field(default_factory=ContainerConfig)
    cpu: CPUConfig = field(default_factory=CPUConfig)
    vlan_passthrough: list[VLANPassthrough] = field(default_factory=list)


# =============================================================================
# Interface Detection
# =============================================================================

def detect_interfaces() -> list[InterfaceInfo]:
    """Detect physical network interfaces."""
    interfaces = []
    net_path = Path("/sys/class/net")

    for iface_path in net_path.iterdir():
        name = iface_path.name

        # Skip virtual interfaces
        if name in ("lo",) or name.startswith(("veth", "br", "docker", "virbr", "incusbr")):
            continue

        # Check if it's a physical device (has a device symlink)
        device_path = iface_path / "device"
        if not device_path.is_symlink():
            continue

        # Get MAC address
        try:
            mac = (iface_path / "address").read_text().strip()
        except (IOError, OSError):
            mac = "unknown"

        # Get PCI address
        try:
            pci_path = device_path.resolve()
            pci = pci_path.name
        except (IOError, OSError):
            pci = None

        # Get driver
        driver_path = device_path / "driver"
        try:
            driver = driver_path.resolve().name if driver_path.is_symlink() else "unknown"
        except (IOError, OSError):
            driver = "unknown"

        interfaces.append(InterfaceInfo(name=name, mac=mac, pci=pci, driver=driver))

    return sorted(interfaces, key=lambda x: x.name)


def show_interface_table(interfaces: list[InterfaceInfo]) -> None:
    """Display a table of network interfaces."""
    print()
    print(f"{Colors.BOLD}{'#':<4} {'NAME':<18} {'MAC':<19} {'PCI':<14} {'DRIVER':<10}{Colors.NC}")
    print("─" * 70)

    for i, iface in enumerate(interfaces, 1):
        pci = iface.pci or "N/A"
        print(f"{i}){'':<3} {iface.name:<18} {iface.mac:<19} {pci:<14} {iface.driver:<10}")
    print()


# =============================================================================
# Input Validation
# =============================================================================

def validate_ipv4(ip: str) -> bool:
    """Validate an IPv4 address."""
    try:
        ipaddress.IPv4Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False


def validate_ipv4_cidr(cidr: str) -> bool:
    """Validate an IPv4 CIDR notation."""
    try:
        ipaddress.IPv4Network(cidr, strict=False)
        return True
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def validate_ipv6(ip: str) -> bool:
    """Validate an IPv6 address."""
    try:
        ipaddress.IPv6Address(ip)
        return True
    except ipaddress.AddressValueError:
        return False


def validate_ipv6_cidr(cidr: str) -> bool:
    """Validate an IPv6 CIDR notation."""
    try:
        ipaddress.IPv6Network(cidr, strict=False)
        return True
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def parse_cidr(cidr: str) -> tuple[str, int]:
    """Parse CIDR notation into address and prefix."""
    addr, prefix = cidr.rsplit("/", 1)
    return addr, int(prefix)


# =============================================================================
# User Prompts
# =============================================================================

def prompt_select(prompt: str, options: list[str], descriptions: list[str] = None) -> int:
    """Prompt user to select from a list of options. Returns index."""
    print(f"\n{Colors.BOLD}{prompt}{Colors.NC}")

    for i, opt in enumerate(options, 1):
        if descriptions:
            print(f"  {i}) {opt} - {descriptions[i-1]}")
        else:
            print(f"  {i}) {opt}")

    while True:
        try:
            choice = input(f"Choice [1-{len(options)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass
        warn(f"Invalid selection. Please enter 1-{len(options)}")


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    """Prompt for yes/no. Returns True for yes."""
    hint = "Y/n" if default else "y/N"

    while True:
        answer = input(f"{prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        warn("Please answer yes or no")


def prompt_string(prompt: str, validate=None, allow_empty: bool = False) -> str:
    """Prompt for a string with optional validation."""
    while True:
        value = input(f"{prompt}: ").strip()
        if not value and allow_empty:
            return value
        if not value:
            warn("Value cannot be empty")
            continue
        if validate and not validate(value):
            warn("Invalid format")
            continue
        return value


def prompt_int(prompt: str, min_val: int = None, max_val: int = None) -> int:
    """Prompt for an integer with optional range validation."""
    while True:
        try:
            value = int(input(f"{prompt}: ").strip())
            if min_val is not None and value < min_val:
                warn(f"Value must be at least {min_val}")
                continue
            if max_val is not None and value > max_val:
                warn(f"Value must be at most {max_val}")
                continue
            return value
        except ValueError:
            warn("Please enter a valid number")


def prompt_ipv4_cidr(prompt: str) -> tuple[str, int]:
    """Prompt for IPv4 in CIDR notation."""
    while True:
        value = input(f"  {prompt} [CIDR, e.g., 192.168.1.1/24]: ").strip()
        if validate_ipv4_cidr(value):
            return parse_cidr(value)
        warn("Invalid IPv4 CIDR format")


def prompt_ipv4(prompt: str) -> str:
    """Prompt for an IPv4 address."""
    while True:
        value = input(f"  {prompt}: ").strip()
        if validate_ipv4(value):
            return value
        warn("Invalid IPv4 address")


def prompt_ipv6_cidr(prompt: str) -> tuple[str, int] | tuple[None, None]:
    """Prompt for optional IPv6 in CIDR notation."""
    value = input(f"  {prompt} [CIDR, optional]: ").strip()
    if not value:
        return None, None
    if validate_ipv6_cidr(value):
        return parse_cidr(value)
    warn("Invalid IPv6 CIDR format, skipping IPv6")
    return None, None


def prompt_ipv6(prompt: str) -> str | None:
    """Prompt for an optional IPv6 address."""
    value = input(f"  {prompt} [optional]: ").strip()
    if not value:
        return None
    if validate_ipv6(value):
        return value
    warn("Invalid IPv6 address, skipping")
    return None


def prompt_list(prompt: str, validate=None) -> list[str]:
    """Prompt for a comma-separated list."""
    while True:
        value = input(f"  {prompt}: ").strip()
        items = [x.strip() for x in value.split(",") if x.strip()]

        if not items:
            warn("At least one value is required")
            continue

        if validate:
            valid = True
            for item in items:
                if not validate(item):
                    warn(f"Invalid: {item}")
                    valid = False
            if not valid:
                continue

        return items


# =============================================================================
# Configuration Phases
# =============================================================================

def phase1_detect_interfaces() -> list[InterfaceInfo]:
    """Phase 1: Detect network interfaces."""
    log("Phase 1: Interface Discovery")

    interfaces = detect_interfaces()

    if not interfaces:
        fatal("No physical network interfaces detected!")

    info(f"Found {len(interfaces)} physical network interface(s):")
    show_interface_table(interfaces)

    return interfaces


def phase2_assign_roles(interfaces: list[InterfaceInfo]) -> tuple[InterfaceInfo, InterfaceInfo, list[InterfaceInfo]]:
    """Phase 2: Assign interface roles. Returns (management, external, internal[])."""
    log("Phase 2: Interface Role Assignment")

    available = list(interfaces)
    names = [i.name for i in available]

    # Management interface
    print(f"\n{Colors.BOLD}Management interface{Colors.NC}")
    print("  This interface stays in the default namespace for SSH access.")
    print("  Typically used for out-of-band management.")
    idx = prompt_select("Select MANAGEMENT interface:", names)
    management = available.pop(idx)
    names = [i.name for i in available]

    if len(available) < 2:
        fatal("Need at least 2 more interfaces for external and internal roles")

    # External interface
    print(f"\n{Colors.BOLD}External interface (WAN/Upstream){Colors.NC}")
    print("  This interface connects to the upstream provider.")
    print("  It will be managed by VPP with DPDK.")
    idx = prompt_select("Select EXTERNAL interface:", names)
    external = available.pop(idx)
    names = [i.name for i in available]

    # Internal interfaces
    print(f"\n{Colors.BOLD}Internal interface(s) (LAN/Downstream){Colors.NC}")
    print("  These interfaces connect to internal networks.")
    print("  Multiple internal interfaces are supported.")

    internal = []
    while available:
        idx = prompt_select(f"Select INTERNAL interface #{len(internal) + 1}:", names)
        internal.append(available.pop(idx))
        names = [i.name for i in available]

        if available:
            if not prompt_yes_no("Add another internal interface?"):
                break

    # Summary
    print(f"\n{Colors.BOLD}Interface Assignment Summary:{Colors.NC}")
    print(f"  Management: {management.name}")
    print(f"  External:   {external.name} (PCI: {external.pci})")
    for i, iface in enumerate(internal):
        print(f"  Internal:   {iface.name} (PCI: {iface.pci}) -> internal{i}")

    return management, external, internal


def phase3_ip_config(external_iface: InterfaceInfo, internal_ifaces: list[InterfaceInfo]) -> tuple[ExternalInterface, list[InternalInterface]]:
    """Phase 3: Collect IP configuration."""
    log("Phase 3: IP Configuration")

    # External interface
    print(f"\n{Colors.BOLD}Configure EXTERNAL interface ({external_iface.name}):{Colors.NC}")

    ipv4, prefix = prompt_ipv4_cidr("IPv4 Address")
    gateway = prompt_ipv4("IPv4 Gateway")
    ipv6, ipv6_prefix = prompt_ipv6_cidr("IPv6 Address")

    ipv6_gateway = None
    if ipv6:
        ipv6_gateway = prompt_ipv6("IPv6 Gateway")

    external = ExternalInterface(
        iface=external_iface.name,
        pci=external_iface.pci,
        ipv4=ipv4,
        ipv4_prefix=prefix,
        ipv4_gateway=gateway,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        ipv6_gateway=ipv6_gateway
    )

    # Internal interfaces
    internal = []
    for i, iface in enumerate(internal_ifaces):
        print(f"\n{Colors.BOLD}Configure INTERNAL interface #{i+1} ({iface.name}):{Colors.NC}")

        ipv4, prefix = prompt_ipv4_cidr("IPv4 Address")
        ipv6, ipv6_prefix = prompt_ipv6_cidr("IPv6 Address")

        internal.append(InternalInterface(
            iface=iface.name,
            pci=iface.pci,
            vpp_name=f"internal{i}",
            ipv4=ipv4,
            ipv4_prefix=prefix,
            ipv6=ipv6,
            ipv6_prefix=ipv6_prefix
        ))

    return external, internal


def phase4_management_config(mgmt_iface: InterfaceInfo) -> ManagementInterface:
    """Phase 4: Configure management interface."""
    log("Phase 4: Management Interface Configuration")

    print(f"\n{Colors.BOLD}Configure MANAGEMENT interface ({mgmt_iface.name}):{Colors.NC}")
    print("  1) DHCP (recommended for out-of-band management)")
    print("  2) Static IP")

    idx = prompt_select("IP Configuration:", ["DHCP", "Static"])

    if idx == 0:
        return ManagementInterface(iface=mgmt_iface.name, mode="dhcp")

    ipv4, prefix = prompt_ipv4_cidr("IPv4 Address")
    gateway = prompt_ipv4("Gateway")

    return ManagementInterface(
        iface=mgmt_iface.name,
        mode="static",
        ipv4=ipv4,
        ipv4_prefix=prefix,
        ipv4_gateway=gateway
    )


def phase5_bgp_config(external: ExternalInterface) -> BGPConfig:
    """Phase 5: Configure BGP (optional)."""
    log("Phase 5: BGP Configuration (Optional)")

    if not prompt_yes_no("Enable BGP routing?"):
        info("BGP disabled. Static default routes will be used.")
        return BGPConfig(enabled=False)

    asn = prompt_int("  Local AS Number", min_val=1, max_val=4294967295)

    router_id = external.ipv4
    user_id = input(f"  Router ID [{router_id}]: ").strip()
    if user_id and validate_ipv4(user_id):
        router_id = user_id

    peer_ipv4 = prompt_ipv4("Peer IPv4 Address")
    peer_ipv6 = prompt_ipv6("Peer IPv6 Address")
    peer_asn = prompt_int("  Peer AS Number", min_val=1, max_val=4294967295)

    return BGPConfig(
        enabled=True,
        asn=asn,
        router_id=router_id,
        peer_ipv4=peer_ipv4,
        peer_ipv6=peer_ipv6,
        peer_asn=peer_asn
    )


def phase6_nat_config(internal: list[InternalInterface], container: ContainerConfig) -> NATConfig:
    """Phase 6: Configure NAT."""
    log("Phase 6: NAT Configuration")

    # BGP prefix for announcement
    print()
    print(f"  {Colors.BOLD}BGP NAT Prefix{Colors.NC}")
    print("  This is the prefix announced via BGP for NAT (e.g., 23.177.24.96/29).")
    print("  It can be larger than the actual det44 pools.")
    print()
    while True:
        bgp_prefix = input("  BGP NAT prefix [CIDR]: ").strip()
        if validate_ipv4_cidr(bgp_prefix):
            break
        warn("Invalid IPv4 CIDR format")

    # det44 mappings
    print()
    print(f"  {Colors.BOLD}NAT Pool Mappings{Colors.NC}")
    print("  Define det44 mappings: which internal networks map to which NAT pools.")
    print("  The NAT pools should be subsets of the BGP prefix.")
    print("  Example: 192.168.20.0/24 -> 23.177.24.96/30")
    print()

    mappings = []

    # Collect internal network mappings
    for iface in internal:
        print(f"  Internal interface {iface.vpp_name} ({iface.network}):")
        while True:
            pool = input(f"    NAT pool for {iface.network} [CIDR]: ").strip()
            if validate_ipv4_cidr(pool):
                break
            warn("Invalid IPv4 CIDR format")
        mappings.append(NATMapping(source_network=iface.network, nat_pool=pool))

    # Container network mapping
    print(f"\n  Container network ({container.network}):")
    while True:
        pool = input(f"    NAT pool for {container.network} [CIDR]: ").strip()
        if validate_ipv4_cidr(pool):
            break
        warn("Invalid IPv4 CIDR format")
    mappings.append(NATMapping(source_network=container.network, nat_pool=pool))

    # ACL bypass pairs
    print()
    print(f"  {Colors.BOLD}NAT Bypass Rules{Colors.NC}")
    print("  Define source/destination pairs that should skip NAT (direct routing).")
    print("  Example: traffic from 192.168.20.0/24 to 192.168.37.0/24 goes directly.")
    print()

    bypass_pairs = []
    if prompt_yes_no("Add NAT bypass rules?", default=False):
        while True:
            print()
            while True:
                src = input("    Source network [CIDR]: ").strip()
                if validate_ipv4_cidr(src):
                    break
                warn("Invalid IPv4 CIDR format")

            while True:
                dst = input("    Destination network [CIDR]: ").strip()
                if validate_ipv4_cidr(dst):
                    break
                warn("Invalid IPv4 CIDR format")

            bypass_pairs.append(ACLBypassPair(source=src, destination=dst))
            info(f"Added bypass: {src} -> {dst}")

            if not prompt_yes_no("Add another bypass rule?", default=False):
                break

    return NATConfig(bgp_prefix=bgp_prefix, mappings=mappings, bypass_pairs=bypass_pairs)


def phase_vlan_passthrough(internal: list[InternalInterface]) -> list[VLANPassthrough]:
    """Configure VLAN pass-through (L2 cross-connect between external and internal)."""
    log("VLAN Pass-through Configuration (Optional)")

    print()
    print("  VLAN pass-through allows L2 traffic on specific VLANs to pass")
    print("  directly between the external and internal interfaces.")
    print("  This is useful for passing customer VLANs, QinQ traffic, etc.")
    print()

    if not prompt_yes_no("Configure VLAN pass-through?", default=False):
        return []

    # Build list of internal interface names
    internal_names = [iface.vpp_name for iface in internal]

    vlans = []
    while True:
        print()
        print(f"  {Colors.BOLD}Add VLAN pass-through rule:{Colors.NC}")

        # VLAN type
        vlan_type_idx = prompt_select("VLAN type:", [
            "802.1Q (single tag)",
            "802.1ad/QinQ (S-tag only, all C-tags pass through)",
            "802.1ad/QinQ (specific S-tag + C-tag)"
        ])

        vlan_type = "dot1q"
        inner_vlan = None

        if vlan_type_idx == 1:
            vlan_type = "dot1ad"
        elif vlan_type_idx == 2:
            vlan_type = "dot1ad"

        # Outer VLAN ID
        vlan_id = prompt_int("  VLAN ID (outer/S-tag)", min_val=1, max_val=4094)

        # Inner VLAN for specific QinQ
        if vlan_type_idx == 2:
            inner_vlan = prompt_int("  Inner VLAN ID (C-tag)", min_val=1, max_val=4094)

        # Select internal interface
        if len(internal_names) == 1:
            internal_iface = internal_names[0]
            info(f"Using internal interface: {internal_iface}")
        else:
            idx = prompt_select("Select internal interface:", internal_names)
            internal_iface = internal_names[idx]

        vlans.append(VLANPassthrough(
            vlan_id=vlan_id,
            internal_interface=internal_iface,
            vlan_type=vlan_type,
            inner_vlan=inner_vlan
        ))

        # Show what was added
        if inner_vlan:
            info(f"Added: VLAN {vlan_id}.{inner_vlan} (QinQ) <-> {internal_iface}")
        elif vlan_type == "dot1ad":
            info(f"Added: S-VLAN {vlan_id} (QinQ, all C-tags) <-> {internal_iface}")
        else:
            info(f"Added: VLAN {vlan_id} (802.1Q) <-> {internal_iface}")

        if not prompt_yes_no("Add another VLAN pass-through?", default=False):
            break

    return vlans


def phase7_confirm(config: RouterConfig) -> bool:
    """Phase 7: Show summary and confirm."""
    log("Phase 7: Configuration Summary")

    print()
    print("=" * 50)
    print("  Configuration Summary")
    print("=" * 50)
    print()
    print("INTERFACES:")
    print(f"  Management: {config.management.iface} ({config.management.mode})")
    print(f"  External:   {config.external.iface} -> {config.external.ipv4}/{config.external.ipv4_prefix}")
    if config.external.ipv6:
        print(f"              {config.external.ipv6}/{config.external.ipv6_prefix}")
    for iface in config.internal:
        print(f"  Internal:   {iface.iface} -> {iface.ipv4}/{iface.ipv4_prefix}")
        if iface.ipv6:
            print(f"              {iface.ipv6}/{iface.ipv6_prefix}")

    print()
    print("ROUTING:")
    if config.bgp.enabled:
        print(f"  BGP AS:     {config.bgp.asn}")
        print(f"  Router ID:  {config.bgp.router_id}")
        print(f"  Peer:       {config.bgp.peer_ipv4} (AS {config.bgp.peer_asn})")
        if config.bgp.peer_ipv6:
            print(f"              {config.bgp.peer_ipv6}")
    else:
        print(f"  Static routing (gateway: {config.external.ipv4_gateway})")

    print()
    print("NAT:")
    print(f"  BGP Prefix: {config.nat.bgp_prefix}")
    print("  Mappings:")
    for m in config.nat.mappings:
        print(f"    {m.source_network} -> {m.nat_pool}")
    if config.nat.bypass_pairs:
        print("  Bypass Rules:")
        for bp in config.nat.bypass_pairs:
            print(f"    {bp.source} -> {bp.destination}")

    print()
    print("CONTAINERS:")
    print(f"  Network:    {config.container.network}")
    print(f"  Gateway:    {config.container.gateway}")

    if config.vlan_passthrough:
        print()
        print("VLAN PASS-THROUGH:")
        for v in config.vlan_passthrough:
            if v.inner_vlan:
                print(f"  VLAN {v.vlan_id}.{v.inner_vlan} (QinQ) <-> {v.internal_interface}")
            elif v.vlan_type == "dot1ad":
                print(f"  S-VLAN {v.vlan_id} (QinQ) <-> {v.internal_interface}")
            else:
                print(f"  VLAN {v.vlan_id} (802.1Q) <-> {v.internal_interface}")

    print()
    print("=" * 50)
    print()

    return prompt_yes_no("Apply this configuration?", default=True)


# =============================================================================
# Template Rendering
# =============================================================================

def render_templates(config: RouterConfig, template_dir: Path, output_dir: Path) -> None:
    """Render all templates with the given configuration."""
    log("Generating configuration files...")

    output_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True
    )

    # Add enumerate to Jinja2 environment
    env.globals['enumerate'] = enumerate

    # Prepare template context
    context = {
        'hostname': config.hostname,
        'management': config.management,
        'external': config.external,
        'internal': config.internal,
        'bgp': config.bgp,
        'nat': config.nat,
        'container': config.container,
        'cpu': config.cpu,
        'vlan_passthrough': config.vlan_passthrough,
    }

    # Render each template
    templates = [
        ("vpp/startup-core.conf.j2", "startup-core.conf"),
        ("vpp/startup-nat.conf.j2", "startup-nat.conf"),
        ("vpp/commands-core.txt.j2", "commands-core.txt"),
        ("vpp/commands-nat.txt.j2", "commands-nat.txt"),
        ("frr/frr.conf.j2", "frr.conf"),
        ("systemd/netns-move-interfaces.service.j2", "netns-move-interfaces.service"),
        ("systemd/management.network.j2", "10-management.network"),
        ("scripts/vpp-core-config.sh.j2", "vpp-core-config.sh"),
        ("scripts/incus-networking.sh.j2", "incus-networking.sh"),
        ("scripts/incus-init.sh.j2", "incus-init.sh"),
    ]

    for template_path, output_name in templates:
        try:
            template = env.get_template(template_path)
            rendered = template.render(**context)

            output_path = output_dir / output_name
            output_path.write_text(rendered)

        except Exception as e:
            fatal(f"Failed to render {template_path}: {e}")

    # Make scripts executable
    for script in ["vpp-core-config.sh", "incus-networking.sh", "incus-init.sh"]:
        (output_dir / script).chmod(0o755)

    log(f"Configuration files generated in {output_dir}")


# =============================================================================
# Apply Configuration
# =============================================================================

def apply_configs(output_dir: Path) -> None:
    """Copy generated configs to system locations."""
    log("Applying configuration...")

    copies = [
        ("startup-core.conf", "/etc/vpp/startup-core.conf"),
        ("startup-nat.conf", "/etc/vpp/startup-nat.conf"),
        ("commands-core.txt", "/etc/vpp/commands-core.txt"),
        ("commands-nat.txt", "/etc/vpp/commands-nat.txt"),
        ("frr.conf", "/etc/frr/frr.conf"),
        ("netns-move-interfaces.service", "/etc/systemd/system/netns-move-interfaces.service"),
        ("10-management.network", "/etc/systemd/network/10-management.network"),
        ("vpp-core-config.sh", "/usr/local/bin/vpp-core-config.sh"),
        ("incus-networking.sh", "/usr/local/bin/incus-networking.sh"),
        ("incus-init.sh", "/usr/local/bin/incus-init.sh"),
    ]

    for src, dst in copies:
        src_path = output_dir / src
        dst_path = Path(dst)

        # Create parent directory if needed
        dst_path.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(src_path, dst_path)

    # Fix FRR permissions
    subprocess.run(["chown", "-R", "frr:frr", "/etc/frr"], check=False)
    subprocess.run(["chmod", "640", "/etc/frr/frr.conf"], check=False)

    # Reload systemd
    subprocess.run(["systemctl", "daemon-reload"], check=True)

    log("Configuration applied")


def enable_services() -> None:
    """Enable all required services."""
    log("Enabling services...")

    services = [
        "systemd-networkd",
        "netns-dataplane",
        "netns-move-interfaces",
        "vpp-core",
        "vpp-core-config",
        "vpp-nat",
        "frr",
        "incus-init",
        "incus-dataplane",
    ]

    for service in services:
        subprocess.run(["systemctl", "enable", service], check=False)

    log("Services enabled")


def save_config(config: RouterConfig, config_file: Path) -> None:
    """Save configuration to JSON file."""
    log(f"Saving configuration to {config_file}...")

    config_file.parent.mkdir(parents=True, exist_ok=True)

    # Convert dataclasses to dicts
    def to_dict(obj):
        if hasattr(obj, '__dataclass_fields__'):
            return {k: to_dict(v) for k, v in asdict(obj).items()}
        elif isinstance(obj, list):
            return [to_dict(i) for i in obj]
        else:
            return obj

    data = to_dict(config)

    with open(config_file, 'w') as f:
        json.dump(data, f, indent=2)

    log(f"Configuration saved to {config_file}")


def load_config(config_file: Path) -> RouterConfig:
    """Load configuration from JSON file."""
    with open(config_file) as f:
        data = json.load(f)

    # Reconstruct dataclasses
    nat_data = data['nat']
    nat = NATConfig(
        bgp_prefix=nat_data.get('bgp_prefix', ''),
        mappings=[NATMapping(**m) for m in nat_data.get('mappings', [])],
        bypass_pairs=[ACLBypassPair(**bp) for bp in nat_data.get('bypass_pairs', [])]
    )

    # Handle VLAN passthrough
    vlan_passthrough = [
        VLANPassthrough(**v) for v in data.get('vlan_passthrough', [])
    ]

    # Always re-detect CPU allocation for current hardware
    cpu = CPUConfig.detect_and_allocate()

    config = RouterConfig(
        hostname=data.get('hostname', 'appliance'),
        management=ManagementInterface(**data['management']),
        external=ExternalInterface(**data['external']),
        internal=[InternalInterface(**i) for i in data['internal']],
        bgp=BGPConfig(**data['bgp']),
        nat=nat,
        container=ContainerConfig(**data['container']),
        cpu=cpu,
        vlan_passthrough=vlan_passthrough,
    )

    return config


# =============================================================================
# Main
# =============================================================================

def show_banner() -> None:
    """Show application banner."""
    print()
    print("=" * 50)
    print("  IMP Router Configuration")
    print("=" * 50)
    print()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Configure IMP router")
    parser.add_argument("--apply-only", action="store_true",
                        help="Apply existing configuration without prompts")
    parser.add_argument("--template-dir", type=Path, default=TEMPLATE_DIR,
                        help=f"Template directory (default: {TEMPLATE_DIR})")
    parser.add_argument("--config-file", type=Path, default=CONFIG_FILE,
                        help=f"Configuration file (default: {CONFIG_FILE})")
    args = parser.parse_args()

    # Check we're running as root
    if os.geteuid() != 0:
        fatal("This script must be run as root")

    # Check template directory exists
    if not args.template_dir.exists():
        fatal(f"Template directory not found: {args.template_dir}")

    # Apply-only mode
    if args.apply_only:
        if not args.config_file.exists():
            fatal(f"No existing configuration found at {args.config_file}")

        log(f"Applying existing configuration from {args.config_file}")
        config = load_config(args.config_file)
        render_templates(config, args.template_dir, GENERATED_DIR)
        apply_configs(GENERATED_DIR)
        log("Configuration applied successfully")
        return

    show_banner()

    # Check for existing config
    if args.config_file.exists():
        warn(f"Existing configuration found at {args.config_file}")
        if prompt_yes_no("Load existing configuration?"):
            config = load_config(args.config_file)
            log("Configuration loaded")

            if phase7_confirm(config):
                render_templates(config, args.template_dir, GENERATED_DIR)
                apply_configs(GENERATED_DIR)
                enable_services()
                log("Configuration complete!")
            return

    # Interactive configuration
    interfaces = phase1_detect_interfaces()
    mgmt_iface, ext_iface, int_ifaces = phase2_assign_roles(interfaces)
    external, internal = phase3_ip_config(ext_iface, int_ifaces)
    management = phase4_management_config(mgmt_iface)

    # Container defaults
    container = ContainerConfig()
    info(f"Container network defaults: {container.network} (gateway: {container.gateway})")
    if not prompt_yes_no("Use default container network?", default=True):
        while True:
            net = input("  Container Network [CIDR]: ").strip()
            if validate_ipv4_cidr(net):
                break
            warn("Invalid CIDR")

        gw = prompt_ipv4("Container Gateway IP")
        container = ContainerConfig.from_network(net, gw)

    bgp = phase5_bgp_config(external)
    nat = phase6_nat_config(internal, container)
    vlan_passthrough = phase_vlan_passthrough(internal)

    # Build config object
    config = RouterConfig(
        hostname=socket.gethostname(),
        management=management,
        external=external,
        internal=internal,
        bgp=bgp,
        nat=nat,
        container=container,
        cpu=CPUConfig.detect_and_allocate(),
        vlan_passthrough=vlan_passthrough,
    )

    # Show CPU allocation
    info(f"CPU allocation ({config.cpu.total_cores} cores detected):")
    info(f"  VPP Core: main={config.cpu.core_main}, workers={config.cpu.core_workers or 'none'}")
    if config.cpu.nat_main > 0:
        info(f"  VPP NAT:  main={config.cpu.nat_main}, workers={config.cpu.nat_workers or 'none'}")
    else:
        info(f"  VPP NAT:  using software threads (no dedicated cores)")

    if not phase7_confirm(config):
        fatal("Configuration cancelled")

    render_templates(config, args.template_dir, GENERATED_DIR)
    apply_configs(GENERATED_DIR)
    enable_services()
    save_config(config, args.config_file)

    print()
    log("Configuration complete!")
    print()
    print("Next steps:")
    print("  1. Reboot to apply network changes")
    print("  2. Verify services: systemctl status vpp-core frr")
    print("  3. Check VPP: vppctl -s /run/vpp/core-cli.sock show interface")
    print()

    if prompt_yes_no("Reboot now?"):
        subprocess.run(["reboot"])


if __name__ == "__main__":
    main()
