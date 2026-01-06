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
from dataclasses import dataclass, field, asdict
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

# Import configuration classes from configure-router
# Also add script directory for live_config import
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, '/usr/local/bin')
try:
    from configure_router import (
        RouterConfig, ExternalInterface, InternalInterface, ManagementInterface,
        SubInterface, LoopbackInterface, BVIConfig, BridgeDomainMember,
        VLANPassthrough, BGPConfig, BGPPeer, OSPFConfig, OSPF6Config,
        NATConfig, NATMapping, ACLBypassPair,
        ContainerConfig, CPUConfig,
        validate_ipv4, validate_ipv4_cidr, validate_ipv6, validate_ipv6_cidr,
        parse_cidr, render_templates, apply_configs, save_config, load_config,
        TEMPLATE_DIR, CONFIG_FILE, GENERATED_DIR
    )
    CONFIG_AVAILABLE = True
except ImportError:
    # Fallback for development/testing without full install
    CONFIG_FILE = Path("/persistent/config/router.json")
    TEMPLATE_DIR = Path("/etc/imp/templates")
    GENERATED_DIR = Path("/tmp/imp-generated-config")
    CONFIG_AVAILABLE = False

# Import module system
try:
    from module_loader import (
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
except ImportError:
    MODULE_LOADER_AVAILABLE = False
    MODULE_DEFINITIONS_DIR = Path("/persistent/config/modules")
    MODULE_EXAMPLES_DIR = Path("/usr/share/imp/module-examples")
    ModuleCommand = None
    ModuleCommandParam = None


def get_nat_config(config):
    """Get NAT config from modules list, returns dict or None."""
    if not config or not hasattr(config, 'modules') or not config.modules:
        return None
    for module in config.modules:
        if module.get('name') == 'nat' and module.get('enabled', False):
            return module.get('config', {})
    return None


def find_module(config, name: str):
    """Find module dict by name in config.modules list."""
    if not config or not hasattr(config, 'modules') or not config.modules:
        return None
    for module in config.modules:
        if module.get('name') == name:
            return module
    return None


def ensure_nat_module(config) -> dict:
    """Ensure NAT module exists in config.modules, return module dict."""
    if not hasattr(config, 'modules'):
        config.modules = []

    nat_module = find_module(config, 'nat')
    if not nat_module:
        nat_module = {
            'name': 'nat',
            'enabled': True,
            'config': {
                'bgp_prefix': '',
                'mappings': [],
                'bypass_pairs': []
            }
        }
        config.modules.append(nat_module)
    elif not nat_module.get('enabled'):
        nat_module['enabled'] = True

    # Ensure config dict exists
    if 'config' not in nat_module:
        nat_module['config'] = {'bgp_prefix': '', 'mappings': [], 'bypass_pairs': []}

    return nat_module


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

    # Check for duplicate (use first param as key if no explicit key)
    key_field = cmd.key or (cmd.params[0].name if cmd.params else None)
    if key_field and key_field in item:
        key_val = item[key_field]
        if any(existing.get(key_field) == key_val for existing in target_array):
            print(f"{Colors.RED}[!] Entry with {key_field}={key_val} already exists{Colors.NC}")
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

IMP_STYLE = Style.from_dict({
    'prompt': '#00aa00 bold',
    'prompt.path': '#0088ff',
    'info': '#888888',
    'warning': '#ffaa00',
    'error': '#ff0000 bold',
    'success': '#00ff00',
})


class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    NC = "\033[0m"


def log(msg: str) -> None:
    print(f"{Colors.GREEN}[+]{Colors.NC} {msg}")


def warn(msg: str) -> None:
    print(f"{Colors.YELLOW}[!]{Colors.NC} {msg}")


def error(msg: str) -> None:
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")


def info(msg: str) -> None:
    print(f"{Colors.CYAN}[i]{Colors.NC} {msg}")


# =============================================================================
# Menu Context
# =============================================================================

@dataclass
class MenuContext:
    """Tracks current position in menu hierarchy and configuration state."""
    path: list[str] = field(default_factory=list)
    config: Optional[Any] = None  # RouterConfig when available
    dirty: bool = False
    original_json: str = ""  # For detecting changes


def get_prompt_text(ctx: MenuContext) -> str:
    """Generate the prompt string based on current menu path."""
    if ctx.path:
        path_str = ".".join(ctx.path)
        dirty_marker = "*" if ctx.dirty else ""
        return f"imp.{path_str}{dirty_marker}> "
    else:
        dirty_marker = "*" if ctx.dirty else ""
        return f"imp{dirty_marker}> "


# =============================================================================
# Dynamic Completer
# =============================================================================

class MenuCompleter(Completer):
    """Dynamic completer that provides context-aware completions."""

    def __init__(self, ctx: MenuContext, menus: dict):
        self.ctx = ctx
        self.menus = menus

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        # Determine what we're completing
        if not words:
            # Empty input - show current menu completions
            completions = self._get_menu_completions([])
            word = ""
        elif text.endswith(' '):
            # User typed a word and space - complete subcommands
            completions = self._get_menu_completions(words)
            word = ""
        else:
            # User is typing a word - complete from parent context
            completions = self._get_menu_completions(words[:-1])
            word = words[-1].lower()

        # Yield matching completions
        for item in completions:
            if item.lower().startswith(word):
                yield Completion(item, start_position=-len(word))

    def _get_menu_at_path(self, path: list[str]):
        """Navigate to a menu based on path segments."""
        menu = self.menus.get("root")
        for segment in path:
            if menu and "children" in menu:
                menu = menu["children"].get(segment)
            else:
                return None
        return menu

    def _get_menu_completions(self, cmd_prefix: list[str]) -> list[str]:
        """Get available commands and submenus for given context.

        Args:
            cmd_prefix: Additional path segments typed on command line
        """
        completions = []

        # Build effective path: current menu path + typed command prefix
        effective_path = self.ctx.path + cmd_prefix

        # Handle "show" command completions (it's a global command, not a menu)
        if not self.ctx.path or self.ctx.path[0] != "config":
            # At root or non-config menu - show is for live state
            if cmd_prefix == ["show"]:
                return ["interfaces", "ip", "ipv6", "neighbors", "sessions", "bgp", "ospf", "config"]
            if cmd_prefix == ["show", "ip"]:
                return ["route", "fib"]
            if cmd_prefix == ["show", "ipv6"]:
                return ["route", "fib"]
            if cmd_prefix == ["show", "config"]:
                return ["interfaces", "loopbacks", "bvi", "vlan-passthrough", "routing", "nat", "containers", "cpu"]

        # Navigate to the menu at this effective path
        menu = self._get_menu_at_path(effective_path)

        # If we couldn't navigate there, no completions
        if menu is None and cmd_prefix:
            return []

        # Only show global commands at the current menu level (not when completing subcommands)
        if not cmd_prefix:
            # Commands available everywhere
            base_commands = ["help", "exit"]

            # Navigation commands only when not at root
            if self.ctx.path:
                base_commands.extend(["back", "home"])

            # Root-only commands
            if not self.ctx.path:
                base_commands.extend(["show", "status", "reload"])
                # Apply only when there are unsaved changes
                if self.ctx.dirty:
                    base_commands.append("apply")
            # At config level, show is for viewing config sections
            elif self.ctx.path == ["config"]:
                base_commands.append("show")

            completions.extend(base_commands)

        if menu:
            # Menu-specific commands from static menu definition
            if "commands" in menu:
                completions.extend(menu["commands"])

            # Child menus from static menu definition
            if "children" in menu:
                completions.extend(menu["children"].keys())

        # Dynamic completions based on effective path
        if effective_path == ["config", "interfaces", "internal"] and self.ctx.config:
            # Add internal interface names
            completions.extend(i.vpp_name for i in self.ctx.config.internal)

        if len(effective_path) == 4 and effective_path[:3] == ["config", "interfaces", "internal"]:
            # Add subinterfaces submenu and OSPF commands for internal interfaces
            completions.append("subinterfaces")
            completions.extend(["show", "ospf", "ospf6"])

        if effective_path == ["config", "interfaces", "external"]:
            # Add OSPF commands for external interface
            completions.extend(["ospf", "ospf6"])

        if len(effective_path) >= 4 and effective_path[-1] == "subinterfaces":
            # Add sub-interface commands
            completions.extend(["list", "add", "delete"])

        if len(effective_path) == 3 and effective_path[:2] == ["config", "loopbacks"]:
            # After "delete" or "edit", show loopback instance numbers
            if effective_path[2] in ("delete", "edit") and self.ctx.config:
                for lo in self.ctx.config.loopbacks:
                    completions.append(str(lo.instance))

        if len(effective_path) == 4 and effective_path[:2] == ["config", "loopbacks"]:
            # After selecting a loopback instance, show OSPF commands
            if effective_path[2] not in ("delete", "edit", "add"):
                completions.extend(["ospf", "ospf6"])

        if len(effective_path) == 3 and effective_path[:2] == ["config", "bvi"]:
            # After "delete" or "edit", show BVI bridge IDs
            if effective_path[2] in ("delete", "edit") and self.ctx.config:
                for bvi in self.ctx.config.bvi_domains:
                    completions.append(str(bvi.bridge_id))

        if len(effective_path) == 4 and effective_path[:2] == ["config", "bvi"]:
            # After selecting a BVI, show OSPF commands
            if effective_path[2] not in ("delete", "edit", "add"):
                completions.extend(["ospf", "ospf6"])

        if len(effective_path) == 3 and effective_path[:2] == ["config", "vlan-passthrough"]:
            # After "delete", show VLAN IDs
            if effective_path[2] == "delete" and self.ctx.config:
                for v in self.ctx.config.vlan_passthrough:
                    completions.append(str(v.vlan_id))

        return list(set(completions))  # Remove duplicates


# =============================================================================
# Menu Definitions
# =============================================================================

def build_menu_tree() -> dict:
    """Build the hierarchical menu structure."""
    return {
        "root": {
            "children": {
                # Configuration submenu - all config items moved here
                "config": {
                    "children": {
                        "interfaces": {
                            "children": {
                                "management": {"commands": ["show", "set-dhcp", "set-static"]},
                                "external": {
                                    "children": {
                                        "subinterfaces": {"commands": ["list", "add", "edit", "delete"]},
                                    },
                                    "commands": ["show", "set"],
                                },
                                "internal": {
                                    "commands": ["list"],
                                    "dynamic": True,  # Children are generated from config
                                },
                            },
                            "commands": ["show"],
                        },
                        "loopbacks": {
                            "commands": ["list", "add", "edit", "delete"],
                        },
                        "bvi": {
                            "commands": ["list", "add", "edit", "delete"],
                        },
                        "vlan-passthrough": {
                            "commands": ["list", "add", "delete"],
                        },
                        "routing": {
                            "children": {
                                "bgp": {
                                    "commands": ["show", "enable", "disable"],
                                    "children": {
                                        "peers": {"commands": ["list", "add", "remove"]},
                                    },
                                },
                                "ospf": {"commands": ["show", "enable", "disable", "set"]},
                                "ospf6": {"commands": ["show", "enable", "disable", "set"]},
                            },
                            "commands": ["show"],
                        },
                        "modules": {
                            "commands": ["available", "list", "install", "enable", "disable"],
                        },
                        "nat": {
                            "children": {
                                "mappings": {"commands": ["list", "add", "delete"]},
                                "bypass": {"commands": ["list", "add", "delete"]},
                            },
                            "commands": ["show", "set-prefix"],
                        },
                        "containers": {
                            "commands": ["show", "set"],
                        },
                        "cpu": {
                            "commands": ["show"],
                        },
                    },
                    "commands": ["show"],
                },
                # Operational commands remain at root
                "shell": {
                    "children": {
                        "routing": {"commands": []},
                        "core": {"commands": []},
                        "nat": {"commands": []},
                    },
                    "commands": [],
                },
                "capture": {
                    "commands": ["start", "stop", "status", "files", "analyze", "export", "delete"],
                },
                "trace": {
                    "commands": ["start", "stop", "status", "show", "clear"],
                },
                "snapshot": {
                    "commands": ["list", "create", "delete", "export", "import", "rollback"],
                },
                "agent": {
                    "commands": [],
                },
            },
            "commands": ["show", "status"],
        }
    }


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

        if config.external:
            ext = config.external
            print(f"  External:    {ext.iface} -> {ext.ipv4}/{ext.ipv4_prefix}")
            if ext.subinterfaces:
                print(f"               + {len(ext.subinterfaces)} sub-interface(s)")

        if config.internal:
            for iface in config.internal:
                print(f"  Internal:    {iface.iface} ({iface.vpp_name}) -> {iface.ipv4}/{iface.ipv4_prefix}")
                if iface.subinterfaces:
                    print(f"               + {len(iface.subinterfaces)} sub-interface(s)")

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

    elif path == ["interfaces", "external"]:
        _show_external(config)

    elif path == ["interfaces", "external", "subinterfaces"]:
        _show_subinterfaces(config.external.subinterfaces if config.external else [], "external")

    elif path == ["interfaces", "management"]:
        _show_management(config)

    elif path == ["interfaces", "internal"]:
        _show_internal_list(config)

    elif len(path) >= 3 and path[0] == "interfaces" and path[1] == "internal":
        iface_name = path[2]
        iface = next((i for i in config.internal if i.vpp_name == iface_name), None)
        if iface:
            if len(path) == 3:
                _show_internal_detail(iface)
            elif path[3] == "subinterfaces":
                _show_subinterfaces(iface.subinterfaces, iface.vpp_name)

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

    elif path == ["nat"]:
        _show_nat(config)

    elif path == ["nat", "mappings"]:
        _show_nat_mappings(config)

    elif path == ["nat", "bypass"]:
        _show_nat_bypass(config)

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
        print("  sessions            - NAT session table")
        print("  bgp                 - BGP neighbor status")
        print("  ospf                - OSPF neighbor status")
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
    elif target == "sessions":
        _show_live_nat_sessions()
    elif target == "bgp":
        _show_live_bgp()
    elif target == "ospf":
        _show_live_ospf()
    else:
        warn(f"Unknown show target: {target}")
        print("Use 'show' for available options")


def _show_live_interfaces() -> None:
    """Show live VPP interface state."""
    print()
    print(f"{Colors.BOLD}VPP Interfaces (Live){Colors.NC}")
    print("=" * 70)

    success, output = vpp_exec("show interface", "core")
    if success:
        print(output)
    else:
        error(f"Failed to get interfaces: {output}")
    print()


def _pager(content: str, title: str = "") -> None:
    """Display content with paging if it exceeds terminal height."""
    import shutil
    import pydoc

    # Get terminal size
    term_size = shutil.get_terminal_size((80, 24))
    lines = content.split('\n')

    # If content fits in terminal, just print it
    if len(lines) <= term_size.lines - 5:  # Leave room for prompt
        if title:
            print()
            print(f"{Colors.BOLD}{title}{Colors.NC}")
            print("=" * 70)
        print(content)
        print()
    else:
        # Use pager for long output
        full_content = f"{title}\n{'=' * 70}\n{content}" if title else content
        pydoc.pager(full_content)


def _show_live_route(af: str, prefix: str = None) -> None:
    """Show routing table from FRR.

    Args:
        af: Address family ("ip" or "ipv6")
        prefix: Optional prefix filter (e.g., "192.168.0.0/16" shows all longer prefixes)
    """
    if af == "ip":
        if prefix:
            cmd = f"show ip route {prefix} longer-prefixes"
        else:
            cmd = "show ip route"
        af_name = "IPv4"
    else:
        if prefix:
            cmd = f"show ipv6 route {prefix} longer-prefixes"
        else:
            cmd = "show ipv6 route"
        af_name = "IPv6"

    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", cmd],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        title = f"{af_name} Routing Table (FRR)"
        if prefix:
            title += f" - filter: {prefix}"
        _pager(result.stdout, title)
    else:
        error(f"Failed to get {af_name} routes (FRR may not be running)")


def _filter_fib_output(output: str, filter_prefix: str, is_ipv6: bool = False) -> str:
    """Filter VPP FIB output to entries within a given prefix.

    VPP's native 'show ip fib <prefix>' performs a longest-match lookup,
    returning the covering route. This function instead filters to show
    all entries that fall within the specified prefix (like FRR's
    'longer-prefixes' option).

    Args:
        output: Raw VPP FIB output
        filter_prefix: Prefix to filter by (e.g., "10.0.0.0/8")
        is_ipv6: True for IPv6, False for IPv4

    Returns:
        Filtered FIB output containing only matching entries
    """
    try:
        filter_net = ipaddress.ip_network(filter_prefix, strict=False)
    except ValueError:
        return output  # Invalid filter, return unfiltered

    # Regex to match FIB entry prefixes at start of line
    # IPv4: 10.0.0.0/8, 192.168.1.0/24, etc.
    # IPv6: 2001:db8::/32, ::1/128, etc.
    # The prefix may be alone on the line or followed by whitespace
    if is_ipv6:
        prefix_pattern = re.compile(r'^([0-9a-fA-F:]+/\d+)(?:\s|$)')
    else:
        prefix_pattern = re.compile(r'^(\d+\.\d+\.\d+\.\d+/\d+)(?:\s|$)')

    lines = output.split('\n')
    result_lines = []
    current_entry = []
    current_prefix = None
    include_current = False
    header_lines = []

    for line in lines:
        # Check if this line starts a new FIB entry
        match = prefix_pattern.match(line)
        if match:
            # Save previous entry if it matched
            if include_current and current_entry:
                result_lines.extend(current_entry)

            # Start new entry
            current_prefix = match.group(1)
            current_entry = [line]

            # Check if this prefix is within our filter
            try:
                entry_net = ipaddress.ip_network(current_prefix, strict=False)
                # Include if entry is equal to or more specific than filter
                include_current = (
                    entry_net.network_address >= filter_net.network_address and
                    entry_net.broadcast_address <= filter_net.broadcast_address
                )
            except ValueError:
                include_current = False
        elif current_prefix is not None:
            # Continuation of current entry (indented lines)
            current_entry.append(line)
        else:
            # Header line (before first entry)
            header_lines.append(line)

    # Don't forget the last entry
    if include_current and current_entry:
        result_lines.extend(current_entry)

    if result_lines:
        return '\n'.join(header_lines + result_lines)
    else:
        return f"No FIB entries within {filter_prefix}"


def _show_live_fib(af: str, prefix: str = None) -> None:
    """Show forwarding table from VPP.

    Args:
        af: Address family ("ip" or "ipv6")
        prefix: Optional prefix filter (e.g., "192.168.0.0/16")
    """
    if af == "ip":
        # Always fetch all entries, filter client-side if needed
        cmd = "show ip fib"
        af_name = "IPv4"
        is_ipv6 = False
    else:
        cmd = "show ip6 fib"  # VPP uses ip6, not ipv6
        af_name = "IPv6"
        is_ipv6 = True

    success, output = vpp_exec(cmd, "core")
    if success:
        if prefix:
            output = _filter_fib_output(output, prefix, is_ipv6)
        title = f"{af_name} FIB (VPP)"
        if prefix:
            title += f" - filter: {prefix}"
        _pager(output, title)
    else:
        error(f"Failed to get {af_name} FIB: {output}")


def _show_live_neighbors() -> None:
    """Show ARP/NDP neighbor table."""
    print()
    print(f"{Colors.BOLD}Neighbor Table (Live){Colors.NC}")
    print("=" * 70)

    print(f"\n{Colors.CYAN}IPv4 (ARP):{Colors.NC}")
    success, output = vpp_exec("show ip neighbor", "core")
    if success:
        print(output if output.strip() else "  (empty)")

    print(f"\n{Colors.CYAN}IPv6 (NDP):{Colors.NC}")
    success, output = vpp_exec("show ip6 neighbor", "core")
    if success:
        print(output if output.strip() else "  (empty)")
    print()


def _show_live_nat_sessions() -> None:
    """Show NAT session table."""
    print()
    print(f"{Colors.BOLD}NAT Sessions (Live){Colors.NC}")
    print("=" * 70)

    success, output = vpp_exec("show det44 sessions", "nat")
    if success:
        print(output if output.strip() else "  (no active sessions)")
    else:
        error(f"Failed to get sessions: {output}")
    print()


def _show_live_bgp() -> None:
    """Show BGP neighbor status from FRR."""
    print()
    print(f"{Colors.BOLD}BGP Status (Live){Colors.NC}")
    print("=" * 70)

    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", "show ip bgp summary"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout if result.stdout.strip() else "  BGP not running or no peers")
    else:
        error("Failed to get BGP status (FRR may not be running)")
    print()


def _show_live_ospf() -> None:
    """Show OSPF neighbor status from FRR."""
    print()
    print(f"{Colors.BOLD}OSPF Status (Live){Colors.NC}")
    print("=" * 70)

    print(f"\n{Colors.CYAN}OSPFv2:{Colors.NC}")
    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", "show ip ospf neighbor"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout if result.stdout.strip() else "  (no neighbors)")

    print(f"\n{Colors.CYAN}OSPFv3:{Colors.NC}")
    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", "show ipv6 ospf6 neighbor"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout if result.stdout.strip() else "  (no neighbors)")
    print()


def _show_interfaces(config) -> None:
    """Show interfaces summary."""
    print(f"{Colors.BOLD}Interfaces{Colors.NC}")
    print("=" * 50)

    if config.management:
        m = config.management
        if m.mode == "dhcp":
            print(f"  management  {m.iface} (DHCP)")
        else:
            print(f"  management  {m.iface} -> {m.ipv4}/{m.ipv4_prefix}")

    if config.external:
        e = config.external
        print(f"  external    {e.iface} -> {e.ipv4}/{e.ipv4_prefix}")
        for sub in e.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            print(f"    .{sub.vlan_id}: {', '.join(ips)}{lcp}")

    for iface in config.internal:
        print(f"  {iface.vpp_name:<10} {iface.iface} -> {iface.ipv4}/{iface.ipv4_prefix}")
        for sub in iface.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            print(f"    .{sub.vlan_id}: {', '.join(ips)}{lcp}")

    print()


def _show_external(config) -> None:
    """Show external interface details."""
    if not config.external:
        warn("External interface not configured")
        return

    e = config.external
    print(f"{Colors.BOLD}External Interface{Colors.NC}")
    print("=" * 50)
    print(f"  Physical:  {e.iface}")
    print(f"  PCI:       {e.pci}")
    print(f"  IPv4:      {e.ipv4}/{e.ipv4_prefix}")
    print(f"  Gateway:   {e.ipv4_gateway}")
    if e.ipv6:
        print(f"  IPv6:      {e.ipv6}/{e.ipv6_prefix}")
        if e.ipv6_gateway:
            print(f"  Gateway6:  {e.ipv6_gateway}")
    print(f"  Subifs:    {len(e.subinterfaces)}")
    print()
    if e.subinterfaces:
        print("Sub-interfaces:")
        for sub in e.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            print(f"  .{sub.vlan_id}: {', '.join(ips)}{lcp}")
        print()
    print("Type 'subinterfaces' to manage VLAN sub-interfaces")
    print()


def _show_management(config) -> None:
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


def _show_internal_list(config) -> None:
    """Show list of internal interfaces."""
    print(f"{Colors.BOLD}Internal Interfaces{Colors.NC}")
    print("=" * 50)
    if not config.internal:
        print("  (none configured)")
    else:
        for iface in config.internal:
            print(f"  {iface.vpp_name}: {iface.iface} -> {iface.ipv4}/{iface.ipv4_prefix}")
    print()
    print("Enter an interface name to see details (e.g., 'internal0')")
    print()


def _show_internal_detail(iface) -> None:
    """Show internal interface details."""
    print(f"{Colors.BOLD}Internal Interface: {iface.vpp_name}{Colors.NC}")
    print("=" * 50)
    print(f"  Physical:  {iface.iface}")
    print(f"  PCI:       {iface.pci}")
    print(f"  VPP Name:  {iface.vpp_name}")
    print(f"  IPv4:      {iface.ipv4}/{iface.ipv4_prefix}")
    print(f"  Network:   {iface.network}")
    if iface.ipv6:
        print(f"  IPv6:      {iface.ipv6}/{iface.ipv6_prefix}")
    print(f"  Subifs:    {len(iface.subinterfaces)}")
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
    print("Type 'subinterfaces' to manage VLAN sub-interfaces")
    print()


def _show_subinterfaces(subifs: list, parent: str) -> None:
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


def _show_loopbacks(config) -> None:
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


def _show_bvi(config) -> None:
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


def _show_vlan_passthrough(config) -> None:
    """Show VLAN passthrough config."""
    print(f"{Colors.BOLD}VLAN Pass-through{Colors.NC}")
    print("=" * 50)
    if not config.vlan_passthrough:
        print("  (none configured)")
    else:
        for v in config.vlan_passthrough:
            if v.inner_vlan:
                print(f"  VLAN {v.vlan_id}.{v.inner_vlan} (QinQ) <-> {v.internal_interface}")
            elif v.vlan_type == "dot1ad":
                print(f"  S-VLAN {v.vlan_id} (QinQ) <-> {v.internal_interface}")
            else:
                print(f"  VLAN {v.vlan_id} (802.1Q) <-> {v.internal_interface}")
    print()


def _show_routing(config) -> None:
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


def _show_bgp(config) -> None:
    """Show BGP configuration."""
    print(f"{Colors.BOLD}BGP Configuration{Colors.NC}")
    print("=" * 50)
    bgp = config.bgp
    print(f"  Enabled:    {bgp.enabled}")
    if bgp.enabled:
        print(f"  Local AS:   {bgp.asn}")
        print(f"  Router ID:  {bgp.router_id}")
        print()
        print(f"  {Colors.BOLD}Peers ({len(bgp.peers)}):{Colors.NC}")
        if bgp.peers:
            for peer in bgp.peers:
                af = "IPv6" if ':' in peer.peer_ip else "IPv4"
                print(f"    {peer.name}: {peer.peer_ip} AS {peer.peer_asn} ({af})")
        else:
            print("    (no peers configured)")
    print()


def _show_ospf(config) -> None:
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
        # Internal interfaces
        for iface in config.internal:
            if iface.ospf_area is not None:
                passive = " (passive)" if iface.ospf_passive else ""
                print(f"    {iface.vpp_name}: area {iface.ospf_area}{passive}")
                has_areas = True
            for sub in iface.subinterfaces:
                if sub.ospf_area is not None:
                    passive = " (passive)" if sub.ospf_passive else ""
                    print(f"    {iface.vpp_name}.{sub.vlan_id}: area {sub.ospf_area}{passive}")
                    has_areas = True
        # External interface
        if config.external and config.external.ospf_area is not None:
            passive = " (passive)" if config.external.ospf_passive else ""
            print(f"    external: area {config.external.ospf_area}{passive}")
            has_areas = True
        for sub in (config.external.subinterfaces if config.external else []):
            if sub.ospf_area is not None:
                passive = " (passive)" if sub.ospf_passive else ""
                print(f"    external.{sub.vlan_id}: area {sub.ospf_area}{passive}")
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


def _show_ospf6(config) -> None:
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
        # Internal interfaces
        for iface in config.internal:
            if iface.ospf6_area is not None:
                passive = " (passive)" if iface.ospf6_passive else ""
                print(f"    {iface.vpp_name}: area {iface.ospf6_area}{passive}")
                has_areas = True
            for sub in iface.subinterfaces:
                if sub.ospf6_area is not None:
                    passive = " (passive)" if sub.ospf6_passive else ""
                    print(f"    {iface.vpp_name}.{sub.vlan_id}: area {sub.ospf6_area}{passive}")
                    has_areas = True
        # External interface
        if config.external and config.external.ospf6_area is not None:
            passive = " (passive)" if config.external.ospf6_passive else ""
            print(f"    external: area {config.external.ospf6_area}{passive}")
            has_areas = True
        for sub in (config.external.subinterfaces if config.external else []):
            if sub.ospf6_area is not None:
                passive = " (passive)" if sub.ospf6_passive else ""
                print(f"    external.{sub.vlan_id}: area {sub.ospf6_area}{passive}")
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


def _show_nat(config) -> None:
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


def _show_nat_mappings(config) -> None:
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


def _show_nat_bypass(config) -> None:
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


def _show_containers(config) -> None:
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


def _show_cpu(config) -> None:
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
                            print("  systemctl restart vpp-core vpp-core-config vpp-nat frr")
                            return
                        else:
                            log("Live changes applied successfully")
                    else:
                        print("Changes saved to config files. Restart services to apply:")
                        print("  systemctl restart vpp-core vpp-core-config vpp-nat frr")
                        return

                # Handle restart-required changes
                if restart_reasons:
                    response = input("Restart services for remaining changes? [y/N]: ").strip().lower()
                    if response == 'y':
                        _restart_services()
                    else:
                        print("Run 'systemctl restart vpp-core vpp-core-config vpp-nat frr' to apply remaining changes")
                else:
                    log("Configuration applied")

            except ImportError:
                # live_config not available, fall back to restart
                warn("Live config module not available, falling back to service restart")
                response = input("Restart services now? [y/N]: ").strip().lower()
                if response == 'y':
                    _restart_services()
                else:
                    print("Run 'systemctl restart vpp-core vpp-core-config vpp-nat frr' to apply changes")

        else:
            # No previous config - this is first-time setup, must restart
            log("Configuration applied (first-time setup)")
            print()
            response = input("Start services now? [y/N]: ").strip().lower()
            if response == 'y':
                _restart_services()
            else:
                print("Run 'systemctl restart vpp-core vpp-core-config vpp-nat frr' to start services")

    except Exception as e:
        error(f"Failed to apply: {e}")
        import traceback
        traceback.print_exc()


def _restart_services() -> None:
    """Restart all dataplane services in correct order."""
    log("Restarting services...")
    # Order matters: vpp-core must be up before vpp-core-config, vpp-nat, and frr
    subprocess.run(["systemctl", "restart", "vpp-core"], check=False)
    subprocess.run(["systemctl", "restart", "vpp-core-config"], check=False)
    subprocess.run(["systemctl", "restart", "vpp-nat"], check=False)
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
# Navigation
# =============================================================================

def navigate(ctx: MenuContext, target: str, menus: dict) -> bool:
    """
    Navigate to a menu. Returns True if navigation succeeded.
    """
    # Get current menu
    menu = menus.get("root")
    for segment in ctx.path:
        if menu and "children" in menu:
            menu = menu["children"].get(segment, {})

    # Check if target is a valid child
    if menu and "children" in menu and target in menu["children"]:
        ctx.path.append(target)
        return True

    # Special case: internal interfaces are dynamic
    if ctx.path == ["config", "interfaces", "internal"] and ctx.config:
        if any(i.vpp_name == target for i in ctx.config.internal):
            ctx.path.append(target)
            return True

    # Special case: subinterfaces on dynamic internal interfaces
    if len(ctx.path) == 4 and ctx.path[:3] == ["config", "interfaces", "internal"] and ctx.config:
        iface_name = ctx.path[3]
        if any(i.vpp_name == iface_name for i in ctx.config.internal):
            if target == "subinterfaces":
                ctx.path.append(target)
                return True

    # Special case: subinterfaces on external interface
    if ctx.path == ["config", "interfaces", "external"] and target == "subinterfaces":
        ctx.path.append(target)
        return True

    return False


# =============================================================================
# CRUD Operations
# =============================================================================

def prompt_value(prompt: str, validator: Callable = None, required: bool = True, default: str = None) -> Optional[str]:
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


def cmd_loopback_add(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_loopback_delete(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_loopback_edit(ctx: MenuContext, args: list[str]) -> None:
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

def cmd_bvi_add(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_bvi_delete(ctx: MenuContext, args: list[str]) -> None:
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

def cmd_vlan_passthrough_add(ctx: MenuContext, args: list[str]) -> None:
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

    # Internal interface
    if ctx.config.internal:
        print()
        print("  Available internal interfaces:")
        for iface in ctx.config.internal:
            print(f"    - {iface.vpp_name}")
        print()

    internal_iface = prompt_value("Internal interface to connect (e.g., internal0)")
    if not internal_iface:
        return

    # Add to config
    ctx.config.vlan_passthrough.append(VLANPassthrough(
        vlan_id=vlan_id,
        vlan_type=vlan_type,
        inner_vlan=inner_vlan,
        internal_interface=internal_iface
    ))

    ctx.dirty = True
    if inner_vlan:
        log(f"Added VLAN passthrough: {vlan_id}.{inner_vlan} ({vlan_type}) <-> {internal_iface}")
    else:
        log(f"Added VLAN passthrough: {vlan_id} ({vlan_type}) <-> {internal_iface}")


def cmd_vlan_passthrough_delete(ctx: MenuContext, args: list[str]) -> None:
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

def _get_parent_interface(ctx: MenuContext):
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


def cmd_subinterface_add(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_subinterface_delete(ctx: MenuContext, args: list[str]) -> None:
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


# =============================================================================
# NAT Mapping CRUD Operations
# =============================================================================

def cmd_nat_mapping_add(ctx: MenuContext, args: list[str]) -> None:
    """Add a new NAT mapping."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"{Colors.BOLD}Add NAT Mapping{Colors.NC}")
    print()
    print("  Maps internal network to a public NAT pool")
    print()

    # Ensure NAT module exists
    nat_module = ensure_nat_module(ctx.config)
    nat_cfg = nat_module['config']
    if 'mappings' not in nat_cfg:
        nat_cfg['mappings'] = []

    # Source network
    source = prompt_value("Source network (CIDR, e.g., 10.0.0.0/24)", validate_ipv4_cidr)
    if not source:
        return

    # Check for duplicate
    if any(m.get('source_network') == source for m in nat_cfg['mappings']):
        error(f"Mapping for {source} already exists")
        return

    # NAT pool
    print()
    print(f"  Current NAT prefix: {nat_cfg.get('bgp_prefix', 'not set')}")
    print()
    nat_pool = prompt_value("NAT pool (CIDR for det44, e.g., 23.177.24.96/29)", validate_ipv4_cidr)
    if not nat_pool:
        return

    # Add to config as dict
    nat_cfg['mappings'].append({
        'source_network': source,
        'nat_pool': nat_pool
    })

    ctx.dirty = True
    log(f"Added NAT mapping: {source} -> {nat_pool}")


def cmd_nat_mapping_delete(ctx: MenuContext, args: list[str]) -> None:
    """Delete a NAT mapping."""
    if not ctx.config:
        error("No configuration loaded")
        return

    nat_cfg = get_nat_config(ctx.config)
    if not nat_cfg:
        error("NAT module not configured")
        return

    mappings = nat_cfg.get('mappings', [])
    if not mappings:
        error("No NAT mappings configured")
        return

    if not args:
        # List and ask for selection
        print()
        print("Current mappings:")
        for i, m in enumerate(mappings, 1):
            print(f"  {i}. {m.get('source_network')} -> {m.get('nat_pool')}")
        print()
        choice = prompt_value("Delete which mapping (number)")
        if not choice:
            return
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(mappings):
                raise ValueError()
        except ValueError:
            error("Invalid selection")
            return
        mapping = mappings[idx]
    else:
        source = args[0]
        mapping = next((m for m in mappings if m.get('source_network') == source), None)
        if not mapping:
            error(f"NAT mapping for {source} not found")
            return

    if prompt_yes_no(f"Delete mapping {mapping.get('source_network')} -> {mapping.get('nat_pool')}?"):
        mappings.remove(mapping)
        ctx.dirty = True
        log(f"Deleted NAT mapping: {mapping.get('source_network')}")


# =============================================================================
# NAT Bypass CRUD Operations
# =============================================================================

def cmd_nat_bypass_add(ctx: MenuContext, args: list[str]) -> None:
    """Add a new NAT bypass rule."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"{Colors.BOLD}Add NAT Bypass Rule{Colors.NC}")
    print()
    print("  Traffic matching these source/destination pairs bypasses NAT")
    print()

    # Ensure NAT module exists
    nat_module = ensure_nat_module(ctx.config)
    nat_cfg = nat_module['config']
    if 'bypass_pairs' not in nat_cfg:
        nat_cfg['bypass_pairs'] = []

    # Source network
    source = prompt_value("Source network (CIDR)", validate_ipv4_cidr)
    if not source:
        return

    # Destination network
    dest = prompt_value("Destination network (CIDR)", validate_ipv4_cidr)
    if not dest:
        return

    # Check for duplicate
    if any(b.get('source') == source and b.get('destination') == dest for b in nat_cfg['bypass_pairs']):
        error(f"Bypass rule {source} -> {dest} already exists")
        return

    # Add to config as dict
    nat_cfg['bypass_pairs'].append({
        'source': source,
        'destination': dest
    })

    ctx.dirty = True
    log(f"Added NAT bypass: {source} -> {dest}")


def cmd_nat_bypass_delete(ctx: MenuContext, args: list[str]) -> None:
    """Delete a NAT bypass rule."""
    if not ctx.config:
        error("No configuration loaded")
        return

    nat_cfg = get_nat_config(ctx.config)
    if not nat_cfg:
        error("NAT module not configured")
        return

    bypass_pairs = nat_cfg.get('bypass_pairs', [])
    if not bypass_pairs:
        error("No bypass rules configured")
        return

    if not args:
        # List and ask for selection
        print()
        print("Current bypass rules:")
        for i, b in enumerate(bypass_pairs, 1):
            print(f"  {i}. {b.get('source')} -> {b.get('destination')}")
        print()
        choice = prompt_value("Delete which rule (number)")
        if not choice:
            return
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(bypass_pairs):
                raise ValueError()
        except ValueError:
            error("Invalid selection")
            return
        bypass = bypass_pairs[idx]
    else:
        # Try to parse source from args
        source = args[0]
        bypass = next((b for b in bypass_pairs if b.get('source') == source), None)
        if not bypass:
            error(f"Bypass rule for source {source} not found")
            return

    if prompt_yes_no(f"Delete bypass {bypass.get('source')} -> {bypass.get('destination')}?"):
        bypass_pairs.remove(bypass)
        ctx.dirty = True
        log(f"Deleted NAT bypass: {bypass.get('source')} -> {bypass.get('destination')}")


def cmd_nat_set_prefix(ctx: MenuContext, args: list[str]) -> None:
    """Set the NAT pool prefix."""
    if not ctx.config:
        error("No configuration loaded")
        return

    # Ensure NAT module exists
    nat_module = ensure_nat_module(ctx.config)
    nat_cfg = nat_module['config']

    print()
    print(f"  Current NAT prefix: {nat_cfg.get('bgp_prefix', 'not set')}")
    if ctx.config.bgp.enabled:
        print("  (This prefix will be announced via BGP)")
    print()

    prefix = prompt_value("New NAT pool prefix (CIDR)", validate_ipv4_cidr)
    if not prefix:
        return

    nat_cfg['bgp_prefix'] = prefix
    ctx.dirty = True
    log(f"Set NAT prefix to: {prefix}")


# =============================================================================
# BGP Configuration Operations
# =============================================================================

def cmd_bgp_enable(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_bgp_disable(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_bgp_peers_list(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_bgp_peers_add(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_bgp_peers_remove(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_ospf_enable(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_ospf_disable(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_ospf6_enable(ctx: MenuContext, args: list[str]) -> None:
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


def cmd_ospf6_disable(ctx: MenuContext, args: list[str]) -> None:
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


# =============================================================================
# Module Commands
# =============================================================================

def cmd_modules_available(ctx: MenuContext, args: list[str]) -> None:
    """List available module examples that can be installed."""
    if not MODULE_LOADER_AVAILABLE:
        error("Module loader not available")
        return

    examples = list_example_modules()
    if not examples:
        info(f"No module examples found in {MODULE_EXAMPLES_DIR}")
        return

    print("\nAvailable module examples:")
    for name, display, desc in examples:
        print(f"  {name:12} - {display}")
        if desc:
            print(f"               {desc}")
    print(f"\nInstall with: config modules install <name>")


def cmd_modules_list(ctx: MenuContext, args: list[str]) -> None:
    """List installed modules."""
    if not MODULE_LOADER_AVAILABLE:
        error("Module loader not available")
        return

    modules = list_available_modules()
    if not modules:
        info(f"No modules installed in {MODULE_DEFINITIONS_DIR}")
        info("Use 'config modules install <name>' to install from examples")
        return

    # Check which are enabled in config
    enabled_modules = set()
    if ctx.config and hasattr(ctx.config, 'modules'):
        for m in ctx.config.modules:
            if m.get('enabled'):
                enabled_modules.add(m.get('name'))

    print("\nInstalled modules:")
    for name, display, desc in modules:
        status = "[enabled]" if name in enabled_modules else "[disabled]"
        print(f"  {name:12} {status:11} - {display}")
    print()


def cmd_modules_install(ctx: MenuContext, args: list[str]) -> None:
    """Install a module from examples."""
    if not MODULE_LOADER_AVAILABLE:
        error("Module loader not available")
        return

    if not args:
        error("Usage: config modules install <name>")
        info("List available modules with: config modules available")
        return

    name = args[0]
    try:
        install_module_from_example(name)
        log(f"Installed module '{name}'")
        info(f"Enable with: config modules enable {name}")
    except FileNotFoundError:
        error(f"Module example '{name}' not found")
        info("List available modules with: config modules available")
    except FileExistsError:
        warn(f"Module '{name}' is already installed")


def cmd_modules_enable(ctx: MenuContext, args: list[str]) -> None:
    """Enable a module."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not args:
        error("Usage: config modules enable <name>")
        return

    name = args[0]

    # Check if module definition exists
    module_yaml = MODULE_DEFINITIONS_DIR / f"{name}.yaml"
    if not module_yaml.exists():
        error(f"Module '{name}' not installed")
        info(f"Install with: config modules install {name}")
        return

    # Check if already in modules list
    for m in ctx.config.modules:
        if m.get('name') == name:
            if m.get('enabled'):
                warn(f"Module '{name}' is already enabled")
                return
            m['enabled'] = True
            ctx.dirty = True
            log(f"Enabled module '{name}'")
            return

    # Add new module entry
    ctx.config.modules.append({
        'name': name,
        'enabled': True,
        'config': {}
    })
    ctx.dirty = True
    log(f"Enabled module '{name}'")
    info(f"Configure with: config {name} ...")


def cmd_modules_disable(ctx: MenuContext, args: list[str]) -> None:
    """Disable a module."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not args:
        error("Usage: config modules disable <name>")
        return

    name = args[0]

    for m in ctx.config.modules:
        if m.get('name') == name:
            if not m.get('enabled'):
                warn(f"Module '{name}' is already disabled")
                return
            m['enabled'] = False
            ctx.dirty = True
            log(f"Disabled module '{name}'")
            return

    error(f"Module '{name}' not in configuration")


# =============================================================================
# VPP Command Execution
# =============================================================================

VPP_CORE_SOCKET = "/run/vpp/core-cli.sock"
VPP_SOCKETS_DIR = Path("/run/vpp")


def get_vpp_socket(instance: str) -> str:
    """Get the socket path for a VPP instance."""
    if instance == "core":
        return VPP_CORE_SOCKET
    return f"/run/vpp/{instance}-cli.sock"


def list_running_modules() -> list[str]:
    """
    Discover running VPP module sockets.

    Returns:
        List of module names (e.g., ['nat', 'nat64'])
    """
    modules = []
    if VPP_SOCKETS_DIR.exists():
        for sock in VPP_SOCKETS_DIR.glob("*-cli.sock"):
            name = sock.stem.replace("-cli", "")
            if name != "core":
                modules.append(name)
    return sorted(modules)


def vpp_exec(command: str, instance: str = "core") -> tuple[bool, str]:
    """
    Execute a VPP command and capture output.

    Args:
        command: VPP CLI command to execute
        instance: "core" or module name (e.g., "nat")

    Returns:
        (success: bool, output: str)
    """
    socket = get_vpp_socket(instance)

    if not Path(socket).exists():
        return False, f"VPP {instance} socket not found: {socket}"

    try:
        result = subprocess.run(
            ["vppctl", "-s", socket, command],
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout.strip()
        if result.returncode != 0 and result.stderr.strip():
            return False, result.stderr.strip()
        return True, output
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


# =============================================================================
# Shell Commands
# =============================================================================

def cmd_shell_routing(ctx: MenuContext, args: list[str]) -> None:
    """Open FRR vtysh shell."""
    ns_path = Path(f"/var/run/netns/dataplane")
    if not ns_path.exists():
        error("Dataplane namespace not found")
        return
    log("Entering FRR routing shell (vtysh)...")
    print("Type 'exit' to return\n")
    subprocess.run(["ip", "netns", "exec", "dataplane", "vtysh"], check=False)


def cmd_shell_core(ctx: MenuContext, args: list[str]) -> None:
    """Open VPP core CLI."""
    socket = "/run/vpp/core-cli.sock"
    if not Path(socket).exists():
        error("VPP core socket not found")
        return
    log("Entering VPP core CLI...")
    print("Type 'quit' to return\n")
    subprocess.run(["vppctl", "-s", socket], check=False)


def cmd_shell_nat(ctx: MenuContext, args: list[str]) -> None:
    """Open VPP NAT CLI (backwards compat)."""
    cmd_shell_module(ctx, ["nat"])


def cmd_shell_module(ctx: MenuContext, args: list[str]) -> None:
    """Open VPP module CLI."""
    if not args:
        # List available modules
        modules = list_running_modules()
        if modules:
            print("Available module shells:")
            for m in modules:
                print(f"  {m}")
            print("\nUsage: shell <module>")
        else:
            warn("No module sockets found in /run/vpp/")
        return

    module_name = args[0]
    socket = get_vpp_socket(module_name)
    if not Path(socket).exists():
        error(f"VPP {module_name} socket not found: {socket}")
        return
    log(f"Entering VPP {module_name} CLI...")
    print("Type 'quit' to return\n")
    subprocess.run(["vppctl", "-s", socket], check=False)


# =============================================================================
# Capture Commands
# =============================================================================

def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def cmd_capture_start(ctx: MenuContext, args: list[str]) -> None:
    """Start a packet capture on a VPP instance."""
    import time

    print()
    print(f"{Colors.BOLD}Start Packet Capture{Colors.NC}")
    print()

    # Instance selection
    instance = input("  VPP instance (core/nat) [core]: ").strip().lower() or "core"
    if instance not in ("core", "nat"):
        error("Instance must be 'core' or 'nat'")
        return

    # Show available interfaces
    success, iface_output = vpp_exec("show interface", instance)
    if success and iface_output:
        print(f"\n  Available interfaces:")
        for line in iface_output.split('\n'):
            line = line.strip()
            if line and not line.startswith(' ') and not line.startswith('Name'):
                parts = line.split()
                if parts:
                    print(f"    - {parts[0]}")
        print()

    # Interface selection
    interface = input("  Interface (or 'any' for all) [any]: ").strip() or "any"

    # Direction
    print("\n  Capture direction:")
    print("    1. rx (receive only)")
    print("    2. tx (transmit only)")
    print("    3. drop (dropped packets only)")
    print("    4. rx tx (both directions)")
    print("    5. rx tx drop (all)")
    print()
    direction_choice = input("  Direction [4]: ").strip() or "4"
    directions = {
        "1": "rx", "2": "tx", "3": "drop",
        "4": "rx tx", "5": "rx tx drop"
    }
    direction = directions.get(direction_choice, "rx tx")

    # Max packets
    max_input = input("  Max packets (0 for unlimited) [10000]: ").strip() or "10000"
    try:
        max_pkts = int(max_input)
    except ValueError:
        max_pkts = 10000

    # Filename (include .pcap so VPP writes with extension)
    default_file = f"capture-{instance}-{int(time.time())}.pcap"
    filename = input(f"  Output filename [{default_file}]: ").strip() or default_file
    if not filename.endswith(".pcap"):
        filename += ".pcap"

    # Build and execute command
    cmd = f"pcap trace {direction} intfc {interface} file {filename}"
    if max_pkts > 0:
        cmd += f" max {max_pkts}"

    success, output = vpp_exec(cmd, instance)
    if success:
        log(f"Capture started on {instance}: /tmp/{filename}.pcap")
        if output:
            print(f"  {output}")
    else:
        error(f"Failed to start capture: {output}")


def cmd_capture_stop(ctx: MenuContext, args: list[str]) -> None:
    """Stop packet capture on a VPP instance."""
    if args:
        instance = args[0].lower()
    else:
        instance = input("  VPP instance to stop (core/nat) [core]: ").strip().lower() or "core"

    if instance not in ("core", "nat"):
        error("Instance must be 'core' or 'nat'")
        return

    success, output = vpp_exec("pcap trace off", instance)
    if success:
        log(f"Capture stopped on {instance}")
        if output:
            print(f"  {output}")
    else:
        error(f"Failed to stop capture: {output}")


def cmd_capture_status(ctx: MenuContext, args: list[str]) -> None:
    """Show active captures on both VPP instances."""
    import re

    print()
    print(f"{Colors.BOLD}Capture Status{Colors.NC}")
    print("=" * 50)

    for instance in ("core", "nat"):
        socket = VPP_CORE_SOCKET if instance == "core" else VPP_NAT_SOCKET
        if not Path(socket).exists():
            print(f"  {instance}: {Colors.DIM}VPP not running{Colors.NC}")
            continue

        success, output = vpp_exec("pcap trace status", instance)
        if success:
            if not output.strip() or "No pcap" in output or "disabled" in output.lower():
                print(f"  {instance}: No active capture")
            else:
                # Parse "X of Y pkts" to determine if capture is complete
                match = re.search(r'(\d+)\s+of\s+(\d+)\s+pkts', output)
                if match:
                    captured, limit = int(match.group(1)), int(match.group(2))
                    if captured >= limit:
                        print(f"  {instance}: {Colors.GREEN}COMPLETE{Colors.NC} - {captured}/{limit} packets (limit reached)")
                    else:
                        print(f"  {instance}: {Colors.CYAN}ACTIVE{Colors.NC} - {captured}/{limit} packets")
                else:
                    print(f"  {instance}:")
                    for line in output.split('\n'):
                        if line.strip():
                            print(f"    {line}")
        else:
            print(f"  {instance}: {Colors.RED}Error{Colors.NC} - {output}")
    print()


def cmd_capture_files(ctx: MenuContext, args: list[str]) -> None:
    """List pcap files in /tmp."""
    import glob
    from datetime import datetime

    print()
    print(f"{Colors.BOLD}Capture Files{Colors.NC}")
    print("=" * 70)

    pcap_files = glob.glob("/tmp/*.pcap")
    if not pcap_files:
        print("  No pcap files found in /tmp")
        print()
        return

    # Get file info
    files = []
    for f in pcap_files:
        try:
            stat = os.stat(f)
            files.append({
                "path": f,
                "name": os.path.basename(f),
                "size": stat.st_size,
                "mtime": stat.st_mtime
            })
        except OSError:
            continue

    # Sort by modification time, newest first
    files.sort(key=lambda x: x["mtime"], reverse=True)

    print(f"  {'FILENAME':<40} {'SIZE':>10} {'MODIFIED':<20}")
    print("  " + "-" * 68)

    for f in files:
        size_str = _format_size(f["size"])
        mtime_str = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  {f['name']:<40} {size_str:>10} {mtime_str:<20}")

    print()


def _get_pcap_files() -> list[dict]:
    """Get list of pcap files in /tmp, sorted by modification time (newest first)."""
    import glob
    from datetime import datetime

    pcap_files = glob.glob("/tmp/*.pcap")
    files = []
    for f in pcap_files:
        try:
            stat = os.stat(f)
            files.append({
                "path": f,
                "name": os.path.basename(f),
                "size": stat.st_size,
                "mtime": stat.st_mtime
            })
        except OSError:
            continue

    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files


def _pick_pcap_file(prompt: str = "Select file") -> str:
    """Show numbered list of pcap files and let user pick one. Returns filepath or empty string."""
    files = _get_pcap_files()

    if not files:
        error("No pcap files found in /tmp")
        return ""

    print()
    print(f"  {Colors.BOLD}Available captures:{Colors.NC}")
    from datetime import datetime
    for i, f in enumerate(files, 1):
        size_str = _format_size(f["size"])
        age = _format_age(f["mtime"])
        print(f"    {i}. {f['name']} ({size_str}, {age})")

    print()
    choice = input(f"  {prompt} [1]: ").strip() or "1"

    # Accept number or filename
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(files):
            return files[idx]["path"]
        else:
            error(f"Invalid selection: {choice}")
            return ""
    except ValueError:
        # Treat as filename
        if not choice.startswith("/"):
            choice = f"/tmp/{choice}"
        if not choice.endswith(".pcap"):
            choice += ".pcap"
        if Path(choice).exists():
            return choice
        error(f"File not found: {choice}")
        return ""


def _format_age(mtime: float) -> str:
    """Format file modification time as relative age."""
    import time
    age_secs = time.time() - mtime
    if age_secs < 60:
        return "just now"
    if age_secs < 3600:
        mins = int(age_secs / 60)
        return f"{mins}m ago"
    if age_secs < 86400:
        hours = int(age_secs / 3600)
        return f"{hours}h ago"
    days = int(age_secs / 86400)
    return f"{days}d ago"


def cmd_capture_analyze(ctx: MenuContext, args: list[str]) -> None:
    """Analyze a pcap file using tshark."""
    if args:
        filename = args[0]
        # Resolve path
        if not filename.startswith("/"):
            filename = f"/tmp/{filename}"
        if not filename.endswith(".pcap"):
            filename += ".pcap"
        if not Path(filename).exists():
            error(f"File not found: {filename}")
            return
    else:
        filename = _pick_pcap_file("Analyze file")
        if not filename:
            return

    print()
    print(f"{Colors.BOLD}Capture Analysis: {os.path.basename(filename)}{Colors.NC}")
    print("=" * 70)

    # File info with capinfos
    print(f"\n{Colors.CYAN}File Information:{Colors.NC}")
    result = subprocess.run(
        ["capinfos", "-c", "-d", "-u", "-e", "-y", "-i", filename],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        for line in result.stdout.strip().split('\n'):
            if line.strip():
                print(f"  {line}")
    else:
        warn("capinfos not available - install wireshark-common or tshark")

    # Protocol hierarchy
    print(f"\n{Colors.CYAN}Protocol Hierarchy:{Colors.NC}")
    result = subprocess.run(
        ["tshark", "-r", filename, "-q", "-z", "io,phs"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        for line in result.stdout.strip().split('\n'):
            print(f"  {line}")
    else:
        warn("tshark not available - install tshark package")

    # Top conversations (IPv4)
    print(f"\n{Colors.CYAN}Top IPv4 Conversations:{Colors.NC}")
    result = subprocess.run(
        ["tshark", "-r", filename, "-q", "-z", "conv,ip"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        lines = result.stdout.strip().split('\n')
        # Limit to top entries
        for line in lines[:15]:
            print(f"  {line}")
        if len(lines) > 15:
            print(f"  ... and {len(lines) - 15} more")

    print()


def cmd_capture_export(ctx: MenuContext, args: list[str]) -> None:
    """Export a capture file to persistent storage."""
    import shutil

    if args:
        src = args[0]
        if not src.startswith("/"):
            src = f"/tmp/{src}"
        if not src.endswith(".pcap"):
            src += ".pcap"
        if not Path(src).exists():
            error(f"File not found: {src}")
            return
    else:
        src = _pick_pcap_file("Export file")
        if not src:
            return

    # Destination
    dest_dir = Path("/persistent/data/captures")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / os.path.basename(src)

    shutil.copy2(src, dest)
    log(f"Exported to: {dest}")


def cmd_capture_delete(ctx: MenuContext, args: list[str]) -> None:
    """Delete a pcap file."""
    if args:
        filepath = args[0]
        if not filepath.startswith("/"):
            filepath = f"/tmp/{filepath}"
        if not filepath.endswith(".pcap"):
            filepath += ".pcap"
        if not Path(filepath).exists():
            error(f"File not found: {filepath}")
            return
    else:
        filepath = _pick_pcap_file("Delete file")
        if not filepath:
            return

    confirm = input(f"  Delete {os.path.basename(filepath)}? [y/N]: ").strip().lower()
    if confirm == 'y':
        os.remove(filepath)
        log(f"Deleted: {filepath}")
    else:
        print("  Cancelled")


# =============================================================================
# VPP Graph Trace Commands
# =============================================================================

# VPP nodes available for tracing, organized by category
# Tuple: (node_name, description, instances) where instances is "core", "nat", or "both"
VPP_TRACE_NODES = [
    # Interface input (capture everything at ingress)
    ("dpdk-input", "All traffic on DPDK interfaces (physical NICs)", "core"),
    ("memif-input", "All traffic on memif interfaces (inter-VPP)", "both"),
    ("host-interface-input", "All traffic on host interfaces (veth/tap)", "core"),
    ("af-packet-input", "All traffic on AF_PACKET interfaces", "core"),
    ("virtio-input", "All traffic on virtio interfaces (VMs)", "core"),
    # Protocol-specific (filter by L3 protocol)
    ("ip4-input", "IPv4 packets only", "both"),
    ("ip6-input", "IPv6 packets only", "both"),
    ("arp-input", "ARP packets only", "both"),
    ("ip4-icmp-input", "ICMPv4 packets only", "both"),
    ("icmp6-input", "ICMPv6/NDP packets only", "both"),
    # Routing decisions
    ("ip4-lookup", "IPv4 FIB lookup (see routing decisions)", "both"),
    ("ip6-lookup", "IPv6 FIB lookup (see routing decisions)", "both"),
    ("ip4-rewrite", "IPv4 output rewrite (egress path)", "both"),
    ("ip6-rewrite", "IPv6 output rewrite (egress path)", "both"),
    # Feature nodes (core-specific)
    ("abf-input-ip4", "ACL-based forwarding (IPv4 policy routing)", "core"),
    ("abf-input-ip6", "ACL-based forwarding (IPv6 policy routing)", "core"),
    ("acl-plugin-in-ip4-fa", "ACL evaluation (IPv4)", "core"),
    ("acl-plugin-in-ip6-fa", "ACL evaluation (IPv6)", "core"),
    # NAT (NAT instance only)
    ("det44-in2out", "Deterministic NAT44 inside-to-outside", "nat"),
    ("det44-out2in", "Deterministic NAT44 outside-to-inside", "nat"),
    # Locally-originated
    ("ip4-local", "IPv4 packets destined to VPP itself", "both"),
    ("ip6-local", "IPv6 packets destined to VPP itself", "both"),
]


def get_trace_nodes_for_instance(instance: str) -> list[tuple[str, str]]:
    """Get trace nodes applicable to the given VPP instance."""
    return [(node, desc) for node, desc, inst in VPP_TRACE_NODES
            if inst == "both" or inst == instance]


def cmd_trace_start(ctx: MenuContext, args: list[str]) -> None:
    """Start VPP graph tracing."""
    print()
    print(f"{Colors.BOLD}Start VPP Graph Trace{Colors.NC}")
    print()

    # Instance selection
    instance = input("  VPP instance (core/nat) [core]: ").strip().lower() or "core"
    if instance not in ("core", "nat"):
        error("Instance must be 'core' or 'nat'")
        return

    # Get trace nodes applicable to this instance
    available_nodes = get_trace_nodes_for_instance(instance)

    # Show available trace nodes
    print(f"\n  {Colors.BOLD}Available trace nodes for {instance}:{Colors.NC}")
    for i, (node, desc) in enumerate(available_nodes, 1):
        print(f"    {i:2}. {node:<25} {Colors.DIM}{desc}{Colors.NC}")
    print(f"    {Colors.DIM}Or enter a custom node name{Colors.NC}")
    print()

    node_input = input("  Trace node [1]: ").strip() or "1"
    try:
        idx = int(node_input) - 1
        if 0 <= idx < len(available_nodes):
            input_node = available_nodes[idx][0]
        else:
            input_node = node_input
    except ValueError:
        input_node = node_input

    # Packet count
    count_input = input("  Number of packets to trace [50]: ").strip() or "50"
    try:
        count = int(count_input)
    except ValueError:
        count = 50

    # Start trace
    cmd = f"trace add {input_node} {count}"
    success, output = vpp_exec(cmd, instance)

    if success:
        log(f"Tracing {count} packets from {input_node} on {instance}")
        if output:
            print(f"  {output}")
    else:
        error(f"Failed to start trace: {output}")


def cmd_trace_stop(ctx: MenuContext, args: list[str]) -> None:
    """Stop/disable tracing on a VPP instance."""
    if args:
        instance = args[0].lower()
    else:
        instance = input("  VPP instance (core/nat) [core]: ").strip().lower() or "core"

    if instance not in ("core", "nat"):
        error("Instance must be 'core' or 'nat'")
        return

    # Clear trace buffer effectively stops tracing
    success, output = vpp_exec("clear trace", instance)
    if success:
        log(f"Trace cleared on {instance}")
    else:
        error(f"Failed to clear trace: {output}")


def cmd_trace_status(ctx: MenuContext, args: list[str]) -> None:
    """Show trace status on both VPP instances."""
    import re

    print()
    print(f"{Colors.BOLD}Trace Status{Colors.NC}")
    print("=" * 50)

    for instance in ("core", "nat"):
        socket = VPP_CORE_SOCKET if instance == "core" else VPP_NAT_SOCKET
        if not Path(socket).exists():
            print(f"  {instance}: {Colors.DIM}VPP not running{Colors.NC}")
            continue

        # Get trace and count actual "Packet N" entries (across all threads)
        success, output = vpp_exec("show trace", instance)
        if success:
            packets = len(re.findall(r'^Packet \d+', output, re.MULTILINE))
            if packets > 0:
                print(f"  {instance}: {Colors.GREEN}{packets} packets traced{Colors.NC}")
            else:
                print(f"  {instance}: No packets traced")
        else:
            print(f"  {instance}: {Colors.RED}Error{Colors.NC} - {output}")
    print()


def cmd_trace_show(ctx: MenuContext, args: list[str]) -> None:
    """Show trace output from a VPP instance."""
    import re

    if args:
        instance = args[0].lower()
    else:
        instance = input("  VPP instance (core/nat) [core]: ").strip().lower() or "core"

    if instance not in ("core", "nat"):
        error("Instance must be 'core' or 'nat'")
        return

    # Max packet count - from args or prompt
    if len(args) > 1:
        try:
            max_count = int(args[1])
        except ValueError:
            max_count = 10
    else:
        max_input = input("  Max packets to display [10]: ").strip() or "10"
        try:
            max_count = int(max_input)
        except ValueError:
            max_count = 10

    success, output = vpp_exec(f"show trace max {max_count}", instance)

    print()
    print(f"{Colors.BOLD}VPP Trace Output ({instance}){Colors.NC}")
    print("=" * 70)

    if success:
        # Check if there are actual packet traces (not just "No packets in trace buffer" messages)
        packets = len(re.findall(r'^Packet \d+', output, re.MULTILINE))
        if packets > 0:
            print(output)
        else:
            print("  No packets traced. Use 'trace start' to begin tracing.")
    else:
        error(f"Failed to get trace: {output}")
    print()


def cmd_trace_clear(ctx: MenuContext, args: list[str]) -> None:
    """Clear trace buffer on a VPP instance."""
    if args:
        instance = args[0].lower()
    else:
        instance = input("  VPP instance (core/nat) [core]: ").strip().lower() or "core"

    if instance not in ("core", "nat"):
        error("Instance must be 'core' or 'nat'")
        return

    success, output = vpp_exec("clear trace", instance)
    if success:
        log(f"Trace buffer cleared on {instance}")
    else:
        error(f"Failed to clear trace: {output}")


# =============================================================================
# Snapshot Commands
# =============================================================================

def cmd_snapshot_list(ctx: MenuContext, args: list[str]) -> None:
    """List all snapshots."""
    subprocess.run(["imp", "snapshot", "list"], check=False)


def cmd_snapshot_create(ctx: MenuContext, args: list[str]) -> None:
    """Create a snapshot."""
    cmd = ["imp", "snapshot", "create"]
    if args:
        cmd.append(args[0])  # Optional snapshot name
    subprocess.run(cmd, check=False)


def cmd_snapshot_delete(ctx: MenuContext, args: list[str]) -> None:
    """Delete a snapshot."""
    if not args:
        error("Usage: delete <name>")
        print("  Use 'snapshot list' to see available snapshots")
        return
    subprocess.run(["imp", "snapshot", "delete", args[0]], check=False)


def cmd_snapshot_export(ctx: MenuContext, args: list[str]) -> None:
    """Export a snapshot to file."""
    if not args:
        error("Usage: export <name> [--full] [-o output]")
        print("  Use 'snapshot list' to see available snapshots")
        return

    cmd = ["imp", "snapshot", "export", args[0]]

    # Parse remaining args for --full and -o
    i = 1
    while i < len(args):
        if args[i] == "--full":
            cmd.append("--full")
        elif args[i] in ("-o", "--output") and i + 1 < len(args):
            cmd.extend(["-o", args[i + 1]])
            i += 1
        i += 1

    subprocess.run(cmd, check=False)


def cmd_snapshot_import(ctx: MenuContext, args: list[str]) -> None:
    """Import a snapshot from file."""
    if not args:
        error("Usage: import <file> [-n name] [--persistent]")
        return

    cmd = ["imp", "snapshot", "import", args[0]]

    # Parse remaining args
    i = 1
    while i < len(args):
        if args[i] == "--persistent":
            cmd.append("--persistent")
        elif args[i] in ("-n", "--name") and i + 1 < len(args):
            cmd.extend(["-n", args[i + 1]])
            i += 1
        i += 1

    subprocess.run(cmd, check=False)


def cmd_snapshot_rollback(ctx: MenuContext, args: list[str]) -> None:
    """Rollback to a snapshot."""
    if not args:
        error("Usage: rollback <name>")
        print("  Use 'snapshot list' to see available snapshots")
        return
    subprocess.run(["imp", "snapshot", "rollback", args[0]], check=False)


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

    # NAT multi-word commands: "nat mappings list", "nat bypass add", etc.
    if command == "nat" and args:
        subcommand = args[0].lower()
        if subcommand == "set-prefix":
            cmd_nat_set_prefix(ctx, args[1:])
            return True
        if subcommand == "mappings":
            if len(args) > 1:
                subcmd = args[1].lower()
                if subcmd == "list":
                    _show_nat_mappings(ctx.config)
                    return True
                if subcmd == "add":
                    cmd_nat_mapping_add(ctx, args[2:])
                    return True
                if subcmd == "delete":
                    cmd_nat_mapping_delete(ctx, args[2:])
                    return True
            # Just "nat mappings" - navigate there
            ctx.path = ["nat", "mappings"]
            return True
        if subcommand == "bypass":
            if len(args) > 1:
                subcmd = args[1].lower()
                if subcmd == "list":
                    _show_nat_bypass(ctx.config)
                    return True
                if subcmd == "add":
                    cmd_nat_bypass_add(ctx, args[2:])
                    return True
                if subcmd == "delete":
                    cmd_nat_bypass_delete(ctx, args[2:])
                    return True
            # Just "nat bypass" - navigate there
            ctx.path = ["nat", "bypass"]
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

    # Multi-word shortcuts for modules: "config modules install nat"
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

    # Generic module commands - check if first path element is a module name
    if config_path and MODULE_LOADER_AVAILABLE:
        module_name = config_path[0]
        # Try to load module commands
        module_cmds = get_module_commands(module_name)
        if module_cmds:
            # Build command path from remaining path elements + command
            # e.g., config_path=["nat", "mappings"], command="add" -> "mappings/add"
            # e.g., config_path=["nat"], command="set-prefix" -> "set-prefix"
            path_parts = config_path[1:] + [command]
            cmd_path = "/".join(path_parts)

            # Find matching command
            for mod_cmd in module_cmds:
                if mod_cmd.path == cmd_path:
                    execute_module_command(ctx, module_name, mod_cmd)
                    return True

            # Also try without command if command is a subpath
            # e.g., "mappings" command with "add" arg -> try "mappings/add"
            if args:
                cmd_path_with_arg = "/".join(config_path[1:] + [command, args[0]])
                for mod_cmd in module_cmds:
                    if mod_cmd.path == cmd_path_with_arg:
                        execute_module_command(ctx, module_name, mod_cmd)
                        return True

    # Legacy NAT commands (kept for backwards compatibility, will use generic above first)
    if config_path == ["nat"]:
        if command == "set-prefix":
            cmd_nat_set_prefix(ctx, args)
            return True

    if config_path == ["nat", "mappings"]:
        if command == "list":
            _show_nat_mappings(ctx.config)
            return True
        if command == "add":
            cmd_nat_mapping_add(ctx, args)
            return True
        if command == "delete":
            cmd_nat_mapping_delete(ctx, args)
            return True

    if config_path == ["nat", "bypass"]:
        if command == "list":
            _show_nat_bypass(ctx.config)
            return True
        if command == "add":
            cmd_nat_bypass_add(ctx, args)
            return True
        if command == "delete":
            cmd_nat_bypass_delete(ctx, args)
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
