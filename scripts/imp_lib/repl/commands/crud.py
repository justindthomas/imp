"""
CRUD operations for REPL.

This module contains create, read, update, delete operations for:
- Loopback interfaces
- BVI domains
- Sub-interfaces
- VLAN passthrough
"""

from typing import Optional

from imp_lib.common import Colors, log, warn, error

# Import from configure_router if available
try:
    from configure_router import (
        LoopbackInterface, BVIConfig, BridgeDomainMember,
        VLANPassthrough, SubInterface,
        validate_ipv4_cidr, validate_ipv6_cidr, parse_cidr
    )
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False


def prompt_value(prompt: str, validator=None, required: bool = True, default: str = None) -> Optional[str]:
    """Prompt for a value with optional validation."""
    while True:
        if default:
            value = input(f"  {prompt} [{default}]: ").strip()
            if not value:
                value = default
        else:
            value = input(f"  {prompt}: ").strip()

        if not value:
            if required:
                warn("Value is required")
                continue
            return None

        if validator and not validator(value):
            warn("Invalid format")
            continue

        return value


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    """Prompt for yes/no answer."""
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"  {prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        warn("Please answer yes or no")


# =============================================================================
# Loopback CRUD Operations
# =============================================================================

def cmd_loopback_add(ctx, args: list[str]) -> None:
    """Add a new loopback interface."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"{Colors.BOLD}Add Loopback Interface{Colors.NC}")
    print()

    # Find next available instance
    used_instances = {lo.instance for lo in ctx.config.loopbacks}
    instance = 0
    while instance in used_instances:
        instance += 1

    # Get name
    name = prompt_value(f"Name for loop{instance} (e.g., 'router-id', 'services')")
    if not name:
        return

    # Get IPv4
    ipv4_input = prompt_value("IPv4 Address [CIDR]", validate_ipv4_cidr, required=False)
    ipv4, ipv4_prefix = None, None
    if ipv4_input:
        ipv4, ipv4_prefix = parse_cidr(ipv4_input)

    # Get IPv6
    ipv6_input = prompt_value("IPv6 Address [CIDR]", validate_ipv6_cidr, required=False)
    ipv6, ipv6_prefix = None, None
    if ipv6_input:
        ipv6, ipv6_prefix = parse_cidr(ipv6_input)

    if not ipv4 and not ipv6:
        error("At least one IP address is required")
        return

    # LCP
    create_lcp = prompt_yes_no("Create linux_cp TAP for FRR visibility?", default=True)

    # Add to config
    ctx.config.loopbacks.append(LoopbackInterface(
        instance=instance,
        name=name,
        ipv4=ipv4,
        ipv4_prefix=ipv4_prefix,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        create_lcp=create_lcp
    ))

    ctx.dirty = True
    log(f"Added loopback: loop{instance} ({name})")


def cmd_loopback_delete(ctx, args: list[str]) -> None:
    """Delete a loopback interface."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.loopbacks:
        error("No loopbacks configured")
        return

    if not args:
        # Show available loopbacks
        available = ", ".join(f"loop{lo.instance}" for lo in ctx.config.loopbacks)
        error(f"Usage: delete <name>  (available: {available})")
        return

    # Accept "loop0" or just "0"
    arg = args[0]
    if arg.startswith("loop"):
        try:
            instance = int(arg[4:])
        except ValueError:
            error(f"Invalid loopback name: {arg}")
            return
    else:
        try:
            instance = int(arg)
        except ValueError:
            error(f"Invalid loopback: {arg} (use 'loop0' or '0')")
            return

    lo = next((l for l in ctx.config.loopbacks if l.instance == instance), None)
    if not lo:
        available = ", ".join(f"loop{lo.instance}" for lo in ctx.config.loopbacks)
        error(f"Loopback loop{instance} not found (available: {available})")
        return

    if prompt_yes_no(f"Delete loop{instance} ({lo.name})?"):
        ctx.config.loopbacks.remove(lo)
        ctx.dirty = True
        log(f"Deleted loopback: loop{instance}")


