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
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

# Add paths for imp_lib imports:
# - Script directory (for local development)
# - Python local site-packages (for production on router)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, '/usr/local/lib/python3/dist-packages')

try:
    from jinja2 import Environment, FileSystemLoader
except ImportError:
    print("ERROR: python3-jinja2 is required. Install with: apt install python3-jinja2")
    sys.exit(1)

# Import configuration dataclasses from imp_lib
from imp_lib.config import (
    InterfaceInfo,
    SubInterface,
    LoopbackInterface,
    BridgeDomainMember,
    BVIConfig,
    InterfaceAddress,
    Interface,
    Route,
    ManagementInterface,
    BGPPeer,
    BGPConfig,
    OSPFConfig,
    OSPF6Config,
    NATMapping,
    ACLBypassPair,
    VLANPassthrough,
    NATConfig,
    ContainerConfig,
    CPUConfig,
    RouterConfig,
    # Validation
    validate_ipv4,
    validate_ipv4_cidr,
    validate_ipv6,
    validate_ipv6_cidr,
    parse_cidr,
    # Serialization
    load_config,
    save_config,
)

# Import module system types from imp_lib
from imp_lib.modules import (
    VPPModuleInstance,
    VPPModuleConnection,
    ModuleShowCommand,
    ModuleABF,
    load_modules_from_config,
    allocate_memif_addresses,
)
HAS_MODULE_LOADER = True


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


# Note: Data classes are now imported from imp_lib.config


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


# Note: Validation functions are now imported from imp_lib.config


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


def phase2_select_interfaces(interfaces: list[InterfaceInfo]) -> tuple[InterfaceInfo, list[InterfaceInfo]]:
    """Phase 2: Select management and dataplane interfaces. Returns (management, dataplane[])."""
    log("Phase 2: Interface Selection")

    available = list(interfaces)
    names = [i.name for i in available]

    # Management interface
    print(f"\n{Colors.BOLD}Management interface{Colors.NC}")
    print("  This interface stays in the default namespace for SSH access.")
    print("  Typically used for out-of-band management.")
    idx = prompt_select("Select MANAGEMENT interface:", names)
    management = available.pop(idx)
    names = [i.name for i in available]

    if len(available) < 1:
        fatal("Need at least 1 more interface for the dataplane")

    # Dataplane interfaces
    print(f"\n{Colors.BOLD}Dataplane interfaces{Colors.NC}")
    print("  These interfaces will be managed by VPP with DPDK.")
    print("  You can configure their roles (wan, lan, etc.) in the next step.")

    dataplane = []
    while available:
        idx = prompt_select(f"Select dataplane interface #{len(dataplane) + 1}:", names)
        dataplane.append(available.pop(idx))
        names = [i.name for i in available]

        if available:
            if not prompt_yes_no("Add another dataplane interface?"):
                break

    # Summary
    print(f"\n{Colors.BOLD}Interface Selection Summary:{Colors.NC}")
    print(f"  Management: {management.name}")
    for iface in dataplane:
        print(f"  Dataplane:  {iface.name} (PCI: {iface.pci})")

    return management, dataplane


