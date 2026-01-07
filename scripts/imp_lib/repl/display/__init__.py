"""
imp_lib.repl.display - Display functions for REPL

This package contains functions for displaying configuration and live state:
- config: Functions for displaying staged configuration
- live: Functions for displaying live VPP/FRR state
"""

from .config import (
    get_nat_config,
    show_interfaces,
    show_interface_detail,
    show_routes,
    show_management,
    show_subinterfaces,
    show_loopbacks,
    show_bvi,
    show_vlan_passthrough,
    show_routing,
    show_bgp,
    show_ospf,
    show_ospf6,
    show_nat,
    show_nat_mappings,
    show_nat_bypass,
    show_containers,
    show_cpu,
)

from .live import (
    show_live_interfaces,
    show_live_route,
    show_live_fib,
    show_live_neighbors,
    show_live_bgp,
    show_live_ospf,
    show_live_module,
    filter_fib_output,
    pager,
)

__all__ = [
    # Config display
    'get_nat_config',
    'show_interfaces',
    'show_interface_detail',
    'show_routes',
    'show_management',
    'show_subinterfaces',
    'show_loopbacks',
    'show_bvi',
    'show_vlan_passthrough',
    'show_routing',
    'show_bgp',
    'show_ospf',
    'show_ospf6',
    'show_nat',
    'show_nat_mappings',
    'show_nat_bypass',
    'show_containers',
    'show_cpu',
    # Live display
    'show_live_interfaces',
    'show_live_route',
    'show_live_fib',
    'show_live_neighbors',
    'show_live_bgp',
    'show_live_ospf',
    'show_live_module',
    'filter_fib_output',
    'pager',
]
