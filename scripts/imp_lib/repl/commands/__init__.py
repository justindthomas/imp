"""
imp_lib.repl.commands - Command handlers for REPL

This package contains command handler functions organized by feature area:
- crud: Loopback, BVI, subinterface, VLAN passthrough CRUD
- nat: NAT mapping and bypass configuration
- routing: BGP and OSPF configuration
- modules: Module lifecycle management
- capture: Packet capture commands
- trace: VPP graph trace commands
- shell: Shell access commands
- snapshot: ZFS snapshot commands
- system: Status, apply, reload commands
"""

# CRUD operations
from .crud import (
    prompt_value,
    prompt_yes_no,
    cmd_loopback_add,
    cmd_loopback_delete,
    cmd_loopback_edit,
    cmd_bvi_add,
    cmd_bvi_delete,
    cmd_vlan_passthrough_add,
    cmd_vlan_passthrough_delete,
    cmd_subinterface_add,
    cmd_subinterface_delete,
)

# NAT operations
from .nat import (
    find_module,
    ensure_nat_module,
    cmd_nat_mapping_add,
    cmd_nat_mapping_delete,
    cmd_nat_bypass_add,
    cmd_nat_bypass_delete,
    cmd_nat_set_prefix,
)

# Routing operations
from .routing import (
    cmd_bgp_enable,
    cmd_bgp_disable,
    cmd_bgp_peers_list,
    cmd_bgp_peers_add,
    cmd_bgp_peers_remove,
    cmd_ospf_enable,
    cmd_ospf_disable,
    cmd_ospf6_enable,
    cmd_ospf6_disable,
)

# Module operations
from .modules import (
    cmd_modules_available,
    cmd_modules_list,
    cmd_modules_install,
    cmd_modules_enable,
    cmd_modules_disable,
)

# Shell operations
from .shell import (
    list_running_modules,
    cmd_shell_routing,
    cmd_shell_core,
    cmd_shell_nat,
    cmd_shell_module,
)

# Capture operations
from .capture import (
    cmd_capture_start,
    cmd_capture_stop,
    cmd_capture_status,
    cmd_capture_files,
    cmd_capture_analyze,
    cmd_capture_export,
    cmd_capture_delete,
)

# Trace operations
from .trace import (
    cmd_trace_start,
    cmd_trace_stop,
    cmd_trace_status,
    cmd_trace_show,
    cmd_trace_clear,
)

# Snapshot operations
from .snapshot import (
    cmd_snapshot_list,
    cmd_snapshot_create,
    cmd_snapshot_delete,
    cmd_snapshot_export,
    cmd_snapshot_import,
    cmd_snapshot_rollback,
)

__all__ = [
    # CRUD
    'prompt_value', 'prompt_yes_no',
    'cmd_loopback_add', 'cmd_loopback_delete', 'cmd_loopback_edit',
    'cmd_bvi_add', 'cmd_bvi_delete',
    'cmd_vlan_passthrough_add', 'cmd_vlan_passthrough_delete',
    'cmd_subinterface_add', 'cmd_subinterface_delete',
    # NAT
    'find_module', 'ensure_nat_module',
    'cmd_nat_mapping_add', 'cmd_nat_mapping_delete',
    'cmd_nat_bypass_add', 'cmd_nat_bypass_delete',
    'cmd_nat_set_prefix',
    # Routing
    'cmd_bgp_enable', 'cmd_bgp_disable',
    'cmd_bgp_peers_list', 'cmd_bgp_peers_add', 'cmd_bgp_peers_remove',
    'cmd_ospf_enable', 'cmd_ospf_disable',
    'cmd_ospf6_enable', 'cmd_ospf6_disable',
    # Modules
    'cmd_modules_available', 'cmd_modules_list', 'cmd_modules_install',
    'cmd_modules_enable', 'cmd_modules_disable',
    # Shell
    'list_running_modules',
    'cmd_shell_routing', 'cmd_shell_core', 'cmd_shell_nat', 'cmd_shell_module',
    # Capture
    'cmd_capture_start', 'cmd_capture_stop', 'cmd_capture_status',
    'cmd_capture_files', 'cmd_capture_analyze', 'cmd_capture_export', 'cmd_capture_delete',
    # Trace
    'cmd_trace_start', 'cmd_trace_stop', 'cmd_trace_status',
    'cmd_trace_show', 'cmd_trace_clear',
    # Snapshot
    'cmd_snapshot_list', 'cmd_snapshot_create', 'cmd_snapshot_delete',
    'cmd_snapshot_export', 'cmd_snapshot_import', 'cmd_snapshot_rollback',
]