def phase3_interface_config(dataplane_ifaces: list[InterfaceInfo]) -> list[Interface]:
    """Phase 3: Configure interfaces (name + IPv4 + optional IPv6)."""
    log("Phase 3: Interface Configuration")

    interfaces = []
    for iface_info in dataplane_ifaces:
        print(f"\n{Colors.BOLD}Configure interface: {iface_info.name}{Colors.NC}")

        # Get user-defined name
        default_name = iface_info.name
        name_input = input(f"  Name (e.g., wan, lan, transit) [{default_name}]: ").strip()
        name = name_input if name_input else default_name

        # Validate name is unique
        while any(i.name == name for i in interfaces):
            warn(f"Name '{name}' is already used")
            name = prompt_string("  Name (must be unique)")

        # Get IPv4 address (required)
        ipv4, prefix = prompt_ipv4_cidr("IPv4 Address")

        # Get optional IPv6 address
        ipv6_addrs = []
        ipv6, ipv6_prefix = prompt_ipv6_cidr("IPv6 Address")
        if ipv6:
            ipv6_addrs.append(InterfaceAddress(address=ipv6, prefix=ipv6_prefix))

        interfaces.append(Interface(
            name=name,
            iface=iface_info.name,
            pci=iface_info.pci,
            ipv4=[InterfaceAddress(address=ipv4, prefix=prefix)],
            ipv6=ipv6_addrs,
            mtu=1500,
        ))

        if ipv6:
            info(f"Added interface: {name} ({iface_info.name}) with {ipv4}/{prefix} and {ipv6}/{ipv6_prefix}")
        else:
            info(f"Added interface: {name} ({iface_info.name}) with {ipv4}/{prefix}")

    return interfaces


def phase4_route_config() -> list[Route]:
    """Phase 4: Configure default routes."""
    log("Phase 4: Route Configuration")

    routes = []

    # Default IPv4 gateway
    print(f"\n{Colors.BOLD}Default Routes{Colors.NC}")
    gateway_v4 = prompt_ipv4("Default IPv4 gateway")
    routes.append(Route(destination="0.0.0.0/0", via=gateway_v4))

    # Optional IPv6 default gateway
    gateway_v6 = prompt_ipv6("Default IPv6 gateway")
    if gateway_v6:
        routes.append(Route(destination="::/0", via=gateway_v6))

    return routes


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


def phase_bgp_config(interfaces: list[Interface]) -> BGPConfig:
    """Configure BGP (optional). Can be used from REPL."""
    log("BGP Configuration")

    if not prompt_yes_no("Enable BGP routing?"):
        info("BGP disabled.")
        return BGPConfig(enabled=False)

    asn = prompt_int("  Local AS Number", min_val=1, max_val=4294967295)

    # Default router-id to first interface's first IPv4 address
    default_router_id = None
    for iface in interfaces:
        if iface.ipv4:
            default_router_id = iface.ipv4[0].address
            break

    if default_router_id:
        user_id = input(f"  Router ID [{default_router_id}]: ").strip()
        router_id = user_id if user_id and validate_ipv4(user_id) else default_router_id
    else:
        router_id = prompt_ipv4("Router ID")

    # Collect BGP peers
    peers = []
    print()
    print(f"  {Colors.BOLD}BGP Peers{Colors.NC}")
    print("  Add one or more BGP peers. You can add more later via 'imp' REPL.")
    print()

    while True:
        print(f"  {Colors.BOLD}Peer #{len(peers) + 1}{Colors.NC}")
        peer_name = input("  Peer name (e.g., upstream, ix-peer): ").strip()
        if not peer_name:
            peer_name = f"peer{len(peers) + 1}"

        # Accept either IPv4 or IPv6
        while True:
            peer_ip = input("  Peer IP Address (IPv4 or IPv6): ").strip()
            if validate_ipv4(peer_ip) or validate_ipv6(peer_ip):
                break
            warn("Invalid IP address")

        peer_asn = prompt_int("  Peer AS Number", min_val=1, max_val=4294967295)

        peers.append(BGPPeer(
            name=peer_name,
            peer_ip=peer_ip,
            peer_asn=peer_asn,
            description=peer_name
        ))

        af = "IPv6" if ':' in peer_ip else "IPv4"
        info(f"Added {af} peer: {peer_name} ({peer_ip}) AS {peer_asn}")

        if not prompt_yes_no("Add another BGP peer?", default=False):
            break

    return BGPConfig(
        enabled=True,
        asn=asn,
        router_id=router_id,
        peers=peers
    )


