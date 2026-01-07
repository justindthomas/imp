"""
Module validation for IMP.

Functions for validating module definitions and configurations.
"""

import ipaddress
import re
from typing import List

try:
    from jinja2 import Environment, TemplateSyntaxError
except ImportError:
    Environment = None
    TemplateSyntaxError = Exception

from .dataclasses import ModuleDefinition


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


def validate_module_definition(data: dict) -> List[str]:
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


def validate_module_config(module_def: ModuleDefinition, config: dict) -> List[str]:
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
