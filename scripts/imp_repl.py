#!/usr/bin/env python3
"""
imp_repl.py - Interactive REPL for IMP configuration management

This module provides a hierarchical menu-driven interface for managing
router configuration. Changes are staged until explicitly applied.
"""

import ipaddress
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Callable, Any

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import WordCompleter, Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style
    from prompt_toolkit.formatted_text import HTML
except ImportError:
    print("ERROR: prompt_toolkit is required. Install with: apt install python3-prompt-toolkit")
    sys.exit(1)

# Add paths for imports:
# - Script directory (for local development)
# - Python local site-packages (for imp_lib package in production)
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, '/usr/local/lib/python3/dist-packages')

# Import shared utilities from imp_lib
from imp_lib.common import Colors, log, warn, error, info
from imp_lib.common.vpp import get_vpp_socket, get_available_vpp_instances, vpp_exec

# Import display functions from imp_lib
from imp_lib.repl.display import (
    get_nat_config,
    show_interfaces as _show_interfaces,
    show_interface_detail as _show_interface_detail,
    show_routes as _show_routes,
    show_management as _show_management,
    show_subinterfaces as _show_subinterfaces,
    show_loopbacks as _show_loopbacks,
    show_bvi as _show_bvi,
    show_vlan_passthrough as _show_vlan_passthrough,
    show_routing as _show_routing,
    show_bgp as _show_bgp,
    show_ospf as _show_ospf,
    show_ospf6 as _show_ospf6,
    show_containers as _show_containers,
    show_cpu as _show_cpu,
    show_live_interfaces as _show_live_interfaces,
    show_live_route as _show_live_route,
    show_live_fib as _show_live_fib,
    show_live_neighbors as _show_live_neighbors,
    show_live_bgp as _show_live_bgp,
    show_live_ospf as _show_live_ospf,
    show_live_module as _show_live_module,
    filter_fib_output as _filter_fib_output,
    pager as _pager,
)

# Import menu system from imp_lib
from imp_lib.repl import (
    MenuContext,
    get_prompt_text,
    build_menu_tree,
    navigate,
    MenuCompleter,
)

# Import command handlers from imp_lib
from imp_lib.repl.commands import (
    # CRUD
    prompt_value, prompt_yes_no,
    cmd_loopback_add, cmd_loopback_delete, cmd_loopback_edit,
    cmd_bvi_add, cmd_bvi_delete,
    cmd_vlan_passthrough_add, cmd_vlan_passthrough_delete,
    cmd_subinterface_add, cmd_subinterface_delete,
    # Module helpers
    find_module,
    # Routing
    cmd_bgp_enable, cmd_bgp_disable,
    cmd_bgp_peers_list, cmd_bgp_peers_add, cmd_bgp_peers_remove,
    cmd_ospf_enable, cmd_ospf_disable,
    cmd_ospf6_enable, cmd_ospf6_disable,
    # Modules
    cmd_modules_available, cmd_modules_list, cmd_modules_install,
    cmd_modules_enable, cmd_modules_disable,
    # Shell
    list_running_modules,
    cmd_shell_routing, cmd_shell_core, cmd_shell_nat, cmd_shell_module,
    # Capture
    cmd_capture_start, cmd_capture_stop, cmd_capture_status,
    cmd_capture_files, cmd_capture_analyze, cmd_capture_export, cmd_capture_delete,
    # Trace
    cmd_trace_start, cmd_trace_stop, cmd_trace_status,
    cmd_trace_show, cmd_trace_clear,
    # Snapshot
    cmd_snapshot_list, cmd_snapshot_create, cmd_snapshot_delete,
    cmd_snapshot_export, cmd_snapshot_import, cmd_snapshot_rollback,
)
# Import configuration dataclasses from imp_lib.config
from imp_lib.config import (
    RouterConfig, Interface, InterfaceAddress, Route, ManagementInterface,
    SubInterface, LoopbackInterface, BVIConfig, BridgeDomainMember,
    VLANPassthrough, BGPConfig, BGPPeer, OSPFConfig, OSPF6Config,
    ContainerConfig, CPUConfig,
    validate_ipv4, validate_ipv4_cidr, validate_ipv6, validate_ipv6_cidr,
    parse_cidr, save_config, load_config,
    TEMPLATE_DIR, CONFIG_FILE, GENERATED_DIR
)

# Import template rendering from configure_router (still there for apply command)
try:
    from configure_router import render_templates, apply_configs
    CONFIG_AVAILABLE = True
except ImportError:
    CONFIG_AVAILABLE = False

# Import module system from imp_lib
from imp_lib.modules import (
    list_available_modules,
    list_example_modules,
    install_module_from_example,
    load_module_definition,
    ensure_modules_dir,
    MODULE_DEFINITIONS_DIR,
    MODULE_EXAMPLES_DIR,
    ModuleCommand,
    ModuleCommandParam,
)
MODULE_LOADER_AVAILABLE = True


# =============================================================================
# Generic Module Command Executor
# =============================================================================

def validate_param_value(value: str, param_type: str) -> tuple[bool, str]:
    """Validate a parameter value against its type. Returns (valid, error_msg)."""
    import ipaddress

    if param_type == 'ipv4_cidr':
        try:
            ipaddress.IPv4Network(value, strict=False)
            return True, ""
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
            return False, "Invalid IPv4 CIDR (e.g., 10.0.0.0/24)"

    elif param_type == 'ipv6_cidr':
        try:
            ipaddress.IPv6Network(value, strict=False)
            return True, ""
        except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
            return False, "Invalid IPv6 CIDR (e.g., 2001:db8::/32)"

    elif param_type == 'ipv4':
        try:
            ipaddress.IPv4Address(value)
            return True, ""
        except ipaddress.AddressValueError:
            return False, "Invalid IPv4 address"

    elif param_type == 'ipv6':
        try:
            ipaddress.IPv6Address(value)
            return True, ""
        except ipaddress.AddressValueError:
            return False, "Invalid IPv6 address"

    elif param_type == 'integer':
        try:
            int(value)
            return True, ""
        except ValueError:
            return False, "Must be an integer"

    elif param_type == 'boolean':
        if value.lower() in ('true', 'false', 'yes', 'no', '1', '0'):
            return True, ""
        return False, "Must be true/false or yes/no"

    # string and other types - accept anything
    return True, ""


def convert_param_value(value: str, param_type: str):
    """Convert string value to appropriate Python type."""
    if param_type == 'integer':
        return int(value)
    elif param_type == 'boolean':
        return value.lower() in ('true', 'yes', '1')
    # All others remain strings
    return value


def execute_module_command(ctx, module_name: str, cmd: 'ModuleCommand') -> None:
    """Execute a generic module command based on its action type."""
    if not ctx.config:
        print(f"{Colors.RED}[!] No configuration loaded{Colors.NC}")
        return

    # Find or create module config
    module_dict = find_module(ctx.config, module_name)
    if not module_dict:
        # Module not in config yet - add it
        module_dict = {
            'name': module_name,
            'enabled': True,
            'config': {}
        }
        if not hasattr(ctx.config, 'modules'):
            ctx.config.modules = []
        ctx.config.modules.append(module_dict)

    if not module_dict.get('enabled'):
        module_dict['enabled'] = True

    if 'config' not in module_dict:
        module_dict['config'] = {}

    mod_cfg = module_dict['config']

    # Execute based on action type
    if cmd.action == 'array_append':
        _exec_array_append(ctx, mod_cfg, cmd)
    elif cmd.action == 'array_remove':
        _exec_array_remove(ctx, mod_cfg, cmd)
    elif cmd.action == 'array_list':
        _exec_array_list(mod_cfg, cmd)
    elif cmd.action == 'set_value':
        _exec_set_value(ctx, mod_cfg, cmd)
    elif cmd.action == 'show':
        _exec_show(module_dict, module_name)
    else:
        print(f"{Colors.RED}[!] Unknown action: {cmd.action}{Colors.NC}")


