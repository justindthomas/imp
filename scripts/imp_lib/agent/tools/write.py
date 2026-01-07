"""
Write tool implementations for IMP agent.

This module contains tools that modify configuration data.
"""

import ipaddress


# =============================================================================
# Helper Functions
# =============================================================================

def _get_config_classes():
    """Get config classes from imp_lib.config."""
    from imp_lib.config import (
        SubInterface, LoopbackInterface, Route,
        VLANPassthrough, BGPPeer, validate_ipv4_cidr, validate_ipv6_cidr, parse_cidr
    )
    return {
        'SubInterface': SubInterface,
        'LoopbackInterface': LoopbackInterface,
        'Route': Route,
        'VLANPassthrough': VLANPassthrough,
        'BGPPeer': BGPPeer,
        'validate_ipv4_cidr': validate_ipv4_cidr,
        'validate_ipv6_cidr': validate_ipv6_cidr,
        'parse_cidr': parse_cidr,
    }


def _get_parent_interface(config, interface: str):
    """Get the parent interface object for subinterface operations."""
    for iface in config.interfaces:
        if iface.name == interface:
            return iface, iface.name

    return None, None


def _find_interface_for_ospf(config, interface: str):
    """Find an interface by name for OSPF configuration.

    Returns (interface_obj, interface_type) where interface_type is one of:
    'dataplane', 'loopback', 'bvi'
    """
    # Check loopbacks: loop0, loop1, etc.
    if interface.startswith("loop"):
        try:
            instance = int(interface[4:])
            for loop in config.loopbacks:
                if loop.instance == instance:
                    return loop, "loopback"
        except ValueError:
            pass

    # Check BVIs: bvi1, bvi2, etc.
    if interface.startswith("bvi"):
        try:
            bridge_id = int(interface[3:])
            for bvi in config.bvi_domains:
                if bvi.bridge_id == bridge_id:
                    return bvi, "bvi"
        except ValueError:
            pass

    # Check dataplane interfaces by name
    for iface in config.interfaces:
        if iface.name == interface:
            return iface, "dataplane"

    return None, None


# =============================================================================
# Write Tool Implementations
# =============================================================================

