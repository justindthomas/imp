"""
Packet capture tool implementations for IMP agent.

This module contains tools for capturing and analyzing packets
using VPP's pcap trace and tshark.
"""

import glob
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from imp_lib.common.vpp import get_vpp_socket, get_available_vpp_instances, vpp_exec


# =============================================================================
# Helper Functions
# =============================================================================

def _format_size(size_bytes: int) -> str:
    """Format bytes as human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# =============================================================================
# Capture Tool Implementations
# =============================================================================

def tool_start_capture(instance: str, interface: str = "any", direction: str = "rx tx",
                       max_packets: int = 10000, filename: str = None) -> str:
    """Start a packet capture on a VPP instance."""
    socket = get_vpp_socket(instance)
    if not Path(socket).exists():
        available = get_available_vpp_instances()
        return f"Error: VPP instance '{instance}' not found. Available: {', '.join(available) if available else 'none'}"

    # Validate direction
    valid_directions = ["rx", "tx", "drop", "rx tx", "rx tx drop", "tx rx", "rx drop", "tx drop"]
    if direction not in valid_directions:
        return f"Error: invalid direction '{direction}'. Use: rx, tx, drop, 'rx tx', or 'rx tx drop'"

    # Generate filename if not specified (include .pcap so VPP writes with extension)
    if not filename:
        filename = f"capture-{instance}-{int(time.time())}.pcap"
    elif not filename.endswith(".pcap"):
        filename += ".pcap"

    # Build command
    cmd = f"pcap trace {direction} intfc {interface} file {filename}"
    if max_packets and max_packets > 0:
        cmd += f" max {max_packets}"

    success, output = vpp_exec(cmd, instance)
    if success:
        return f"Started capture on {instance}: /tmp/{filename}.pcap (interface: {interface}, direction: {direction})"
    else:
        return f"Error starting capture: {output}"


def tool_stop_capture(instance: str) -> str:
    """Stop an active packet capture."""
    socket = get_vpp_socket(instance)
    if not Path(socket).exists():
        available = get_available_vpp_instances()
        return f"Error: VPP instance '{instance}' not found. Available: {', '.join(available) if available else 'none'}"

    success, output = vpp_exec("pcap trace off", instance)
    if success:
        if output:
            return f"Stopped capture on {instance}: {output}"
        return f"Stopped capture on {instance}"
    else:
        return f"Error stopping capture: {output}"


def tool_get_capture_status() -> str:
    """Show active captures on all running VPP instances."""
    instances = get_available_vpp_instances()
    if not instances:
        return "No VPP instances running"

    lines = ["Capture Status:"]

    for instance in instances:
        success, output = vpp_exec("pcap trace status", instance)
        if success:
            if not output.strip() or "No pcap" in output or "disabled" in output.lower():
                lines.append(f"  {instance}: No active capture")
            else:
                # Parse "X of Y pkts" to determine if capture is complete
                match = re.search(r'(\d+)\s+of\s+(\d+)\s+pkts', output)
                if match:
                    captured, limit = int(match.group(1)), int(match.group(2))
                    if captured >= limit:
                        lines.append(f"  {instance}: COMPLETE - captured {captured}/{limit} packets (limit reached, file written)")
                    else:
                        lines.append(f"  {instance}: ACTIVE - captured {captured}/{limit} packets")
                else:
                    # Fallback to raw output
                    lines.append(f"  {instance}: {output.split(chr(10))[0]}")
        else:
            lines.append(f"  {instance}: Error - {output}")

    return "\n".join(lines)


def tool_list_capture_files() -> str:
    """List pcap files in /tmp."""
    pcap_files = glob.glob("/tmp/*.pcap")
    if not pcap_files:
        return "No pcap files found in /tmp"

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

    lines = ["Capture Files:"]
    for f in files:
        size_str = _format_size(f["size"])
        mtime_str = datetime.fromtimestamp(f["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"  {f['name']}: {size_str}, {mtime_str}")

    return "\n".join(lines)


def tool_analyze_capture(filename: str) -> str:
    """Analyze a pcap file using tshark."""
    # Resolve path
    if not filename.startswith("/"):
        filename = f"/tmp/{filename}"
    if not filename.endswith(".pcap"):
        filename += ".pcap"

    if not Path(filename).exists():
        return f"Error: File not found: {filename}"

    lines = [f"Analysis of {os.path.basename(filename)}:", ""]

    # File info with capinfos
    try:
        result = subprocess.run(
            ["capinfos", "-c", "-d", "-u", "-e", "-y", "-i", filename],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            lines.append("File Information:")
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    lines.append(f"  {line}")
            lines.append("")
    except FileNotFoundError:
        lines.append("(capinfos not available)")
    except Exception:
        pass

    # Protocol hierarchy
    try:
        result = subprocess.run(
            ["tshark", "-r", filename, "-q", "-z", "io,phs"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            lines.append("Protocol Hierarchy:")
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    lines.append(f"  {line}")
            lines.append("")
    except FileNotFoundError:
        lines.append("(tshark not available)")
        return "\n".join(lines)
    except Exception:
        pass

    # Top conversations
    try:
        result = subprocess.run(
            ["tshark", "-r", filename, "-q", "-z", "conv,ip"],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            conv_lines = result.stdout.strip().split('\n')
            lines.append("Top IPv4 Conversations:")
            # Show header + first 10 data lines
            for line in conv_lines[:12]:
                if line.strip():
                    lines.append(f"  {line}")
            if len(conv_lines) > 12:
                lines.append(f"  ... and {len(conv_lines) - 12} more")
    except Exception:
        pass

    return "\n".join(lines)


def tool_delete_capture(filename: str) -> str:
    """Delete a pcap file."""
    # Resolve path
    if not filename.startswith("/"):
        filepath = f"/tmp/{filename}"
    else:
        filepath = filename
    if not filepath.endswith(".pcap"):
        filepath += ".pcap"

    if not Path(filepath).exists():
        return f"Error: File not found: {filepath}"

    try:
        os.remove(filepath)
        return f"Deleted: {filepath}"
    except OSError as e:
        return f"Error deleting file: {e}"


def tool_tshark_query(filename: str, display_filter: str = None,
                      fields: str = None, max_packets: int = 50) -> str:
    """Run a tshark query on a pcap file for detailed analysis."""
    # Resolve path
    if not filename.startswith("/"):
        filename = f"/tmp/{filename}"
    if not filename.endswith(".pcap"):
        filename += ".pcap"

    if not Path(filename).exists():
        return f"Error: File not found: {filename}"

    # Build tshark command
    cmd = ["tshark", "-r", filename]

    # Add display filter
    if display_filter:
        cmd.extend(["-Y", display_filter])

    # Add field extraction or use default summary
    if fields:
        cmd.append("-T")
        cmd.append("fields")
        for field in fields.split(","):
            cmd.extend(["-e", field.strip()])
        cmd.extend(["-E", "header=y", "-E", "separator=\t"])

    # Limit output
    cmd.extend(["-c", str(max_packets)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0 and result.stderr.strip():
            return f"Error: {result.stderr.strip()}"

        output = result.stdout.strip()
        if not output:
            filter_msg = f" matching '{display_filter}'" if display_filter else ""
            return f"No packets found{filter_msg}"

        # Count lines and truncate if needed
        lines = output.split('\n')
        if len(lines) > 60:
            output = '\n'.join(lines[:60])
            output += f"\n... ({len(lines) - 60} more lines)"

        return output

    except subprocess.TimeoutExpired:
        return "Error: Query timed out"
    except FileNotFoundError:
        return "Error: tshark not installed"
    except Exception as e:
        return f"Error: {e}"
