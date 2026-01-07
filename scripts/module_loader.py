#!/usr/bin/env python3
"""
module_loader.py - VPP Module Definition Loader

Loads and validates VPP module definitions from YAML files.
Allocates memif socket IDs and IP addresses for module connections.
"""

import ipaddress
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:
    yaml = None

try:
    from jinja2 import Environment, TemplateSyntaxError
except ImportError:
    Environment = None
    TemplateSyntaxError = Exception


# =============================================================================
# Paths
# =============================================================================

MODULE_DEFINITIONS_DIR = Path("/persistent/config/modules")
MODULE_EXAMPLES_DIR = Path("/usr/share/imp/module-examples")


# =============================================================================
# Module Definition Dataclasses (from YAML)
# =============================================================================

@dataclass
class ModuleConnection:
    """A memif connection definition from module YAML."""
    name: str
    purpose: str = ""
    create_lcp: bool = False


@dataclass
class ModuleShowCommand:
    """A show command exposed by the module."""
    name: str
    vpp_command: str
    description: str = ""


@dataclass
class ModuleABF:
    """ABF (ACL-Based Forwarding) configuration for traffic steering."""
    source: Optional[str] = None  # e.g., "internal_interfaces"
    match: Optional[str] = None  # e.g., "destination_prefix"
    exclude: list[str] = field(default_factory=list)  # e.g., ["container_network", "bypass_pairs"]
    prefix_field: Optional[str] = None  # config field to use for prefix match


@dataclass
class ModuleAdvertise:
    """A prefix to advertise via BGP and route to the module."""
    config_field: str  # Field name in module.config containing the prefix
    via_connection: str  # Connection name to route traffic through
    address_family: str = "ipv4"  # "ipv4" or "ipv6"


@dataclass
class ModuleRouting:
    """Routing configuration for the module."""
    advertise: list[ModuleAdvertise] = field(default_factory=list)


@dataclass
class ModuleCommandParam:
    """A parameter for a module CLI command."""
    name: str
    type: str  # "ipv4_cidr", "ipv6_cidr", "string", "integer", "boolean", "choice"
    prompt: str = ""
    required: bool = True
    choices: list[str] = field(default_factory=list)  # For "choice" type


@dataclass
class ModuleCommand:
    """A CLI command defined by a module."""
    path: str  # e.g., "mappings/add", "set-prefix"
    description: str
    action: str  # "array_append", "array_remove", "array_list", "set_value", "show"
    target: str  # Config field to operate on
    params: list[ModuleCommandParam] = field(default_factory=list)
    key: Optional[str | list[str]] = None  # Uniqueness key: single field or list for compound keys
    format: Optional[str] = None  # For array_list: display format string


@dataclass
class ModuleConfigSchemaField:
    """A single field in the config schema."""
    name: str
    type: str  # "string", "array", "integer", "boolean"
    format: Optional[str] = None  # "ipv4_cidr", "ipv6_cidr", etc.
    description: str = ""
    default: Any = None
    required: bool = False
    item_schema: Optional[dict] = None  # For arrays


@dataclass
class ModuleCPU:
    """CPU requirements for the module."""
    min_cores: int = 0
    ideal_cores: int = 2


@dataclass
class ModuleDefinition:
    """Complete module definition parsed from YAML."""
    name: str
    display_name: str
    description: str
    connections: list[ModuleConnection]
    vpp_commands: str  # Jinja2 template for VPP commands
    plugins: list[str] = field(default_factory=list)
    disable_plugins: list[str] = field(default_factory=list)
    cpu: ModuleCPU = field(default_factory=ModuleCPU)
    config_schema: list[ModuleConfigSchemaField] = field(default_factory=list)
    show_commands: list[ModuleShowCommand] = field(default_factory=list)
    abf: Optional[ModuleABF] = None
    routing: Optional[ModuleRouting] = None
    cli_commands: list[ModuleCommand] = field(default_factory=list)

    @property
    def connection_names(self) -> list[str]:
        """Get list of connection names."""
        return [c.name for c in self.connections]


# =============================================================================
# Runtime/Computed Dataclasses
# =============================================================================

@dataclass
class VPPModuleConnection:
    """A computed module connection with allocated socket ID and IPs."""
    name: str
    socket_id: int
    socket_path: str
    core_ip: str
    module_ip: str
    prefix: int = 31
    create_lcp: bool = False
    purpose: str = ""


