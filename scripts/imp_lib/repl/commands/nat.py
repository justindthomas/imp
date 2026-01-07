"""
NAT configuration commands for REPL.

This module contains commands for managing NAT mappings, bypass rules,
and NAT prefix configuration.
"""

from imp_lib.common import Colors, log, warn, error
from imp_lib.repl.display import get_nat_config
from .crud import prompt_value, prompt_yes_no

from imp_lib.config import validate_ipv4_cidr


def find_module(config, name: str):
    """Find module dict by name in config.modules list."""
    if not config or not hasattr(config, 'modules') or not config.modules:
        return None
    for module in config.modules:
        if module.get('name') == name:
            return module
    return None


def ensure_nat_module(config) -> dict:
    """Ensure NAT module exists in config.modules, return module dict."""
    if not hasattr(config, 'modules'):
        config.modules = []

    nat_module = find_module(config, 'nat')
    if not nat_module:
        nat_module = {
            'name': 'nat',
            'enabled': True,
            'config': {
                'bgp_prefix': '',
                'mappings': [],
                'bypass_pairs': []
            }
        }
        config.modules.append(nat_module)
    elif not nat_module.get('enabled'):
        nat_module['enabled'] = True

    # Ensure config dict exists
    if 'config' not in nat_module:
        nat_module['config'] = {'bgp_prefix': '', 'mappings': [], 'bypass_pairs': []}

    return nat_module


# =============================================================================
# NAT Mapping CRUD Operations
# =============================================================================

def cmd_nat_mapping_add(ctx, args: list[str]) -> None:
    """Add a new NAT mapping."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"{Colors.BOLD}Add NAT Mapping{Colors.NC}")
    print()
    print("  Maps internal network to a public NAT pool")
    print()

    # Ensure NAT module exists
    nat_module = ensure_nat_module(ctx.config)
    nat_cfg = nat_module['config']
    if 'mappings' not in nat_cfg:
        nat_cfg['mappings'] = []

    # Source network
    source = prompt_value("Source network (CIDR, e.g., 10.0.0.0/24)", validate_ipv4_cidr)
    if not source:
        return

    # Check for duplicate
    if any(m.get('source_network') == source for m in nat_cfg['mappings']):
        error(f"Mapping for {source} already exists")
        return

    # NAT pool
    print()
    print(f"  Current NAT prefix: {nat_cfg.get('bgp_prefix', 'not set')}")
    print()
    nat_pool = prompt_value("NAT pool (CIDR for det44, e.g., 23.177.24.96/29)", validate_ipv4_cidr)
    if not nat_pool:
        return

    # Add to config as dict
    nat_cfg['mappings'].append({
        'source_network': source,
        'nat_pool': nat_pool
    })

    ctx.dirty = True
    log(f"Added NAT mapping: {source} -> {nat_pool}")


def cmd_nat_mapping_delete(ctx, args: list[str]) -> None:
    """Delete a NAT mapping."""
    if not ctx.config:
        error("No configuration loaded")
        return

    nat_cfg = get_nat_config(ctx.config)
    if not nat_cfg:
        error("NAT module not configured")
        return

    mappings = nat_cfg.get('mappings', [])
    if not mappings:
        error("No NAT mappings configured")
        return

    if not args:
        # List and ask for selection
        print()
        print("Current mappings:")
        for i, m in enumerate(mappings, 1):
            print(f"  {i}. {m.get('source_network')} -> {m.get('nat_pool')}")
        print()
        choice = prompt_value("Delete which mapping (number)")
        if not choice:
            return
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(mappings):
                raise ValueError()
        except ValueError:
            error("Invalid selection")
            return
        mapping = mappings[idx]
    else:
        source = args[0]
        mapping = next((m for m in mappings if m.get('source_network') == source), None)
        if not mapping:
            error(f"NAT mapping for {source} not found")
            return

    if prompt_yes_no(f"Delete mapping {mapping.get('source_network')} -> {mapping.get('nat_pool')}?"):
        mappings.remove(mapping)
        ctx.dirty = True
        log(f"Deleted NAT mapping: {mapping.get('source_network')}")


# =============================================================================
# NAT Bypass CRUD Operations
# =============================================================================

def cmd_nat_bypass_add(ctx, args: list[str]) -> None:
    """Add a new NAT bypass rule."""
    if not ctx.config:
        error("No configuration loaded")
        return

    print()
    print(f"{Colors.BOLD}Add NAT Bypass Rule{Colors.NC}")
    print()
    print("  Traffic matching these source/destination pairs bypasses NAT")
    print()

    # Ensure NAT module exists
    nat_module = ensure_nat_module(ctx.config)
    nat_cfg = nat_module['config']
    if 'bypass_pairs' not in nat_cfg:
        nat_cfg['bypass_pairs'] = []

    # Source network
    source = prompt_value("Source network (CIDR)", validate_ipv4_cidr)
    if not source:
        return

    # Destination network
    dest = prompt_value("Destination network (CIDR)", validate_ipv4_cidr)
    if not dest:
        return

    # Check for duplicate
    if any(b.get('source') == source and b.get('destination') == dest for b in nat_cfg['bypass_pairs']):
        error(f"Bypass rule {source} -> {dest} already exists")
        return

    # Add to config as dict
    nat_cfg['bypass_pairs'].append({
        'source': source,
        'destination': dest
    })

    ctx.dirty = True
    log(f"Added NAT bypass: {source} -> {dest}")


def cmd_nat_bypass_delete(ctx, args: list[str]) -> None:
    """Delete a NAT bypass rule."""
    if not ctx.config:
        error("No configuration loaded")
        return

    nat_cfg = get_nat_config(ctx.config)
    if not nat_cfg:
        error("NAT module not configured")
        return

    bypass_pairs = nat_cfg.get('bypass_pairs', [])
    if not bypass_pairs:
        error("No bypass rules configured")
        return

    if not args:
        # List and ask for selection
        print()
        print("Current bypass rules:")
        for i, b in enumerate(bypass_pairs, 1):
            print(f"  {i}. {b.get('source')} -> {b.get('destination')}")
        print()
        choice = prompt_value("Delete which rule (number)")
        if not choice:
            return
        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(bypass_pairs):
                raise ValueError()
        except ValueError:
            error("Invalid selection")
            return
        bypass = bypass_pairs[idx]
    else:
        # Try to parse source from args
        source = args[0]
        bypass = next((b for b in bypass_pairs if b.get('source') == source), None)
        if not bypass:
            error(f"Bypass rule for source {source} not found")
            return

    if prompt_yes_no(f"Delete bypass {bypass.get('source')} -> {bypass.get('destination')}?"):
        bypass_pairs.remove(bypass)
        ctx.dirty = True
        log(f"Deleted NAT bypass: {bypass.get('source')} -> {bypass.get('destination')}")


def cmd_nat_set_prefix(ctx, args: list[str]) -> None:
    """Set the NAT pool prefix."""
    if not ctx.config:
        error("No configuration loaded")
        return

    # Ensure NAT module exists
    nat_module = ensure_nat_module(ctx.config)
    nat_cfg = nat_module['config']

    print()
    print(f"  Current NAT prefix: {nat_cfg.get('bgp_prefix', 'not set')}")
    if ctx.config.bgp.enabled:
        print("  (This prefix will be announced via BGP)")
    print()

    prefix = prompt_value("New NAT pool prefix (CIDR)", validate_ipv4_cidr)
    if not prefix:
        return

    nat_cfg['bgp_prefix'] = prefix
    ctx.dirty = True
    log(f"Set NAT prefix to: {prefix}")
