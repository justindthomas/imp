"""
Read tool implementations for IMP agent.

This module contains tools that read configuration data without making changes.
"""


# =============================================================================
# Module Helpers
# =============================================================================

def get_module_config_dict(config, module_name: str) -> dict:
    """
    Get configuration for a specific module from config.modules.

    Returns:
        Module config dict or empty dict if not found/disabled
    """
    if not config or not hasattr(config, 'modules'):
        return {}

    for m in config.modules:
        if m.get('name') == module_name and m.get('enabled'):
            return m.get('config', {})
    return {}


def find_module(config, module_name: str) -> dict:
    """
    Find a module entry in config.modules.

    Returns:
        Module dict or None if not found
    """
    if not config or not hasattr(config, 'modules'):
        return None

    for m in config.modules:
        if m.get('name') == module_name:
            return m
    return None


# =============================================================================
# Read Tool Implementations
# =============================================================================

def tool_get_config_summary(config) -> str:
    """Get configuration summary."""
    if not config:
        return "No configuration loaded"

    lines = [f"Hostname: {config.hostname}"]

    if config.management:
        lines.append(f"Management: {config.management.iface} ({config.management.mode})")

    # Dataplane interfaces
    lines.append(f"Interfaces: {len(config.interfaces)}")
    for iface in config.interfaces:
        ipv4_str = ", ".join(f"{a.address}/{a.prefix}" for a in iface.ipv4) if iface.ipv4 else "none"
        lines.append(f"  {iface.name} ({iface.iface}): {ipv4_str}")
        if iface.subinterfaces:
            lines.append(f"    Sub-interfaces: {len(iface.subinterfaces)}")

    # Routes
    lines.append(f"Routes: {len(config.routes)}")
    for route in config.routes:
        lines.append(f"  {route.destination} via {route.via}")

    if config.bgp.enabled:
        peer_count = len(config.bgp.peers) if config.bgp.peers else 0
        lines.append(f"BGP: AS {config.bgp.asn}, {peer_count} peer(s)")
    else:
        lines.append("BGP: Disabled")

    nat_config = get_module_config_dict(config, 'nat')
    if nat_config:
        lines.append(f"NAT prefix: {nat_config.get('bgp_prefix', 'not set')}")
        lines.append(f"NAT mappings: {len(nat_config.get('mappings', []))}")
        lines.append(f"NAT bypass rules: {len(nat_config.get('bypass_pairs', []))}")
    else:
        lines.append("NAT: Not configured (use 'imp config modules enable nat')")
    lines.append(f"Loopbacks: {len(config.loopbacks)}")
    lines.append(f"BVI domains: {len(config.bvi_domains)}")
    lines.append(f"VLAN passthrough: {len(config.vlan_passthrough)}")

    return "\n".join(lines)


def tool_get_interfaces(config) -> str:
    """Get all interfaces."""
    if not config:
        return "No configuration loaded"

    lines = []

    if config.management:
        m = config.management
        if m.mode == "dhcp":
            lines.append(f"management ({m.iface}): DHCP")
        else:
            lines.append(f"management ({m.iface}): {m.ipv4}/{m.ipv4_prefix}, gateway {m.ipv4_gateway}")

    # Dataplane interfaces
    for iface in config.interfaces:
        ipv4_str = ", ".join(f"{a.address}/{a.prefix}" for a in iface.ipv4) if iface.ipv4 else "none"
        lines.append(f"{iface.name} ({iface.iface}, PCI {iface.pci}): {ipv4_str}")
        if iface.ipv6:
            ipv6_str = ", ".join(f"{a.address}/{a.prefix}" for a in iface.ipv6)
            lines.append(f"  IPv6: {ipv6_str}")
        for sub in iface.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            lines.append(f"  .{sub.vlan_id}: {', '.join(ips)}{lcp}")

    return "\n".join(lines) if lines else "No interfaces configured"


