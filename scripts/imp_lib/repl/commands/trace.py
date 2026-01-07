"""
VPP graph trace commands for REPL.

This module provides commands for VPP graph tracing to analyze packet flow
through the VPP processing graph.
"""

import re
from pathlib import Path

from imp_lib.common import Colors, log, error
from imp_lib.common.vpp import vpp_exec

# VPP socket paths
VPP_CORE_SOCKET = "/run/vpp/core-cli.sock"
VPP_NAT_SOCKET = "/run/vpp/nat-cli.sock"

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


def cmd_trace_start(ctx, args: list[str]) -> None:
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


def cmd_trace_stop(ctx, args: list[str]) -> None:
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


def cmd_trace_status(ctx, args: list[str]) -> None:
    """Show trace status on both VPP instances."""
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


def cmd_trace_show(ctx, args: list[str]) -> None:
    """Show trace output from a VPP instance."""
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


def cmd_trace_clear(ctx, args: list[str]) -> None:
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
