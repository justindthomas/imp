"""
Live state lookup tool implementations for IMP agent.

This module contains tools that query live state from VPP and FRR
without modifying configuration.
"""

import ipaddress
import re
import subprocess

from imp_lib.common.vpp import vpp_exec


# =============================================================================
# FRR Routing Table Tools
# =============================================================================

def tool_show_ip_route(prefix: str = None) -> str:
    """Show IPv4 routing table from FRR."""
    if prefix:
        cmd = f"show ip route {prefix} longer-prefixes"
    else:
        cmd = "show ip route"

    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", cmd],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        output = result.stdout.strip()
        if not output:
            return "No routes found" + (f" matching {prefix}" if prefix else "")
        return output
    else:
        return f"Error: Failed to get routes (FRR may not be running)"


def tool_show_ipv6_route(prefix: str = None) -> str:
    """Show IPv6 routing table from FRR."""
    if prefix:
        cmd = f"show ipv6 route {prefix} longer-prefixes"
    else:
        cmd = "show ipv6 route"

    result = subprocess.run(
        ["ip", "netns", "exec", "dataplane", "vtysh", "-c", cmd],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        output = result.stdout.strip()
        if not output:
            return "No routes found" + (f" matching {prefix}" if prefix else "")
        return output
    else:
        return f"Error: Failed to get routes (FRR may not be running)"


# =============================================================================
# VPP FIB Tools
# =============================================================================

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
        match = prefix_pattern.match(line)
        if match:
            if include_current and current_entry:
                result_lines.extend(current_entry)

            current_prefix = match.group(1)
            current_entry = [line]

            try:
                entry_net = ipaddress.ip_network(current_prefix, strict=False)
                include_current = (
                    entry_net.network_address >= filter_net.network_address and
                    entry_net.broadcast_address <= filter_net.broadcast_address
                )
            except ValueError:
                include_current = False
        elif current_prefix is not None:
            current_entry.append(line)
        else:
            header_lines.append(line)

    if include_current and current_entry:
        result_lines.extend(current_entry)

    if result_lines:
        return '\n'.join(header_lines + result_lines)
    else:
        return f"No FIB entries within {filter_prefix}"


def tool_show_ip_fib(prefix: str = None) -> str:
    """Show IPv4 FIB from VPP."""
    # Always fetch all entries, filter client-side if needed
    cmd = "show ip fib"

    success, output = vpp_exec(cmd, "core")
    if success:
        output = output.strip()
        if not output:
            return "No FIB entries found"

        # Apply client-side filtering if prefix specified
        if prefix:
            output = _filter_fib_output(output, prefix, is_ipv6=False)

        # Limit output length for agent context
        lines = output.split('\n')
        if len(lines) > 100:
            return '\n'.join(lines[:100]) + f"\n... ({len(lines) - 100} more entries)"
        return output
    else:
        return f"Error: Failed to get FIB: {output}"


def tool_show_ipv6_fib(prefix: str = None) -> str:
    """Show IPv6 FIB from VPP."""
    # Always fetch all entries, filter client-side if needed
    cmd = "show ip6 fib"  # VPP uses ip6, not ipv6

    success, output = vpp_exec(cmd, "core")
    if success:
        output = output.strip()
        if not output:
            return "No FIB entries found"

        # Apply client-side filtering if prefix specified
        if prefix:
            output = _filter_fib_output(output, prefix, is_ipv6=True)

        # Limit output length for agent context
        lines = output.split('\n')
        if len(lines) > 100:
            return '\n'.join(lines[:100]) + f"\n... ({len(lines) - 100} more entries)"
        return output
    else:
        return f"Error: Failed to get FIB: {output}"


# =============================================================================
# VPP Interface and Neighbor Tools
# =============================================================================

def tool_show_interfaces_live() -> str:
    """Show live interface state from VPP."""
    success, output = vpp_exec("show interface", "core")
    if success:
        return output.strip() if output.strip() else "No interfaces found"
    else:
        return f"Error: Failed to get interfaces: {output}"


def tool_show_neighbors() -> str:
    """Show ARP and NDP neighbor tables from VPP."""
    lines = ["IPv4 Neighbors (ARP):"]

    success, output = vpp_exec("show ip neighbor", "core")
    if success:
        lines.append(output.strip() if output.strip() else "  (empty)")
    else:
        lines.append(f"  Error: {output}")

    lines.append("")
    lines.append("IPv6 Neighbors (NDP):")

    success, output = vpp_exec("show ip6 neighbor", "core")
    if success:
        lines.append(output.strip() if output.strip() else "  (empty)")
    else:
        lines.append(f"  Error: {output}")

    return '\n'.join(lines)