def tool_get_interface_detail(config, interface: str) -> str:
    """Get detailed interface info."""
    if not config:
        return "No configuration loaded"

    if interface == "management":
        if not config.management:
            return "Management interface not configured"
        m = config.management
        lines = [
            f"Interface: {m.iface}",
            f"Mode: {m.mode}",
        ]
        if m.mode == "static":
            lines.extend([
                f"IPv4: {m.ipv4}/{m.ipv4_prefix}",
                f"Gateway: {m.ipv4_gateway}",
            ])
        return "\n".join(lines)

    # Check dataplane interfaces by name
    for iface in config.interfaces:
        if iface.name == interface:
            lines = [
                f"Name: {iface.name}",
                f"Interface: {iface.iface}",
                f"PCI: {iface.pci}",
                f"MTU: {iface.mtu}",
            ]
            if iface.ipv4:
                for addr in iface.ipv4:
                    lines.append(f"IPv4: {addr.address}/{addr.prefix}")
            if iface.ipv6:
                for addr in iface.ipv6:
                    lines.append(f"IPv6: {addr.address}/{addr.prefix}")
            lines.append(f"Sub-interfaces: {len(iface.subinterfaces)}")
            for sub in iface.subinterfaces:
                ips = []
                if sub.ipv4:
                    ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
                if sub.ipv6:
                    ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
                lcp = " (LCP)" if sub.create_lcp else ""
                lines.append(f"  .{sub.vlan_id}: {', '.join(ips)}{lcp}")
            return "\n".join(lines)

    available = ["management"] + [i.name for i in config.interfaces]
    return f"Interface '{interface}' not found. Available: {', '.join(available)}"


def tool_get_routes(config) -> str:
    """Get configured static routes."""
    if not config:
        return "No configuration loaded"

    if not config.routes:
        return "No routes configured"

    lines = ["Static Routes:"]
    for route in config.routes:
        via_iface = f" via {route.interface}" if route.interface else ""
        lines.append(f"  {route.destination} via {route.via}{via_iface}")

    return "\n".join(lines)


def tool_get_loopbacks(config) -> str:
    """Get loopback interfaces."""
    if not config:
        return "No configuration loaded"

    if not config.loopbacks:
        return "No loopbacks configured"

    lines = []
    for lo in config.loopbacks:
        ips = []
        if lo.ipv4:
            ips.append(f"{lo.ipv4}/{lo.ipv4_prefix}")
        if lo.ipv6:
            ips.append(f"{lo.ipv6}/{lo.ipv6_prefix}")
        lcp = " (LCP)" if lo.create_lcp else ""
        lines.append(f"loop{lo.instance} ({lo.name}): {', '.join(ips)}{lcp}")

    return "\n".join(lines)


def tool_get_bvi_domains(config) -> str:
    """Get BVI domains."""
    if not config:
        return "No configuration loaded"

    if not config.bvi_domains:
        return "No BVI domains configured"

    lines = []
    for bvi in config.bvi_domains:
        ips = []
        if bvi.ipv4:
            ips.append(f"{bvi.ipv4}/{bvi.ipv4_prefix}")
        if bvi.ipv6:
            ips.append(f"{bvi.ipv6}/{bvi.ipv6_prefix}")
        lcp = " (LCP)" if bvi.create_lcp else ""
        members = ", ".join(
            f"{m.interface}.{m.vlan_id}" if m.vlan_id else m.interface
            for m in bvi.members
        )
        lines.append(f"bvi{bvi.bridge_id} ({bvi.name}): {', '.join(ips)}{lcp}")
        lines.append(f"  Members: {members}")

    return "\n".join(lines)


