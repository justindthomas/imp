"""
Routing protocol commands for REPL.

This module contains commands for configuring BGP and OSPF routing protocols.
"""

from imp_lib.common import Colors, log, warn, error, info
from .crud import prompt_value, prompt_yes_no

from imp_lib.config import validate_ipv4, validate_ipv6, BGPPeer


# =============================================================================
# BGP Configuration Operations
# =============================================================================

def cmd_bgp_enable(ctx, args: list[str]) -> None:
    """Enable and configure BGP."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if ctx.config.bgp.enabled:
        warn("BGP is already enabled")
        return

    print()
    print(f"{Colors.BOLD}Enable BGP{Colors.NC}")
    print()

    # Local ASN
    asn_str = prompt_value("Local AS number")
    if not asn_str:
        return
    try:
        asn = int(asn_str)
    except ValueError:
        error("Invalid AS number")
        return

    # Router ID
    router_id = prompt_value("Router ID (IPv4 address)", validate_ipv4)
    if not router_id:
        return

    # Update config
    ctx.config.bgp.enabled = True
    ctx.config.bgp.asn = asn
    ctx.config.bgp.router_id = router_id

    ctx.dirty = True
    log(f"Enabled BGP: AS {asn}")
    print()
    info("Use 'routing bgp peers add' to add BGP peers")


def cmd_bgp_disable(ctx, args: list[str]) -> None:
    """Disable BGP."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.bgp.enabled:
        warn("BGP is already disabled")
        return

    if prompt_yes_no("Disable BGP? This will remove the BGP configuration"):
        ctx.config.bgp.enabled = False
        ctx.config.bgp.peers = []  # Clear peers when disabling
        ctx.dirty = True
        log("BGP disabled")


def cmd_bgp_peers_list(ctx, args: list[str]) -> None:
    """List all BGP peers."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.bgp.enabled:
        warn("BGP is not enabled")
        return

    print()
    print(f"{Colors.BOLD}BGP Peers ({len(ctx.config.bgp.peers)}){Colors.NC}")
    print("=" * 50)

    if not ctx.config.bgp.peers:
        print("  (no peers configured)")
    else:
        for peer in ctx.config.bgp.peers:
            af = "IPv6" if ':' in peer.peer_ip else "IPv4"
            print(f"  {peer.name}: {peer.peer_ip} AS {peer.peer_asn} ({af})")
    print()


def cmd_bgp_peers_add(ctx, args: list[str]) -> None:
    """Add a BGP peer."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.bgp.enabled:
        error("BGP is not enabled. Use 'routing bgp enable' first")
        return

    print()
    print(f"{Colors.BOLD}Add BGP Peer{Colors.NC}")
    print()

    # Peer name
    name = prompt_value("Peer name (e.g., upstream, ix-peer)")
    if not name:
        return

    # Peer IP (IPv4 or IPv6)
    def validate_ip(ip):
        return validate_ipv4(ip) or validate_ipv6(ip)

    peer_ip = prompt_value("Peer IP address (IPv4 or IPv6)", validate_ip)
    if not peer_ip:
        return

    # Check for duplicate
    for p in ctx.config.bgp.peers:
        if p.peer_ip == peer_ip:
            error(f"Peer {peer_ip} already exists")
            return

    # Peer ASN
    peer_asn_str = prompt_value("Peer AS number")
    if not peer_asn_str:
        return
    try:
        peer_asn = int(peer_asn_str)
    except ValueError:
        error("Invalid AS number")
        return

    # Description (optional, defaults to name)
    description = prompt_value("Description", required=False) or name

    # Create peer
    peer = BGPPeer(
        name=name,
        peer_ip=peer_ip,
        peer_asn=peer_asn,
        description=description
    )
    ctx.config.bgp.peers.append(peer)
    ctx.dirty = True

    af = "IPv6" if ':' in peer_ip else "IPv4"
    log(f"Added {af} BGP peer: {name} ({peer_ip}) AS {peer_asn}")


