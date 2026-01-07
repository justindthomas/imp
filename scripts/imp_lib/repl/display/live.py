"""
Live state display functions for IMP REPL.

These functions query VPP and FRR for live operational state
and display it to the user.
"""

import ipaddress
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from imp_lib.common import Colors, error
from imp_lib.common.vpp import vpp_exec

# Import module loader for show module commands
from imp_lib.modules import load_module_definition
MODULE_LOADER_AVAILABLE = True

# Config file path for reading module config
CONFIG_FILE = Path("/persistent/config/router.json")


def show_live_interfaces() -> None:
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


def pager(content: str, title: str = "") -> None:
    """Display content with paging if it exceeds terminal height."""
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


def show_live_route(af: str, prefix: str = None) -> None:
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
        pager(result.stdout, title)
    else:
        error(f"Failed to get {af_name} routes (FRR may not be running)")


def filter_fib_output(output: str, filter_prefix: str, is_ipv6: bool = False) -> str:
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


def show_live_fib(af: str, prefix: str = None) -> None:
    """Show forwarding table from VPP.

    Args:
        af: Address family ("ip" or "ipv6")
        prefix: Optional prefix filter (e.g., "192.168.0.0/16")
    """
    if af == "ip":
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
            output = filter_fib_output(output, prefix, is_ipv6)
        title = f"{af_name} FIB (VPP)"
        if prefix:
            title += f" - filter: {prefix}"
        pager(output, title)
    else:
        error(f"Failed to get {af_name} FIB: {output}")


def show_live_neighbors() -> None:
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


def show_live_bgp() -> None:
    """Show BGP neighbor status from FRR."""
    print()
    print(f"{Colors.BOLD}BGP Status (Live){Colors.NC}")
    print("=" * 70)

    print(f"\n{Colors.CYAN}IPv4 Unicast:{Colors.NC}")
    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", "show ip bgp summary"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout if result.stdout.strip() else "  (no peers)")
    else:
        error("Failed to get BGP status (FRR may not be running)")

    print(f"\n{Colors.CYAN}IPv6 Unicast:{Colors.NC}")
    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", "show bgp ipv6 unicast summary"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout if result.stdout.strip() else "  (no peers)")
    print()


def show_live_ospf() -> None:
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


def show_live_module(args: list[str]) -> None:
    """Show module-specific live state using show_commands from module YAML."""
    if not MODULE_LOADER_AVAILABLE:
        error("Module loader not available")
        return

    # Get enabled modules with show_commands
    enabled_modules = []
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                config_data = json.load(f)
            for mod in config_data.get("modules", []):
                if mod.get("enabled") and mod.get("name"):
                    mod_name = mod["name"]
                    try:
                        mod_def = load_module_definition(mod_name)
                        if mod_def.show_commands:
                            enabled_modules.append((mod_name, mod_def))
                    except Exception:
                        pass
        except Exception:
            pass

    if not args:
        # List available modules and their commands
        print()
        print(f"{Colors.BOLD}Module Show Commands:{Colors.NC}")
        if not enabled_modules:
            print("  (no modules with show commands enabled)")
        else:
            for mod_name, mod_def in enabled_modules:
                print(f"\n  {Colors.CYAN}{mod_name}{Colors.NC}:")
                for cmd in mod_def.show_commands:
                    desc = f" - {cmd.description}" if cmd.description else ""
                    print(f"    {cmd.name}{desc}")
        print()
        print("Usage: show module <module_name> <command>")
        print()
        return

    module_name = args[0]

    # Find the module
    mod_def = None
    for name, mdef in enabled_modules:
        if name == module_name:
            mod_def = mdef
            break

    if not mod_def:
        error(f"Module '{module_name}' not found or has no show commands")
        if enabled_modules:
            print(f"Available: {', '.join(n for n, _ in enabled_modules)}")
        return

    if len(args) < 2:
        # List commands for this module
        print()
        print(f"{Colors.BOLD}Show commands for {module_name}:{Colors.NC}")
        for cmd in mod_def.show_commands:
            desc = f" - {cmd.description}" if cmd.description else ""
            print(f"  {cmd.name}{desc}")
        print()
        print(f"Usage: show module {module_name} <command>")
        print()
        return

    cmd_name = args[1]

    # Find the command
    show_cmd = None
    for cmd in mod_def.show_commands:
        if cmd.name == cmd_name:
            show_cmd = cmd
            break

    if not show_cmd:
        error(f"Unknown command '{cmd_name}' for module '{module_name}'")
        print(f"Available: {', '.join(c.name for c in mod_def.show_commands)}")
        return

    # Execute the VPP command
    print()
    print(f"{Colors.BOLD}{show_cmd.description or show_cmd.name} ({module_name}){Colors.NC}")
    print("=" * 70)

    success, output = vpp_exec(show_cmd.vpp_command, module_name)
    if success:
        print(output if output.strip() else "  (no output)")
    else:
        error(f"Failed to execute: {output}")
    print()