def tool_get_vlan_passthrough(config) -> str:
    """Get VLAN passthrough rules."""
    if not config:
        return "No configuration loaded"

    if not config.vlan_passthrough:
        return "No VLAN passthrough rules configured"

    lines = []
    for v in config.vlan_passthrough:
        if v.inner_vlan:
            lines.append(f"VLAN {v.vlan_id}.{v.inner_vlan} (QinQ) {v.from_interface} <-> {v.to_interface}")
        elif v.vlan_type == "dot1ad":
            lines.append(f"S-VLAN {v.vlan_id} (QinQ) {v.from_interface} <-> {v.to_interface}")
        else:
            lines.append(f"VLAN {v.vlan_id} (802.1Q) {v.from_interface} <-> {v.to_interface}")

    return "\n".join(lines)


def tool_list_modules(config) -> str:
    """List available IMP VPP modules and their status."""
    from imp_lib.modules import (
        list_available_modules,
        list_example_modules,
        load_module_definition,
    )

    lines = ["IMP VPP Modules:"]

    # Get installed modules
    installed = list_available_modules()
    examples = list_example_modules()

    # Get enabled modules from config
    enabled_modules = {}
    if config and hasattr(config, 'modules'):
        for m in config.modules:
            if m.get('name'):
                enabled_modules[m['name']] = m.get('enabled', False)

    if not installed and not examples:
        lines.append("  No modules installed or available")
        lines.append(f"\nInstall modules from examples with: config modules install <name>")
        return "\n".join(lines)

    lines.append("\nInstalled modules:")
    if installed:
        for name, display_name, desc in installed:
            status = "enabled" if enabled_modules.get(name) else "disabled"
            lines.append(f"  {name}: {display_name} [{status}]")

            # Show available commands for this module
            try:
                mod_def = load_module_definition(name)
                if mod_def.cli_commands:
                    lines.append(f"    Commands: {', '.join(c.path for c in mod_def.cli_commands)}")
            except Exception:
                pass
    else:
        lines.append("  (none)")

    # Show available examples
    example_names = {e[0] for e in examples}
    installed_names = {i[0] for i in installed}
    uninstalled = example_names - installed_names

    if uninstalled:
        lines.append("\nAvailable to install:")
        for name, display_name, desc in examples:
            if name in uninstalled:
                lines.append(f"  {name}: {display_name}")

    return "\n".join(lines)


def tool_get_module_config(config, module_name: str) -> str:
    """Get configuration for a specific IMP VPP module."""
    if not config:
        return "No configuration loaded"

    module = find_module(config, module_name)
    if not module:
        return f"Module '{module_name}' not found in configuration"

    if not module.get('enabled'):
        return f"Module '{module_name}' is disabled"

    mod_config = module.get('config', {})
    if not mod_config:
        return f"Module '{module_name}' has no configuration"

    # Format the config nicely
    lines = [f"Module '{module_name}' configuration:"]

    def format_value(key, value, indent=2):
        prefix = " " * indent
        if isinstance(value, list):
            if not value:
                return [f"{prefix}{key}: (empty)"]
            result = [f"{prefix}{key}: ({len(value)} items)"]
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    item_str = ", ".join(f"{k}={v}" for k, v in item.items())
                    result.append(f"{prefix}  - {item_str}")
                else:
                    result.append(f"{prefix}  - {item}")
            return result
        elif isinstance(value, dict):
            result = [f"{prefix}{key}:"]
            for k, v in value.items():
                result.extend(format_value(k, v, indent + 2))
            return result
        else:
            return [f"{prefix}{key}: {value}"]

    for key, value in mod_config.items():
        lines.extend(format_value(key, value))

    return "\n".join(lines)