@dataclass
class VPPModuleInstance:
    """A module instance with configuration and computed values."""
    name: str
    enabled: bool
    config: dict  # User configuration from router.json
    connections: list[VPPModuleConnection] = field(default_factory=list)
    socket_path: str = ""  # CLI socket path
    main_core: Optional[int] = None
    worker_cores: Optional[str] = None
    # Cached from definition
    display_name: str = ""
    description: str = ""
    plugins: list[str] = field(default_factory=list)
    disable_plugins: list[str] = field(default_factory=list)
    show_commands: list[ModuleShowCommand] = field(default_factory=list)
    abf: Optional[ModuleABF] = None
    routing: Optional[ModuleRouting] = None
    cli_commands: list[ModuleCommand] = field(default_factory=list)
    vpp_commands: str = ""  # Raw Jinja2 template
    vpp_commands_rendered: str = ""  # Rendered commands (filled at apply time)


# =============================================================================
# Validation
# =============================================================================

class ModuleValidationError(Exception):
    """Raised when module validation fails."""
    pass


def validate_ipv4_cidr(value: str) -> bool:
    """Validate IPv4 CIDR notation."""
    try:
        ipaddress.IPv4Network(value, strict=False)
        return True
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def validate_ipv6_cidr(value: str) -> bool:
    """Validate IPv6 CIDR notation."""
    try:
        ipaddress.IPv6Network(value, strict=False)
        return True
    except (ipaddress.AddressValueError, ipaddress.NetmaskValueError, ValueError):
        return False


def validate_module_definition(data: dict) -> list[str]:
    """
    Validate a module definition YAML structure.
    Returns list of error messages (empty if valid).
    """
    errors = []

    # Required fields
    required = ['name', 'topology', 'vpp_commands']
    for field_name in required:
        if field_name not in data:
            errors.append(f"Missing required field: {field_name}")

    if errors:
        return errors  # Can't continue without required fields

    # Validate name format
    name = data.get('name', '')
    if not re.match(r'^[a-z][a-z0-9_-]*$', name):
        errors.append(f"Invalid module name '{name}': must start with lowercase letter, contain only a-z, 0-9, _, -")

    # Validate topology.connections
    topology = data.get('topology', {})
    connections = topology.get('connections', [])
    conn_names = []
    if not connections:
        errors.append("topology.connections must have at least one connection")
    else:
        for i, conn in enumerate(connections):
            if 'name' not in conn:
                errors.append(f"topology.connections[{i}]: missing 'name' field")
            else:
                if conn['name'] in conn_names:
                    errors.append(f"Duplicate connection name: {conn['name']}")
                conn_names.append(conn['name'])

    # Validate ABF references
    abf = data.get('abf', {})
    if abf:
        source = abf.get('source')

        # If using internal connection reference in ABF
        if source and source not in ['internal_interfaces']:
            # Check if it references a connection name
            if source not in conn_names and connections:
                errors.append(f"ABF source '{source}' is not a valid connection name or 'internal_interfaces'")

    # Validate Jinja2 template syntax for VPP commands
    vpp_commands = data.get('vpp_commands', '')
    if vpp_commands and Environment:
        try:
            env = Environment()
            env.parse(vpp_commands)
        except TemplateSyntaxError as e:
            errors.append(f"Jinja2 template syntax error in vpp_commands: {e}")

    # Validate config_schema field types
    config_schema = data.get('config_schema', {})
    valid_types = ['string', 'array', 'integer', 'boolean']
    valid_formats = ['ipv4_cidr', 'ipv6_cidr', 'ipv4', 'ipv6', None]
    for field_name, field_def in config_schema.items():
        if isinstance(field_def, dict):
            field_type = field_def.get('type')
            if field_type and field_type not in valid_types:
                errors.append(f"config_schema.{field_name}: invalid type '{field_type}'")
            field_format = field_def.get('format')
            if field_format and field_format not in valid_formats:
                errors.append(f"config_schema.{field_name}: invalid format '{field_format}'")

    # Validate routing.advertise
    routing = data.get('routing', {})
    if routing:
        for i, adv in enumerate(routing.get('advertise', [])):
            if 'config_field' not in adv:
                errors.append(f"routing.advertise[{i}]: missing 'config_field'")
            if 'via_connection' not in adv:
                errors.append(f"routing.advertise[{i}]: missing 'via_connection'")
            elif adv['via_connection'] not in conn_names:
                errors.append(f"routing.advertise[{i}]: via_connection '{adv['via_connection']}' not found in connections")
            af = adv.get('address_family', 'ipv4')
            if af not in ['ipv4', 'ipv6']:
                errors.append(f"routing.advertise[{i}]: address_family must be 'ipv4' or 'ipv6'")

    # Validate CLI commands
    valid_actions = ['array_append', 'array_remove', 'array_list', 'set_value', 'show']
    valid_param_types = ['ipv4_cidr', 'ipv6_cidr', 'ipv4', 'ipv6', 'string', 'integer', 'boolean', 'choice']
    for i, cmd in enumerate(data.get('commands', [])):
        if 'path' not in cmd:
            errors.append(f"commands[{i}]: missing 'path'")
        if 'action' not in cmd:
            errors.append(f"commands[{i}]: missing 'action'")
        elif cmd['action'] not in valid_actions:
            errors.append(f"commands[{i}]: invalid action '{cmd['action']}'")
        if 'target' not in cmd:
            errors.append(f"commands[{i}]: missing 'target'")
        # Validate params
        for j, param in enumerate(cmd.get('params', [])):
            if 'name' not in param:
                errors.append(f"commands[{i}].params[{j}]: missing 'name'")
            param_type = param.get('type', 'string')
            if param_type not in valid_param_types:
                errors.append(f"commands[{i}].params[{j}]: invalid type '{param_type}'")

    return errors


