"""
imp_lib.modules - VPP module definition loader for IMP.

This package contains:
- dataclasses: Module definition and instance data structures
- validation: Module definition and config validation
- loader: YAML parsing and module loading functions
"""

from .dataclasses import (
    # Paths
    MODULE_DEFINITIONS_DIR,
    MODULE_EXAMPLES_DIR,
    # Definition dataclasses
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
    # Runtime dataclasses
    VPPModuleConnection,
    VPPModuleInstance,
)

from .validation import (
    ModuleValidationError,
    validate_ipv4_cidr,
    validate_ipv6_cidr,
    validate_module_definition,
    validate_module_config,
)

from .loader import (
    parse_module_definition,
    load_module_definition,
    ensure_modules_dir,
    list_available_modules,
    list_example_modules,
    install_module_from_example,
    allocate_memif_addresses,
    allocate_cpu_cores,
    create_module_instance,
    load_modules_from_config,
)

__all__ = [
    # Paths
    'MODULE_DEFINITIONS_DIR',
    'MODULE_EXAMPLES_DIR',
    # Definition dataclasses
    'ModuleConnection',
    'ModuleShowCommand',
    'ModuleABF',
    'ModuleAdvertise',
    'ModuleRouting',
    'ModuleCommandParam',
    'ModuleCommand',
    'ModuleConfigSchemaField',
    'ModuleCPU',
    'ModuleDefinition',
    # Runtime dataclasses
    'VPPModuleConnection',
    'VPPModuleInstance',
    # Validation
    'ModuleValidationError',
    'validate_ipv4_cidr',
    'validate_ipv6_cidr',
    'validate_module_definition',
    'validate_module_config',
    # Loader
    'parse_module_definition',
    'load_module_definition',
    'ensure_modules_dir',
    'list_available_modules',
    'list_example_modules',
    'install_module_from_example',
    'allocate_memif_addresses',
    'allocate_cpu_cores',
    'create_module_instance',
    'load_modules_from_config',
]