def tool_execute_module_command(config, ctx, module_name: str, command_path: str, params: dict = None) -> str:
    """Execute a command defined by an IMP VPP module."""
    from imp_lib.modules import load_module_definition

    if not config:
        return "No configuration loaded"

    # Load module definition to get command info
    try:
        mod_def = load_module_definition(module_name)
    except FileNotFoundError:
        return f"Module '{module_name}' not installed. Use 'config modules install {module_name}' first."
    except Exception as e:
        return f"Error loading module '{module_name}': {e}"

    # Find the command
    cmd = None
    for c in mod_def.cli_commands:
        if c.path == command_path:
            cmd = c
            break

    if not cmd:
        available = [c.path for c in mod_def.cli_commands]
        return f"Command '{command_path}' not found in module '{module_name}'. Available: {', '.join(available)}"

    # Ensure module exists in config
    module = find_module(config, module_name)
    if not module:
        # Create the module entry
        config.modules.append({
            'name': module_name,
            'enabled': True,
            'config': {}
        })
        module = config.modules[-1]
    elif not module.get('enabled'):
        module['enabled'] = True

    if 'config' not in module:
        module['config'] = {}

    mod_config = module['config']
    params = params or {}

    # Validate required parameters
    if cmd.action in ('array_append', 'set_value'):
        for param in cmd.params:
            if param.required and param.name not in params:
                return f"Missing required parameter: {param.name}"

    # Execute based on action type
    if cmd.action == 'array_append':
        if cmd.target not in mod_config:
            mod_config[cmd.target] = []

        # Build the item from params
        item = {}
        for param in cmd.params:
            if param.name in params:
                item[param.name] = params[param.name]

        # Check for duplicates using key
        key_fields = cmd.key if cmd.key else ([cmd.params[0].name] if cmd.params else [])
        if isinstance(key_fields, str):
            key_fields = [key_fields]

        if key_fields and all(k in item for k in key_fields):
            for existing in mod_config[cmd.target]:
                if all(existing.get(k) == item.get(k) for k in key_fields):
                    key_display = ", ".join(f"{k}={item[k]}" for k in key_fields)
                    return f"Error: Entry with {key_display} already exists"

        mod_config[cmd.target].append(item)
        ctx.dirty = True

        if cmd.format:
            display = cmd.format.format(**item)
        else:
            display = str(item)
        return f"Added: {display}"

    elif cmd.action == 'array_remove':
        if cmd.target not in mod_config or not mod_config[cmd.target]:
            return f"No {cmd.target} to remove"

        # For agent, we need params to identify what to remove
        if not params:
            # List current items
            items = mod_config[cmd.target]
            lines = [f"Current {cmd.target}:"]
            for i, item in enumerate(items):
                if cmd.format:
                    try:
                        display = cmd.format.format(**item)
                    except KeyError:
                        display = str(item)
                else:
                    display = str(item)
                lines.append(f"  {i+1}. {display}")
            lines.append(f"\nProvide params to identify item to remove")
            return "\n".join(lines)

        # Find and remove matching item
        items = mod_config[cmd.target]
        for i, item in enumerate(items):
            matches = all(item.get(k) == v for k, v in params.items() if k in item)
            if matches:
                removed = items.pop(i)
                ctx.dirty = True
                if cmd.format:
                    try:
                        display = cmd.format.format(**removed)
                    except KeyError:
                        display = str(removed)
                else:
                    display = str(removed)
                return f"Removed: {display}"

        return f"No matching item found in {cmd.target}"

    elif cmd.action == 'array_list':
        if cmd.target not in mod_config or not mod_config[cmd.target]:
            return f"No {cmd.target} configured"

        lines = [f"{cmd.target}:"]
        for item in mod_config[cmd.target]:
            if cmd.format:
                try:
                    display = cmd.format.format(**item)
                except KeyError:
                    display = str(item)
            else:
                display = str(item)
            lines.append(f"  {display}")
        return "\n".join(lines)

    elif cmd.action == 'set_value':
        if not params or cmd.target.split('.')[-1] not in params:
            # Check for the parameter name matching target
            param_name = cmd.params[0].name if cmd.params else cmd.target
            if param_name not in params:
                return f"Missing parameter: {param_name}"
            value = params[param_name]
        else:
            value = params.get(cmd.target.split('.')[-1]) or params.get(cmd.params[0].name if cmd.params else cmd.target)

        old_value = mod_config.get(cmd.target, '')
        mod_config[cmd.target] = value
        ctx.dirty = True
        return f"Set {cmd.target}: {old_value or '(none)'} -> {value}"

    elif cmd.action == 'show':
        return tool_get_module_config(config, module_name)

    else:
        return f"Unknown action type: {cmd.action}"