def _exec_array_append(ctx, mod_cfg: dict, cmd: 'ModuleCommand') -> None:
    """Execute array_append action - add item to array."""
    # Ensure target array exists
    if cmd.target not in mod_cfg:
        mod_cfg[cmd.target] = []
    target_array = mod_cfg[cmd.target]

    print()
    print(f"{Colors.BOLD}{cmd.description}{Colors.NC}")
    print()

    # Collect parameter values
    item = {}
    for param in cmd.params:
        prompt_text = param.prompt or f"{param.name}"
        while True:
            value = input(f"  {prompt_text}: ").strip()
            if not value:
                if param.required:
                    print(f"  {Colors.RED}Required field{Colors.NC}")
                    continue
                else:
                    break

            valid, err = validate_param_value(value, param.type)
            if not valid:
                print(f"  {Colors.RED}{err}{Colors.NC}")
                continue

            item[param.name] = convert_param_value(value, param.type)
            break

        if not value and not param.required:
            continue

    if not item:
        print(f"{Colors.YELLOW}[!] Cancelled{Colors.NC}")
        return

    # Check for duplicate using key field(s)
    # key can be a single field name or a list of field names for compound keys
    # If no key specified, use first param as key
    key_fields = cmd.key if cmd.key else (cmd.params[0].name if cmd.params else None)
    if key_fields:
        # Normalize to list
        if isinstance(key_fields, str):
            key_fields = [key_fields]

        # Check if all key fields are present in item
        if all(k in item for k in key_fields):
            def matches_key(existing):
                return all(existing.get(k) == item.get(k) for k in key_fields)

            if any(matches_key(existing) for existing in target_array):
                key_display = ", ".join(f"{k}={item[k]}" for k in key_fields)
                print(f"{Colors.RED}[!] Entry with {key_display} already exists{Colors.NC}")
                return

    target_array.append(item)
    ctx.dirty = True

    # Format output
    if cmd.format:
        display = cmd.format.format(**item)
    else:
        display = str(item)
    print(f"{Colors.GREEN}[+] Added: {display}{Colors.NC}")


def _exec_array_remove(ctx, mod_cfg: dict, cmd: 'ModuleCommand') -> None:
    """Execute array_remove action - remove item from array."""
    if cmd.target not in mod_cfg or not mod_cfg[cmd.target]:
        print(f"{Colors.YELLOW}[!] No items to delete{Colors.NC}")
        return

    target_array = mod_cfg[cmd.target]
    key_field = cmd.key or 'name'

    print()
    print(f"Current {cmd.target}:")
    for i, item in enumerate(target_array, 1):
        if cmd.format:
            try:
                display = cmd.format.format(**item)
            except KeyError:
                display = str(item)
        else:
            display = str(item)
        print(f"  {i}. {display}")
    print()

    choice = input("Delete which entry (number or press Enter to cancel): ").strip()
    if not choice:
        return

    try:
        idx = int(choice) - 1
        if idx < 0 or idx >= len(target_array):
            print(f"{Colors.RED}[!] Invalid selection{Colors.NC}")
            return
    except ValueError:
        print(f"{Colors.RED}[!] Invalid number{Colors.NC}")
        return

    removed = target_array.pop(idx)
    ctx.dirty = True

    if cmd.format:
        try:
            display = cmd.format.format(**removed)
        except KeyError:
            display = str(removed)
    else:
        display = str(removed)
    print(f"{Colors.GREEN}[+] Deleted: {display}{Colors.NC}")


def _exec_array_list(mod_cfg: dict, cmd: 'ModuleCommand') -> None:
    """Execute array_list action - display array contents."""
    if cmd.target not in mod_cfg or not mod_cfg[cmd.target]:
        print(f"  (none configured)")
        return

    target_array = mod_cfg[cmd.target]
    for item in target_array:
        if cmd.format:
            try:
                display = cmd.format.format(**item)
            except KeyError:
                display = str(item)
        else:
            display = str(item)
        print(f"  {display}")


def _exec_set_value(ctx, mod_cfg: dict, cmd: 'ModuleCommand') -> None:
    """Execute set_value action - set a scalar config field."""
    current = mod_cfg.get(cmd.target, '')
    print()
    if current:
        print(f"  Current value: {current}")

    if not cmd.params:
        print(f"{Colors.RED}[!] No parameters defined for set_value{Colors.NC}")
        return

    param = cmd.params[0]
    prompt_text = param.prompt or f"New value for {cmd.target}"

    while True:
        value = input(f"  {prompt_text}: ").strip()
        if not value:
            print(f"{Colors.YELLOW}[!] Cancelled{Colors.NC}")
            return

        valid, err = validate_param_value(value, param.type)
        if not valid:
            print(f"  {Colors.RED}{err}{Colors.NC}")
            continue

        mod_cfg[cmd.target] = convert_param_value(value, param.type)
        ctx.dirty = True
        print(f"{Colors.GREEN}[+] Set {cmd.target} = {value}{Colors.NC}")
        break


def _exec_show(module_dict: dict, module_name: str) -> None:
    """Execute show action - display module configuration."""
    print()
    print(f"{Colors.BOLD}Module: {module_name}{Colors.NC}")
    print(f"  Enabled: {module_dict.get('enabled', False)}")
    print()

    config = module_dict.get('config', {})
    if not config:
        print("  (no configuration)")
        return

    for key, value in config.items():
        if isinstance(value, list):
            print(f"  {key}: ({len(value)} items)")
            for item in value:
                print(f"    - {item}")
        else:
            print(f"  {key}: {value}")


def get_module_commands(module_name: str) -> list:
    """Load CLI commands for a module from its definition."""
    if not MODULE_LOADER_AVAILABLE:
        return []

    try:
        module_def = load_module_definition(module_name)
        return module_def.cli_commands
    except Exception:
        return []


# =============================================================================
# Colors and Styling
# =============================================================================
# Colors, log, warn, error, info are imported from imp_lib.common

IMP_STYLE = Style.from_dict({
    'prompt': '#00aa00 bold',
    'prompt.path': '#0088ff',
    'info': '#888888',
    'warning': '#ffaa00',
    'error': '#ff0000 bold',
    'success': '#00ff00',
})


# =============================================================================
# Command Handlers
# =============================================================================

def cmd_help(ctx: MenuContext, args: list[str], menus: dict) -> None:
    """Show help for current menu."""
    print()
    print(f"{Colors.BOLD}Available Commands:{Colors.NC}")
    print()

    # Navigation commands
    print(f"  {Colors.CYAN}Navigation:{Colors.NC}")
    print("    help, ?         Show this help")
    if ctx.path:  # Only show when not at root
        print("    back, ..        Go up one level")
        print("    home, /         Return to root menu")
    print("    exit, quit      Exit the REPL")
    print()

    # Operational commands
    print(f"  {Colors.CYAN}Operations:{Colors.NC}")
    if not ctx.path or ctx.path[0] != "config":
        print("    show            Display live state (show interfaces, routes, bgp, etc.)")
        print("    show config     Display staged configuration")
    else:
        print("    show            Display configuration at current level")
    print("    status          Show staged vs applied status")
    if ctx.dirty:
        print("    apply           Save and regenerate config files")
    print("    reload          Reload from JSON (discard changes)")
    print("    agent           Enter LLM-powered agent mode (Ollama)")
    print()

    # Get current menu
    menu = menus.get("root")
    for segment in ctx.path:
        if menu and "children" in menu:
            menu = menu["children"].get(segment, {})

    # Show submenus
    if menu and "children" in menu:
        print(f"  {Colors.CYAN}Submenus:{Colors.NC}")
        for name in sorted(menu["children"].keys()):
            print(f"    {name}")
        print()

    # Show menu-specific commands
    if menu and "commands" in menu:
        cmds = [c for c in menu["commands"] if c not in ["show"]]
        if cmds:
            print(f"  {Colors.CYAN}Actions:{Colors.NC}")
            for cmd in cmds:
                print(f"    {cmd}")
            print()