def phase_vlan_passthrough(interfaces: list[Interface]) -> list[VLANPassthrough]:
    """Configure VLAN pass-through (L2 cross-connect between interfaces)."""
    log("VLAN Pass-through Configuration (Optional)")

    print()
    print("  VLAN pass-through allows L2 traffic on specific VLANs to pass")
    print("  directly between interfaces (e.g., external to internal).")
    print("  This is useful for passing customer VLANs, QinQ traffic, etc.")
    print()

    if not prompt_yes_no("Configure VLAN pass-through?", default=False):
        return []

    # Build list of interface names
    interface_names = [iface.name for iface in interfaces]

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

        # Select source interface
        if len(interface_names) < 2:
            error("Need at least 2 interfaces for VLAN passthrough")
            return []

        print()
        from_idx = prompt_select("Select source interface:", interface_names)
        from_iface = interface_names[from_idx]

        # Select destination interface (exclude source)
        remaining = [n for n in interface_names if n != from_iface]
        if len(remaining) == 1:
            to_iface = remaining[0]
            info(f"Using destination: {to_iface}")
        else:
            to_idx = prompt_select("Select destination interface:", remaining)
            to_iface = remaining[to_idx]

        vlans.append(VLANPassthrough(
            vlan_id=vlan_id,
            from_interface=from_iface,
            to_interface=to_iface,
            vlan_type=vlan_type,
            inner_vlan=inner_vlan
        ))

        # Show what was added
        if inner_vlan:
            info(f"Added: VLAN {vlan_id}.{inner_vlan} (QinQ) {from_iface} <-> {to_iface}")
        elif vlan_type == "dot1ad":
            info(f"Added: S-VLAN {vlan_id} (QinQ, all C-tags) {from_iface} <-> {to_iface}")
        else:
            info(f"Added: VLAN {vlan_id} (802.1Q) {from_iface} <-> {to_iface}")

        if not prompt_yes_no("Add another VLAN pass-through?", default=False):
            break

    return vlans


def configure_subinterfaces(interface_name: str, vpp_name: str) -> list[SubInterface]:
    """Configure VLAN sub-interfaces (L3 terminated) for an interface."""
    print()
    print(f"  {Colors.BOLD}VLAN Sub-interfaces for {interface_name}{Colors.NC}")
    print("  Sub-interfaces allow you to assign IPs to VLANs on this interface.")
    print("  (This is L3 termination, not L2 passthrough)")
    print()

    if not prompt_yes_no(f"Add VLAN sub-interfaces to {interface_name}?", default=False):
        return []

    subinterfaces = []
    while True:
        print()
        vlan_id = prompt_int("  VLAN ID", min_val=1, max_val=4094)

        # Check for duplicate VLAN ID
        if any(s.vlan_id == vlan_id for s in subinterfaces):
            warn(f"VLAN {vlan_id} already configured on this interface")
            continue

        # IPv4 configuration (optional)
        print(f"\n  IPv4 for {vpp_name}.{vlan_id}:")
        ipv4_input = input("    IPv4 Address [CIDR, optional]: ").strip()
        ipv4, ipv4_prefix = None, None
        if ipv4_input:
            if validate_ipv4_cidr(ipv4_input):
                ipv4, ipv4_prefix = parse_cidr(ipv4_input)
            else:
                warn("Invalid IPv4 CIDR, skipping")

        # IPv6 configuration (optional)
        ipv6, ipv6_prefix = prompt_ipv6_cidr(f"IPv6 for {vpp_name}.{vlan_id}")

        # At least one IP is required
        if not ipv4 and not ipv6:
            warn("At least one IP address is required for a sub-interface")
            continue

        # LCP (TAP for FRR visibility)
        create_lcp = prompt_yes_no("Create linux_cp TAP for FRR visibility?", default=True)

        subinterfaces.append(SubInterface(
            vlan_id=vlan_id,
            ipv4=ipv4,
            ipv4_prefix=ipv4_prefix,
            ipv6=ipv6,
            ipv6_prefix=ipv6_prefix,
            create_lcp=create_lcp
        ))

        info(f"Added sub-interface: {vpp_name}.{vlan_id}")
        if ipv4:
            print(f"    IPv4: {ipv4}/{ipv4_prefix}")
        if ipv6:
            print(f"    IPv6: {ipv6}/{ipv6_prefix}")

        if not prompt_yes_no("Add another sub-interface?", default=False):
            break

    return subinterfaces


