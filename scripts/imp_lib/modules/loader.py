"""
Module loader for IMP.

Functions for loading module definitions from YAML files.
"""

from pathlib import Path
from typing import List, Tuple

try:
    import yaml
except ImportError:
    yaml = None

from .dataclasses import (
    MODULE_DEFINITIONS_DIR,
    MODULE_EXAMPLES_DIR,
    ModuleConnection,
    ModuleShowCommand,
    ModuleABF,
    ModuleAdvertise,
    ModuleRouting,
    ModuleCommandParam,
    ModuleCommand,
    ModuleConfigSchemaField,
    ModuleCPU,
    ModuleDefinition,
    VPPModuleConnection,
    VPPModuleInstance,
)
from .validation import (
    ModuleValidationError,
    validate_module_definition,
    validate_module_config,
)


def parse_module_definition(data: dict) -> ModuleDefinition:
    """Parse a module definition from YAML data dict."""
    # Parse connections
    topology = data.get('topology', {})
    connections = [
        ModuleConnection(
            name=c['name'],
            purpose=c.get('purpose', ''),
            create_lcp=c.get('create_lcp', False)
        )
        for c in topology.get('connections', [])
    ]

    # Parse CPU requirements
    cpu_data = data.get('cpu', {})
    cpu = ModuleCPU(
        min_cores=cpu_data.get('min_cores', 0),
        ideal_cores=cpu_data.get('ideal_cores', 2)
    )

    # Parse config schema
    config_schema = []
    for field_name, field_def in data.get('config_schema', {}).items():
        if isinstance(field_def, dict):
            config_schema.append(ModuleConfigSchemaField(
                name=field_name,
                type=field_def.get('type', 'string'),
                format=field_def.get('format'),
                description=field_def.get('description', ''),
                default=field_def.get('default'),
                required=field_def.get('required', False),
                item_schema=field_def.get('item_schema')
            ))

    # Parse show commands
    show_commands = [
        ModuleShowCommand(
            name=cmd['name'],
            vpp_command=cmd['vpp_command'],
            description=cmd.get('description', '')
        )
        for cmd in data.get('show_commands', [])
    ]

    # Parse ABF config
    abf_data = data.get('abf')
    abf = None
    if abf_data:
        abf = ModuleABF(
            source=abf_data.get('source'),
            match=abf_data.get('match'),
            exclude=abf_data.get('exclude', []),
            prefix_field=abf_data.get('prefix_field')
        )

    # Parse routing config
    routing_data = data.get('routing')
    routing = None
    if routing_data:
        advertise_list = []
        for adv in routing_data.get('advertise', []):
            advertise_list.append(ModuleAdvertise(
                config_field=adv['config_field'],
                via_connection=adv['via_connection'],
                address_family=adv.get('address_family', 'ipv4')
            ))
        routing = ModuleRouting(advertise=advertise_list)

    # Parse CLI commands
    cli_commands = []
    for cmd_data in data.get('commands', []):
        # Parse params
        params = []
        for param_data in cmd_data.get('params', []):
            params.append(ModuleCommandParam(
                name=param_data['name'],
                type=param_data.get('type', 'string'),
                prompt=param_data.get('prompt', ''),
                required=param_data.get('required', True),
                choices=param_data.get('choices', [])
            ))
        cli_commands.append(ModuleCommand(
            path=cmd_data['path'],
            description=cmd_data.get('description', ''),
            action=cmd_data['action'],
            target=cmd_data['target'],
            params=params,
            key=cmd_data.get('key'),
            format=cmd_data.get('format')
        ))

    return ModuleDefinition(
        name=data['name'],
        display_name=data.get('display_name', data['name']),
        description=data.get('description', ''),
        connections=connections,
        vpp_commands=data.get('vpp_commands', ''),
        plugins=data.get('plugins', []),
        disable_plugins=data.get('disable_plugins', []),
        cpu=cpu,
        config_schema=config_schema,
        show_commands=show_commands,
        abf=abf,
        routing=routing,
        cli_commands=cli_commands
    )