def cmd_loopback_edit(ctx, args: list[str]) -> None:
    """Edit an existing loopback interface."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.loopbacks:
        error("No loopbacks configured")
        return

    if not args:
        # Show available loopbacks
        available = ", ".join(f"loop{lo.instance}" for lo in ctx.config.loopbacks)
        error(f"Usage: edit <instance>  (available: {available})")
        return

    # Accept "loop0" or just "0"
    arg = args[0]
    if arg.startswith("loop"):
        try:
            instance = int(arg[4:])
        except ValueError:
            error(f"Invalid loopback name: {arg}")
            return
    else:
        try:
            instance = int(arg)
        except ValueError:
            error(f"Invalid loopback: {arg} (use 'loop0' or '0')")
            return

    lo = next((l for l in ctx.config.loopbacks if l.instance == instance), None)
    if not lo:
        available = ", ".join(f"loop{lo.instance}" for lo in ctx.config.loopbacks)
        error(f"Loopback loop{instance} not found (available: {available})")
        return

    print()
    print(f"{Colors.BOLD}Edit Loopback: loop{instance}{Colors.NC}")
    print()

    # Show current values
    current_ipv4 = f"{lo.ipv4}/{lo.ipv4_prefix}" if lo.ipv4 else "(none)"
    current_ipv6 = f"{lo.ipv6}/{lo.ipv6_prefix}" if lo.ipv6 else "(none)"
    print(f"  Current name: {lo.name}")
    print(f"  Current IPv4: {current_ipv4}")
    print(f"  Current IPv6: {current_ipv6}")
    print()

    changed = False

    # Edit name
    new_name = input(f"Name [{lo.name}]: ").strip()
    if new_name and new_name != lo.name:
        lo.name = new_name
        changed = True

    # Edit IPv4
    current_ipv4_display = f"{lo.ipv4}/{lo.ipv4_prefix}" if lo.ipv4 else ""
    new_ipv4 = input(f"IPv4 CIDR [{current_ipv4_display}]: ").strip()
    if new_ipv4:
        if new_ipv4.lower() == "none" or new_ipv4 == "-":
            if lo.ipv4:
                lo.ipv4 = None
                lo.ipv4_prefix = None
                changed = True
        elif validate_ipv4_cidr(new_ipv4):
            new_ip, new_prefix = parse_cidr(new_ipv4)
            if new_ip != lo.ipv4 or new_prefix != lo.ipv4_prefix:
                lo.ipv4 = new_ip
                lo.ipv4_prefix = new_prefix
                changed = True
        else:
            warn(f"Invalid IPv4 CIDR: {new_ipv4}, keeping current value")

    # Edit IPv6
    current_ipv6_display = f"{lo.ipv6}/{lo.ipv6_prefix}" if lo.ipv6 else ""
    new_ipv6 = input(f"IPv6 CIDR [{current_ipv6_display}]: ").strip()
    if new_ipv6:
        if new_ipv6.lower() == "none" or new_ipv6 == "-":
            if lo.ipv6:
                lo.ipv6 = None
                lo.ipv6_prefix = None
                changed = True
        elif validate_ipv6_cidr(new_ipv6):
            new_ip, new_prefix = parse_cidr(new_ipv6)
            if new_ip != lo.ipv6 or new_prefix != lo.ipv6_prefix:
                lo.ipv6 = new_ip
                lo.ipv6_prefix = new_prefix
                changed = True
        else:
            warn(f"Invalid IPv6 CIDR: {new_ipv6}, keeping current value")

    if changed:
        ctx.dirty = True
        log(f"Updated loopback: loop{instance}")
    else:
        print("No changes made")


# =============================================================================
# BVI CRUD Operations
# =============================================================================

def cmd_bvi_add(ctx, args: list[str]) -> None:
    """Add a new BVI domain."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"{Colors.BOLD}Add BVI Domain{Colors.NC}")
    print()

    # Find next available bridge ID (start at 100 to avoid loopback conflicts)
    used_ids = {bvi.bridge_id for bvi in ctx.config.bvi_domains}
    bridge_id = 100
    while bridge_id in used_ids:
        bridge_id += 1

    # Get name
    name = prompt_value(f"Name for BVI {bridge_id} (e.g., 'customer-vlan', 'mgmt-bridge')")
    if not name:
        return

    # Get member interfaces
    print()
    print("  Enter member interfaces (format: 'interface' or 'interface.vlan')")
    print("  Available: external, internal0, internal1, ...")
    print("  Examples: 'external.100' or 'internal0.200'")
    print("  Enter blank line when done")
    print()

    members = []
    while True:
        member_input = input("  Member: ").strip()
        if not member_input:
            break

        # Parse interface.vlan format
        if '.' in member_input:
            iface, vlan_str = member_input.rsplit('.', 1)
            try:
                vlan_id = int(vlan_str)
            except ValueError:
                warn("Invalid VLAN ID")
                continue
            members.append(BridgeDomainMember(interface=iface, vlan_id=vlan_id))
        else:
            members.append(BridgeDomainMember(interface=member_input, vlan_id=None))

        log(f"Added member: {member_input}")

    if not members:
        error("At least one member interface is required")
        return

    # Get IPv4
    ipv4_input = prompt_value("BVI IPv4 Address [CIDR]", validate_ipv4_cidr, required=False)
    ipv4, ipv4_prefix = None, None
    if ipv4_input:
        ipv4, ipv4_prefix = parse_cidr(ipv4_input)

    # Get IPv6
    ipv6_input = prompt_value("BVI IPv6 Address [CIDR]", validate_ipv6_cidr, required=False)
    ipv6, ipv6_prefix = None, None
    if ipv6_input:
        ipv6, ipv6_prefix = parse_cidr(ipv6_input)

    if not ipv4 and not ipv6:
        error("At least one IP address is required")
        return

    # LCP
    create_lcp = prompt_yes_no("Create linux_cp TAP for FRR visibility?", default=True)

    # Add to config
    ctx.config.bvi_domains.append(BVIConfig(
        bridge_id=bridge_id,
        name=name,
        members=members,
        ipv4=ipv4,
        ipv4_prefix=ipv4_prefix,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        create_lcp=create_lcp
    ))

    ctx.dirty = True
    log(f"Added BVI domain: loop{bridge_id} ({name}) with {len(members)} members")