def phase_loopback_config() -> list[LoopbackInterface]:
    """Configure loopback interfaces."""
    log("Loopback Interface Configuration (Optional)")

    print()
    print("  Loopback interfaces are virtual interfaces useful for:")
    print("  - Router ID (stable address for BGP/OSPF)")
    print("  - Service addresses (DNS, management)")
    print("  - Anycast addresses")
    print()

    if not prompt_yes_no("Configure loopback interfaces?", default=False):
        return []

    loopbacks = []
    instance = 0  # VPP loopback instance counter

    while True:
        print()
        print(f"  {Colors.BOLD}Loopback interface #{instance}{Colors.NC}")

        # Friendly name
        name = prompt_string(f"  Name for loop{instance} (e.g., 'router-id', 'services')")

        # IPv4 configuration (optional)
        print(f"\n  IPv4 for loop{instance}:")
        ipv4_input = input("    IPv4 Address [CIDR, optional]: ").strip()
        ipv4, ipv4_prefix = None, None
        if ipv4_input:
            if validate_ipv4_cidr(ipv4_input):
                ipv4, ipv4_prefix = parse_cidr(ipv4_input)
            else:
                warn("Invalid IPv4 CIDR, skipping")

        # IPv6 configuration (optional)
        ipv6, ipv6_prefix = prompt_ipv6_cidr(f"IPv6 for loop{instance}")

        # At least one IP is required
        if not ipv4 and not ipv6:
            warn("At least one IP address is required for a loopback")
            continue

        # LCP (TAP for FRR visibility)
        create_lcp = prompt_yes_no("Create linux_cp TAP for FRR visibility?", default=True)

        loopbacks.append(LoopbackInterface(
            instance=instance,
            name=name,
            ipv4=ipv4,
            ipv4_prefix=ipv4_prefix,
            ipv6=ipv6,
            ipv6_prefix=ipv6_prefix,
            create_lcp=create_lcp
        ))

        info(f"Added loopback: loop{instance} ({name})")
        if ipv4:
            print(f"    IPv4: {ipv4}/{ipv4_prefix}")
        if ipv6:
            print(f"    IPv6: {ipv6}/{ipv6_prefix}")

        instance += 1

        if not prompt_yes_no("Add another loopback interface?", default=False):
            break

    return loopbacks


