"""
Module definition and instance dataclasses for IMP.

These define the structure of VPP module definitions (from YAML)
and runtime module instances.
"""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Union


# =============================================================================
# Paths
# =============================================================================

from pathlib import Path

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
    exclude: List[str] = field(default_factory=list)  # e.g., ["container_network", "bypass_pairs"]
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
    advertise: List[ModuleAdvertise] = field(default_factory=list)


@dataclass
class ModuleCommandParam:
    """A parameter for a module CLI command."""
    name: str
    type: str  # "ipv4_cidr", "ipv6_cidr", "string", "integer", "boolean", "choice"
    prompt: str = ""
    required: bool = True
    choices: List[str] = field(default_factory=list)  # For "choice" type


@dataclass
class ModuleCommand:
    """A CLI command defined by a module."""
    path: str  # e.g., "mappings/add", "set-prefix"
    description: str
    action: str  # "array_append", "array_remove", "array_list", "set_value", "show"
    target: str  # Config field to operate on
    params: List[ModuleCommandParam] = field(default_factory=list)
    key: Optional[Union[str, List[str]]] = None  # Uniqueness key: single field or list for compound keys
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
    connections: List[ModuleConnection]
    vpp_commands: str  # Jinja2 template for VPP commands
    plugins: List[str] = field(default_factory=list)
    disable_plugins: List[str] = field(default_factory=list)
    cpu: ModuleCPU = field(default_factory=ModuleCPU)
    config_schema: List[ModuleConfigSchemaField] = field(default_factory=list)
    show_commands: List[ModuleShowCommand] = field(default_factory=list)
    abf: Optional[ModuleABF] = None
    routing: Optional[ModuleRouting] = None
    cli_commands: List[ModuleCommand] = field(default_factory=list)

    @property
    def connection_names(self) -> List[str]:
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
    connections: List[VPPModuleConnection] = field(default_factory=list)
    socket_path: str = ""  # CLI socket path
    main_core: Optional[int] = None
    worker_cores: Optional[str] = None
    # Cached from definition
    display_name: str = ""
    description: str = ""
    plugins: List[str] = field(default_factory=list)
    disable_plugins: List[str] = field(default_factory=list)
    show_commands: List[ModuleShowCommand] = field(default_factory=list)
    abf: Optional[ModuleABF] = None
    routing: Optional[ModuleRouting] = None
    cli_commands: List[ModuleCommand] = field(default_factory=list)
    vpp_commands: str = ""  # Raw Jinja2 template
    vpp_commands_rendered: str = ""  # Rendered commands (filled at apply time)