def tool_add_subinterface(config, ctx, interface: str, vlan_id: int,
                          ipv4_cidr: str = None, ipv6_cidr: str = None,
                          create_lcp: bool = True) -> str:
    """Add a sub-interface."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    parent, parent_name = _get_parent_interface(config, interface)
    if not parent:
        available = [i.name for i in config.interfaces]
        return f"Interface '{interface}' not found. Available: {', '.join(available)}"

    # Validate VLAN ID
    if vlan_id < 1 or vlan_id > 4094:
        return "Error: VLAN ID must be between 1 and 4094"

    # Check for duplicate
    if any(s.vlan_id == vlan_id for s in parent.subinterfaces):
        return f"Error: Sub-interface .{vlan_id} already exists on {parent_name}"

    # Validate and parse IPs
    ipv4, ipv4_prefix = None, None
    ipv6, ipv6_prefix = None, None

    if ipv4_cidr:
        if not classes['validate_ipv4_cidr'](ipv4_cidr):
            return f"Error: Invalid IPv4 CIDR: {ipv4_cidr}"
        ipv4, ipv4_prefix = classes['parse_cidr'](ipv4_cidr)

    if ipv6_cidr:
        if not classes['validate_ipv6_cidr'](ipv6_cidr):
            return f"Error: Invalid IPv6 CIDR: {ipv6_cidr}"
        ipv6, ipv6_prefix = classes['parse_cidr'](ipv6_cidr)

    if not ipv4 and not ipv6:
        return "Error: At least one of ipv4_cidr or ipv6_cidr is required"

    # Create and add sub-interface
    subif = classes['SubInterface'](
        vlan_id=vlan_id,
        ipv4=ipv4,
        ipv4_prefix=ipv4_prefix,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        create_lcp=create_lcp
    )
    parent.subinterfaces.append(subif)
    ctx.dirty = True

    ips = []
    if ipv4:
        ips.append(f"{ipv4}/{ipv4_prefix}")
    if ipv6:
        ips.append(f"{ipv6}/{ipv6_prefix}")

    return f"Added {parent_name}.{vlan_id} with {', '.join(ips)}"


def tool_delete_subinterface(config, ctx, interface: str, vlan_id: int) -> str:
    """Delete a sub-interface."""
    parent, parent_name = _get_parent_interface(config, interface)
    if not parent:
        available = [i.name for i in config.interfaces]
        return f"Interface '{interface}' not found. Available: {', '.join(available)}"

    sub = next((s for s in parent.subinterfaces if s.vlan_id == vlan_id), None)
    if not sub:
        return f"Sub-interface .{vlan_id} not found on {parent_name}"

    parent.subinterfaces.remove(sub)
    ctx.dirty = True
    return f"Deleted {parent_name}.{vlan_id}"


def tool_add_loopback(config, ctx, name: str, ipv4_cidr: str = None,
                      ipv6_cidr: str = None, create_lcp: bool = True) -> str:
    """Add a loopback interface."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    # Find next available instance
    used_instances = {lo.instance for lo in config.loopbacks}
    instance = 0
    while instance in used_instances:
        instance += 1

    # Validate and parse IPs
    ipv4, ipv4_prefix = None, None
    ipv6, ipv6_prefix = None, None

    if ipv4_cidr:
        if not classes['validate_ipv4_cidr'](ipv4_cidr):
            return f"Error: Invalid IPv4 CIDR: {ipv4_cidr}"
        ipv4, ipv4_prefix = classes['parse_cidr'](ipv4_cidr)

    if ipv6_cidr:
        if not classes['validate_ipv6_cidr'](ipv6_cidr):
            return f"Error: Invalid IPv6 CIDR: {ipv6_cidr}"
        ipv6, ipv6_prefix = classes['parse_cidr'](ipv6_cidr)

    if not ipv4 and not ipv6:
        return "Error: At least one of ipv4_cidr or ipv6_cidr is required"

    # Create and add loopback
    loopback = classes['LoopbackInterface'](
        instance=instance,
        name=name,
        ipv4=ipv4,
        ipv4_prefix=ipv4_prefix,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        create_lcp=create_lcp
    )
    config.loopbacks.append(loopback)
    ctx.dirty = True

    ips = []
    if ipv4:
        ips.append(f"{ipv4}/{ipv4_prefix}")
    if ipv6:
        ips.append(f"{ipv6}/{ipv6_prefix}")

    return f"Added loop{instance} ({name}) with {', '.join(ips)}"


def tool_delete_loopback(config, ctx, name: str) -> str:
    """Delete a loopback interface."""
    # Parse name - accept "loop0" or "0"
    if name.startswith("loop"):
        try:
            instance = int(name[4:])
        except ValueError:
            return f"Error: Invalid loopback name: {name}"
    else:
        try:
            instance = int(name)
        except ValueError:
            return f"Error: Invalid loopback: {name} (use 'loop0' or '0')"

    lo = next((l for l in config.loopbacks if l.instance == instance), None)
    if not lo:
        available = ", ".join(f"loop{l.instance}" for l in config.loopbacks)
        return f"Loopback loop{instance} not found (available: {available})"

    config.loopbacks.remove(lo)
    ctx.dirty = True
    return f"Deleted loop{instance} ({lo.name})"