def phase_bvi_config(interfaces: list[Interface]) -> list[BVIConfig]:
    """Configure BVI (Bridge Virtual Interface) domains - switch-like VLAN interfaces."""
    log("BVI Configuration (Optional)")

    print()
    print("  BVI (Bridge Virtual Interface) creates switch-like VLAN interfaces:")
    print("  - Multiple ports/VLANs are bridged together at L2")
    print("  - A single L3 gateway (the BVI) serves all members")
    print("  - Similar to 'interface vlan X' on a traditional switch")
    print()

    if not prompt_yes_no("Configure BVI domains?", default=False):
        return []

    # Build list of available interfaces for bridge membership
    available_interfaces = [iface.name for iface in interfaces]

    bvi_domains = []
    bridge_id = 100  # Start bridge domain IDs at 100 to avoid conflicts

    while True:
        print()
        print(f"  {Colors.BOLD}Bridge Domain {bridge_id}{Colors.NC}")

        # Friendly name
        name = prompt_string(f"  Name for this BVI (e.g., 'customer-lan', 'vlan100')")

        # Collect bridge domain members
        print()
        print("  Add interfaces/VLANs to this bridge domain:")
        print("  (These will be L2-switched together)")

        members = []
        while True:
            print()
            print(f"  Available interfaces: {', '.join(available_interfaces)}")
            iface_name = prompt_string("  Interface name (or 'done' to finish)")

            if iface_name.lower() == 'done':
                if not members:
                    warn("At least one member is required")
                    continue
                break

            if iface_name not in available_interfaces:
                warn(f"Unknown interface: {iface_name}")
                continue

            # Ask for VLAN ID (optional - if not set, uses the interface directly)
            vlan_input = input("    VLAN ID (optional, press Enter for untagged): ").strip()
            vlan_id = None
            if vlan_input:
                try:
                    vlan_id = int(vlan_input)
                    if not 1 <= vlan_id <= 4094:
                        warn("VLAN ID must be 1-4094")
                        continue
                except ValueError:
                    warn("Invalid VLAN ID")
                    continue

            members.append(BridgeDomainMember(interface=iface_name, vlan_id=vlan_id))

            if vlan_id:
                info(f"Added: {iface_name}.{vlan_id}")
            else:
                info(f"Added: {iface_name} (untagged)")

        # BVI IP configuration
        print()
        print(f"  {Colors.BOLD}BVI IP Configuration{Colors.NC}")
        print("  The BVI is the L3 gateway for this bridge domain.")

        # IPv4 (optional)
        print()
        ipv4_input = input("    IPv4 Address [CIDR, optional]: ").strip()
        ipv4, ipv4_prefix = None, None
        if ipv4_input:
            if validate_ipv4_cidr(ipv4_input):
                ipv4, ipv4_prefix = parse_cidr(ipv4_input)
            else:
                warn("Invalid IPv4 CIDR, skipping")

        # IPv6 (optional)
        ipv6, ipv6_prefix = prompt_ipv6_cidr("IPv6 Address")

        # At least one IP is required
        if not ipv4 and not ipv6:
            warn("At least one IP address is required for the BVI")
            continue

        # LCP (TAP for FRR visibility)
        create_lcp = prompt_yes_no("Create linux_cp TAP for FRR visibility?", default=True)

        bvi_domains.append(BVIConfig(
            bridge_id=bridge_id,
            name=name,
            members=members,
            ipv4=ipv4,
            ipv4_prefix=ipv4_prefix,
            ipv6=ipv6,
            ipv6_prefix=ipv6_prefix,
            create_lcp=create_lcp
        ))

        # Show summary
        info(f"Added BVI: loop{bridge_id} ({name})")
        print(f"    Members: {', '.join(m.interface + (f'.{m.vlan_id}' if m.vlan_id else '') for m in members)}")
        if ipv4:
            print(f"    IPv4: {ipv4}/{ipv4_prefix}")
        if ipv6:
            print(f"    IPv6: {ipv6}/{ipv6_prefix}")

        bridge_id += 1

        if not prompt_yes_no("Add another BVI domain?", default=False):
            break

    return bvi_domains


