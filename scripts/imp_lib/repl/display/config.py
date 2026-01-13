"""
Configuration display functions for IMP REPL.

These functions display the staged configuration state to the user.
"""

from typing import Any, List, Optional

from imp_lib.common import Colors, warn


def get_nat_config(config) -> Optional[dict]:
    """Get NAT config from modules list, returns dict or None."""
    if not config or not hasattr(config, 'modules') or not config.modules:
        return None
    for module in config.modules:
        if module.get('name') == 'nat' and module.get('enabled', False):
            return module.get('config', {})
    return None


def show_interfaces(config) -> None:
    """Show interfaces summary."""
    print(f"{Colors.BOLD}Interfaces{Colors.NC}")
    print("=" * 50)

    if config.management:
        m = config.management
        if m.mode == "dhcp":
            print(f"  management  {m.iface} (DHCP)")
        else:
            print(f"  management  {m.iface} -> {m.ipv4}/{m.ipv4_prefix}")

    for iface in config.interfaces:
        ipv4_str = ", ".join(f"{a.address}/{a.prefix}" for a in iface.ipv4) if iface.ipv4 else "none"
        mtu_str = f" MTU:{iface.mtu}" if iface.mtu != 1500 else ""
        print(f"  {iface.name:<12} {iface.iface} -> {ipv4_str}{mtu_str}")
        for addr in iface.ipv6:
            print(f"               IPv6: {addr.address}/{addr.prefix}")
        for sub in iface.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            print(f"    .{sub.vlan_id}: {', '.join(ips)}{lcp}")

    print()
    print("Enter an interface name to see details (e.g., 'wan', 'lan')")
    print()


def show_interface_detail(iface) -> None:
    """Show interface details."""
    print(f"{Colors.BOLD}Interface: {iface.name}{Colors.NC}")
    print("=" * 50)
    print(f"  Physical:  {iface.iface}")
    print(f"  PCI:       {iface.pci}")
    print(f"  MTU:       {iface.mtu}")
    if iface.ipv4:
        print("  IPv4 addresses:")
        for addr in iface.ipv4:
            print(f"    {addr.address}/{addr.prefix}")
    else:
        print("  IPv4:      (none)")
    if iface.ipv6:
        print("  IPv6 addresses:")
        for addr in iface.ipv6:
            print(f"    {addr.address}/{addr.prefix}")
    print(f"  Subifs:    {len(iface.subinterfaces)}")
    if iface.ospf_area is not None:
        passive = " (passive)" if iface.ospf_passive else ""
        print(f"  OSPF:      area {iface.ospf_area}{passive}")
    if iface.ospf6_area is not None:
        passive = " (passive)" if iface.ospf6_passive else ""
        print(f"  OSPFv3:    area {iface.ospf6_area}{passive}")
    # IPv6 RA configuration (only show if IPv6 is configured)
    if iface.ipv6:
        if iface.ipv6_ra_enabled:
            status = "suppressed" if iface.ipv6_ra_suppress else "active"
            print(f"  IPv6 RA:   {status} ({iface.ipv6_ra_interval_max}/{iface.ipv6_ra_interval_min}s)")
            if iface.ipv6_ra_prefixes:
                for p in iface.ipv6_ra_prefixes:
                    print(f"             custom prefix: {p}")
        else:
            print(f"  IPv6 RA:   disabled")
    print()
    if iface.subinterfaces:
        print("Sub-interfaces:")
        for sub in iface.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            print(f"  .{sub.vlan_id}: {', '.join(ips)}{lcp}")
        print()
    print("Commands: set-ipv4, set-ipv6, set-mtu, subinterfaces, ospf, ospf6, ipv6-ra")
    print()


def show_routes(config) -> None:
    """Show static routes."""
    print(f"{Colors.BOLD}Static Routes{Colors.NC}")
    print("=" * 50)
    if not config.routes:
        print("  (none configured)")
    else:
        for route in config.routes:
            iface_str = f" via {route.interface}" if route.interface else ""
            default_marker = " [default]" if route.destination in ("0.0.0.0/0", "::/0") else ""
            print(f"  {route.destination:<20} -> {route.via}{iface_str}{default_marker}")
    print()
    print("Commands: add, delete, set-default-v4, set-default-v6")
    print()