def cmd_bvi_delete(ctx, args: list[str]) -> None:
    """Delete a BVI domain."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.bvi_domains:
        error("No BVI domains configured")
        return

    if not args:
        # Show available BVIs
        available = ", ".join(f"bvi{b.bridge_id}" for b in ctx.config.bvi_domains)
        error(f"Usage: delete <name>  (available: {available})")
        return

    # Accept "bvi100" or just "100"
    arg = args[0]
    if arg.startswith("bvi"):
        try:
            bridge_id = int(arg[3:])
        except ValueError:
            error(f"Invalid BVI name: {arg}")
            return
    else:
        try:
            bridge_id = int(arg)
        except ValueError:
            error(f"Invalid BVI: {arg} (use 'bvi100' or '100')")
            return

    bvi = next((b for b in ctx.config.bvi_domains if b.bridge_id == bridge_id), None)
    if not bvi:
        available = ", ".join(f"bvi{b.bridge_id}" for b in ctx.config.bvi_domains)
        error(f"BVI bvi{bridge_id} not found (available: {available})")
        return

    if prompt_yes_no(f"Delete bvi{bridge_id} ({bvi.name})?"):
        ctx.config.bvi_domains.remove(bvi)
        ctx.dirty = True
        log(f"Deleted BVI domain: bvi{bridge_id}")


# =============================================================================
# VLAN Passthrough CRUD Operations
# =============================================================================

def cmd_vlan_passthrough_add(ctx, args: list[str]) -> None:
    """Add a new VLAN passthrough."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"{Colors.BOLD}Add VLAN Passthrough{Colors.NC}")
    print()
    print("  VLAN passthrough creates L2 xconnect between external and internal VLANs")
    print()

    # Get VLAN ID
    vlan_str = prompt_value("External VLAN ID (1-4094)")
    if not vlan_str:
        return
    try:
        vlan_id = int(vlan_str)
        if vlan_id < 1 or vlan_id > 4094:
            raise ValueError()
    except ValueError:
        error("Invalid VLAN ID (must be 1-4094)")
        return

    # Check for duplicate
    if any(v.vlan_id == vlan_id and not v.inner_vlan for v in ctx.config.vlan_passthrough):
        error(f"VLAN {vlan_id} passthrough already exists")
        return

    # VLAN type
    print()
    print("  VLAN encapsulation types:")
    print("    1. dot1q  - Standard 802.1Q (most common)")
    print("    2. dot1ad - Provider bridge (QinQ outer tag)")
    print()
    type_choice = prompt_value("Type [1/2]", default="1")
    vlan_type = "dot1ad" if type_choice == "2" else "dot1q"

    # Inner VLAN (for QinQ)
    inner_vlan = None
    if vlan_type == "dot1ad":
        inner_str = prompt_value("Inner VLAN ID (for QinQ, or blank for trunk)", required=False)
        if inner_str:
            try:
                inner_vlan = int(inner_str)
            except ValueError:
                error("Invalid inner VLAN ID")
                return

    # Interface selection
    if ctx.config.interfaces:
        print()
        print("  Available interfaces:")
        for iface in ctx.config.interfaces:
            print(f"    - {iface.name}")
        print()

    from_iface = prompt_value("Source interface (e.g., wan)")
    if not from_iface:
        return

    to_iface = prompt_value("Destination interface (e.g., lan)")
    if not to_iface:
        return

    # Add to config
    ctx.config.vlan_passthrough.append(VLANPassthrough(
        vlan_id=vlan_id,
        vlan_type=vlan_type,
        inner_vlan=inner_vlan,
        from_interface=from_iface,
        to_interface=to_iface
    ))

    ctx.dirty = True
    if inner_vlan:
        log(f"Added VLAN passthrough: {vlan_id}.{inner_vlan} ({vlan_type}) {from_iface} <-> {to_iface}")
    else:
        log(f"Added VLAN passthrough: {vlan_id} ({vlan_type}) {from_iface} <-> {to_iface}")