def cmd_show(ctx: MenuContext, args: list[str]) -> None:
    """Show configuration at current level."""
    if not ctx.config:
        warn("No configuration loaded")
        return

    config = ctx.config
    # Strip "config" prefix for path matching (allows same logic for config menu)
    path = ctx.path[1:] if ctx.path and ctx.path[0] == "config" else ctx.path

    print()

    if not path:
        # Root level - show summary
        print(f"{Colors.BOLD}Configuration Summary{Colors.NC}")
        print("=" * 50)
        print()
        print(f"  Hostname:    {config.hostname}")

        if config.management:
            print(f"  Management:  {config.management.iface} ({config.management.mode})")

        if config.interfaces:
            for iface in config.interfaces:
                ipv4_str = ", ".join(f"{a.address}/{a.prefix}" for a in iface.ipv4) if iface.ipv4 else "none"
                print(f"  {iface.name}: {iface.iface} -> {ipv4_str}")
                if iface.subinterfaces:
                    print(f"    + {len(iface.subinterfaces)} sub-interface(s)")

        if config.routes:
            default_v4 = next((r for r in config.routes if r.destination == "0.0.0.0/0"), None)
            default_v6 = next((r for r in config.routes if r.destination == "::/0"), None)
            print(f"  Default v4:  {default_v4.via if default_v4 else 'none'}")
            print(f"  Default v6:  {default_v6.via if default_v6 else 'none'}")

        if config.bgp.enabled:
            peer_count = len(config.bgp.peers)
            print(f"  BGP:         AS {config.bgp.asn}, {peer_count} peer{'s' if peer_count != 1 else ''}")
        else:
            print(f"  BGP:         Disabled")

        nat_cfg = get_nat_config(config)
        if nat_cfg:
            print(f"  NAT prefix:  {nat_cfg.get('bgp_prefix', 'not set')}")
            print(f"  NAT mappings: {len(nat_cfg.get('mappings', []))}")
        else:
            print(f"  NAT:         Not configured (use 'config modules enable nat')")
        print(f"  Loopbacks:   {len(config.loopbacks)}")
        print(f"  BVI domains: {len(config.bvi_domains)}")
        print(f"  VLAN pass:   {len(config.vlan_passthrough)}")
        print()

    elif path == ["interfaces"]:
        _show_interfaces(config)

    elif path == ["interfaces", "management"]:
        _show_management(config)

    elif len(path) >= 2 and path[0] == "interfaces" and path[1] not in ("management",):
        # Dynamic interface handling
        iface_name = path[1]
        iface = next((i for i in config.interfaces if i.name == iface_name), None)
        if iface:
            if len(path) == 2:
                _show_interface_detail(iface)
            elif len(path) >= 3 and path[2] == "subinterfaces":
                _show_subinterfaces(iface.subinterfaces, iface.name)

    elif path == ["routes"]:
        _show_routes(config)

    elif path == ["loopbacks"]:
        _show_loopbacks(config)

    elif path == ["bvi"]:
        _show_bvi(config)

    elif path == ["vlan-passthrough"]:
        _show_vlan_passthrough(config)

    elif path == ["routing"]:
        _show_routing(config)

    elif path == ["routing", "bgp"]:
        _show_bgp(config)

    elif path == ["routing", "bgp", "peers"]:
        cmd_bgp_peers_list(MenuContext(config=config), [])

    elif path == ["routing", "ospf"]:
        _show_ospf(config)

    elif path == ["routing", "ospf6"]:
        _show_ospf6(config)

    elif path == ["containers"]:
        _show_containers(config)

    elif path == ["cpu"]:
        _show_cpu(config)

    else:
        warn(f"No show handler for path: {'.'.join(path)}")


def cmd_show_live(ctx: MenuContext, args: list[str]) -> None:
    """Show live operational state from VPP/FRR."""
    if not args:
        # Show available categories
        print()
        print(f"{Colors.BOLD}Live State Categories:{Colors.NC}")
        print("  interfaces          - VPP interface state and counters")
        print("  ip route [prefix]   - IPv4 routing table (FRR)")
        print("  ipv6 route [prefix] - IPv6 routing table (FRR)")
        print("  ip fib [prefix]     - IPv4 forwarding table (VPP)")
        print("  ipv6 fib [prefix]   - IPv6 forwarding table (VPP)")
        print("  neighbors           - ARP/NDP neighbor table")
        print("  bgp                 - BGP neighbor status")
        print("  ospf                - OSPF neighbor status")
        print("  module <name> <cmd> - Module-specific commands")
        print()
        print("Prefix filter shows routes within the given prefix, e.g.:")
        print("  show ip route 10.0.0.0/8")
        print()
        print("Use 'show config' to view staged configuration")
        return

    target = args[0].lower()

    if target == "config":
        # Delegate to config show
        if len(args) > 1:
            # Build path from remaining args for config show
            temp_ctx = MenuContext(config=ctx.config, path=["config"] + args[1:])
            cmd_show(temp_ctx, [])
        else:
            temp_ctx = MenuContext(config=ctx.config, path=[])
            cmd_show(temp_ctx, [])
        return

    if target == "interfaces":
        _show_live_interfaces()
    elif target == "ip" and len(args) > 1:
        subtarget = args[1].lower()
        prefix_filter = args[2] if len(args) > 2 else None
        if subtarget == "route":
            _show_live_route("ip", prefix_filter)
        elif subtarget == "fib":
            _show_live_fib("ip", prefix_filter)
        else:
            warn(f"Unknown: show ip {subtarget}")
            print("Use: show ip route [prefix], show ip fib [prefix]")
    elif target == "ipv6" and len(args) > 1:
        subtarget = args[1].lower()
        prefix_filter = args[2] if len(args) > 2 else None
        if subtarget == "route":
            _show_live_route("ipv6", prefix_filter)
        elif subtarget == "fib":
            _show_live_fib("ipv6", prefix_filter)
        else:
            warn(f"Unknown: show ipv6 {subtarget}")
            print("Use: show ipv6 route [prefix], show ipv6 fib [prefix]")
    elif target in ("ip", "ipv6"):
        warn(f"Incomplete command: show {target}")
        print(f"Use: show {target} route [prefix], show {target} fib [prefix]")
    elif target == "neighbors":
        _show_live_neighbors()
    elif target == "bgp":
        _show_live_bgp()
    elif target == "ospf":
        _show_live_ospf()
    elif target == "module":
        _show_live_module(args[1:])
    else:
        warn(f"Unknown show target: {target}")
        print("Use 'show' for available options")


# =============================================================================
# Live Display Functions
# =============================================================================
# These functions are imported from imp_lib.repl.display.live:
# _show_live_interfaces, _pager, _show_live_route, _filter_fib_output,
# _show_live_fib, _show_live_neighbors, _show_live_bgp, _show_live_ospf,
# _show_live_module




# =============================================================================
# Config Display Functions
# =============================================================================
# These functions are imported from imp_lib.repl.display.config:
# _show_interfaces, _show_interface_detail, _show_routes, _show_management,
# _show_subinterfaces, _show_loopbacks, _show_bvi, _show_vlan_passthrough,
# _show_routing, _show_bgp, _show_ospf, _show_ospf6, _show_nat,
# _show_nat_mappings, _show_nat_bypass, _show_containers, _show_cpu


def cmd_status(ctx: MenuContext, args: list[str]) -> None:
    """Show staged vs applied configuration status."""
    print()
    print(f"{Colors.BOLD}Configuration Status{Colors.NC}")
    print("=" * 50)

    if CONFIG_FILE.exists():
        print(f"  Config file: {CONFIG_FILE}")
        print(f"  Status:      {'MODIFIED (unsaved)' if ctx.dirty else 'Clean'}")
    else:
        print(f"  Config file: Not found")
        print(f"  Status:      New configuration")

    print()