def load_module_definition(name: str, definitions_dir: Path = MODULE_DEFINITIONS_DIR) -> ModuleDefinition:
    """
    Load a module definition from YAML file.

    Args:
        name: Module name (without .yaml extension)
        definitions_dir: Directory containing module YAML files

    Returns:
        Parsed ModuleDefinition

    Raises:
        FileNotFoundError: If module YAML doesn't exist
        ModuleValidationError: If module YAML is invalid
    """
    if yaml is None:
        raise ImportError("PyYAML is required. Install with: apt install python3-yaml")

    yaml_path = definitions_dir / f"{name}.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"Module definition not found: {yaml_path}")

    with open(yaml_path) as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ModuleValidationError(f"YAML syntax error in {yaml_path}: {e}")

    if not isinstance(data, dict):
        raise ModuleValidationError(f"Module YAML must be a dict, got {type(data).__name__}")

    # Validate
    errors = validate_module_definition(data)
    if errors:
        raise ModuleValidationError(f"Module '{name}' validation failed:\n  " + "\n  ".join(errors))

    return parse_module_definition(data)


def ensure_modules_dir(definitions_dir: Path = MODULE_DEFINITIONS_DIR) -> None:
    """Create the modules directory if it doesn't exist."""
    definitions_dir.mkdir(parents=True, exist_ok=True)


def list_available_modules(definitions_dir: Path = MODULE_DEFINITIONS_DIR) -> List[Tuple[str, str, str]]:
    """
    List available module definitions.

    Returns:
        List of (name, display_name, description) tuples
    """
    if yaml is None:
        return []

    if not definitions_dir.exists():
        return []

    modules = []
    for yaml_path in sorted(definitions_dir.glob("*.yaml")):
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                name = data.get('name', yaml_path.stem)
                display_name = data.get('display_name', name)
                description = data.get('description', '')
                modules.append((name, display_name, description))
        except Exception:
            # Skip invalid files
            pass

    return modules


def list_example_modules(examples_dir: Path = MODULE_EXAMPLES_DIR) -> List[Tuple[str, str, str]]:
    """
    List available module examples (shipped with image).

    Returns:
        List of (name, display_name, description) tuples
    """
    return list_available_modules(examples_dir)


def install_module_from_example(
    name: str,
    examples_dir: Path = MODULE_EXAMPLES_DIR,
    definitions_dir: Path = MODULE_DEFINITIONS_DIR
) -> None:
    """
    Install a module by copying from examples to definitions directory.

    Args:
        name: Module name (without .yaml extension)
        examples_dir: Directory containing example YAML files
        definitions_dir: Directory to install to

    Raises:
        FileNotFoundError: If example doesn't exist
        FileExistsError: If module already installed
    """
    import shutil

    src = examples_dir / f"{name}.yaml"
    dst = definitions_dir / f"{name}.yaml"

    if not src.exists():
        raise FileNotFoundError(f"Module example not found: {src}")

    if dst.exists():
        raise FileExistsError(f"Module already installed: {dst}")

    # Ensure destination directory exists
    ensure_modules_dir(definitions_dir)

    # Copy the file
    shutil.copy2(src, dst)


# =============================================================================
# Address Allocation
# =============================================================================

def allocate_memif_addresses(modules: List[VPPModuleInstance]) -> None:
    """
    Allocate socket IDs and IP addresses for all enabled module connections.

    Modifies modules in place, setting:
    - connections[].socket_id
    - connections[].socket_path
    - connections[].core_ip
    - connections[].module_ip
    - socket_path (CLI socket)

    Allocation formula:
    - Socket ID n gets IPs 169.254.1.(2*(n-1)) (core) and 169.254.1.(2*(n-1)+1) (module)
    """
    socket_id = 1  # Start at 1

    for module in modules:
        if not module.enabled:
            continue

        # Allocate CLI socket path
        module.socket_path = f"/run/vpp/{module.name}-cli.sock"

        # Allocate connections
        for conn in module.connections:
            # Calculate IPs
            core_last_octet = 2 * (socket_id - 1)
            module_last_octet = core_last_octet + 1

            conn.socket_id = socket_id
            conn.socket_path = f"/run/vpp/memif-{module.name}-{conn.name}.sock"
            conn.core_ip = f"169.254.1.{core_last_octet}"
            conn.module_ip = f"169.254.1.{module_last_octet}"
            conn.prefix = 31

            socket_id += 1