def show_management(config) -> None:
    """Show management interface details."""
    if not config.management:
        warn("Management interface not configured")
        return

    m = config.management
    print(f"{Colors.BOLD}Management Interface{Colors.NC}")
    print("=" * 50)
    print(f"  Interface: {m.iface}")
    print(f"  Mode:      {m.mode}")
    if m.mode == "static":
        print(f"  IPv4:      {m.ipv4}/{m.ipv4_prefix}")
        print(f"  Gateway:   {m.ipv4_gateway}")
    print()


def show_subinterfaces(subifs: list, parent: str) -> None:
    """Show sub-interfaces for a parent interface."""
    print(f"{Colors.BOLD}Sub-interfaces on {parent}{Colors.NC}")
    print("=" * 50)
    if not subifs:
        print("  (none configured)")
    else:
        for sub in subifs:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            print(f"  .{sub.vlan_id}: {', '.join(ips)}{lcp}")
    print()


def show_loopbacks(config) -> None:
    """Show loopback interfaces."""
    print(f"{Colors.BOLD}Loopback Interfaces{Colors.NC}")
    print("=" * 50)
    if not config.loopbacks:
        print("  (none configured)")
    else:
        for lo in config.loopbacks:
            ips = []
            if lo.ipv4:
                ips.append(f"{lo.ipv4}/{lo.ipv4_prefix}")
            if lo.ipv6:
                ips.append(f"{lo.ipv6}/{lo.ipv6_prefix}")
            lcp = " (LCP)" if lo.create_lcp else ""
            print(f"  loop{lo.instance} ({lo.name}): {', '.join(ips)}{lcp}")
    print()


def show_bvi(config) -> None:
    """Show BVI domains."""
    print(f"{Colors.BOLD}BVI Domains{Colors.NC}")
    print("=" * 50)
    if not config.bvi_domains:
        print("  (none configured)")
    else:
        for bvi in config.bvi_domains:
            ips = []
            if bvi.ipv4:
                ips.append(f"{bvi.ipv4}/{bvi.ipv4_prefix}")
            if bvi.ipv6:
                ips.append(f"{bvi.ipv6}/{bvi.ipv6_prefix}")
            lcp = " (LCP)" if bvi.create_lcp else ""
            members = ", ".join(
                f"{m.interface}.{m.vlan_id}" if m.vlan_id else m.interface
                for m in bvi.members
            )
            print(f"  loop{bvi.bridge_id} ({bvi.name}): {', '.join(ips)}{lcp}")
            print(f"    Members: {members}")
    print()


def show_vlan_passthrough(config) -> None:
    """Show VLAN passthrough config."""
    print(f"{Colors.BOLD}VLAN Pass-through{Colors.NC}")
    print("=" * 50)
    if not config.vlan_passthrough:
        print("  (none configured)")
    else:
        for v in config.vlan_passthrough:
            if v.inner_vlan:
                print(f"  VLAN {v.vlan_id}.{v.inner_vlan} (QinQ) {v.from_interface} <-> {v.to_interface}")
            elif v.vlan_type == "dot1ad":
                print(f"  S-VLAN {v.vlan_id} (QinQ) {v.from_interface} <-> {v.to_interface}")
            else:
                print(f"  VLAN {v.vlan_id} (802.1Q) {v.from_interface} <-> {v.to_interface}")
    print()


def show_routing(config) -> None:
    """Show routing summary."""
    print(f"{Colors.BOLD}Routing{Colors.NC}")
    print("=" * 50)
    if config.bgp.enabled:
        peer_count = len(config.bgp.peers)
        print(f"  BGP:    Enabled (AS {config.bgp.asn}, {peer_count} peer{'s' if peer_count != 1 else ''})")
    else:
        print(f"  BGP:    Disabled")
    if config.ospf.enabled:
        print(f"  OSPF:   Enabled (router-id {config.ospf.router_id or config.bgp.router_id})")
    else:
        print(f"  OSPF:   Disabled")
    if config.ospf6.enabled:
        print(f"  OSPFv3: Enabled (router-id {config.ospf6.router_id or config.ospf.router_id or config.bgp.router_id})")
    else:
        print(f"  OSPFv3: Disabled")
    print()