def validate_module_config(module_def: ModuleDefinition, config: dict) -> list[str]:
    """
    Validate user configuration against module's config_schema.
    Returns list of error messages (empty if valid).
    """
    errors = []

    for schema_field in module_def.config_schema:
        value = config.get(schema_field.name)

        # Check required fields
        if schema_field.required and value is None:
            errors.append(f"Missing required config field: {schema_field.name}")
            continue

        if value is None:
            continue  # Optional field not provided

        # Type validation
        if schema_field.type == 'string':
            if not isinstance(value, str):
                errors.append(f"Config field '{schema_field.name}' must be a string")
                continue

            # Format validation
            if schema_field.format == 'ipv4_cidr' and not validate_ipv4_cidr(value):
                errors.append(f"Config field '{schema_field.name}' must be valid IPv4 CIDR")
            elif schema_field.format == 'ipv6_cidr' and not validate_ipv6_cidr(value):
                errors.append(f"Config field '{schema_field.name}' must be valid IPv6 CIDR")

        elif schema_field.type == 'array':
            if not isinstance(value, list):
                errors.append(f"Config field '{schema_field.name}' must be an array")
                continue

            # Validate array items if item_schema defined
            if schema_field.item_schema:
                for i, item in enumerate(value):
                    if not isinstance(item, dict):
                        errors.append(f"Config field '{schema_field.name}[{i}]' must be an object")
                        continue
                    for item_field, item_def in schema_field.item_schema.items():
                        item_value = item.get(item_field)
                        if item_value is not None and isinstance(item_def, dict):
                            item_format = item_def.get('format')
                            if item_format == 'ipv4_cidr' and not validate_ipv4_cidr(item_value):
                                errors.append(f"Config field '{schema_field.name}[{i}].{item_field}' must be valid IPv4 CIDR")
                            elif item_format == 'ipv6_cidr' and not validate_ipv6_cidr(item_value):
                                errors.append(f"Config field '{schema_field.name}[{i}].{item_field}' must be valid IPv6 CIDR")

        elif schema_field.type == 'integer':
            if not isinstance(value, int):
                errors.append(f"Config field '{schema_field.name}' must be an integer")

        elif schema_field.type == 'boolean':
            if not isinstance(value, bool):
                errors.append(f"Config field '{schema_field.name}' must be a boolean")

    return errors


# =============================================================================
# Loading Functions
# =============================================================================

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


def list_available_modules(definitions_dir: Path = MODULE_DEFINITIONS_DIR) -> list[tuple[str, str, str]]:
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


def list_example_modules(examples_dir: Path = MODULE_EXAMPLES_DIR) -> list[tuple[str, str, str]]:
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

def allocate_memif_addresses(modules: list[VPPModuleInstance]) -> None:
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


def allocate_cpu_cores(modules: list[VPPModuleInstance], pool: str) -> None:
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
    modules_config: list[dict],
    definitions_dir: Path = MODULE_DEFINITIONS_DIR
) -> tuple[list[VPPModuleInstance], list[str]]:
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