def phase_confirm(config: RouterConfig) -> bool:
    """Show summary and confirm."""
    log("Configuration Summary")

    print()
    print("=" * 50)
    print("  Configuration Summary")
    print("=" * 50)
    print()
    print("INTERFACES:")
    print(f"  Management: {config.management.iface} ({config.management.mode})")
    for iface in config.interfaces:
        ipv4_str = ", ".join(f"{a.address}/{a.prefix}" for a in iface.ipv4) if iface.ipv4 else "none"
        ipv6_str = ", ".join(f"{a.address}/{a.prefix}" for a in iface.ipv6) if iface.ipv6 else ""
        print(f"  {iface.name}: {iface.iface} -> {ipv4_str}")
        if ipv6_str:
            print(f"    {' ' * len(iface.name)}  IPv6: {ipv6_str}")
        if iface.mtu != 1500:
            print(f"    {' ' * len(iface.name)}  MTU: {iface.mtu}")
        for sub in iface.subinterfaces:
            sub_ips = []
            if sub.ipv4:
                sub_ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                sub_ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp_note = " (LCP)" if sub.create_lcp else ""
            print(f"    .{sub.vlan_id}: {', '.join(sub_ips)}{lcp_note}")

    print()
    print("ROUTES:")
    for route in config.routes:
        iface_note = f" via {route.interface}" if route.interface else ""
        print(f"  {route.destination} -> {route.via}{iface_note}")

    print()
    print("ROUTING PROTOCOLS:")
    if config.bgp.enabled:
        print(f"  BGP AS:     {config.bgp.asn}")
        print(f"  Router ID:  {config.bgp.router_id}")
        print(f"  Peers ({len(config.bgp.peers)}):")
        for peer in config.bgp.peers:
            af = "IPv6" if ':' in peer.peer_ip else "IPv4"
            print(f"    {peer.name}: {peer.peer_ip} AS {peer.peer_asn} ({af})")
    else:
        print("  (none configured - use 'imp' REPL to enable BGP/OSPF)")

    print()
    print("MODULES:")
    if config.modules:
        for mod in config.modules:
            status = "enabled" if mod.get('enabled') else "disabled"
            print(f"  {mod.get('name', 'unknown')}: [{status}]")
    else:
        print("  (none configured - use 'imp' REPL to add modules)")

    print()
    print("CONTAINERS:")
    print(f"  Network:    {config.container.network}")
    print(f"  Gateway:    {config.container.gateway}")

    if config.vlan_passthrough:
        print()
        print("VLAN PASS-THROUGH:")
        for v in config.vlan_passthrough:
            if v.inner_vlan:
                print(f"  VLAN {v.vlan_id}.{v.inner_vlan} (QinQ) {v.from_interface} <-> {v.to_interface}")
            elif v.vlan_type == "dot1ad":
                print(f"  S-VLAN {v.vlan_id} (QinQ) {v.from_interface} <-> {v.to_interface}")
            else:
                print(f"  VLAN {v.vlan_id} (802.1Q) {v.from_interface} <-> {v.to_interface}")

    if config.loopbacks:
        print()
        print("LOOPBACK INTERFACES:")
        for lo in config.loopbacks:
            lo_ips = []
            if lo.ipv4:
                lo_ips.append(f"{lo.ipv4}/{lo.ipv4_prefix}")
            if lo.ipv6:
                lo_ips.append(f"{lo.ipv6}/{lo.ipv6_prefix}")
            lcp_note = " (LCP)" if lo.create_lcp else ""
            print(f"  loop{lo.instance} ({lo.name}): {', '.join(lo_ips)}{lcp_note}")

    if config.bvi_domains:
        print()
        print("BVI DOMAINS (L2 bridge + L3 gateway):")
        for bvi in config.bvi_domains:
            bvi_ips = []
            if bvi.ipv4:
                bvi_ips.append(f"{bvi.ipv4}/{bvi.ipv4_prefix}")
            if bvi.ipv6:
                bvi_ips.append(f"{bvi.ipv6}/{bvi.ipv6_prefix}")
            lcp_note = " (LCP)" if bvi.create_lcp else ""
            members_str = ", ".join(
                f"{m.interface}.{m.vlan_id}" if m.vlan_id else m.interface
                for m in bvi.members
            )
            print(f"  loop{bvi.bridge_id} ({bvi.name}): {', '.join(bvi_ips)}{lcp_note}")
            print(f"    Members: {members_str}")

    print()
    print("=" * 50)
    print()

    return prompt_yes_no("Apply this configuration?", default=True)


# =============================================================================
# Template Rendering
# =============================================================================

