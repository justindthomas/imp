"""
VPP graph trace tool implementations for IMP agent.

This module contains tools for tracing packets through VPP's
processing graph to debug packet flow and routing issues.
"""

import re
from pathlib import Path

from imp_lib.common.vpp import get_vpp_socket, get_available_vpp_instances, vpp_exec


# =============================================================================
# Trace Tool Implementations
# =============================================================================

def tool_start_trace(instance: str, input_node: str, count: int = 50) -> str:
    """Start VPP graph tracing."""
    socket = get_vpp_socket(instance)
    if not Path(socket).exists():
        available = get_available_vpp_instances()
        return f"Error: VPP instance '{instance}' not found. Available: {', '.join(available) if available else 'none'}"

    cmd = f"trace add {input_node} {count}"
    success, output = vpp_exec(cmd, instance)

    if success:
        return f"Started tracing {count} packets from {input_node} on {instance}"
    else:
        return f"Error starting trace: {output}"


def tool_show_trace(instance: str, max_packets: int = 10) -> str:
    """Show VPP graph trace output."""
    socket = get_vpp_socket(instance)
    if not Path(socket).exists():
        available = get_available_vpp_instances()
        return f"Error: VPP instance '{instance}' not found. Available: {', '.join(available) if available else 'none'}"

    success, output = vpp_exec(f"show trace max {max_packets}", instance)

    if success:
        # Check if there are actual packet traces (not just "No packets in trace buffer" messages)
        packets = len(re.findall(r'^Packet \d+', output, re.MULTILINE))
        if packets == 0:
            return "No packets traced. Use start_trace to begin tracing."
        # Truncate if too long
        lines = output.split('\n')
        if len(lines) > 100:
            output = '\n'.join(lines[:100])
            output += f"\n... ({len(lines) - 100} more lines)"
        return output
    else:
        return f"Error getting trace: {output}"


def tool_get_trace_status() -> str:
    """Show trace status on all running VPP instances."""
    instances = get_available_vpp_instances()
    if not instances:
        return "No VPP instances running"

    lines = ["Trace Status:"]

    for instance in instances:
        # Get trace and count actual "Packet N" entries (across all threads)
        success, output = vpp_exec("show trace", instance)
        if success:
            packets = len(re.findall(r'^Packet \d+', output, re.MULTILINE))
            if packets > 0:
                lines.append(f"  {instance}: {packets} packets traced")
            else:
                lines.append(f"  {instance}: No packets traced")
        else:
            lines.append(f"  {instance}: Error - {output}")

    return "\n".join(lines)


def tool_clear_trace(instance: str) -> str:
    """Clear trace buffer."""
    socket = get_vpp_socket(instance)
    if not Path(socket).exists():
        available = get_available_vpp_instances()
        return f"Error: VPP instance '{instance}' not found. Available: {', '.join(available) if available else 'none'}"

    success, output = vpp_exec("clear trace", instance)
    if success:
        return f"Trace buffer cleared on {instance}"
    else:
        return f"Error clearing trace: {output}"
