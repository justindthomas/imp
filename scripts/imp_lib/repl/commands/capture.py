"""
Packet capture commands for REPL.

This module provides commands for VPP packet capture (pcap trace).
"""

import os
import subprocess
from pathlib import Path

from imp_lib.common import Colors, log, warn, error
from imp_lib.common.vpp import vpp_exec

# VPP socket paths
VPP_CORE_SOCKET = "/run/vpp/core-cli.sock"
VPP_NAT_SOCKET = "/run/vpp/nat-cli.sock"


def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


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


def cmd_capture_start(ctx, args: list[str]) -> None:
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


def cmd_capture_stop(ctx, args: list[str]) -> None:
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


def cmd_capture_status(ctx, args: list[str]) -> None:
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


def cmd_capture_files(ctx, args: list[str]) -> None:
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


def cmd_capture_analyze(ctx, args: list[str]) -> None:
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


def cmd_capture_export(ctx, args: list[str]) -> None:
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


def cmd_capture_delete(ctx, args: list[str]) -> None:
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