def allocate_cpu_cores(modules: List[VPPModuleInstance], pool: str) -> None:
    """
    Allocate CPU cores from a pool to modules.

    Args:
        modules: List of module instances
        pool: Core pool string like "6-7" or "6,7,8"

    The allocation algorithm:
    1. Parse pool into list of available cores
    2. Sort modules by ideal_cores descending
    3. Allocate: ideal if available, else min, else 0
    """
    # Parse pool string into list of core numbers
    available_cores = []
    if pool:
        for part in pool.split(','):
            part = part.strip()
            if '-' in part:
                start, end = part.split('-')
                available_cores.extend(range(int(start), int(end) + 1))
            elif part:
                available_cores.append(int(part))

    # Sort modules by ideal_cores descending (greedy first)
    enabled_modules = [m for m in modules if m.enabled]
    # We need module definition CPU info - assume it's stored on the instance

    for module in enabled_modules:
        # For now, simple allocation: main core only if available
        if available_cores:
            module.main_core = available_cores.pop(0)
            if available_cores:
                # Give remaining as worker
                module.worker_cores = str(available_cores.pop(0))


# =============================================================================
# Instance Creation
# =============================================================================

def create_module_instance(
    module_def: ModuleDefinition,
    enabled: bool,
    config: dict
) -> VPPModuleInstance:
    """
    Create a VPPModuleInstance from a ModuleDefinition and user config.

    The connections are created without socket IDs - call allocate_memif_addresses()
    after creating all instances.
    """
    # Create connections from definition
    connections = [
        VPPModuleConnection(
            name=conn.name,
            socket_id=0,  # Allocated later
            socket_path="",  # Allocated later
            core_ip="",  # Allocated later
            module_ip="",  # Allocated later
            create_lcp=conn.create_lcp,
            purpose=conn.purpose
        )
        for conn in module_def.connections
    ]

    return VPPModuleInstance(
        name=module_def.name,
        enabled=enabled,
        config=config,
        connections=connections,
        display_name=module_def.display_name,
        description=module_def.description,
        plugins=module_def.plugins,
        disable_plugins=module_def.disable_plugins,
        show_commands=module_def.show_commands,
        abf=module_def.abf,
        routing=module_def.routing,
        cli_commands=module_def.cli_commands,
        vpp_commands=module_def.vpp_commands
    )


def load_modules_from_config(
    modules_config: List[dict],
    definitions_dir: Path = MODULE_DEFINITIONS_DIR
) -> Tuple[List[VPPModuleInstance], List[str]]:
    """
    Load module instances from router.json modules config.

    Args:
        modules_config: List of module dicts from router.json
        definitions_dir: Directory containing module YAML definitions

    Returns:
        Tuple of (module_instances, error_messages)
    """
    instances = []
    errors = []

    for mod_cfg in modules_config:
        name = mod_cfg.get('name')
        if not name:
            errors.append("Module config missing 'name' field")
            continue

        enabled = mod_cfg.get('enabled', False)
        config = mod_cfg.get('config', {})

        try:
            # Load definition
            module_def = load_module_definition(name, definitions_dir)

            # Validate config against schema
            config_errors = validate_module_config(module_def, config)
            if config_errors:
                errors.extend([f"Module '{name}': {e}" for e in config_errors])
                # Still create instance but mark as disabled due to errors
                enabled = False

            # Create instance
            instance = create_module_instance(module_def, enabled, config)
            instances.append(instance)

        except FileNotFoundError as e:
            errors.append(f"Module '{name}': {e}")
        except ModuleValidationError as e:
            errors.append(str(e))

    # Allocate addresses for all instances
    allocate_memif_addresses(instances)

    return instances, errors