def cmd_vlan_passthrough_delete(ctx, args: list[str]) -> None:
    """Delete a VLAN passthrough."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not args:
        error("Usage: delete <vlan_id>")
        return

    try:
        vlan_id = int(args[0])
    except ValueError:
        error("VLAN ID must be a number")
        return

    vlan = next((v for v in ctx.config.vlan_passthrough if v.vlan_id == vlan_id), None)
    if not vlan:
        error(f"VLAN passthrough {vlan_id} not found")
        return

    if prompt_yes_no(f"Delete VLAN passthrough {vlan_id}?"):
        ctx.config.vlan_passthrough.remove(vlan)
        ctx.dirty = True
        log(f"Deleted VLAN passthrough: {vlan_id}")


# =============================================================================
# Sub-interface CRUD Operations
# =============================================================================

def _get_parent_interface(ctx):
    """Get the parent interface for sub-interface operations based on current path."""
    # Strip config prefix for path matching
    path = ctx.path[1:] if ctx.path and ctx.path[0] == "config" else ctx.path

    if path[:2] == ["interfaces", "external"] and "subinterfaces" in path:
        return ctx.config.external, "external"

    if len(path) >= 3 and path[0] == "interfaces" and path[1] == "internal":
        iface_name = path[2]
        for iface in ctx.config.internal:
            if iface.vpp_name == iface_name:
                if len(path) >= 4 and path[3] == "subinterfaces":
                    return iface, iface.vpp_name

    return None, None


def cmd_subinterface_add(ctx, args: list[str]) -> None:
    """Add a new sub-interface."""
    if not ctx.config:
        error("No configuration loaded")
        return

    parent, parent_name = _get_parent_interface(ctx)
    if not parent:
        error("Navigate to an interface's subinterfaces menu first")
        return

    print()
    print(f"{Colors.BOLD}Add Sub-interface on {parent_name}{Colors.NC}")
    print()

    # Get VLAN ID
    vlan_str = prompt_value("VLAN ID (1-4094)")
    if not vlan_str:
        return
    try:
        vlan_id = int(vlan_str)
        if vlan_id < 1 or vlan_id > 4094:
            raise ValueError()
    except ValueError:
        error("Invalid VLAN ID (must be 1-4094)")
        return

    # Check for duplicate
    if any(s.vlan_id == vlan_id for s in parent.subinterfaces):
        error(f"Sub-interface .{vlan_id} already exists on {parent_name}")
        return

    # Get IPv4
    ipv4_input = prompt_value("IPv4 Address [CIDR]", validate_ipv4_cidr, required=False)
    ipv4, ipv4_prefix = None, None
    if ipv4_input:
        ipv4, ipv4_prefix = parse_cidr(ipv4_input)

    # Get IPv6
    ipv6_input = prompt_value("IPv6 Address [CIDR]", validate_ipv6_cidr, required=False)
    ipv6, ipv6_prefix = None, None
    if ipv6_input:
        ipv6, ipv6_prefix = parse_cidr(ipv6_input)

    if not ipv4 and not ipv6:
        error("At least one IP address is required")
        return

    # LCP
    create_lcp = prompt_yes_no("Create linux_cp TAP for FRR visibility?", default=True)

    # Add to parent
    parent.subinterfaces.append(SubInterface(
        vlan_id=vlan_id,
        ipv4=ipv4,
        ipv4_prefix=ipv4_prefix,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        create_lcp=create_lcp
    ))

    ctx.dirty = True
    log(f"Added sub-interface: {parent_name}.{vlan_id}")


def cmd_subinterface_delete(ctx, args: list[str]) -> None:
    """Delete a sub-interface."""
    if not ctx.config:
        error("No configuration loaded")
        return

    parent, parent_name = _get_parent_interface(ctx)
    if not parent:
        error("Navigate to an interface's subinterfaces menu first")
        return

    if not args:
        error("Usage: delete <vlan_id>")
        return

    try:
        vlan_id = int(args[0])
    except ValueError:
        error("VLAN ID must be a number")
        return

    sub = next((s for s in parent.subinterfaces if s.vlan_id == vlan_id), None)
    if not sub:
        error(f"Sub-interface .{vlan_id} not found on {parent_name}")
        return

    if prompt_yes_no(f"Delete {parent_name}.{vlan_id}?"):
        parent.subinterfaces.remove(sub)
        ctx.dirty = True
        log(f"Deleted sub-interface: {parent_name}.{vlan_id}")
