"""
Configuration serialization for IMP.

Functions for saving and loading router configuration to/from JSON.
"""

import json
from dataclasses import asdict
from pathlib import Path

from .dataclasses import (
    RouterConfig,
    ManagementInterface,
    Interface,
    InterfaceAddress,
    SubInterface,
    Route,
    BGPConfig,
    BGPPeer,
    OSPFConfig,
    OSPF6Config,
    NATConfig,
    NATMapping,
    ACLBypassPair,
    ContainerConfig,
    CPUConfig,
    VLANPassthrough,
    LoopbackInterface,
    BVIConfig,
    BridgeDomainMember,
)


def to_dict(obj):
    """Convert dataclasses to dicts recursively."""
    if hasattr(obj, '__dataclass_fields__'):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    elif isinstance(obj, list):
        return [to_dict(i) for i in obj]
    else:
        return obj


def save_config(config: RouterConfig, config_file: Path, quiet: bool = False) -> None:
    """Save configuration to JSON file."""
    from imp_lib.common import log

    config_file.parent.mkdir(parents=True, exist_ok=True)

    data = to_dict(config)

    with open(config_file, 'w') as f:
        json.dump(data, f, indent=2)

    if not quiet:
        log(f"Configuration saved to {config_file}")


def load_config(config_file: Path) -> RouterConfig:
    """Load configuration from JSON file."""
    with open(config_file) as f:
        data = json.load(f)

    # Load modules
    modules = data.get('modules', [])

    # Load interfaces (new format)
    interfaces = []
    for iface_data in data.get('interfaces', []):
        ipv4_addrs = [InterfaceAddress(**a) for a in iface_data.get('ipv4', [])]
        ipv6_addrs = [InterfaceAddress(**a) for a in iface_data.get('ipv6', [])]
        subifs = [SubInterface(**s) for s in iface_data.get('subinterfaces', [])]
        interfaces.append(Interface(
            name=iface_data['name'],
            iface=iface_data['iface'],
            pci=iface_data['pci'],
            ipv4=ipv4_addrs,
            ipv6=ipv6_addrs,
            mtu=iface_data.get('mtu', 1500),
            subinterfaces=subifs,
            ospf_area=iface_data.get('ospf_area'),
            ospf_passive=iface_data.get('ospf_passive', False),
            ospf6_area=iface_data.get('ospf6_area'),
            ospf6_passive=iface_data.get('ospf6_passive', False),
            # IPv6 RA settings
            ipv6_ra_enabled=iface_data.get('ipv6_ra_enabled', True),
            ipv6_ra_interval_max=iface_data.get('ipv6_ra_interval_max', 30),
            ipv6_ra_interval_min=iface_data.get('ipv6_ra_interval_min', 15),
            ipv6_ra_suppress=iface_data.get('ipv6_ra_suppress', False),
            ipv6_ra_prefixes=iface_data.get('ipv6_ra_prefixes', []),
        ))

    # Load routes
    routes = [Route(**r) for r in data.get('routes', [])]

    # Handle VLAN passthrough
    vlan_passthrough = [
        VLANPassthrough(**v) for v in data.get('vlan_passthrough', [])
    ]

    # Handle loopback interfaces
    loopbacks = [
        LoopbackInterface(**lo) for lo in data.get('loopbacks', [])
    ]

    # Handle BVI domains
    bvi_domains = []
    for bvi_data in data.get('bvi_domains', []):
        members = [BridgeDomainMember(**m) for m in bvi_data.get('members', [])]
        bvi_domains.append(BVIConfig(
            bridge_id=bvi_data['bridge_id'],
            name=bvi_data['name'],
            members=members,
            ipv4=bvi_data.get('ipv4'),
            ipv4_prefix=bvi_data.get('ipv4_prefix'),
            ipv6=bvi_data.get('ipv6'),
            ipv6_prefix=bvi_data.get('ipv6_prefix'),
            create_lcp=bvi_data.get('create_lcp', True),
            ospf_area=bvi_data.get('ospf_area'),
            ospf_passive=bvi_data.get('ospf_passive', False),
            ospf6_area=bvi_data.get('ospf6_area'),
            ospf6_passive=bvi_data.get('ospf6_passive', False),
            # IPv6 RA settings
            ipv6_ra_enabled=bvi_data.get('ipv6_ra_enabled', True),
            ipv6_ra_interval_max=bvi_data.get('ipv6_ra_interval_max', 30),
            ipv6_ra_interval_min=bvi_data.get('ipv6_ra_interval_min', 15),
            ipv6_ra_suppress=bvi_data.get('ipv6_ra_suppress', False),
            ipv6_ra_prefixes=bvi_data.get('ipv6_ra_prefixes', []),
        ))

    # Always re-detect CPU allocation for current hardware
    cpu = CPUConfig.detect_and_allocate()

    # Handle OSPF configs
    ospf_data = data.get('ospf', {})
    ospf = OSPFConfig(
        enabled=ospf_data.get('enabled', False),
        router_id=ospf_data.get('router_id'),
        default_originate=ospf_data.get('default_originate', False),
    )

    ospf6_data = data.get('ospf6', {})
    ospf6 = OSPF6Config(
        enabled=ospf6_data.get('enabled', False),
        router_id=ospf6_data.get('router_id'),
        default_originate=ospf6_data.get('default_originate', False),
    )

    # Handle NAT config
    nat_data = data.get('nat', {})
    nat_mappings = [NATMapping(**m) for m in nat_data.get('mappings', [])]
    nat_bypass = [ACLBypassPair(**b) for b in nat_data.get('bypass_pairs', [])]
    nat = NATConfig(
        bgp_prefix=nat_data.get('bgp_prefix', ''),
        mappings=nat_mappings,
        bypass_pairs=nat_bypass,
    )

    # Handle BGP config with peers list
    bgp_data = data.get('bgp', {})
    bgp_peers = [BGPPeer(**p) for p in bgp_data.get('peers', [])]
    bgp = BGPConfig(
        enabled=bgp_data.get('enabled', False),
        asn=bgp_data.get('asn'),
        router_id=bgp_data.get('router_id'),
        announced_prefixes=bgp_data.get('announced_prefixes', []),
        peers=bgp_peers,
    )

    config = RouterConfig(
        hostname=data.get('hostname', 'appliance'),
        management=ManagementInterface(**data['management']) if data.get('management') else None,
        interfaces=interfaces,
        routes=routes,
        bgp=bgp,
        ospf=ospf,
        ospf6=ospf6,
        nat=nat,
        container=ContainerConfig(**data['container']) if data.get('container') else ContainerConfig(),
        cpu=cpu,
        vlan_passthrough=vlan_passthrough,
        loopbacks=loopbacks,
        bvi_domains=bvi_domains,
        modules=modules,
    )

    return config