def render_templates(config: RouterConfig, template_dir: Path, output_dir: Path, quiet: bool = False) -> None:
    """Render all templates with the given configuration."""

    output_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True
    )

    # Add enumerate to Jinja2 environment
    env.globals['enumerate'] = enumerate

    # Add custom test for substring matching (used in templates for IPv6 detection)
    env.tests['contains'] = lambda value, substring: substring in str(value)

    # Load and prepare module instances
    module_instances = []
    if HAS_MODULE_LOADER and config.modules:
        module_instances, errors = load_modules_from_config(config.modules)
        if errors and not quiet:
            for err in errors:
                warn(f"Module warning: {err}")

        # Render module VPP commands using Jinja2
        for module in module_instances:
            if module.enabled and module.vpp_commands:
                try:
                    # Create a template from the module's VPP commands
                    cmd_template = env.from_string(module.vpp_commands)
                    module.vpp_commands_rendered = cmd_template.render(
                        module=module,
                        interfaces=config.interfaces,
                        routes=config.routes,
                        container=config.container,
                    )
                except Exception as e:
                    if not quiet:
                        warn(f"Failed to render VPP commands for module {module.name}: {e}")
                    module.vpp_commands_rendered = f"# Error rendering commands: {e}"

    # Prepare template context
    context = {
        'hostname': config.hostname,
        'management': config.management,
        'interfaces': config.interfaces,
        'routes': config.routes,
        'bgp': config.bgp,
        'ospf': config.ospf,
        'ospf6': config.ospf6,
        'container': config.container,
        'cpu': config.cpu,
        'vlan_passthrough': config.vlan_passthrough,
        'loopbacks': config.loopbacks,
        'bvi_domains': config.bvi_domains,
        'modules': module_instances,
    }

    # Render core templates
    templates = [
        ("vpp/startup-core.conf.j2", "startup-core.conf"),
        ("vpp/commands-core.txt.j2", "commands-core.txt"),
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

    # Render per-module templates
    for module in module_instances:
        if not module.enabled:
            continue

        module_context = {'module': module, **context}

        # Module startup config
        try:
            template = env.get_template("vpp/startup-module.conf.j2")
            rendered = template.render(**module_context)
            (output_dir / f"startup-{module.name}.conf").write_text(rendered)
        except Exception as e:
            if not quiet:
                warn(f"Failed to render startup config for {module.name}: {e}")

        # Module commands
        try:
            template = env.get_template("vpp/commands-module.txt.j2")
            rendered = template.render(**module_context)
            (output_dir / f"commands-{module.name}.txt").write_text(rendered)
        except Exception as e:
            if not quiet:
                warn(f"Failed to render commands for {module.name}: {e}")

        # Module systemd service
        try:
            template = env.get_template("systemd/vpp-module.service.j2")
            rendered = template.render(**module_context)
            (output_dir / f"vpp-{module.name}.service").write_text(rendered)
        except Exception as e:
            if not quiet:
                warn(f"Failed to render service for {module.name}: {e}")

    # Make scripts executable
    for script in ["vpp-core-config.sh", "incus-networking.sh", "incus-init.sh"]:
        (output_dir / script).chmod(0o755)

    if not quiet:
        log(f"Configuration files generated in {output_dir}")


# =============================================================================
# Apply Configuration
# =============================================================================

def apply_configs(output_dir: Path, quiet: bool = False) -> None:
    """Copy generated configs to system locations."""

    # Core config files
    copies = [
        ("startup-core.conf", "/etc/vpp/startup-core.conf"),
        ("commands-core.txt", "/etc/vpp/commands-core.txt"),
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

        if src_path.exists():
            shutil.copy2(src_path, dst_path)

    # Copy module configs dynamically
    # VPP startup and command files for modules
    for src_path in output_dir.glob("startup-*.conf"):
        if src_path.name != "startup-core.conf":
            dst_path = Path("/etc/vpp") / src_path.name
            shutil.copy2(src_path, dst_path)

    for src_path in output_dir.glob("commands-*.txt"):
        if src_path.name != "commands-core.txt":
            dst_path = Path("/etc/vpp") / src_path.name
            shutil.copy2(src_path, dst_path)

    # Module systemd services
    # Track which modules should be enabled (only those with generated service files)
    enabled_module_services = set()
    for src_path in output_dir.glob("vpp-*.service"):
        dst_path = Path("/etc/systemd/system") / src_path.name
        shutil.copy2(src_path, dst_path)
        enabled_module_services.add(src_path.stem)  # e.g., "vpp-nat"

    # Find existing module services that should be disabled (no longer generated)
    systemd_dir = Path("/etc/systemd/system")
    for service_file in systemd_dir.glob("vpp-*.service"):
        # Skip vpp-core* services (not dynamically managed)
        if service_file.name.startswith("vpp-core"):
            continue
        service_name = service_file.stem
        if service_name not in enabled_module_services:
            # Module was disabled - stop, disable, and remove service
            subprocess.run(["systemctl", "stop", service_name], check=False)
            subprocess.run(["systemctl", "disable", service_name], check=False)
            service_file.unlink(missing_ok=True)
            # Also remove corresponding config files
            (Path("/etc/vpp") / f"startup-{service_name.replace('vpp-', '')}.conf").unlink(missing_ok=True)
            (Path("/etc/vpp") / f"commands-{service_name.replace('vpp-', '')}.txt").unlink(missing_ok=True)

    # Fix FRR permissions
    subprocess.run(["chown", "-R", "frr:frr", "/etc/frr"], check=False)
    subprocess.run(["chmod", "640", "/etc/frr/frr.conf"], check=False)

    # Reload systemd
    subprocess.run(["systemctl", "daemon-reload"], check=True)

    # Enable module services for enabled modules
    for service_name in enabled_module_services:
        subprocess.run(["systemctl", "enable", service_name], check=False)

    if not quiet:
        log("Configuration applied")


def enable_services() -> None:
    """Enable all required services."""
    log("Enabling services...")

    # Core services
    services = [
        "systemd-networkd",
        "netns-dataplane",
        "netns-move-interfaces",
        "vpp-core",
        "vpp-core-config",
        "frr",
        "incus-init",
        "incus-dataplane",
    ]

    for service in services:
        subprocess.run(["systemctl", "enable", service], check=False)

    # Enable module services dynamically (find vpp-*.service in /etc/systemd/system)
    systemd_dir = Path("/etc/systemd/system")
    for service_file in systemd_dir.glob("vpp-*.service"):
        # Skip vpp-core* services (already enabled above)
        if service_file.name.startswith("vpp-core"):
            continue
        service_name = service_file.stem
        subprocess.run(["systemctl", "enable", service_name], check=False)

    log("Services enabled")



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

            if phase_confirm(config):
                render_templates(config, args.template_dir, GENERATED_DIR)
                apply_configs(GENERATED_DIR)
                enable_services()
                log("Configuration complete!")
            return

    # Interactive configuration (minimal wizard)
    detected_interfaces = phase1_detect_interfaces()
    mgmt_iface, dataplane_ifaces = phase2_select_interfaces(detected_interfaces)
    interfaces = phase3_interface_config(dataplane_ifaces)
    routes = phase4_route_config()
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

    # Build config object
    # Note: BGP, OSPF, NAT, etc. are configured via 'imp' REPL after initial setup
    config = RouterConfig(
        hostname=socket.gethostname(),
        management=management,
        interfaces=interfaces,
        routes=routes,
        container=container,
        cpu=CPUConfig.detect_and_allocate(),
        modules=[],  # Configure modules via 'imp' REPL
    )

    # Show CPU allocation
    info(f"CPU allocation ({config.cpu.total_cores} cores detected):")
    info(f"  VPP Core: main={config.cpu.core_main}, workers={config.cpu.core_workers or 'none'}")

    if not phase_confirm(config):
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