def cmd_apply(ctx: MenuContext, args: list[str]) -> None:
    """Save configuration and apply changes (live if possible)."""
    if not ctx.config:
        error("No configuration to apply")
        return

    if not CONFIG_AVAILABLE:
        error("Configuration module not available")
        return

    try:
        # Load previous config for diffing
        old_config = None
        if CONFIG_FILE.exists():
            try:
                old_config = load_config(CONFIG_FILE)
            except Exception:
                pass

        # Save new config and regenerate files
        save_config(ctx.config, CONFIG_FILE)
        ctx.dirty = False
        ctx.original_json = json.dumps(asdict(ctx.config), sort_keys=True)
        render_templates(ctx.config, TEMPLATE_DIR, GENERATED_DIR)
        apply_configs(GENERATED_DIR)

        # Try live apply if we have a previous config to diff against
        if old_config:
            try:
                from live_config import LiveConfigApplier, requires_restart, get_change_summary

                # Check for changes that require restart
                restart_reasons = requires_restart(old_config, ctx.config)

                # Create applier and show dry run
                applier = LiveConfigApplier(old_config, ctx.config)
                success, messages = applier.apply(dry_run=True)

                print()
                print(f"{Colors.BOLD}Changes detected:{Colors.NC}")
                for msg in messages:
                    print(f"  {msg}")
                print()

                if restart_reasons:
                    warn("Some changes require service restart:")
                    for reason in restart_reasons:
                        print(f"    - {reason}")
                    print()

                # Check if there are any live-applicable changes
                has_live_changes = any("DRY-RUN" in msg for msg in messages)

                if has_live_changes:
                    response = input("Apply changes live? [Y/n]: ").strip().lower()
                    if response != 'n':
                        log("Applying changes live...")
                        success, messages = applier.apply(dry_run=False)
                        for msg in messages:
                            if "ERROR" in msg:
                                error(msg)
                            elif msg.startswith("  OK"):
                                log(msg.strip())
                            else:
                                print(f"  {msg}")

                        if not success:
                            error("Some changes failed to apply. Manual intervention may be required.")
                            print("You can restart services to apply all changes from config files:")
                            print(f"  {_get_restart_command()}")
                            return
                        else:
                            log("Live changes applied successfully")
                    else:
                        print("Changes saved to config files. Restart services to apply:")
                        print(f"  {_get_restart_command()}")
                        return

                # Handle restart-required changes
                if restart_reasons:
                    response = input("Restart services for remaining changes? [y/N]: ").strip().lower()
                    if response == 'y':
                        _restart_services()
                    else:
                        print(f"Run '{_get_restart_command()}' to apply remaining changes")
                else:
                    log("Configuration applied")

            except ImportError:
                # live_config not available, fall back to restart
                warn("Live config module not available, falling back to service restart")
                response = input("Restart services now? [y/N]: ").strip().lower()
                if response == 'y':
                    _restart_services()
                else:
                    print(f"Run '{_get_restart_command()}' to apply changes")

        else:
            # No previous config - this is first-time setup, must restart
            log("Configuration applied (first-time setup)")
            print()
            response = input("Start services now? [y/N]: ").strip().lower()
            if response == 'y':
                _restart_services()
            else:
                print(f"Run '{_get_restart_command()}' to start services")

    except Exception as e:
        error(f"Failed to apply: {e}")
        import traceback
        traceback.print_exc()


def _get_module_services() -> list[str]:
    """Get list of enabled module service names from config."""
    if not CONFIG_FILE.exists():
        return []
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        services = []
        for mod in config.get("modules", []):
            if mod.get("enabled", False) and mod.get("name"):
                services.append(f"vpp-{mod['name']}")
        return services
    except Exception:
        return []


def _get_restart_command() -> str:
    """Get the full systemctl restart command for all services."""
    base_services = ["vpp-core", "vpp-core-config"]
    module_services = _get_module_services()
    all_services = base_services + module_services + ["frr"]
    return "systemctl restart " + " ".join(all_services)


def _restart_services() -> None:
    """Restart all dataplane services in correct order."""
    log("Restarting services...")
    # Order matters: vpp-core must be up before vpp-core-config, modules, and frr
    subprocess.run(["systemctl", "restart", "vpp-core"], check=False)
    subprocess.run(["systemctl", "restart", "vpp-core-config"], check=False)

    # Restart all enabled module services
    for module_service in _get_module_services():
        subprocess.run(["systemctl", "restart", module_service], check=False)

    subprocess.run(["systemctl", "restart", "frr"], check=False)
    log("Services restarted")


def cmd_reload(ctx: MenuContext, args: list[str]) -> None:
    """Reload configuration from JSON file, discarding changes."""
    if not CONFIG_FILE.exists():
        error(f"No configuration file at {CONFIG_FILE}")
        return

    if ctx.dirty:
        response = input("Discard unsaved changes? [y/N]: ").strip().lower()
        if response != 'y':
            print("Cancelled")
            return

    try:
        ctx.config = load_config(CONFIG_FILE)
        ctx.dirty = False
        ctx.original_json = json.dumps(asdict(ctx.config), sort_keys=True)
        log("Configuration reloaded")
    except Exception as e:
        error(f"Failed to reload: {e}")


# =============================================================================
# Agent Command
# =============================================================================

def cmd_agent(ctx: MenuContext, args: list[str]) -> None:
    """Enter LLM-powered agent mode."""
    try:
        from imp_agent import run_agent
    except ImportError:
        error("Agent module not available")
        print("  Ensure imp_agent.py is installed in /usr/local/bin")
        return

    # Parse args for --ollama-host and --model
    host = None
    model = None
    i = 0
    while i < len(args):
        if args[i] in ("--ollama-host", "-h") and i + 1 < len(args):
            host = args[i + 1]
            i += 2
        elif args[i] in ("--model", "-m") and i + 1 < len(args):
            model = args[i + 1]
            i += 2
        else:
            i += 1

    run_agent(ctx, host=host, model=model)


# =============================================================================
# Command Dispatcher
# =============================================================================

