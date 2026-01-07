"""
Shell commands for REPL.

This module provides commands to access VPP and FRR shells directly.
"""

import subprocess
from pathlib import Path

from imp_lib.common import log, warn, error
from imp_lib.common.vpp import get_vpp_socket, get_available_vpp_instances


def list_running_modules() -> list[str]:
    """
    Discover running VPP module sockets (excludes core).

    Returns:
        List of module names (e.g., ['nat', 'nat64'])
    """
    return [name for name in get_available_vpp_instances() if name != "core"]


def cmd_shell_routing(ctx, args: list[str]) -> None:
    """Open FRR vtysh shell."""
    ns_path = Path(f"/var/run/netns/dataplane")
    if not ns_path.exists():
        error("Dataplane namespace not found")
        return
    log("Entering FRR routing shell (vtysh)...")
    print("Type 'exit' to return\n")
    subprocess.run(["ip", "netns", "exec", "dataplane", "vtysh"], check=False)


def cmd_shell_core(ctx, args: list[str]) -> None:
    """Open VPP core CLI."""
    socket = "/run/vpp/core-cli.sock"
    if not Path(socket).exists():
        error("VPP core socket not found")
        return
    log("Entering VPP core CLI...")
    print("Type 'quit' to return\n")
    subprocess.run(["vppctl", "-s", socket], check=False)


def cmd_shell_nat(ctx, args: list[str]) -> None:
    """Open VPP NAT CLI (backwards compat)."""
    cmd_shell_module(ctx, ["nat"])


def cmd_shell_module(ctx, args: list[str]) -> None:
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