def cmd_bgp_peers_remove(ctx, args: list[str]) -> None:
    """Remove a BGP peer."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.bgp.enabled:
        warn("BGP is not enabled")
        return

    if not ctx.config.bgp.peers:
        warn("No peers configured")
        return

    # Get peer IP from args or prompt
    if args:
        peer_ip = args[0]
    else:
        print()
        print("Current peers:")
        for p in ctx.config.bgp.peers:
            print(f"  {p.name}: {p.peer_ip}")
        print()
        peer_ip = prompt_value("Peer IP to remove")
        if not peer_ip:
            return

    # Find and remove peer
    for p in ctx.config.bgp.peers:
        if p.peer_ip == peer_ip:
            if prompt_yes_no(f"Remove peer {p.name} ({peer_ip})?"):
                ctx.config.bgp.peers.remove(p)
                ctx.dirty = True
                log(f"Removed BGP peer {peer_ip}")
            return

    error(f"Peer {peer_ip} not found")


# =============================================================================
# OSPF Configuration Operations
# =============================================================================

def cmd_ospf_enable(ctx, args: list[str]) -> None:
    """Enable and configure OSPF."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if ctx.config.ospf.enabled:
        warn("OSPF is already enabled")
        return

    print()
    print(f"{Colors.BOLD}Enable OSPF{Colors.NC}")
    print()

    # Router ID - default to BGP router-id if available
    default_id = ctx.config.bgp.router_id if ctx.config.bgp.enabled else None
    if default_id:
        print(f"  Router ID [{default_id}]: ", end="")
        router_id = input().strip() or default_id
    else:
        router_id = prompt_value("Router ID (IPv4 address)", validate_ipv4)
        if not router_id:
            return

    if not validate_ipv4(router_id):
        error("Invalid IPv4 address")
        return

    # Default originate
    default_originate = prompt_yes_no("Inject default route (default-information originate)?", default=False)

    # Update config
    ctx.config.ospf.enabled = True
    ctx.config.ospf.router_id = router_id
    ctx.config.ospf.default_originate = default_originate

    ctx.dirty = True
    log(f"Enabled OSPF with router-id {router_id}")
    print()
    info("Use 'interfaces <name> ospf area <n>' to add interfaces to OSPF")


def cmd_ospf_disable(ctx, args: list[str]) -> None:
    """Disable OSPF."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.ospf.enabled:
        warn("OSPF is already disabled")
        return

    if prompt_yes_no("Disable OSPF? This will remove the OSPF configuration"):
        ctx.config.ospf.enabled = False
        ctx.dirty = True
        log("OSPF disabled")


def cmd_ospf6_enable(ctx, args: list[str]) -> None:
    """Enable and configure OSPFv3."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if ctx.config.ospf6.enabled:
        warn("OSPFv3 is already enabled")
        return

    print()
    print(f"{Colors.BOLD}Enable OSPFv3{Colors.NC}")
    print()

    # Router ID - default to OSPF or BGP router-id if available
    default_id = ctx.config.ospf.router_id or ctx.config.bgp.router_id
    if default_id:
        print(f"  Router ID [{default_id}]: ", end="")
        router_id = input().strip() or default_id
    else:
        router_id = prompt_value("Router ID (IPv4 address)", validate_ipv4)
        if not router_id:
            return

    if not validate_ipv4(router_id):
        error("Invalid IPv4 address")
        return

    # Default originate
    default_originate = prompt_yes_no("Inject default route (default-information originate)?", default=False)

    # Update config
    ctx.config.ospf6.enabled = True
    ctx.config.ospf6.router_id = router_id
    ctx.config.ospf6.default_originate = default_originate

    ctx.dirty = True
    log(f"Enabled OSPFv3 with router-id {router_id}")
    print()
    info("Use 'interfaces <name> ospf6 area <n>' to add interfaces to OSPFv3")


def cmd_ospf6_disable(ctx, args: list[str]) -> None:
    """Disable OSPFv3."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.ospf6.enabled:
        warn("OSPFv3 is already disabled")
        return

    if prompt_yes_no("Disable OSPFv3? This will remove the OSPFv3 configuration"):
        ctx.config.ospf6.enabled = False
        ctx.dirty = True
        log("OSPFv3 disabled")
