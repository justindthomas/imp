"""
imp_lib.config - Router configuration dataclasses and utilities for IMP.

This package contains:
- dataclasses: Configuration data structures (RouterConfig, Interface, etc.)
- validation: IP address and CIDR validation functions
- constants: Path constants (TEMPLATE_DIR, CONFIG_FILE, etc.)
- serialization: JSON save/load functions
"""

from .constants import (
    TEMPLATE_DIR,
    CONFIG_FILE,
    GENERATED_DIR,
)

from .validation import (
    validate_ipv4,
    validate_ipv4_cidr,
    validate_ipv6,
    validate_ipv6_cidr,
    parse_cidr,
)

from .dataclasses import (
    InterfaceInfo,
    SubInterface,
    LoopbackInterface,
    BridgeDomainMember,
    BVIConfig,
    InterfaceAddress,
    Interface,
    Route,
    ManagementInterface,
    BGPPeer,
    BGPConfig,
    OSPFConfig,
    OSPF6Config,
    NATMapping,
    ACLBypassPair,
    VLANPassthrough,
    NATConfig,
    ContainerConfig,
    CPUConfig,
    RouterConfig,
)

from .serialization import (
    to_dict,
    save_config,
    load_config,
)

__all__ = [
    # Constants
    'TEMPLATE_DIR',
    'CONFIG_FILE',
    'GENERATED_DIR',
    # Validation
    'validate_ipv4',
    'validate_ipv4_cidr',
    'validate_ipv6',
    'validate_ipv6_cidr',
    'parse_cidr',
    # Dataclasses
    'InterfaceInfo',
    'SubInterface',
    'LoopbackInterface',
    'BridgeDomainMember',
    'BVIConfig',
    'InterfaceAddress',
    'Interface',
    'Route',
    'ManagementInterface',
    'BGPPeer',
    'BGPConfig',
    'OSPFConfig',
    'OSPF6Config',
    'NATMapping',
    'ACLBypassPair',
    'VLANPassthrough',
    'NATConfig',
    'ContainerConfig',
    'CPUConfig',
    'RouterConfig',
    # Serialization
    'to_dict',
    'save_config',
    'load_config',
]