def tool_get_bgp_config(config) -> str:
    """Get BGP configuration."""
    if not config:
        return "No configuration loaded"

    bgp = config.bgp
    if not bgp.enabled:
        return "BGP is disabled"

    lines = [
        "BGP Configuration:",
        f"  Enabled: {bgp.enabled}",
        f"  Local AS: {bgp.asn}",
        f"  Router ID: {bgp.router_id}",
        f"  Peers ({len(bgp.peers)}):",
    ]
    if bgp.peers:
        for p in bgp.peers:
            af = "IPv6" if ':' in p.peer_ip else "IPv4"
            lines.append(f"    - {p.name}: {p.peer_ip} AS {p.peer_asn} ({af})")
    else:
        lines.append("    (no peers configured)")

    return "\n".join(lines)


def tool_get_ospf_config(config) -> str:
    """Get OSPF configuration."""
    if not config:
        return "No configuration loaded"

    ospf = config.ospf
    if not ospf.enabled:
        return "OSPF is disabled"

    router_id = ospf.router_id or config.bgp.router_id
    lines = [
        f"Enabled: {ospf.enabled}",
        f"Router ID: {router_id}",
        f"Default Originate: {ospf.default_originate}",
        "",
        "Interface Areas:"
    ]

    has_areas = False
    # Loopbacks
    for loop in config.loopbacks:
        if loop.ospf_area is not None:
            passive = " (passive)" if loop.ospf_passive else ""
            lines.append(f"  loop{loop.instance}: area {loop.ospf_area}{passive}")
            has_areas = True
    # Dataplane interfaces
    for iface in config.interfaces:
        if iface.ospf_area is not None:
            passive = " (passive)" if iface.ospf_passive else ""
            lines.append(f"  {iface.name}: area {iface.ospf_area}{passive}")
            has_areas = True
    # BVI interfaces
    for bvi in config.bvi_domains:
        if bvi.ospf_area is not None:
            passive = " (passive)" if bvi.ospf_passive else ""
            lines.append(f"  bvi{bvi.bridge_id}: area {bvi.ospf_area}{passive}")
            has_areas = True

    if not has_areas:
        lines.append("  (no interfaces configured)")

    return "\n".join(lines)


def tool_get_ospf6_config(config) -> str:
    """Get OSPFv3 configuration."""
    if not config:
        return "No configuration loaded"

    ospf6 = config.ospf6
    if not ospf6.enabled:
        return "OSPFv3 is disabled"

    router_id = ospf6.router_id or config.ospf.router_id or config.bgp.router_id
    lines = [
        f"Enabled: {ospf6.enabled}",
        f"Router ID: {router_id}",
        f"Default Originate: {ospf6.default_originate}",
        "",
        "Interface Areas:"
    ]

    has_areas = False
    # Loopbacks
    for loop in config.loopbacks:
        if loop.ospf6_area is not None:
            passive = " (passive)" if loop.ospf6_passive else ""
            lines.append(f"  loop{loop.instance}: area {loop.ospf6_area}{passive}")
            has_areas = True
    # Dataplane interfaces
    for iface in config.interfaces:
        if iface.ospf6_area is not None:
            passive = " (passive)" if iface.ospf6_passive else ""
            lines.append(f"  {iface.name}: area {iface.ospf6_area}{passive}")
            has_areas = True
    # BVI interfaces
    for bvi in config.bvi_domains:
        if bvi.ospf6_area is not None:
            passive = " (passive)" if bvi.ospf6_passive else ""
            lines.append(f"  bvi{bvi.bridge_id}: area {bvi.ospf6_area}{passive}")
            has_areas = True

    if not has_areas:
        lines.append("  (no interfaces configured)")

    return "\n".join(lines)