def handle_command(cmd: str, ctx: MenuContext, menus: dict) -> bool:
    """
    Handle a command. Returns False if should exit REPL.
    """
    parts = cmd.strip().split()
    if not parts:
        return True

    command = parts[0].lower()
    args = parts[1:]

    # Global commands
    if command in ("exit", "quit"):
        if ctx.dirty:
            if not prompt_yes_no("Discard unsaved changes?"):
                return True
        return False

    if command in ("help", "?"):
        cmd_help(ctx, args, menus)
        return True

    if command in ("back", ".."):
        if ctx.path:
            ctx.path.pop()
        return True

    if command in ("home", "/"):
        ctx.path = []
        return True

    if command == "show":
        # At root: show live operational state
        # In config menu: show staged configuration
        if not ctx.path or (ctx.path and ctx.path[0] != "config"):
            cmd_show_live(ctx, args)
        else:
            cmd_show(ctx, args)
        return True

    if command == "status":
        # Context-aware status: capture/trace menus show their specific status
        if ctx.path == ["capture"]:
            cmd_capture_status(ctx, args)
        elif ctx.path == ["trace"]:
            cmd_trace_status(ctx, args)
        else:
            cmd_status(ctx, args)
        return True

    if command == "apply":
        cmd_apply(ctx, args)
        return True

    if command == "reload":
        cmd_reload(ctx, args)
        return True

    # Config multi-word shortcuts: "config loopbacks add", "config nat mappings list", etc.
    if command == "config":
        if not args:
            # Just "config" - navigate there
            ctx.path = ["config"]
            return True
        # Re-invoke handle_command as if we're in config menu
        original_path = ctx.path
        ctx.path = ["config"]
        # Rebuild command from args
        new_cmd = " ".join(args)
        result = handle_command(new_cmd, ctx, menus)
        # If command wasn't recognized and path is still just ["config"], try navigation
        if ctx.path == ["config"] and args:
            target = args[0].lower()
            if not navigate(ctx, target, menus):
                # Navigation failed, restore path
                ctx.path = original_path
                warn(f"Unknown config command: {target}")
        return result

    # Path-specific commands
    path = ctx.path
    # For config items, strip "config" prefix so existing path comparisons work
    config_path = path[1:] if path and path[0] == "config" else path

    # Shell commands - support "shell core" from any level
    if command == "shell" and args:
        subcommand = args[0].lower()
        if subcommand == "routing":
            cmd_shell_routing(ctx, args[1:])
            return True
        if subcommand == "core":
            cmd_shell_core(ctx, args[1:])
            return True
        # Dynamic module shells
        cmd_shell_module(ctx, [subcommand] + args[1:])
        return True

    # Shell commands when already in shell menu
    if path == ["shell"]:
        if command == "routing":
            cmd_shell_routing(ctx, args)
            return True
        if command == "core":
            cmd_shell_core(ctx, args)
            return True
        # Dynamic module shells
        cmd_shell_module(ctx, [command] + args)
        return True

    # Capture commands - support "capture start" from any level
    if command == "capture" and args:
        subcommand = args[0].lower()
        if subcommand == "start":
            cmd_capture_start(ctx, args[1:])
            return True
        if subcommand == "stop":
            cmd_capture_stop(ctx, args[1:])
            return True
        if subcommand == "status":
            cmd_capture_status(ctx, args[1:])
            return True
        if subcommand == "files":
            cmd_capture_files(ctx, args[1:])
            return True
        if subcommand == "analyze":
            cmd_capture_analyze(ctx, args[1:])
            return True
        if subcommand == "export":
            cmd_capture_export(ctx, args[1:])
            return True
        if subcommand == "delete":
            cmd_capture_delete(ctx, args[1:])
            return True

    # Capture commands when in capture menu
    if path == ["capture"]:
        if command == "start":
            cmd_capture_start(ctx, args)
            return True
        if command == "stop":
            cmd_capture_stop(ctx, args)
            return True
        if command == "status":
            cmd_capture_status(ctx, args)
            return True
        if command == "files":
            cmd_capture_files(ctx, args)
            return True
        if command == "analyze":
            cmd_capture_analyze(ctx, args)
            return True
        if command == "export":
            cmd_capture_export(ctx, args)
            return True
        if command == "delete":
            cmd_capture_delete(ctx, args)
            return True

    # Trace commands - support "trace start", "trace show core", etc from any level
    if command == "trace" and args:
        subcommand = args[0].lower()
        if subcommand == "start":
            cmd_trace_start(ctx, args[1:])
            return True
        if subcommand == "stop":
            cmd_trace_stop(ctx, args[1:])
            return True
        if subcommand == "status":
            cmd_trace_status(ctx, args[1:])
            return True
        if subcommand == "show":
            cmd_trace_show(ctx, args[1:])
            return True
        if subcommand == "clear":
            cmd_trace_clear(ctx, args[1:])
            return True

    # Trace commands when in trace menu
    if path == ["trace"]:
        if command == "start":
            cmd_trace_start(ctx, args)
            return True
        if command == "stop":
            cmd_trace_stop(ctx, args)
            return True
        if command == "status":
            cmd_trace_status(ctx, args)
            return True
        if command == "show":
            cmd_trace_show(ctx, args)
            return True
        if command == "clear":
            cmd_trace_clear(ctx, args)
            return True

    # Multi-word commands from any level: "interfaces <name> ospf area <n>", etc.
    if command == "interfaces" and args and ctx.config:
        subcommand = args[0].lower()
        # Check for internal interface with ospf command: "interfaces internal0 ospf area 0"
        iface = next((i for i in ctx.config.internal if i.vpp_name == subcommand), None)
        if iface and len(args) >= 2:
            if args[1].lower() == "ospf" and len(args) >= 4 and args[2].lower() == "area":
                try:
                    area = int(args[3])
                    iface.ospf_area = area
                    ctx.dirty = True
                    log(f"Set {iface.vpp_name} OSPF area to {area}")
                    return True
                except ValueError:
                    error("Invalid area number")
                    return True
            if args[1].lower() == "ospf" and len(args) >= 3 and args[2].lower() == "passive":
                iface.ospf_passive = True
                ctx.dirty = True
                log(f"Set {iface.vpp_name} as OSPF passive")
                return True
            if args[1].lower() == "ospf6" and len(args) >= 4 and args[2].lower() == "area":
                try:
                    area = int(args[3])
                    iface.ospf6_area = area
                    ctx.dirty = True
                    log(f"Set {iface.vpp_name} OSPFv3 area to {area}")
                    return True
                except ValueError:
                    error("Invalid area number")
                    return True
            if args[1].lower() == "ospf6" and len(args) >= 3 and args[2].lower() == "passive":
                iface.ospf6_passive = True
                ctx.dirty = True
                log(f"Set {iface.vpp_name} as OSPFv3 passive")
                return True
            # IPv6 RA commands: "interfaces <name> ipv6-ra enable/disable/interval/suppress"
            if args[1].lower() == "ipv6-ra" and len(args) >= 3:
                ra_cmd = args[2].lower()
                if ra_cmd == "enable":
                    iface.ipv6_ra_enabled = True
                    ctx.dirty = True
                    log(f"Enabled IPv6 RA on {iface.vpp_name}")
                    return True
                if ra_cmd == "disable":
                    iface.ipv6_ra_enabled = False
                    ctx.dirty = True
                    log(f"Disabled IPv6 RA on {iface.vpp_name}")
                    return True
                if ra_cmd == "suppress":
                    iface.ipv6_ra_suppress = True
                    ctx.dirty = True
                    log(f"Suppressed IPv6 RA on {iface.vpp_name}")
                    return True
                if ra_cmd == "no-suppress":
                    iface.ipv6_ra_suppress = False
                    ctx.dirty = True
                    log(f"Enabled IPv6 RA sending on {iface.vpp_name}")
                    return True
                if ra_cmd == "interval" and len(args) >= 5:
                    try:
                        max_int = int(args[3])
                        min_int = int(args[4])
                        iface.ipv6_ra_interval_max = max_int
                        iface.ipv6_ra_interval_min = min_int
                        ctx.dirty = True
                        log(f"Set {iface.vpp_name} RA interval to {max_int}/{min_int}s")
                        return True
                    except ValueError:
                        error("Invalid interval values")
                        return True
                if ra_cmd == "prefix" and len(args) >= 4:
                    prefix_cmd = args[3].lower()
                    if prefix_cmd == "add" and len(args) >= 5:
                        prefix = args[4]
                        if prefix not in iface.ipv6_ra_prefixes:
                            iface.ipv6_ra_prefixes.append(prefix)
                            ctx.dirty = True
                            log(f"Added RA prefix {prefix} to {iface.vpp_name}")
                        return True
                    if prefix_cmd == "remove" and len(args) >= 5:
                        prefix = args[4]
                        if prefix in iface.ipv6_ra_prefixes:
                            iface.ipv6_ra_prefixes.remove(prefix)
                            ctx.dirty = True
                            log(f"Removed RA prefix {prefix} from {iface.vpp_name}")
                        return True
                    if prefix_cmd == "clear":
                        iface.ipv6_ra_prefixes.clear()
                        ctx.dirty = True
                        log(f"Cleared custom RA prefixes on {iface.vpp_name}")
                        return True
        # Check for external interface with ospf command: "interfaces external ospf area 0"
        if subcommand == "external" and ctx.config.external and len(args) >= 2:
            if args[1].lower() == "ospf" and len(args) >= 4 and args[2].lower() == "area":
                try:
                    area = int(args[3])
                    ctx.config.external.ospf_area = area
                    ctx.dirty = True
                    log(f"Set external OSPF area to {area}")
                    return True
                except ValueError:
                    error("Invalid area number")
                    return True
            if args[1].lower() == "ospf" and len(args) >= 3 and args[2].lower() == "passive":
                ctx.config.external.ospf_passive = True
                ctx.dirty = True
                log(f"Set external as OSPF passive")
                return True
            if args[1].lower() == "ospf6" and len(args) >= 4 and args[2].lower() == "area":
                try:
                    area = int(args[3])
                    ctx.config.external.ospf6_area = area
                    ctx.dirty = True
                    log(f"Set external OSPFv3 area to {area}")
                    return True
                except ValueError:
                    error("Invalid area number")
                    return True
            if args[1].lower() == "ospf6" and len(args) >= 3 and args[2].lower() == "passive":
                ctx.config.external.ospf6_passive = True
                ctx.dirty = True
                log(f"Set external as OSPFv3 passive")
                return True
            # IPv6 RA commands for external interface
            if args[1].lower() == "ipv6-ra" and len(args) >= 3:
                ra_cmd = args[2].lower()
                ext = ctx.config.external
                if ra_cmd == "enable":
                    ext.ipv6_ra_enabled = True
                    ctx.dirty = True
                    log(f"Enabled IPv6 RA on external")
                    return True
                if ra_cmd == "disable":
                    ext.ipv6_ra_enabled = False
                    ctx.dirty = True
                    log(f"Disabled IPv6 RA on external")
                    return True
                if ra_cmd == "suppress":
                    ext.ipv6_ra_suppress = True
                    ctx.dirty = True
                    log(f"Suppressed IPv6 RA on external")
                    return True
                if ra_cmd == "no-suppress":
                    ext.ipv6_ra_suppress = False
                    ctx.dirty = True
                    log(f"Enabled IPv6 RA sending on external")
                    return True
                if ra_cmd == "interval" and len(args) >= 5:
                    try:
                        max_int = int(args[3])
                        min_int = int(args[4])
                        ext.ipv6_ra_interval_max = max_int
                        ext.ipv6_ra_interval_min = min_int
                        ctx.dirty = True
                        log(f"Set external RA interval to {max_int}/{min_int}s")
                        return True
                    except ValueError:
                        error("Invalid interval values")
                        return True
                if ra_cmd == "prefix" and len(args) >= 4:
                    prefix_cmd = args[3].lower()
                    if prefix_cmd == "add" and len(args) >= 5:
                        prefix = args[4]
                        if prefix not in ext.ipv6_ra_prefixes:
                            ext.ipv6_ra_prefixes.append(prefix)
                            ctx.dirty = True
                            log(f"Added RA prefix {prefix} to external")
                        return True
                    if prefix_cmd == "remove" and len(args) >= 5:
                        prefix = args[4]
                        if prefix in ext.ipv6_ra_prefixes:
                            ext.ipv6_ra_prefixes.remove(prefix)
                            ctx.dirty = True
                            log(f"Removed RA prefix {prefix} from external")
                        return True
                    if prefix_cmd == "clear":
                        ext.ipv6_ra_prefixes.clear()
                        ctx.dirty = True
                        log(f"Cleared custom RA prefixes on external")
                        return True

    # Multi-word commands from any level: "loopbacks add", "nat mappings", etc.
    if command == "loopbacks" and args:
        subcommand = args[0].lower()
        if subcommand == "list":
            _show_loopbacks(ctx.config)
            return True
        if subcommand == "add":
            cmd_loopback_add(ctx, args[1:])
            return True
        if subcommand == "edit":
            cmd_loopback_edit(ctx, args[1:])
            return True
        if subcommand == "delete":
            cmd_loopback_delete(ctx, args[1:])
            return True
        # Check for loopback instance with ospf command: "loopbacks 0 ospf area 0"
        if ctx.config and subcommand.isdigit():
            instance = int(subcommand)
            loop = next((l for l in ctx.config.loopbacks if l.instance == instance), None)
            if loop and len(args) >= 2:
                if args[1].lower() == "ospf" and len(args) >= 4 and args[2].lower() == "area":
                    try:
                        area = int(args[3])
                        loop.ospf_area = area
                        ctx.dirty = True
                        log(f"Set loop{instance} OSPF area to {area}")
                        return True
                    except ValueError:
                        error("Invalid area number")
                        return True
                if args[1].lower() == "ospf" and len(args) >= 3 and args[2].lower() == "passive":
                    loop.ospf_passive = True
                    ctx.dirty = True
                    log(f"Set loop{instance} as OSPF passive")
                    return True
                if args[1].lower() == "ospf6" and len(args) >= 4 and args[2].lower() == "area":
                    try:
                        area = int(args[3])
                        loop.ospf6_area = area
                        ctx.dirty = True
                        log(f"Set loop{instance} OSPFv3 area to {area}")
                        return True
                    except ValueError:
                        error("Invalid area number")
                        return True
                if args[1].lower() == "ospf6" and len(args) >= 3 and args[2].lower() == "passive":
                    loop.ospf6_passive = True
                    ctx.dirty = True
                    log(f"Set loop{instance} as OSPFv3 passive")
                    return True
                # IPv6 RA commands for loopback
                if args[1].lower() == "ipv6-ra" and len(args) >= 3:
                    ra_cmd = args[2].lower()
                    if ra_cmd == "enable":
                        loop.ipv6_ra_enabled = True
                        ctx.dirty = True
                        log(f"Enabled IPv6 RA on loop{instance}")
                        return True
                    if ra_cmd == "disable":
                        loop.ipv6_ra_enabled = False
                        ctx.dirty = True
                        log(f"Disabled IPv6 RA on loop{instance}")
                        return True
                    if ra_cmd == "suppress":
                        loop.ipv6_ra_suppress = True
                        ctx.dirty = True
                        log(f"Suppressed IPv6 RA on loop{instance}")
                        return True
                    if ra_cmd == "no-suppress":
                        loop.ipv6_ra_suppress = False
                        ctx.dirty = True
                        log(f"Enabled IPv6 RA sending on loop{instance}")
                        return True
                    if ra_cmd == "interval" and len(args) >= 5:
                        try:
                            max_int = int(args[3])
                            min_int = int(args[4])
                            loop.ipv6_ra_interval_max = max_int
                            loop.ipv6_ra_interval_min = min_int
                            ctx.dirty = True
                            log(f"Set loop{instance} RA interval to {max_int}/{min_int}s")
                            return True
                        except ValueError:
                            error("Invalid interval values")
                            return True
                    if ra_cmd == "prefix" and len(args) >= 4:
                        prefix_cmd = args[3].lower()
                        if prefix_cmd == "add" and len(args) >= 5:
                            prefix = args[4]
                            if prefix not in loop.ipv6_ra_prefixes:
                                loop.ipv6_ra_prefixes.append(prefix)
                                ctx.dirty = True
                                log(f"Added RA prefix {prefix} to loop{instance}")
                            return True
                        if prefix_cmd == "remove" and len(args) >= 5:
                            prefix = args[4]
                            if prefix in loop.ipv6_ra_prefixes:
                                loop.ipv6_ra_prefixes.remove(prefix)
                                ctx.dirty = True
                                log(f"Removed RA prefix {prefix} from loop{instance}")
                            return True
                        if prefix_cmd == "clear":
                            loop.ipv6_ra_prefixes.clear()
                            ctx.dirty = True
                            log(f"Cleared custom RA prefixes on loop{instance}")
                            return True

    if command == "bvi" and args:
        subcommand = args[0].lower()
        if subcommand == "list":
            _show_bvi(ctx.config)
            return True
        if subcommand == "add":
            cmd_bvi_add(ctx, args[1:])
            return True
        if subcommand == "delete":
            cmd_bvi_delete(ctx, args[1:])
            return True
        # Check for BVI instance with ospf command: "bvi 1 ospf area 0"
        if ctx.config and subcommand.isdigit():
            bridge_id = int(subcommand)
            bvi = next((b for b in ctx.config.bvi_domains if b.bridge_id == bridge_id), None)
            if bvi and len(args) >= 2:
                if args[1].lower() == "ospf" and len(args) >= 4 and args[2].lower() == "area":
                    try:
                        area = int(args[3])
                        bvi.ospf_area = area
                        ctx.dirty = True
                        log(f"Set BVI {bridge_id} OSPF area to {area}")
                        return True
                    except ValueError:
                        error("Invalid area number")
                        return True
                if args[1].lower() == "ospf" and len(args) >= 3 and args[2].lower() == "passive":
                    bvi.ospf_passive = True
                    ctx.dirty = True
                    log(f"Set BVI {bridge_id} as OSPF passive")
                    return True
                if args[1].lower() == "ospf6" and len(args) >= 4 and args[2].lower() == "area":
                    try:
                        area = int(args[3])
                        bvi.ospf6_area = area
                        ctx.dirty = True
                        log(f"Set BVI {bridge_id} OSPFv3 area to {area}")
                        return True
                    except ValueError:
                        error("Invalid area number")
                        return True
                if args[1].lower() == "ospf6" and len(args) >= 3 and args[2].lower() == "passive":
                    bvi.ospf6_passive = True
                    ctx.dirty = True
                    log(f"Set BVI {bridge_id} as OSPFv3 passive")
                    return True
                # IPv6 RA commands for BVI
                if args[1].lower() == "ipv6-ra" and len(args) >= 3:
                    ra_cmd = args[2].lower()
                    if ra_cmd == "enable":
                        bvi.ipv6_ra_enabled = True
                        ctx.dirty = True
                        log(f"Enabled IPv6 RA on BVI {bridge_id}")
                        return True
                    if ra_cmd == "disable":
                        bvi.ipv6_ra_enabled = False
                        ctx.dirty = True
                        log(f"Disabled IPv6 RA on BVI {bridge_id}")
                        return True
                    if ra_cmd == "suppress":
                        bvi.ipv6_ra_suppress = True
                        ctx.dirty = True
                        log(f"Suppressed IPv6 RA on BVI {bridge_id}")
                        return True
                    if ra_cmd == "no-suppress":
                        bvi.ipv6_ra_suppress = False
                        ctx.dirty = True
                        log(f"Enabled IPv6 RA sending on BVI {bridge_id}")
                        return True
                    if ra_cmd == "interval" and len(args) >= 5:
                        try:
                            max_int = int(args[3])
                            min_int = int(args[4])
                            bvi.ipv6_ra_interval_max = max_int
                            bvi.ipv6_ra_interval_min = min_int
                            ctx.dirty = True
                            log(f"Set BVI {bridge_id} RA interval to {max_int}/{min_int}s")
                            return True
                        except ValueError:
                            error("Invalid interval values")
                            return True
                    if ra_cmd == "prefix" and len(args) >= 4:
                        prefix_cmd = args[3].lower()
                        if prefix_cmd == "add" and len(args) >= 5:
                            prefix = args[4]
                            if prefix not in bvi.ipv6_ra_prefixes:
                                bvi.ipv6_ra_prefixes.append(prefix)
                                ctx.dirty = True
                                log(f"Added RA prefix {prefix} to BVI {bridge_id}")
                            return True
                        if prefix_cmd == "remove" and len(args) >= 5:
                            prefix = args[4]
                            if prefix in bvi.ipv6_ra_prefixes:
                                bvi.ipv6_ra_prefixes.remove(prefix)
                                ctx.dirty = True
                                log(f"Removed RA prefix {prefix} from BVI {bridge_id}")
                            return True
                        if prefix_cmd == "clear":
                            bvi.ipv6_ra_prefixes.clear()
                            ctx.dirty = True
                            log(f"Cleared custom RA prefixes on BVI {bridge_id}")
                            return True

    if command == "vlan-passthrough" and args:
        subcommand = args[0].lower()
        if subcommand == "list":
            _show_vlan_passthrough(ctx.config)
            return True
        if subcommand == "add":
            cmd_vlan_passthrough_add(ctx, args[1:])
            return True
        if subcommand == "delete":
            cmd_vlan_passthrough_delete(ctx, args[1:])
            return True

    # BGP multi-word commands: "routing bgp enable", "routing bgp peers add", etc.
    if command == "routing" and args:
        subcommand = args[0].lower()
        if subcommand == "bgp":
            if len(args) > 1:
                subcmd = args[1].lower()
                if subcmd == "enable":
                    cmd_bgp_enable(ctx, args[2:])
                    return True
                if subcmd == "disable":
                    cmd_bgp_disable(ctx, args[2:])
                    return True
                if subcmd == "show":
                    _show_bgp(ctx.config)
                    return True
                if subcmd == "peers":
                    if len(args) > 2:
                        peers_cmd = args[2].lower()
                        if peers_cmd == "list":
                            cmd_bgp_peers_list(ctx, args[3:])
                            return True
                        if peers_cmd == "add":
                            cmd_bgp_peers_add(ctx, args[3:])
                            return True
                        if peers_cmd == "remove":
                            cmd_bgp_peers_remove(ctx, args[3:])
                            return True
                    # Just "routing bgp peers" - navigate there
                    ctx.path = ["routing", "bgp", "peers"]
                    return True
            # Just "routing bgp" - navigate there
            ctx.path = ["routing", "bgp"]
            return True
        if subcommand == "ospf":
            if len(args) > 1:
                subcmd = args[1].lower()
                if subcmd == "enable":
                    cmd_ospf_enable(ctx, args[2:])
                    return True
                if subcmd == "disable":
                    cmd_ospf_disable(ctx, args[2:])
                    return True
                if subcmd == "show":
                    _show_ospf(ctx.config)
                    return True
            # Just "routing ospf" - navigate there
            ctx.path = ["routing", "ospf"]
            return True
        if subcommand == "ospf6":
            if len(args) > 1:
                subcmd = args[1].lower()
                if subcmd == "enable":
                    cmd_ospf6_enable(ctx, args[2:])
                    return True
                if subcmd == "disable":
                    cmd_ospf6_disable(ctx, args[2:])
                    return True
                if subcmd == "show":
                    _show_ospf6(ctx.config)
                    return True
            # Just "routing ospf6" - navigate there
            ctx.path = ["routing", "ospf6"]
            return True

    # Snapshot multi-word commands: "snapshot list", "snapshot create", etc.
    if command == "snapshot" and args:
        subcommand = args[0].lower()
        if subcommand == "list":
            cmd_snapshot_list(ctx, args[1:])
            return True
        if subcommand == "create":
            cmd_snapshot_create(ctx, args[1:])
            return True
        if subcommand == "delete":
            cmd_snapshot_delete(ctx, args[1:])
            return True
        if subcommand == "export":
            cmd_snapshot_export(ctx, args[1:])
            return True
        if subcommand in ("import", "receive"):
            cmd_snapshot_import(ctx, args[1:])
            return True
        if subcommand == "rollback":
            cmd_snapshot_rollback(ctx, args[1:])
            return True

    # Agent command - can be invoked from any level
    if command == "agent":
        cmd_agent(ctx, args)
        return True

    # Loopback commands (under config)
    if config_path == ["loopbacks"]:
        if command == "list":
            _show_loopbacks(ctx.config)
            return True
        if command == "add":
            cmd_loopback_add(ctx, args)
            return True
        if command == "edit":
            cmd_loopback_edit(ctx, args)
            return True
        if command == "delete":
            cmd_loopback_delete(ctx, args)
            return True

    # BVI commands (under config)
    if config_path == ["bvi"]:
        if command == "list":
            _show_bvi(ctx.config)
            return True
        if command == "add":
            cmd_bvi_add(ctx, args)
            return True
        if command == "delete":
            cmd_bvi_delete(ctx, args)
            return True

    # VLAN passthrough commands (under config)
    if config_path == ["vlan-passthrough"]:
        if command == "list":
            _show_vlan_passthrough(ctx.config)
            return True
        if command == "add":
            cmd_vlan_passthrough_add(ctx, args)
            return True
        if command == "delete":
            cmd_vlan_passthrough_delete(ctx, args)
            return True

    # Sub-interface commands (for external and internal interfaces, under config)
    if len(config_path) >= 3 and config_path[-1] == "subinterfaces":
        if command == "list":
            parent, parent_name = _get_parent_interface(ctx)
            if parent:
                _show_subinterfaces(parent.subinterfaces, parent_name)
            return True
        if command == "add":
            cmd_subinterface_add(ctx, args)
            return True
        if command == "delete":
            cmd_subinterface_delete(ctx, args)
            return True

    # Modules commands (under config)
    if config_path == ["modules"]:
        if command == "available":
            cmd_modules_available(ctx, args)
            return True
        if command == "list":
            cmd_modules_list(ctx, args)
            return True
        if command == "install":
            cmd_modules_install(ctx, args)
            return True
        if command == "enable":
            cmd_modules_enable(ctx, args)
            return True
        if command == "disable":
            cmd_modules_disable(ctx, args)
            return True

    # Multi-word shortcuts for modules: "config modules install nat", "config modules nat mappings add"
    if command == "modules" and args:
        subcmd = args[0].lower()
        if subcmd == "available":
            cmd_modules_available(ctx, args[1:])
            return True
        if subcmd == "list":
            cmd_modules_list(ctx, args[1:])
            return True
        if subcmd == "install":
            cmd_modules_install(ctx, args[1:])
            return True
        if subcmd == "enable":
            cmd_modules_enable(ctx, args[1:])
            return True
        if subcmd == "disable":
            cmd_modules_disable(ctx, args[1:])
            return True

        # Module-specific commands: "modules nat mappings add", "modules nat set-prefix"
        if MODULE_LOADER_AVAILABLE:
            module_name = subcmd  # e.g., "nat"
            module_cmds = get_module_commands(module_name)
            if module_cmds:
                # Build command path from remaining args
                # e.g., args=["nat", "mappings", "add"] -> cmd_path="mappings/add"
                # e.g., args=["nat", "set-prefix"] -> cmd_path="set-prefix"
                remaining_args = args[1:]
                if remaining_args:
                    cmd_path = "/".join(remaining_args)
                    for mod_cmd in module_cmds:
                        if mod_cmd.path == cmd_path:
                            execute_module_command(ctx, module_name, mod_cmd)
                            return True
                else:
                    # Just "modules nat" - navigate there
                    ctx.path = ["modules", module_name]
                    return True

    # Generic module commands - config modules <module-name> <command>
    # e.g., config_path=["modules", "nat"], command="set-prefix" -> "set-prefix"
    # e.g., config_path=["modules", "nat", "mappings"], command="add" -> "mappings/add"
    if len(config_path) >= 2 and config_path[0] == "modules" and MODULE_LOADER_AVAILABLE:
        module_name = config_path[1]
        # Try to load module commands
        module_cmds = get_module_commands(module_name)
        if module_cmds:
            # Build command path from remaining path elements + command
            path_parts = config_path[2:] + [command]
            cmd_path = "/".join(path_parts)

            # Find matching command
            for mod_cmd in module_cmds:
                if mod_cmd.path == cmd_path:
                    execute_module_command(ctx, module_name, mod_cmd)
                    return True

            # Also try without command if command is a subpath
            # e.g., "mappings" command with "add" arg -> try "mappings/add"
            if args:
                cmd_path_with_arg = "/".join(config_path[2:] + [command, args[0]])
                for mod_cmd in module_cmds:
                    if mod_cmd.path == cmd_path_with_arg:
                        execute_module_command(ctx, module_name, mod_cmd)
                        return True

    # BGP commands (under config)
    if config_path == ["routing", "bgp"]:
        if command == "enable":
            cmd_bgp_enable(ctx, args)
            return True
        if command == "disable":
            cmd_bgp_disable(ctx, args)
            return True
        # Handle "peers add" when in routing/bgp
        if command == "peers" and args:
            peers_cmd = args[0].lower()
            if peers_cmd == "list":
                cmd_bgp_peers_list(ctx, args[1:])
                return True
            if peers_cmd == "add":
                cmd_bgp_peers_add(ctx, args[1:])
                return True
            if peers_cmd == "remove":
                cmd_bgp_peers_remove(ctx, args[1:])
                return True

    # BGP peer commands (when navigated to config/routing/bgp/peers)
    if config_path == ["routing", "bgp", "peers"]:
        if command == "list":
            cmd_bgp_peers_list(ctx, args)
            return True
        if command == "add":
            cmd_bgp_peers_add(ctx, args)
            return True
        if command == "remove":
            cmd_bgp_peers_remove(ctx, args)
            return True

    # OSPF commands (under config)
    if config_path == ["routing", "ospf"]:
        if command == "enable":
            cmd_ospf_enable(ctx, args)
            return True
        if command == "disable":
            cmd_ospf_disable(ctx, args)
            return True

    # OSPFv3 commands (under config)
    if config_path == ["routing", "ospf6"]:
        if command == "enable":
            cmd_ospf6_enable(ctx, args)
            return True
        if command == "disable":
            cmd_ospf6_disable(ctx, args)
            return True

    # Snapshot commands (when in snapshot menu)
    if path == ["snapshot"]:
        if command == "list":
            cmd_snapshot_list(ctx, args)
            return True
        if command == "create":
            cmd_snapshot_create(ctx, args)
            return True
        if command == "delete":
            cmd_snapshot_delete(ctx, args)
            return True
        if command == "export":
            cmd_snapshot_export(ctx, args)
            return True
        if command in ("import", "receive"):
            cmd_snapshot_import(ctx, args)
            return True
        if command == "rollback":
            cmd_snapshot_rollback(ctx, args)
            return True

    # Try navigation
    if navigate(ctx, command, menus):
        return True

    warn(f"Unknown command: {command}")
    print("Type 'help' for available commands")
    return True