def show_bgp(config) -> None:
    """Show BGP configuration."""
    print(f"{Colors.BOLD}BGP Configuration{Colors.NC}")
    print("=" * 50)
    bgp = config.bgp
    print(f"  Enabled:    {bgp.enabled}")
    if bgp.enabled:
        print(f"  Local AS:   {bgp.asn}")
        print(f"  Router ID:  {bgp.router_id}")
        print()
        print(f"  {Colors.BOLD}Announced Prefixes ({len(bgp.announced_prefixes)}):{Colors.NC}")
        if bgp.announced_prefixes:
            for prefix in bgp.announced_prefixes:
                af = "IPv6" if ':' in prefix else "IPv4"
                print(f"    {prefix} ({af})")
        else:
            print("    (no prefixes configured)")
        print()
        print(f"  {Colors.BOLD}Peers ({len(bgp.peers)}):{Colors.NC}")
        if bgp.peers:
            for peer in bgp.peers:
                af = "IPv6" if ':' in peer.peer_ip else "IPv4"
                print(f"    {peer.name}: {peer.peer_ip} AS {peer.peer_asn} ({af})")
        else:
            print("    (no peers configured)")
    print()


def show_ospf(config) -> None:
    """Show OSPF configuration."""
    print(f"{Colors.BOLD}OSPF Configuration{Colors.NC}")
    print("=" * 50)
    ospf = config.ospf
    print(f"  Enabled:          {ospf.enabled}")
    if ospf.enabled:
        router_id = ospf.router_id or config.bgp.router_id
        print(f"  Router ID:        {router_id}")
        print(f"  Default Originate: {ospf.default_originate}")
        print()
        print(f"  {Colors.BOLD}Interface Areas:{Colors.NC}")
        has_areas = False
        # Loopbacks
        for loop in config.loopbacks:
            if loop.ospf_area is not None:
                passive = " (passive)" if loop.ospf_passive else ""
                print(f"    loop{loop.instance}: area {loop.ospf_area}{passive}")
                has_areas = True
        # Dataplane interfaces
        for iface in config.interfaces:
            if iface.ospf_area is not None:
                passive = " (passive)" if iface.ospf_passive else ""
                print(f"    {iface.name}: area {iface.ospf_area}{passive}")
                has_areas = True
            for sub in iface.subinterfaces:
                if sub.ospf_area is not None:
                    passive = " (passive)" if sub.ospf_passive else ""
                    print(f"    {iface.name}.{sub.vlan_id}: area {sub.ospf_area}{passive}")
                    has_areas = True
        # BVI interfaces
        for bvi in config.bvi_domains:
            if bvi.ospf_area is not None:
                passive = " (passive)" if bvi.ospf_passive else ""
                print(f"    loop{bvi.bridge_id}: area {bvi.ospf_area}{passive}")
                has_areas = True
        if not has_areas:
            print("    (no interfaces configured)")
    print()


def show_ospf6(config) -> None:
    """Show OSPFv3 configuration."""
    print(f"{Colors.BOLD}OSPFv3 Configuration{Colors.NC}")
    print("=" * 50)
    ospf6 = config.ospf6
    print(f"  Enabled:          {ospf6.enabled}")
    if ospf6.enabled:
        router_id = ospf6.router_id or config.ospf.router_id or config.bgp.router_id
        print(f"  Router ID:        {router_id}")
        print(f"  Default Originate: {ospf6.default_originate}")
        print()
        print(f"  {Colors.BOLD}Interface Areas:{Colors.NC}")
        has_areas = False
        # Loopbacks
        for loop in config.loopbacks:
            if loop.ospf6_area is not None:
                passive = " (passive)" if loop.ospf6_passive else ""
                print(f"    loop{loop.instance}: area {loop.ospf6_area}{passive}")
                has_areas = True
        # Dataplane interfaces
        for iface in config.interfaces:
            if iface.ospf6_area is not None:
                passive = " (passive)" if iface.ospf6_passive else ""
                print(f"    {iface.name}: area {iface.ospf6_area}{passive}")
                has_areas = True
            for sub in iface.subinterfaces:
                if sub.ospf6_area is not None:
                    passive = " (passive)" if sub.ospf6_passive else ""
                    print(f"    {iface.name}.{sub.vlan_id}: area {sub.ospf6_area}{passive}")
                    has_areas = True
        # BVI interfaces
        for bvi in config.bvi_domains:
            if bvi.ospf6_area is not None:
                passive = " (passive)" if bvi.ospf6_passive else ""
                print(f"    loop{bvi.bridge_id}: area {bvi.ospf6_area}{passive}")
                has_areas = True
        if not has_areas:
            print("    (no interfaces configured)")
    print()


