"""
Snapshot commands for REPL.

This module provides commands for ZFS snapshot management
(list, create, delete, export, import, rollback).

These commands delegate to the `imp snapshot` CLI for the actual operations.
"""

import subprocess

from imp_lib.common import error


def cmd_snapshot_list(ctx, args: list[str]) -> None:
    """List all snapshots."""
    subprocess.run(["imp", "snapshot", "list"], check=False)


def cmd_snapshot_create(ctx, args: list[str]) -> None:
    """Create a snapshot."""
    cmd = ["imp", "snapshot", "create"]
    if args:
        cmd.append(args[0])  # Optional snapshot name
    subprocess.run(cmd, check=False)


def cmd_snapshot_delete(ctx, args: list[str]) -> None:
    """Delete a snapshot."""
    if not args:
        error("Usage: delete <name>")
        print("  Use 'snapshot list' to see available snapshots")
        return
    subprocess.run(["imp", "snapshot", "delete", args[0]], check=False)


def cmd_snapshot_export(ctx, args: list[str]) -> None:
    """Export a snapshot to file."""
    if not args:
        error("Usage: export <name> [--full] [--clean] [-o output]")
        print("  Use 'snapshot list' to see available snapshots")
        print("  --clean: Remove generated configs for deployment image")
        return

    cmd = ["imp", "snapshot", "export", args[0]]

    # Parse remaining args for --full, --clean, and -o
    i = 1
    while i < len(args):
        if args[i] == "--full":
            cmd.append("--full")
        elif args[i] == "--clean":
            cmd.append("--clean")
        elif args[i] in ("-o", "--output") and i + 1 < len(args):
            cmd.extend(["-o", args[i + 1]])
            i += 1
        i += 1

    subprocess.run(cmd, check=False)


def cmd_snapshot_import(ctx, args: list[str]) -> None:
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


def cmd_snapshot_rollback(ctx, args: list[str]) -> None:
    """Rollback to a snapshot."""
    if not args:
        error("Usage: rollback <name>")
        print("  Use 'snapshot list' to see available snapshots")
        return
    subprocess.run(["imp", "snapshot", "rollback", args[0]], check=False)
