"""
Module management commands for REPL.

This module contains commands for listing, installing, enabling, and
disabling VPP modules.
"""

from pathlib import Path

from imp_lib.common import log, warn, error, info

# Import module system
try:
    from module_loader import (
        list_available_modules,
        list_example_modules,
        install_module_from_example,
        MODULE_DEFINITIONS_DIR,
        MODULE_EXAMPLES_DIR,
    )
    MODULE_LOADER_AVAILABLE = True
except ImportError:
    MODULE_LOADER_AVAILABLE = False
    MODULE_DEFINITIONS_DIR = Path("/persistent/config/modules")
    MODULE_EXAMPLES_DIR = Path("/usr/share/imp/module-examples")

    def list_available_modules():
        return []

    def list_example_modules():
        return []

    def install_module_from_example(name):
        raise FileNotFoundError()


def cmd_modules_available(ctx, args: list[str]) -> None:
    """List available module examples that can be installed."""
    if not MODULE_LOADER_AVAILABLE:
        error("Module loader not available")
        return

    examples = list_example_modules()
    if not examples:
        info(f"No module examples found in {MODULE_EXAMPLES_DIR}")
        print("\nTo add module examples, either:")
        print("  1. Rebuild the image with latest install-imp script")
        print("  2. Manually create module YAML files in /usr/share/imp/module-examples/")
        print("  3. Create modules directly in /persistent/config/modules/")
        print("\nSee VPP_MODULES.md for module YAML format documentation.")
        return

    print("\nAvailable module examples:")
    for name, display, desc in examples:
        print(f"  {name:12} - {display}")
        if desc:
            print(f"               {desc}")
    print(f"\nInstall with: config modules install <name>")


def cmd_modules_list(ctx, args: list[str]) -> None:
    """List installed modules."""
    if not MODULE_LOADER_AVAILABLE:
        error("Module loader not available")
        return

    modules = list_available_modules()
    if not modules:
        info(f"No modules installed in {MODULE_DEFINITIONS_DIR}")
        info("Use 'config modules install <name>' to install from examples")
        return

    # Check which are enabled in config
    enabled_modules = set()
    if ctx.config and hasattr(ctx.config, 'modules'):
        for m in ctx.config.modules:
            if m.get('enabled'):
                enabled_modules.add(m.get('name'))

    print("\nInstalled modules:")
    for name, display, desc in modules:
        status = "[enabled]" if name in enabled_modules else "[disabled]"
        print(f"  {name:12} {status:11} - {display}")
    print()


def cmd_modules_install(ctx, args: list[str]) -> None:
    """Install a module from examples."""
    if not MODULE_LOADER_AVAILABLE:
        error("Module loader not available")
        return

    if not args:
        error("Usage: config modules install <name>")
        info("List available modules with: config modules available")
        return

    name = args[0]
    try:
        install_module_from_example(name)
        log(f"Installed module '{name}'")
        info(f"Enable with: config modules enable {name}")
    except FileNotFoundError:
        error(f"Module example '{name}' not found")
        info("List available modules with: config modules available")
    except FileExistsError:
        warn(f"Module '{name}' is already installed")


def cmd_modules_enable(ctx, args: list[str]) -> None:
    """Enable a module."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not args:
        error("Usage: config modules enable <name>")
        return

    name = args[0]

    # Check if module definition exists
    module_yaml = MODULE_DEFINITIONS_DIR / f"{name}.yaml"
    if not module_yaml.exists():
        error(f"Module '{name}' not installed")
        info(f"Install with: config modules install {name}")
        return

    # Check if already in modules list
    for m in ctx.config.modules:
        if m.get('name') == name:
            if m.get('enabled'):
                warn(f"Module '{name}' is already enabled")
                return
            m['enabled'] = True
            ctx.dirty = True
            log(f"Enabled module '{name}'")
            return

    # Add new module entry
    ctx.config.modules.append({
        'name': name,
        'enabled': True,
        'config': {}
    })
    ctx.dirty = True
    log(f"Enabled module '{name}'")
    info(f"Configure with: config {name} ...")


def cmd_modules_disable(ctx, args: list[str]) -> None:
    """Disable a module."""
    if not ctx.config:
        error("No configuration loaded")
        return

    if not args:
        error("Usage: config modules disable <name>")
        return

    name = args[0]

    for m in ctx.config.modules:
        if m.get('name') == name:
            if not m.get('enabled'):
                warn(f"Module '{name}' is already disabled")
                return
            m['enabled'] = False
            ctx.dirty = True
            log(f"Disabled module '{name}'")
            return

    error(f"Module '{name}' not in configuration")