# =============================================================================
# Main REPL Loop
# =============================================================================

def run_repl() -> int:
    """Main REPL entry point."""
    print()
    print(f"{Colors.BOLD}IMP Configuration Manager{Colors.NC}")
    print("Type 'help' for commands, 'exit' to quit")
    print()

    # Initialize context
    ctx = MenuContext()

    # Try to load existing config
    if CONFIG_AVAILABLE and CONFIG_FILE.exists():
        try:
            ctx.config = load_config(CONFIG_FILE)
            ctx.original_json = json.dumps(asdict(ctx.config), sort_keys=True)
            info(f"Loaded configuration from {CONFIG_FILE}")
        except Exception as e:
            warn(f"Failed to load config: {e}")
            ctx.config = None
    elif CONFIG_AVAILABLE:
        info(f"No configuration file found at {CONFIG_FILE}")
        info("Use 'imp config edit' to create initial configuration")
    else:
        warn("Configuration module not available (development mode)")

    # Build menu tree
    menus = build_menu_tree()

    # Create prompt session
    history_file = Path.home() / ".imp_history"
    completer = MenuCompleter(ctx, menus)

    session = PromptSession(
        history=FileHistory(str(history_file)),
        completer=completer,
        style=IMP_STYLE,
    )

    # Main loop
    while True:
        try:
            prompt = get_prompt_text(ctx)
            cmd = session.prompt(prompt)

            if not handle_command(cmd, ctx, menus):
                break

        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            print()
            if ctx.dirty:
                if prompt_yes_no("Discard unsaved changes?"):
                    break
            else:
                break

    print("Goodbye!")
    return 0


if __name__ == "__main__":
    sys.exit(run_repl())