def show_nat(config) -> None:
    """Show NAT configuration."""
    print(f"{Colors.BOLD}NAT Configuration{Colors.NC}")
    print("=" * 50)
    nat_cfg = get_nat_config(config)
    if nat_cfg:
        print(f"  Pool prefix: {nat_cfg.get('bgp_prefix', 'not set')}")
        print(f"  Mappings:    {len(nat_cfg.get('mappings', []))}")
        print(f"  Bypass rules: {len(nat_cfg.get('bypass_pairs', []))}")
    else:
        print("  NAT module not configured")
        print("  Use 'config modules enable nat' to enable")
    print()


def show_nat_mappings(config) -> None:
    """Show NAT mappings."""
    print(f"{Colors.BOLD}NAT Mappings{Colors.NC}")
    print("=" * 50)
    nat_cfg = get_nat_config(config)
    mappings = nat_cfg.get('mappings', []) if nat_cfg else []
    if not mappings:
        print("  (none configured)")
    else:
        for m in mappings:
            src = m.get('source_network', m.source_network if hasattr(m, 'source_network') else '?')
            pool = m.get('nat_pool', m.nat_pool if hasattr(m, 'nat_pool') else '?')
            print(f"  {src} -> {pool}")
    print()


def show_nat_bypass(config) -> None:
    """Show NAT bypass rules."""
    print(f"{Colors.BOLD}NAT Bypass Rules{Colors.NC}")
    print("=" * 50)
    nat_cfg = get_nat_config(config)
    bypass_pairs = nat_cfg.get('bypass_pairs', []) if nat_cfg else []
    if not bypass_pairs:
        print("  (none configured)")
    else:
        for bp in bypass_pairs:
            src = bp.get('source', bp.source if hasattr(bp, 'source') else '?')
            dst = bp.get('destination', bp.destination if hasattr(bp, 'destination') else '?')
            print(f"  {src} -> {dst}")
    print()


def show_containers(config) -> None:
    """Show container configuration."""
    print(f"{Colors.BOLD}Container Network{Colors.NC}")
    print("=" * 50)
    c = config.container
    print(f"  Network:    {c.network}")
    print(f"  Gateway:    {c.gateway}")
    print(f"  Bridge IP:  {c.bridge_ip}")
    print(f"  DHCP range: {c.dhcp_start} - {c.dhcp_end}")
    if c.ipv6:
        print(f"  IPv6:       {c.ipv6}/{c.ipv6_prefix}")
    print()


def show_cpu(config) -> None:
    """Show CPU allocation."""
    print(f"{Colors.BOLD}CPU Allocation{Colors.NC}")
    print("=" * 50)
    cpu = config.cpu
    print(f"  Total cores: {cpu.total_cores}")
    print()
    print(f"  VPP Core:")
    print(f"    Main core:    {cpu.core_main}")
    print(f"    Worker cores: {cpu.core_workers or '(none)'}")
    print()
    print(f"  VPP NAT:")
    if cpu.nat_main > 0:
        print(f"    Main core:    {cpu.nat_main}")
        print(f"    Worker cores: {cpu.nat_workers or '(none)'}")
    else:
        print(f"    Using software threads (no dedicated cores)")
    print()
