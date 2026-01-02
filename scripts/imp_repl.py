#!/usr/bin/env python3
"""
imp_repl.py - Interactive REPL for IMP configuration management

This module provides a hierarchical menu-driven interface for managing
router configuration. Changes are staged until explicitly applied.
"""

import json
import os
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
sys.path.insert(0, '/usr/local/bin')
try:
    from configure_router import (
        RouterConfig, ExternalInterface, InternalInterface, ManagementInterface,
        SubInterface, LoopbackInterface, BVIConfig, BridgeDomainMember,
        VLANPassthrough, BGPConfig, NATConfig, NATMapping, ACLBypassPair,
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
        text = document.text_before_cursor.lower()
        words = text.split()

        # Get available completions
        completions = self._get_menu_completions()

        # Filter based on current input
        word = words[-1] if words else ""
        for item in completions:
            if item.lower().startswith(word):
                yield Completion(item, start_position=-len(word))

    def _get_current_menu(self):
        """Navigate to the current menu based on path."""
        menu = self.menus.get("root")
        for segment in self.ctx.path:
            if menu and "children" in menu:
                menu = menu["children"].get(segment)
            else:
                return None
        return menu

    def _get_menu_completions(self) -> list[str]:
        """Get available commands and submenus for current context."""
        completions = []

        # Global commands
        completions.extend(["help", "back", "home", "show", "status", "apply", "save", "reload", "exit"])

        menu = self._get_current_menu()
        path = self.ctx.path

        # Menu-specific commands from static menu definition
        if menu and "commands" in menu:
            completions.extend(menu["commands"])

        # Child menus from static menu definition
        if menu and "children" in menu:
            completions.extend(menu["children"].keys())

        # Dynamic completions based on context
        if path == ["interfaces", "internal"] and self.ctx.config:
            # Add internal interface names
            completions.extend(i.vpp_name for i in self.ctx.config.internal)

        if len(path) == 3 and path[:2] == ["interfaces", "internal"]:
            # Add subinterfaces submenu for internal interfaces
            completions.append("subinterfaces")
            completions.extend(["show"])

        if len(path) >= 3 and path[-1] == "subinterfaces":
            # Add sub-interface commands
            completions.extend(["list", "add", "delete"])

        if path == ["loopbacks"] and self.ctx.config:
            # Add loopback instance numbers for delete completion
            for lo in self.ctx.config.loopbacks:
                completions.append(str(lo.instance))

        if path == ["bvi"] and self.ctx.config:
            # Add BVI bridge IDs for delete completion
            for bvi in self.ctx.config.bvi_domains:
                completions.append(str(bvi.bridge_id))

        if path == ["vlan-passthrough"] and self.ctx.config:
            # Add VLAN IDs for delete completion
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
                        "bgp": {"commands": ["show", "enable", "disable", "set"]},
                    },
                    "commands": ["show"],
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
                "shell": {
                    "children": {
                        "routing": {"commands": []},
                        "core": {"commands": []},
                        "nat": {"commands": []},
                    },
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

    # Global commands
    print(f"  {Colors.CYAN}Navigation:{Colors.NC}")
    print("    help, ?         Show this help")
    print("    back, ..        Go up one level")
    print("    home, /         Return to root menu")
    print("    exit, quit      Exit the REPL")
    print()

    print(f"  {Colors.CYAN}Configuration:{Colors.NC}")
    print("    show            Display configuration at current level")
    print("    status          Show staged vs applied status")
    print("    save            Save configuration to JSON")
    print("    apply           Save and regenerate config files")
    print("    reload          Reload from JSON (discard changes)")
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
    path = ctx.path

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
            print(f"  BGP:         AS {config.bgp.asn}, peer {config.bgp.peer_ipv4}")
        else:
            print(f"  BGP:         Disabled")

        print(f"  NAT prefix:  {config.nat.bgp_prefix}")
        print(f"  NAT mappings: {len(config.nat.mappings)}")
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
        print(f"  BGP: Enabled (AS {config.bgp.asn})")
    else:
        print(f"  BGP: Disabled")
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
        print(f"  Peer IPv4:  {bgp.peer_ipv4}")
        if bgp.peer_ipv6:
            print(f"  Peer IPv6:  {bgp.peer_ipv6}")
        print(f"  Peer AS:    {bgp.peer_asn}")
    print()


def _show_nat(config) -> None:
    """Show NAT configuration."""
    print(f"{Colors.BOLD}NAT Configuration{Colors.NC}")
    print("=" * 50)
    print(f"  Pool prefix: {config.nat.bgp_prefix}")
    print(f"  Mappings:    {len(config.nat.mappings)}")
    print(f"  Bypass rules: {len(config.nat.bypass_pairs)}")
    print()


def _show_nat_mappings(config) -> None:
    """Show NAT mappings."""
    print(f"{Colors.BOLD}NAT Mappings{Colors.NC}")
    print("=" * 50)
    if not config.nat.mappings:
        print("  (none configured)")
    else:
        for m in config.nat.mappings:
            print(f"  {m.source_network} -> {m.nat_pool}")
    print()


def _show_nat_bypass(config) -> None:
    """Show NAT bypass rules."""
    print(f"{Colors.BOLD}NAT Bypass Rules{Colors.NC}")
    print("=" * 50)
    if not config.nat.bypass_pairs:
        print("  (none configured)")
    else:
        for bp in config.nat.bypass_pairs:
            print(f"  {bp.source} -> {bp.destination}")
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


def cmd_save(ctx: MenuContext, args: list[str]) -> None:
    """Save configuration to JSON file."""
    if not ctx.config:
        error("No configuration to save")
        return

    if not CONFIG_AVAILABLE:
        error("Configuration module not available")
        return

    try:
        save_config(ctx.config, CONFIG_FILE)
        ctx.dirty = False
        ctx.original_json = json.dumps(asdict(ctx.config), sort_keys=True)
        log(f"Configuration saved to {CONFIG_FILE}")
    except Exception as e:
        error(f"Failed to save: {e}")


def cmd_apply(ctx: MenuContext, args: list[str]) -> None:
    """Save configuration and regenerate config files."""
    if not ctx.config:
        error("No configuration to apply")
        return

    if not CONFIG_AVAILABLE:
        error("Configuration module not available")
        return

    try:
        # Save first
        log("Saving configuration...")
        save_config(ctx.config, CONFIG_FILE)
        ctx.dirty = False
        ctx.original_json = json.dumps(asdict(ctx.config), sort_keys=True)

        # Render templates
        log("Regenerating configuration files...")
        render_templates(ctx.config, TEMPLATE_DIR, GENERATED_DIR)
        apply_configs(GENERATED_DIR)

        log("Configuration applied")
        print()

        # Ask about restart
        response = input("Restart services now? [y/N]: ").strip().lower()
        if response == 'y':
            log("Restarting services...")
            subprocess.run(["systemctl", "restart", "vpp-core"], check=False)
            subprocess.run(["systemctl", "restart", "vpp-nat"], check=False)
            subprocess.run(["systemctl", "restart", "frr"], check=False)
            log("Services restarted")
        else:
            print("Run 'systemctl restart vpp-core vpp-nat frr' to apply changes")

    except Exception as e:
        error(f"Failed to apply: {e}")


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
    if ctx.path == ["interfaces", "internal"] and ctx.config:
        if any(i.vpp_name == target for i in ctx.config.internal):
            ctx.path.append(target)
            return True

    # Special case: subinterfaces on dynamic internal interfaces
    if len(ctx.path) == 3 and ctx.path[:2] == ["interfaces", "internal"] and ctx.config:
        iface_name = ctx.path[2]
        if any(i.vpp_name == iface_name for i in ctx.config.internal):
            if target == "subinterfaces":
                ctx.path.append(target)
                return True

    # Special case: subinterfaces on external interface
    if ctx.path == ["interfaces", "external"] and target == "subinterfaces":
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

    if not args:
        error("Usage: delete <instance>")
        return

    try:
        instance = int(args[0])
    except ValueError:
        error("Instance must be a number")
        return

    lo = next((l for l in ctx.config.loopbacks if l.instance == instance), None)
    if not lo:
        error(f"Loopback loop{instance} not found")
        return

    if prompt_yes_no(f"Delete loop{instance} ({lo.name})?"):
        ctx.config.loopbacks.remove(lo)
        ctx.dirty = True
        log(f"Deleted loopback: loop{instance}")


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

    if not args:
        error("Usage: delete <bridge_id>")
        return

    try:
        bridge_id = int(args[0])
    except ValueError:
        error("Bridge ID must be a number")
        return

    bvi = next((b for b in ctx.config.bvi_domains if b.bridge_id == bridge_id), None)
    if not bvi:
        error(f"BVI domain {bridge_id} not found")
        return

    if prompt_yes_no(f"Delete BVI {bridge_id} ({bvi.name})?"):
        ctx.config.bvi_domains.remove(bvi)
        ctx.dirty = True
        log(f"Deleted BVI domain: {bridge_id}")


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
    path = ctx.path

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

    # Source network
    source = prompt_value("Source network (CIDR, e.g., 10.0.0.0/24)", validate_ipv4_cidr)
    if not source:
        return

    # Check for duplicate
    if any(m.source_network == source for m in ctx.config.nat.mappings):
        error(f"Mapping for {source} already exists")
        return

    # NAT pool
    print()
    print(f"  Current NAT prefix: {ctx.config.nat.bgp_prefix}")
    print()
    nat_pool = prompt_value("NAT pool (CIDR for det44, e.g., 23.177.24.96/29)", validate_ipv4_cidr)
    if not nat_pool:
        return

    # Add to config
    ctx.config.nat.mappings.append(NATMapping(
        source_network=source,
        nat_pool=nat_pool
    ))

    ctx.dirty = True
    log(f"Added NAT mapping: {source} -> {nat_pool}")


def cmd_nat_mapping_delete(ctx: MenuContext, args: list[str]) -> None:
    """Delete a NAT mapping."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not args:
        # List and ask for selection
        if not ctx.config.nat.mappings:
            error("No NAT mappings configured")
            return
        print()
        print("Current mappings:")
        for i, m in enumerate(ctx.config.nat.mappings, 1):
            print(f"  {i}. {m.source_network} -> {m.nat_pool}")
        print()
        choice = prompt_value("Delete which mapping (number)")
        if not choice:
            return
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(ctx.config.nat.mappings):
                raise ValueError()
        except ValueError:
            error("Invalid selection")
            return
        mapping = ctx.config.nat.mappings[idx]
    else:
        source = args[0]
        mapping = next((m for m in ctx.config.nat.mappings if m.source_network == source), None)
        if not mapping:
            error(f"NAT mapping for {source} not found")
            return

    if prompt_yes_no(f"Delete mapping {mapping.source_network} -> {mapping.nat_pool}?"):
        ctx.config.nat.mappings.remove(mapping)
        ctx.dirty = True
        log(f"Deleted NAT mapping: {mapping.source_network}")


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

    # Source network
    source = prompt_value("Source network (CIDR)", validate_ipv4_cidr)
    if not source:
        return

    # Destination network
    dest = prompt_value("Destination network (CIDR)", validate_ipv4_cidr)
    if not dest:
        return

    # Check for duplicate
    if any(b.source == source and b.destination == dest for b in ctx.config.nat.bypass_pairs):
        error(f"Bypass rule {source} -> {dest} already exists")
        return

    # Add to config
    ctx.config.nat.bypass_pairs.append(ACLBypassPair(
        source=source,
        destination=dest
    ))

    ctx.dirty = True
    log(f"Added NAT bypass: {source} -> {dest}")


def cmd_nat_bypass_delete(ctx: MenuContext, args: list[str]) -> None:
    """Delete a NAT bypass rule."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not ctx.config.nat.bypass_pairs:
        error("No bypass rules configured")
        return

    if not args:
        # List and ask for selection
        print()
        print("Current bypass rules:")
        for i, b in enumerate(ctx.config.nat.bypass_pairs, 1):
            print(f"  {i}. {b.source} -> {b.destination}")
        print()
        choice = prompt_value("Delete which rule (number)")
        if not choice:
            return
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(ctx.config.nat.bypass_pairs):
                raise ValueError()
        except ValueError:
            error("Invalid selection")
            return
        bypass = ctx.config.nat.bypass_pairs[idx]
    else:
        # Try to parse source from args
        source = args[0]
        bypass = next((b for b in ctx.config.nat.bypass_pairs if b.source == source), None)
        if not bypass:
            error(f"Bypass rule for source {source} not found")
            return

    if prompt_yes_no(f"Delete bypass {bypass.source} -> {bypass.destination}?"):
        ctx.config.nat.bypass_pairs.remove(bypass)
        ctx.dirty = True
        log(f"Deleted NAT bypass: {bypass.source} -> {bypass.destination}")


def cmd_nat_set_prefix(ctx: MenuContext, args: list[str]) -> None:
    """Set the NAT pool prefix."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"  Current NAT prefix: {ctx.config.nat.bgp_prefix}")
    if ctx.config.bgp.enabled:
        print("  (This prefix will be announced via BGP)")
    print()

    prefix = prompt_value("New NAT pool prefix (CIDR)", validate_ipv4_cidr)
    if not prefix:
        return

    ctx.config.nat.bgp_prefix = prefix
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

    # Peer IPv4
    peer_ipv4 = prompt_value("Peer IPv4 address", validate_ipv4)
    if not peer_ipv4:
        return

    # Peer IPv6
    peer_ipv6 = prompt_value("Peer IPv6 address", validate_ipv6, required=False)

    # Peer ASN
    peer_asn_str = prompt_value("Peer AS number")
    if not peer_asn_str:
        return
    try:
        peer_asn = int(peer_asn_str)
    except ValueError:
        error("Invalid AS number")
        return

    # Update config
    ctx.config.bgp.enabled = True
    ctx.config.bgp.asn = asn
    ctx.config.bgp.router_id = router_id
    ctx.config.bgp.peer_ipv4 = peer_ipv4
    ctx.config.bgp.peer_ipv6 = peer_ipv6
    ctx.config.bgp.peer_asn = peer_asn

    ctx.dirty = True
    log(f"Enabled BGP: AS {asn} peering with AS {peer_asn}")


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
        ctx.dirty = True
        log("BGP disabled")


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
    """Open VPP NAT CLI."""
    socket = "/run/vpp/nat-cli.sock"
    if not Path(socket).exists():
        error("VPP NAT socket not found")
        return
    log("Entering VPP NAT CLI...")
    print("Type 'quit' to return\n")
    subprocess.run(["vppctl", "-s", socket], check=False)


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
        cmd_show(ctx, args)
        return True

    if command == "status":
        cmd_status(ctx, args)
        return True

    if command == "save":
        cmd_save(ctx, args)
        return True

    if command == "apply":
        cmd_apply(ctx, args)
        return True

    if command == "reload":
        cmd_reload(ctx, args)
        return True

    # Path-specific commands
    path = ctx.path

    # Shell commands
    if path == ["shell", "routing"] or (path == ["shell"] and command == "routing"):
        cmd_shell_routing(ctx, args)
        return True
    if path == ["shell", "core"] or (path == ["shell"] and command == "core"):
        cmd_shell_core(ctx, args)
        return True
    if path == ["shell", "nat"] or (path == ["shell"] and command == "nat"):
        cmd_shell_nat(ctx, args)
        return True

    # Loopback commands
    if path == ["loopbacks"]:
        if command == "list":
            _show_loopbacks(ctx.config)
            return True
        if command == "add":
            cmd_loopback_add(ctx, args)
            return True
        if command == "delete":
            cmd_loopback_delete(ctx, args)
            return True

    # BVI commands
    if path == ["bvi"]:
        if command == "list":
            _show_bvi(ctx.config)
            return True
        if command == "add":
            cmd_bvi_add(ctx, args)
            return True
        if command == "delete":
            cmd_bvi_delete(ctx, args)
            return True

    # VLAN passthrough commands
    if path == ["vlan-passthrough"]:
        if command == "list":
            _show_vlan_passthrough(ctx.config)
            return True
        if command == "add":
            cmd_vlan_passthrough_add(ctx, args)
            return True
        if command == "delete":
            cmd_vlan_passthrough_delete(ctx, args)
            return True

    # Sub-interface commands (for external and internal interfaces)
    if len(path) >= 3 and path[-1] == "subinterfaces":
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

    # NAT commands
    if path == ["nat"]:
        if command == "set-prefix":
            cmd_nat_set_prefix(ctx, args)
            return True

    if path == ["nat", "mappings"]:
        if command == "list":
            _show_nat_mappings(ctx.config)
            return True
        if command == "add":
            cmd_nat_mapping_add(ctx, args)
            return True
        if command == "delete":
            cmd_nat_mapping_delete(ctx, args)
            return True

    if path == ["nat", "bypass"]:
        if command == "list":
            _show_nat_bypass(ctx.config)
            return True
        if command == "add":
            cmd_nat_bypass_add(ctx, args)
            return True
        if command == "delete":
            cmd_nat_bypass_delete(ctx, args)
            return True

    # BGP commands
    if path == ["routing", "bgp"]:
        if command == "enable":
            cmd_bgp_enable(ctx, args)
            return True
        if command == "disable":
            cmd_bgp_disable(ctx, args)
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