def tool_add_vlan_passthrough(config, ctx, vlan_id: int, from_interface: str,
                               to_interface: str, vlan_type: str = "dot1q") -> str:
    """Add a VLAN passthrough rule."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    # Validate VLAN ID
    if vlan_id < 1 or vlan_id > 4094:
        return "Error: VLAN ID must be between 1 and 4094"

    available = [i.name for i in config.interfaces]

    # Check interfaces exist
    if not any(i.name == from_interface for i in config.interfaces):
        return f"Interface '{from_interface}' not found. Available: {', '.join(available)}"
    if not any(i.name == to_interface for i in config.interfaces):
        return f"Interface '{to_interface}' not found. Available: {', '.join(available)}"

    # Check for duplicate
    if any(v.vlan_id == vlan_id for v in config.vlan_passthrough):
        return f"Error: VLAN passthrough {vlan_id} already exists"

    config.vlan_passthrough.append(classes['VLANPassthrough'](
        vlan_id=vlan_id,
        from_interface=from_interface,
        to_interface=to_interface,
        vlan_type=vlan_type
    ))
    ctx.dirty = True
    return f"Added VLAN passthrough: {vlan_id} ({vlan_type}) {from_interface} <-> {to_interface}"


def tool_delete_vlan_passthrough(config, ctx, vlan_id: int) -> str:
    """Delete a VLAN passthrough rule."""
    vlan = next((v for v in config.vlan_passthrough if v.vlan_id == vlan_id), None)
    if not vlan:
        return f"VLAN passthrough {vlan_id} not found"

    config.vlan_passthrough.remove(vlan)
    ctx.dirty = True
    return f"Deleted VLAN passthrough {vlan_id}"


def tool_add_route(config, ctx, destination: str, via: str, interface: str = None) -> str:
    """Add a static route."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    # Validate destination
    try:
        ipaddress.ip_network(destination, strict=False)
    except Exception as e:
        return f"Error: Invalid destination: {e}"

    # Validate next-hop
    try:
        ipaddress.ip_address(via)
    except Exception as e:
        return f"Error: Invalid next-hop address: {e}"

    # Check interface exists if specified
    if interface:
        if not any(i.name == interface for i in config.interfaces):
            available = [i.name for i in config.interfaces]
            return f"Interface '{interface}' not found. Available: {', '.join(available)}"

    # Check for duplicate
    for route in config.routes:
        if route.destination == destination:
            return f"Error: Route to {destination} already exists (via {route.via})"

    config.routes.append(classes['Route'](
        destination=destination,
        via=via,
        interface=interface
    ))
    ctx.dirty = True

    via_iface = f" via {interface}" if interface else ""
    return f"Added route: {destination} via {via}{via_iface}"


def tool_delete_route(config, ctx, destination: str) -> str:
    """Delete a static route."""
    route = next((r for r in config.routes if r.destination == destination), None)
    if not route:
        return f"Route to {destination} not found"

    config.routes.remove(route)
    ctx.dirty = True
    return f"Deleted route to {destination}"


def tool_configure_bgp(config, ctx, asn: int, router_id: str) -> str:
    """Configure BGP ASN and router-id without touching peers."""
    try:
        ipaddress.IPv4Address(router_id)
    except Exception as e:
        return f"Error: Invalid router ID: {e}"

    config.bgp.enabled = True
    config.bgp.asn = asn
    config.bgp.router_id = router_id
    ctx.dirty = True

    return f"Configured BGP: AS {asn}, router-id {router_id}"


