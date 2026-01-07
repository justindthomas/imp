"""
VPP command execution utilities.

Provides functions for executing commands against VPP instances
via their CLI sockets.
"""

import subprocess
from pathlib import Path
from typing import Tuple, List


def get_vpp_socket(instance: str) -> str:
    """
    Get VPP CLI socket path for an instance.

    Args:
        instance: VPP instance name (e.g., "core", "nat", "nat64")

    Returns:
        Path to the VPP CLI socket
    """
    return f"/run/vpp/{instance}-cli.sock"


def get_available_vpp_instances() -> List[str]:
    """
    Get list of available VPP instances by scanning socket files.

    Returns:
        Sorted list of instance names (e.g., ["core", "nat"])
    """
    instances = []
    vpp_dir = Path("/run/vpp")
    if vpp_dir.exists():
        for sock in vpp_dir.glob("*-cli.sock"):
            name = sock.name.replace("-cli.sock", "")
            instances.append(name)
    return sorted(instances)


def vpp_exec(command: str, instance: str = "core") -> Tuple[bool, str]:
    """
    Execute a VPP CLI command and capture output.

    Args:
        command: VPP CLI command to execute (e.g., "show interface")
        instance: VPP instance name (default: "core")

    Returns:
        Tuple of (success: bool, output: str)
        On failure, output contains the error message.
    """
    socket = get_vpp_socket(instance)

    if not Path(socket).exists():
        available = get_available_vpp_instances()
        if available:
            return False, f"VPP {instance} socket not found. Available: {', '.join(available)}"
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