def tool_add_bgp_peer(config, ctx, name: str, peer_ip: str, peer_asn: int,
                      description: str = None) -> str:
    """Add a BGP peer."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    if not config.bgp.enabled:
        return "Error: BGP is not enabled. Use configure_bgp first."

    # Validate IP address
    try:
        # Try IPv4 first, then IPv6
        try:
            ipaddress.IPv4Address(peer_ip)
            af = "IPv4"
        except ipaddress.AddressValueError:
            ipaddress.IPv6Address(peer_ip)
            af = "IPv6"
    except Exception as e:
        return f"Error: Invalid peer IP address: {e}"

    # Check for duplicate
    for p in config.bgp.peers:
        if p.peer_ip == peer_ip:
            return f"Error: Peer {peer_ip} already exists"

    BGPPeer = classes['BGPPeer']
    peer = BGPPeer(
        name=name,
        peer_ip=peer_ip,
        peer_asn=peer_asn,
        description=description or name
    )
    config.bgp.peers.append(peer)
    ctx.dirty = True

    return f"Added {af} BGP peer: {name} ({peer_ip}) AS {peer_asn}"


def tool_remove_bgp_peer(config, ctx, peer_ip: str) -> str:
    """Remove a BGP peer by IP address."""
    if not config.bgp.enabled:
        return "Error: BGP is not enabled"

    for p in config.bgp.peers:
        if p.peer_ip == peer_ip:
            config.bgp.peers.remove(p)
            ctx.dirty = True
            return f"Removed BGP peer {peer_ip}"

    return f"Error: Peer {peer_ip} not found"


def tool_disable_bgp(config, ctx) -> str:
    """Disable BGP and remove all peers."""
    if not config.bgp.enabled:
        return "BGP is already disabled"

    config.bgp.enabled = False
    config.bgp.peers = []  # Clear all peers
    ctx.dirty = True
    return "Disabled BGP and removed all peers"


def tool_enable_ospf(config, ctx, router_id: str = None, default_originate: bool = False) -> str:
    """Enable OSPF."""
    if config.ospf.enabled:
        return "OSPF is already enabled"

    # Use BGP router-id as fallback if not provided
    if not router_id:
        router_id = config.bgp.router_id if config.bgp.enabled else None

    if not router_id:
        return "Error: router_id is required (no BGP router-id available as fallback)"

    # Validate router_id
    try:
        ipaddress.IPv4Address(router_id)
    except Exception as e:
        return f"Error: Invalid router ID: {e}"

    config.ospf.enabled = True
    config.ospf.router_id = router_id
    config.ospf.default_originate = default_originate
    ctx.dirty = True

    return f"Enabled OSPF with router-id {router_id}"


def tool_disable_ospf(config, ctx) -> str:
    """Disable OSPF."""
    if not config.ospf.enabled:
        return "OSPF is already disabled"

    config.ospf.enabled = False
    ctx.dirty = True
    return "Disabled OSPF"


def tool_enable_ospf6(config, ctx, router_id: str = None, default_originate: bool = False) -> str:
    """Enable OSPFv3."""
    if config.ospf6.enabled:
        return "OSPFv3 is already enabled"

    # Use OSPF or BGP router-id as fallback if not provided
    if not router_id:
        router_id = config.ospf.router_id or (config.bgp.router_id if config.bgp.enabled else None)

    if not router_id:
        return "Error: router_id is required (no OSPF/BGP router-id available as fallback)"

    # Validate router_id
    try:
        ipaddress.IPv4Address(router_id)
    except Exception as e:
        return f"Error: Invalid router ID: {e}"

    config.ospf6.enabled = True
    config.ospf6.router_id = router_id
    config.ospf6.default_originate = default_originate
    ctx.dirty = True

    return f"Enabled OSPFv3 with router-id {router_id}"


def tool_disable_ospf6(config, ctx) -> str:
    """Disable OSPFv3."""
    if not config.ospf6.enabled:
        return "OSPFv3 is already disabled"

    config.ospf6.enabled = False
    ctx.dirty = True
    return "Disabled OSPFv3"


def tool_set_interface_ospf(config, ctx, interface: str, area: int, passive: bool = False) -> str:
    """Set OSPF area for an interface."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    iface.ospf_area = area
    iface.ospf_passive = passive
    ctx.dirty = True

    passive_str = " (passive)" if passive else ""
    return f"Set {interface} OSPF area to {area}{passive_str}"


def tool_set_interface_ospf6(config, ctx, interface: str, area: int, passive: bool = False) -> str:
    """Set OSPFv3 area for an interface."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    iface.ospf6_area = area
    iface.ospf6_passive = passive
    ctx.dirty = True

    passive_str = " (passive)" if passive else ""
    return f"Set {interface} OSPFv3 area to {area}{passive_str}"


def tool_clear_interface_ospf(config, ctx, interface: str) -> str:
    """Remove interface from OSPF."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    if iface.ospf_area is None:
        return f"Interface '{interface}' is not in OSPF"

    iface.ospf_area = None
    iface.ospf_passive = False
    ctx.dirty = True

    return f"Removed {interface} from OSPF"


def tool_clear_interface_ospf6(config, ctx, interface: str) -> str:
    """Remove interface from OSPFv3."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    if iface.ospf6_area is None:
        return f"Interface '{interface}' is not in OSPFv3"

    iface.ospf6_area = None
    iface.ospf6_passive = False
    ctx.dirty = True

    return f"Removed {interface} from OSPFv3"
