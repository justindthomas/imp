#!/usr/bin/env python3
"""
live_config.py - Live configuration change engine for IMP

This module compares two RouterConfig objects and generates the minimal
set of VPP/FRR commands to transition from old to new state without
requiring service restarts.

Usage:
    from live_config import LiveConfigApplier, requires_restart

    applier = LiveConfigApplier(old_config, new_config)
    success, messages = applier.apply(dry_run=True)  # Preview
    success, messages = applier.apply(dry_run=False) # Apply for real
"""

import subprocess
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional, Any

# VPP socket paths
VPP_CORE_SOCKET = Path("/run/vpp/core-cli.sock")
VPP_NAT_SOCKET = Path("/run/vpp/nat-cli.sock")


def _get_nat_config(config) -> dict:
    """Get NAT config from modules list, returns dict or empty dict."""
    if not config or not hasattr(config, 'modules') or not config.modules:
        return {}
    for module in config.modules:
        if module.get('name') == 'nat' and module.get('enabled', False):
            return module.get('config', {})
    return {}


# =============================================================================
# Change Types and Data Structures
# =============================================================================

class ChangeType(Enum):
    ADD = "add"
    DELETE = "delete"
    MODIFY = "modify"


@dataclass
class ConfigChange:
    """Represents a single configuration change."""
    change_type: ChangeType
    category: str  # e.g., "loopback", "subinterface", "bgp_peer"
    identifier: str  # e.g., "loop0", "internal0.100", "192.168.1.1"
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None

    def __str__(self) -> str:
        if self.change_type == ChangeType.ADD:
            return f"ADD {self.category} {self.identifier}"
        elif self.change_type == ChangeType.DELETE:
            return f"DELETE {self.category} {self.identifier}"
        else:
            return f"MODIFY {self.category} {self.identifier}"


@dataclass
class CommandBatch:
    """A batch of commands to execute together."""
    target: str  # "vpp-core", "vpp-nat", "frr"
    commands: list[str] = field(default_factory=list)
    rollback_commands: list[str] = field(default_factory=list)
    description: str = ""

    def is_empty(self) -> bool:
        return len(self.commands) == 0


# =============================================================================
# VPP Command Execution
# =============================================================================

def vpp_exec(command: str, instance: str = "core") -> tuple[bool, str]:
    """Execute a VPP command and capture output."""
    socket = VPP_CORE_SOCKET if instance == "core" else VPP_NAT_SOCKET

    if not socket.exists():
        return False, f"VPP {instance} socket not found: {socket}"

    try:
        result = subprocess.run(
            ["vppctl", "-s", str(socket), command],
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout.strip()
        stderr = result.stderr.strip()

        # Check returncode first
        if result.returncode != 0:
            return False, stderr or output

        # VPP often returns errors in stdout with returncode 0
        # Check for common error patterns
        error_patterns = [
            "unknown input",
            "not specified",
            "not found",
            "failed",
            "error",
            "invalid",
            "already exists",
            "does not exist",
        ]
        output_lower = output.lower()
        for pattern in error_patterns:
            if pattern in output_lower:
                return False, output

        return True, output
    except subprocess.TimeoutExpired:
        return False, "Command timed out"
    except Exception as e:
        return False, str(e)


def frr_exec(commands: list[str]) -> tuple[bool, str]:
    """Execute FRR commands via vtysh in dataplane namespace."""
    if not commands:
        return True, "No commands to execute"

    # Build vtysh input
    vtysh_input = "configure terminal\n"
    for cmd in commands:
        vtysh_input += cmd + "\n"
    vtysh_input += "end\n"

    try:
        result = subprocess.run(
            ["ip", "netns", "exec", "dataplane", "vtysh"],
            input=vtysh_input,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            return False, result.stderr.strip() or result.stdout.strip()
        return True, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "vtysh command timed out"
    except Exception as e:
        return False, str(e)


# =============================================================================
# Configuration Diff Engine
# =============================================================================

class ConfigDiffEngine:
    """Compares two RouterConfig objects and generates changes."""

    def __init__(self, old_config: Any, new_config: Any):
        self.old = old_config
        self.new = new_config
        self.changes: list[ConfigChange] = []

    def compute_diff(self) -> list[ConfigChange]:
        """Compute all differences between old and new config."""
        self.changes = []

        # VPP Core changes
        self._diff_loopbacks()
        self._diff_subinterfaces()
        self._diff_bvi_domains()
        self._diff_vlan_passthrough()
        self._diff_static_routes()

        # NAT changes
        self._diff_nat_mappings()
        self._diff_nat_bypass()

        # FRR changes
        self._diff_bgp_peers()
        self._diff_ospf()
        self._diff_ospf6()

        return self.changes

    def _diff_loopbacks(self) -> None:
        """Compare loopback interfaces."""
        old_loops = {lo.instance: lo for lo in (self.old.loopbacks if self.old else [])}
        new_loops = {lo.instance: lo for lo in (self.new.loopbacks if self.new else [])}

        # Find added loopbacks
        for inst, lo in new_loops.items():
            if inst not in old_loops:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="loopback",
                    identifier=f"loop{inst}",
                    new_value=asdict(lo)
                ))
            elif asdict(old_loops[inst]) != asdict(lo):
                self.changes.append(ConfigChange(
                    change_type=ChangeType.MODIFY,
                    category="loopback",
                    identifier=f"loop{inst}",
                    old_value=asdict(old_loops[inst]),
                    new_value=asdict(lo)
                ))

        # Find deleted loopbacks
        for inst in old_loops:
            if inst not in new_loops:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="loopback",
                    identifier=f"loop{inst}",
                    old_value=asdict(old_loops[inst])
                ))

    def _diff_subinterfaces(self) -> None:
        """Compare sub-interfaces on internal and external interfaces."""
        # Compare external sub-interfaces
        old_ext_subs = {}
        new_ext_subs = {}

        if self.old and self.old.external:
            for sub in self.old.external.subinterfaces:
                old_ext_subs[sub.vlan_id] = sub

        if self.new and self.new.external:
            for sub in self.new.external.subinterfaces:
                new_ext_subs[sub.vlan_id] = sub

        self._compare_subinterfaces("external", old_ext_subs, new_ext_subs)

        # Compare internal sub-interfaces
        old_int_map = {i.vpp_name: i for i in (self.old.internal if self.old else [])}
        new_int_map = {i.vpp_name: i for i in (self.new.internal if self.new else [])}

        # Check all interfaces in both old and new
        all_ifaces = set(old_int_map.keys()) | set(new_int_map.keys())
        for iface_name in all_ifaces:
            old_subs = {}
            new_subs = {}

            if iface_name in old_int_map:
                for sub in old_int_map[iface_name].subinterfaces:
                    old_subs[sub.vlan_id] = sub

            if iface_name in new_int_map:
                for sub in new_int_map[iface_name].subinterfaces:
                    new_subs[sub.vlan_id] = sub

            self._compare_subinterfaces(iface_name, old_subs, new_subs)

    def _compare_subinterfaces(self, parent: str, old_subs: dict, new_subs: dict) -> None:
        """Compare sub-interfaces on a parent interface."""
        # Added
        for vlan_id, sub in new_subs.items():
            if vlan_id not in old_subs:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="subinterface",
                    identifier=f"{parent}.{vlan_id}",
                    new_value=asdict(sub)
                ))
            elif asdict(old_subs[vlan_id]) != asdict(sub):
                self.changes.append(ConfigChange(
                    change_type=ChangeType.MODIFY,
                    category="subinterface",
                    identifier=f"{parent}.{vlan_id}",
                    old_value=asdict(old_subs[vlan_id]),
                    new_value=asdict(sub)
                ))

        # Deleted
        for vlan_id in old_subs:
            if vlan_id not in new_subs:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="subinterface",
                    identifier=f"{parent}.{vlan_id}",
                    old_value=asdict(old_subs[vlan_id])
                ))

    def _diff_bvi_domains(self) -> None:
        """Compare BVI (bridge virtual interface) domains."""
        old_bvis = {bvi.bridge_id: bvi for bvi in (self.old.bvi_domains if self.old else [])}
        new_bvis = {bvi.bridge_id: bvi for bvi in (self.new.bvi_domains if self.new else [])}

        for bid, bvi in new_bvis.items():
            if bid not in old_bvis:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="bvi",
                    identifier=f"bvi{bid}",
                    new_value=asdict(bvi)
                ))
            elif asdict(old_bvis[bid]) != asdict(bvi):
                self.changes.append(ConfigChange(
                    change_type=ChangeType.MODIFY,
                    category="bvi",
                    identifier=f"bvi{bid}",
                    old_value=asdict(old_bvis[bid]),
                    new_value=asdict(bvi)
                ))

        for bid in old_bvis:
            if bid not in new_bvis:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="bvi",
                    identifier=f"bvi{bid}",
                    old_value=asdict(old_bvis[bid])
                ))

    def _diff_vlan_passthrough(self) -> None:
        """Compare VLAN passthrough configurations."""
        def make_key(v):
            return (v.vlan_id, v.internal_interface, v.inner_vlan or 0)

        old_vlans = {make_key(v): v for v in (self.old.vlan_passthrough if self.old else [])}
        new_vlans = {make_key(v): v for v in (self.new.vlan_passthrough if self.new else [])}

        for key, v in new_vlans.items():
            if key not in old_vlans:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="vlan_passthrough",
                    identifier=f"vlan{key[0]}->{key[1]}",
                    new_value=asdict(v)
                ))

        for key in old_vlans:
            if key not in new_vlans:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="vlan_passthrough",
                    identifier=f"vlan{key[0]}->{key[1]}",
                    old_value=asdict(old_vlans[key])
                ))

    def _diff_static_routes(self) -> None:
        """Compare static routes (external gateway changes)."""
        # Check for default gateway changes
        old_gw = self.old.external.ipv4_gateway if self.old and self.old.external else None
        new_gw = self.new.external.ipv4_gateway if self.new and self.new.external else None

        if old_gw != new_gw:
            if old_gw and new_gw:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.MODIFY,
                    category="route",
                    identifier="default-v4",
                    old_value={"gateway": old_gw},
                    new_value={"gateway": new_gw}
                ))

        # IPv6 default gateway
        old_gw6 = self.old.external.ipv6_gateway if self.old and self.old.external else None
        new_gw6 = self.new.external.ipv6_gateway if self.new and self.new.external else None

        if old_gw6 != new_gw6:
            if old_gw6 and new_gw6:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.MODIFY,
                    category="route",
                    identifier="default-v6",
                    old_value={"gateway": old_gw6},
                    new_value={"gateway": new_gw6}
                ))
            elif new_gw6 and not old_gw6:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="route",
                    identifier="default-v6",
                    new_value={"gateway": new_gw6}
                ))
            elif old_gw6 and not new_gw6:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="route",
                    identifier="default-v6",
                    old_value={"gateway": old_gw6}
                ))

    def _diff_nat_mappings(self) -> None:
        """Compare NAT (det44) mappings."""
        def make_key(m):
            src = m.get('source_network') if isinstance(m, dict) else m.source_network
            pool = m.get('nat_pool') if isinstance(m, dict) else m.nat_pool
            return (src, pool)

        def to_dict(m):
            if isinstance(m, dict):
                return m
            return asdict(m)

        old_nat = _get_nat_config(self.old)
        new_nat = _get_nat_config(self.new)

        old_mappings = {make_key(m): m for m in old_nat.get('mappings', [])}
        new_mappings = {make_key(m): m for m in new_nat.get('mappings', [])}

        for key, m in new_mappings.items():
            if key not in old_mappings:
                src, pool = key
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="nat_mapping",
                    identifier=f"{src}->{pool}",
                    new_value=to_dict(m)
                ))

        for key in old_mappings:
            if key not in new_mappings:
                m = old_mappings[key]
                src, pool = key
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="nat_mapping",
                    identifier=f"{src}->{pool}",
                    old_value=to_dict(m)
                ))

    def _diff_nat_bypass(self) -> None:
        """Compare NAT bypass (ACL) rules."""
        def make_key(b):
            src = b.get('source') if isinstance(b, dict) else b.source
            dst = b.get('destination') if isinstance(b, dict) else b.destination
            return (src, dst)

        def to_dict(b):
            if isinstance(b, dict):
                return b
            return asdict(b)

        old_nat = _get_nat_config(self.old)
        new_nat = _get_nat_config(self.new)

        old_bypasses = {make_key(b): b for b in old_nat.get('bypass_pairs', [])}
        new_bypasses = {make_key(b): b for b in new_nat.get('bypass_pairs', [])}

        for key, b in new_bypasses.items():
            if key not in old_bypasses:
                src, dst = key
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="nat_bypass",
                    identifier=f"{src}->{dst}",
                    new_value=to_dict(b)
                ))

        for key in old_bypasses:
            if key not in new_bypasses:
                b = old_bypasses[key]
                src, dst = key
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="nat_bypass",
                    identifier=f"{src}->{dst}",
                    old_value=to_dict(b)
                ))

    def _diff_bgp_peers(self) -> None:
        """Compare BGP peer configurations."""
        old_peers = {p.peer_ip: p for p in (self.old.bgp.peers if self.old and self.old.bgp else [])}
        new_peers = {p.peer_ip: p for p in (self.new.bgp.peers if self.new and self.new.bgp else [])}

        # Check BGP enabled state
        old_enabled = self.old.bgp.enabled if self.old else False
        new_enabled = self.new.bgp.enabled if self.new else False

        if old_enabled != new_enabled:
            if new_enabled:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="bgp",
                    identifier="bgp",
                    new_value={
                        "enabled": True,
                        "asn": self.new.bgp.asn,
                        "router_id": self.new.bgp.router_id
                    }
                ))
            else:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="bgp",
                    identifier="bgp",
                    old_value={
                        "enabled": True,
                        "asn": self.old.bgp.asn,
                        "router_id": self.old.bgp.router_id
                    }
                ))

        # Only diff peers if BGP is enabled in both
        if old_enabled and new_enabled:
            for peer_ip, peer in new_peers.items():
                if peer_ip not in old_peers:
                    self.changes.append(ConfigChange(
                        change_type=ChangeType.ADD,
                        category="bgp_peer",
                        identifier=peer_ip,
                        new_value=asdict(peer)
                    ))
                elif asdict(old_peers[peer_ip]) != asdict(peer):
                    self.changes.append(ConfigChange(
                        change_type=ChangeType.MODIFY,
                        category="bgp_peer",
                        identifier=peer_ip,
                        old_value=asdict(old_peers[peer_ip]),
                        new_value=asdict(peer)
                    ))

            for peer_ip in old_peers:
                if peer_ip not in new_peers:
                    self.changes.append(ConfigChange(
                        change_type=ChangeType.DELETE,
                        category="bgp_peer",
                        identifier=peer_ip,
                        old_value=asdict(old_peers[peer_ip])
                    ))

    def _diff_ospf(self) -> None:
        """Compare OSPF (IPv4) configurations."""
        old_enabled = self.old.ospf.enabled if self.old else False
        new_enabled = self.new.ospf.enabled if self.new else False

        if old_enabled != new_enabled:
            if new_enabled:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="ospf",
                    identifier="ospf",
                    new_value=asdict(self.new.ospf)
                ))
            else:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="ospf",
                    identifier="ospf",
                    old_value=asdict(self.old.ospf)
                ))
        elif old_enabled and new_enabled:
            # Check for changes within OSPF config
            if asdict(self.old.ospf) != asdict(self.new.ospf):
                self.changes.append(ConfigChange(
                    change_type=ChangeType.MODIFY,
                    category="ospf",
                    identifier="ospf",
                    old_value=asdict(self.old.ospf),
                    new_value=asdict(self.new.ospf)
                ))

        # Also check interface-level OSPF settings
        self._diff_interface_ospf()

    def _diff_interface_ospf(self) -> None:
        """Compare interface-level OSPF settings."""
        # Compare loopback OSPF settings
        old_loops = {lo.instance: lo for lo in (self.old.loopbacks if self.old else [])}
        new_loops = {lo.instance: lo for lo in (self.new.loopbacks if self.new else [])}

        for inst in set(old_loops.keys()) & set(new_loops.keys()):
            old_lo = old_loops[inst]
            new_lo = new_loops[inst]

            if (old_lo.ospf_area != new_lo.ospf_area or
                old_lo.ospf_passive != new_lo.ospf_passive):
                self.changes.append(ConfigChange(
                    change_type=ChangeType.MODIFY,
                    category="ospf_interface",
                    identifier=f"loop{inst}",
                    old_value={"ospf_area": old_lo.ospf_area, "ospf_passive": old_lo.ospf_passive},
                    new_value={"ospf_area": new_lo.ospf_area, "ospf_passive": new_lo.ospf_passive}
                ))

    def _diff_ospf6(self) -> None:
        """Compare OSPFv3 (IPv6) configurations."""
        old_enabled = self.old.ospf6.enabled if self.old else False
        new_enabled = self.new.ospf6.enabled if self.new else False

        if old_enabled != new_enabled:
            if new_enabled:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.ADD,
                    category="ospf6",
                    identifier="ospf6",
                    new_value=asdict(self.new.ospf6)
                ))
            else:
                self.changes.append(ConfigChange(
                    change_type=ChangeType.DELETE,
                    category="ospf6",
                    identifier="ospf6",
                    old_value=asdict(self.old.ospf6)
                ))


# =============================================================================
# Command Generator
# =============================================================================

class CommandGenerator:
    """Generates VPP/FRR commands from ConfigChanges."""

    def __init__(self, changes: list[ConfigChange], new_config: Any):
        self.changes = changes
        self.new_config = new_config
        self.vpp_core_batch = CommandBatch(target="vpp-core", description="VPP Core changes")
        self.vpp_nat_batch = CommandBatch(target="vpp-nat", description="VPP NAT changes")
        self.frr_batch = CommandBatch(target="frr", description="FRR routing changes")

    def generate_commands(self) -> list[CommandBatch]:
        """Generate all command batches."""
        # Process changes in dependency order
        order = [
            "loopback", "subinterface", "bvi", "vlan_passthrough",  # VPP interfaces first
            "route",  # Then routes
            "nat_mapping", "nat_bypass",  # NAT
            "bgp", "bgp_peer", "ospf", "ospf6", "ospf_interface"  # FRR last
        ]

        changes_by_category = {}
        for change in self.changes:
            if change.category not in changes_by_category:
                changes_by_category[change.category] = []
            changes_by_category[change.category].append(change)

        for category in order:
            for change in changes_by_category.get(category, []):
                self._process_change(change)

        return [self.vpp_core_batch, self.vpp_nat_batch, self.frr_batch]

    def _process_change(self, change: ConfigChange) -> None:
        """Route a change to the appropriate command generator."""
        handlers = {
            "loopback": self._gen_loopback_commands,
            "subinterface": self._gen_subinterface_commands,
            "bvi": self._gen_bvi_commands,
            "vlan_passthrough": self._gen_vlan_passthrough_commands,
            "route": self._gen_route_commands,
            "nat_mapping": self._gen_nat_mapping_commands,
            "nat_bypass": self._gen_nat_bypass_commands,
            "bgp": self._gen_bgp_commands,
            "bgp_peer": self._gen_bgp_peer_commands,
            "ospf": self._gen_ospf_commands,
            "ospf6": self._gen_ospf6_commands,
            "ospf_interface": self._gen_ospf_interface_commands,
        }

        handler = handlers.get(change.category)
        if handler:
            handler(change)

    def _gen_loopback_commands(self, change: ConfigChange) -> None:
        """Generate VPP commands for loopback changes."""
        if change.change_type == ChangeType.ADD:
            new = change.new_value
            inst = new['instance']
            cmds = [
                f"create loopback interface instance {inst}",
                f"set interface state loop{inst} up",
            ]
            # Create LCP BEFORE adding IPs so linux_cp can sync addresses
            if new.get('create_lcp'):
                cmds.append(f"lcp create loop{inst} host-if lo{inst}")
            # Now add IPs - they'll sync to Linux via linux_cp
            if new.get('ipv4'):
                cmds.append(f"set interface ip address loop{inst} {new['ipv4']}/{new['ipv4_prefix']}")
            if new.get('ipv6'):
                cmds.append(f"set interface ip address loop{inst} {new['ipv6']}/{new['ipv6_prefix']}")

            self.vpp_core_batch.commands.extend(cmds)
            self.vpp_core_batch.rollback_commands.extend([
                f"delete loopback interface intfc loop{inst}",
            ])

            # Add FRR commands if loopback participates in OSPF
            if new.get('ospf_area') is not None and new.get('ipv4'):
                lcp_name = f"lo{inst}"
                frr_cmds = [
                    "router ospf",
                    f" network {new['ipv4']}/32 area {new['ospf_area']}",
                ]
                if new.get('ospf_passive'):
                    frr_cmds.append(f" passive-interface {lcp_name}")
                frr_cmds.append("exit")
                self.frr_batch.commands.extend(frr_cmds)

            # OSPFv3 for IPv6
            if new.get('ospf6_area') is not None and new.get('ipv6') and new.get('create_lcp'):
                lcp_name = f"lo{inst}"
                frr_cmds = [
                    f"interface {lcp_name}",
                    f" ipv6 ospf6 area {new['ospf6_area']}",
                ]
                if new.get('ospf6_passive'):
                    frr_cmds.append(" ipv6 ospf6 passive")
                frr_cmds.append("exit")
                self.frr_batch.commands.extend(frr_cmds)

        elif change.change_type == ChangeType.DELETE:
            old = change.old_value
            inst = old['instance']
            if old.get('create_lcp'):
                self.vpp_core_batch.commands.append(f"lcp delete loop{inst}")
            self.vpp_core_batch.commands.append(f"delete loopback interface intfc loop{inst}")

        elif change.change_type == ChangeType.MODIFY:
            old, new = change.old_value, change.new_value
            inst = new['instance']

            # Handle IP address changes
            if old.get('ipv4') and old['ipv4'] != new.get('ipv4'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address del loop{inst} {old['ipv4']}/{old['ipv4_prefix']}"
                )
            if new.get('ipv4') and new['ipv4'] != old.get('ipv4'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address loop{inst} {new['ipv4']}/{new['ipv4_prefix']}"
                )

            if old.get('ipv6') and old['ipv6'] != new.get('ipv6'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address del loop{inst} {old['ipv6']}/{old['ipv6_prefix']}"
                )
            if new.get('ipv6') and new['ipv6'] != old.get('ipv6'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address loop{inst} {new['ipv6']}/{new['ipv6_prefix']}"
                )

            # Handle LCP changes
            if old.get('create_lcp') and not new.get('create_lcp'):
                self.vpp_core_batch.commands.append(f"lcp delete loop{inst}")
            elif not old.get('create_lcp') and new.get('create_lcp'):
                self.vpp_core_batch.commands.append(f"lcp create loop{inst} host-if lo{inst}")

    def _gen_subinterface_commands(self, change: ConfigChange) -> None:
        """Generate VPP commands for sub-interface changes."""
        # Parse parent.vlan from identifier
        parent, vlan_str = change.identifier.rsplit('.', 1)
        vlan_id = int(vlan_str)

        if change.change_type == ChangeType.ADD:
            new = change.new_value
            cmds = [
                f"create sub-interfaces {parent} {vlan_id}",
                f"set interface state {parent}.{vlan_id} up",
            ]
            # Create LCP BEFORE IPs so linux_cp syncs addresses to Linux
            if new.get('create_lcp'):
                lcp_name = f"{parent[:3]}-v{vlan_id}"
                cmds.append(f"lcp create {parent}.{vlan_id} host-if {lcp_name}")
            # Add IPs after LCP
            if new.get('ipv4'):
                cmds.append(f"set interface ip address {parent}.{vlan_id} {new['ipv4']}/{new['ipv4_prefix']}")
            if new.get('ipv6'):
                cmds.append(f"set interface ip address {parent}.{vlan_id} {new['ipv6']}/{new['ipv6_prefix']}")

            self.vpp_core_batch.commands.extend(cmds)
            self.vpp_core_batch.rollback_commands.append(f"delete sub-interfaces {parent} {vlan_id}")

        elif change.change_type == ChangeType.DELETE:
            old = change.old_value
            if old.get('create_lcp'):
                # lcp delete uses VPP interface name, not Linux name
                self.vpp_core_batch.commands.append(f"lcp delete {parent}.{vlan_id}")
            self.vpp_core_batch.commands.append(f"delete sub-interfaces {parent} {vlan_id}")

        elif change.change_type == ChangeType.MODIFY:
            old, new = change.old_value, change.new_value

            # Handle IP changes
            if old.get('ipv4') and old['ipv4'] != new.get('ipv4'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address del {parent}.{vlan_id} {old['ipv4']}/{old['ipv4_prefix']}"
                )
            if new.get('ipv4') and new['ipv4'] != old.get('ipv4'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address {parent}.{vlan_id} {new['ipv4']}/{new['ipv4_prefix']}"
                )

            if old.get('ipv6') and old['ipv6'] != new.get('ipv6'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address del {parent}.{vlan_id} {old['ipv6']}/{old['ipv6_prefix']}"
                )
            if new.get('ipv6') and new['ipv6'] != old.get('ipv6'):
                self.vpp_core_batch.commands.append(
                    f"set interface ip address {parent}.{vlan_id} {new['ipv6']}/{new['ipv6_prefix']}"
                )

    def _gen_bvi_commands(self, change: ConfigChange) -> None:
        """Generate VPP commands for BVI domain changes."""
        if change.change_type == ChangeType.ADD:
            new = change.new_value
            bid = new['bridge_id']

            cmds = [
                f"create loopback interface instance {bid}",
                f"set interface state loop{bid} up",
                f"create bridge-domain {bid}",
                f"set interface l2 bridge loop{bid} {bid} bvi",
            ]

            # Add bridge members
            for member in new.get('members', []):
                iface = member['interface']
                vlan = member.get('vlan_id')
                if vlan:
                    cmds.extend([
                        f"create sub-interfaces {iface} {vlan}",
                        f"set interface state {iface}.{vlan} up",
                        f"set interface l2 bridge {iface}.{vlan} {bid}",
                    ])
                else:
                    cmds.append(f"set interface l2 bridge {iface} {bid}")

            # Create LCP BEFORE IPs so linux_cp syncs addresses to Linux
            if new.get('create_lcp'):
                cmds.append(f"lcp create loop{bid} host-if bvi{bid}")

            # Add IPs after LCP
            if new.get('ipv4'):
                cmds.append(f"set interface ip address loop{bid} {new['ipv4']}/{new['ipv4_prefix']}")
            if new.get('ipv6'):
                cmds.append(f"set interface ip address loop{bid} {new['ipv6']}/{new['ipv6_prefix']}")

            self.vpp_core_batch.commands.extend(cmds)

        elif change.change_type == ChangeType.DELETE:
            old = change.old_value
            bid = old['bridge_id']

            if old.get('create_lcp'):
                # lcp delete uses VPP interface name (loopback), not Linux name
                self.vpp_core_batch.commands.append(f"lcp delete loop{bid}")

            # Remove bridge members first
            for member in old.get('members', []):
                iface = member['interface']
                vlan = member.get('vlan_id')
                if vlan:
                    self.vpp_core_batch.commands.append(f"delete sub-interfaces {iface} {vlan}")

            self.vpp_core_batch.commands.extend([
                f"delete bridge-domain {bid}",
                f"delete loopback interface intfc loop{bid}",
            ])

    def _gen_vlan_passthrough_commands(self, change: ConfigChange) -> None:
        """Generate VPP commands for VLAN passthrough (L2 xconnect)."""
        if change.change_type == ChangeType.ADD:
            v = change.new_value
            vlan_id = v['vlan_id']
            internal = v['internal_interface']
            inner = v.get('inner_vlan')
            vlan_type = v.get('vlan_type', 'dot1q')

            if inner:
                # QinQ with specific inner tag
                cmds = [
                    f"create sub-interface external {vlan_id} inner-dot1q {inner}",
                    f"create sub-interface {internal} {vlan_id} inner-dot1q {inner}",
                    f"set interface state external.{vlan_id}.{inner} up",
                    f"set interface state {internal}.{vlan_id}.{inner} up",
                    f"set interface l2 xconnect external.{vlan_id}.{inner} {internal}.{vlan_id}.{inner}",
                    f"set interface l2 xconnect {internal}.{vlan_id}.{inner} external.{vlan_id}.{inner}",
                ]
            elif vlan_type == 'dot1ad':
                # QinQ S-VLAN
                cmds = [
                    f"create sub-interface external {vlan_id} dot1ad",
                    f"create sub-interface {internal} {vlan_id} dot1ad",
                    f"set interface state external.{vlan_id} up",
                    f"set interface state {internal}.{vlan_id} up",
                    f"set interface l2 xconnect external.{vlan_id} {internal}.{vlan_id}",
                    f"set interface l2 xconnect {internal}.{vlan_id} external.{vlan_id}",
                ]
            else:
                # Standard 802.1Q
                cmds = [
                    f"create sub-interface external {vlan_id}",
                    f"create sub-interface {internal} {vlan_id}",
                    f"set interface state external.{vlan_id} up",
                    f"set interface state {internal}.{vlan_id} up",
                    f"set interface l2 xconnect external.{vlan_id} {internal}.{vlan_id}",
                    f"set interface l2 xconnect {internal}.{vlan_id} external.{vlan_id}",
                ]

            self.vpp_core_batch.commands.extend(cmds)

        elif change.change_type == ChangeType.DELETE:
            v = change.old_value
            vlan_id = v['vlan_id']
            internal = v['internal_interface']
            inner = v.get('inner_vlan')

            if inner:
                cmds = [
                    f"delete sub-interface external.{vlan_id}.{inner}",
                    f"delete sub-interface {internal}.{vlan_id}.{inner}",
                ]
            else:
                cmds = [
                    f"delete sub-interface external.{vlan_id}",
                    f"delete sub-interface {internal}.{vlan_id}",
                ]

            self.vpp_core_batch.commands.extend(cmds)

    def _gen_route_commands(self, change: ConfigChange) -> None:
        """Generate VPP commands for static route changes."""
        if change.identifier == "default-v4":
            if change.change_type == ChangeType.MODIFY:
                old_gw = change.old_value['gateway']
                new_gw = change.new_value['gateway']
                self.vpp_core_batch.commands.extend([
                    f"ip route del 0.0.0.0/0",
                    f"ip route add 0.0.0.0/0 via {new_gw} external",
                ])
        elif change.identifier == "default-v6":
            if change.change_type == ChangeType.ADD:
                new_gw = change.new_value['gateway']
                self.vpp_core_batch.commands.append(f"ip route add ::/0 via {new_gw} external")
            elif change.change_type == ChangeType.DELETE:
                self.vpp_core_batch.commands.append(f"ip route del ::/0")
            elif change.change_type == ChangeType.MODIFY:
                new_gw = change.new_value['gateway']
                self.vpp_core_batch.commands.extend([
                    f"ip route del ::/0",
                    f"ip route add ::/0 via {new_gw} external",
                ])

    def _gen_nat_mapping_commands(self, change: ConfigChange) -> None:
        """Generate VPP NAT commands for det44 mapping changes."""
        if change.change_type == ChangeType.ADD:
            m = change.new_value
            self.vpp_nat_batch.commands.append(
                f"det44 add in {m['source_network']} out {m['nat_pool']}"
            )
        elif change.change_type == ChangeType.DELETE:
            m = change.old_value
            self.vpp_nat_batch.commands.append(
                f"det44 del in {m['source_network']} out {m['nat_pool']}"
            )

    def _gen_nat_bypass_commands(self, change: ConfigChange) -> None:
        """Generate VPP commands for NAT bypass (ACL) changes.

        Note: ACL changes require regenerating the entire ACL set because
        VPP ACLs are ordered lists. We mark these for full ACL rebuild.
        """
        # For now, we'll just note that ACLs need rebuild
        # The actual rebuild will happen in the apply phase
        if change.change_type in (ChangeType.ADD, ChangeType.DELETE):
            # Mark that ACLs need full regeneration
            # This will be handled specially in the applier
            pass

    def _gen_bgp_commands(self, change: ConfigChange) -> None:
        """Generate FRR commands for BGP enable/disable."""
        if change.change_type == ChangeType.ADD:
            new = change.new_value
            self.frr_batch.commands.extend([
                f"router bgp {new['asn']}",
                f" bgp router-id {new['router_id']}",
                f" no bgp default ipv4-unicast",
                f"exit",
            ])
        elif change.change_type == ChangeType.DELETE:
            old = change.old_value
            self.frr_batch.commands.append(f"no router bgp {old['asn']}")

    def _gen_bgp_peer_commands(self, change: ConfigChange) -> None:
        """Generate FRR commands for BGP peer changes."""
        if change.change_type == ChangeType.ADD:
            peer = change.new_value
            peer_ip = peer['peer_ip']
            is_v6 = ':' in peer_ip

            cmds = [
                f"router bgp {self.new_config.bgp.asn}",
                f" neighbor {peer_ip} remote-as {peer['peer_asn']}",
            ]
            if peer.get('description'):
                cmds.append(f" neighbor {peer_ip} description {peer['description']}")
            if peer.get('update_source'):
                cmds.append(f" neighbor {peer_ip} update-source {peer['update_source']}")

            # Activate in appropriate address family
            if is_v6:
                cmds.extend([
                    f" address-family ipv6 unicast",
                    f"  neighbor {peer_ip} activate",
                    f"  neighbor {peer_ip} soft-reconfiguration inbound",
                    f" exit-address-family",
                ])
            else:
                cmds.extend([
                    f" address-family ipv4 unicast",
                    f"  neighbor {peer_ip} activate",
                    f"  neighbor {peer_ip} soft-reconfiguration inbound",
                    f" exit-address-family",
                ])
            cmds.append("exit")

            self.frr_batch.commands.extend(cmds)

        elif change.change_type == ChangeType.DELETE:
            peer_ip = change.identifier
            self.frr_batch.commands.extend([
                f"router bgp {self.new_config.bgp.asn}",
                f" no neighbor {peer_ip}",
                "exit",
            ])

        elif change.change_type == ChangeType.MODIFY:
            # For modify, we delete and re-add
            old_peer = change.old_value
            new_peer = change.new_value
            peer_ip = new_peer['peer_ip']
            is_v6 = ':' in peer_ip

            cmds = [
                f"router bgp {self.new_config.bgp.asn}",
                f" no neighbor {peer_ip}",
                f" neighbor {peer_ip} remote-as {new_peer['peer_asn']}",
            ]
            if new_peer.get('description'):
                cmds.append(f" neighbor {peer_ip} description {new_peer['description']}")
            if new_peer.get('update_source'):
                cmds.append(f" neighbor {peer_ip} update-source {new_peer['update_source']}")

            if is_v6:
                cmds.extend([
                    f" address-family ipv6 unicast",
                    f"  neighbor {peer_ip} activate",
                    f"  neighbor {peer_ip} soft-reconfiguration inbound",
                    f" exit-address-family",
                ])
            else:
                cmds.extend([
                    f" address-family ipv4 unicast",
                    f"  neighbor {peer_ip} activate",
                    f"  neighbor {peer_ip} soft-reconfiguration inbound",
                    f" exit-address-family",
                ])
            cmds.append("exit")

            self.frr_batch.commands.extend(cmds)

    def _gen_ospf_commands(self, change: ConfigChange) -> None:
        """Generate FRR commands for OSPF enable/disable/modify."""
        if change.change_type == ChangeType.ADD:
            new = change.new_value
            router_id = new.get('router_id') or self.new_config.bgp.router_id
            cmds = [
                f"router ospf",
                f" ospf router-id {router_id}",
            ]
            if new.get('default_originate'):
                cmds.append(" default-information originate")
            cmds.append("exit")
            self.frr_batch.commands.extend(cmds)

        elif change.change_type == ChangeType.DELETE:
            self.frr_batch.commands.append("no router ospf")

        elif change.change_type == ChangeType.MODIFY:
            old, new = change.old_value, change.new_value
            cmds = ["router ospf"]

            if old.get('default_originate') and not new.get('default_originate'):
                cmds.append(" no default-information originate")
            elif not old.get('default_originate') and new.get('default_originate'):
                cmds.append(" default-information originate")

            cmds.append("exit")
            self.frr_batch.commands.extend(cmds)

    def _gen_ospf6_commands(self, change: ConfigChange) -> None:
        """Generate FRR commands for OSPFv3 enable/disable/modify."""
        if change.change_type == ChangeType.ADD:
            new = change.new_value
            router_id = (new.get('router_id') or
                        (self.new_config.ospf.router_id if self.new_config.ospf else None) or
                        self.new_config.bgp.router_id)
            cmds = [
                f"router ospf6",
                f" ospf6 router-id {router_id}",
            ]
            if new.get('default_originate'):
                cmds.append(" default-information originate")
            cmds.append("exit")
            self.frr_batch.commands.extend(cmds)

        elif change.change_type == ChangeType.DELETE:
            self.frr_batch.commands.append("no router ospf6")

    def _gen_ospf_interface_commands(self, change: ConfigChange) -> None:
        """Generate FRR commands for interface-level OSPF changes."""
        if change.change_type == ChangeType.MODIFY:
            iface = change.identifier
            old, new = change.old_value, change.new_value

            cmds = ["router ospf"]

            # Handle area changes
            if old.get('ospf_area') is not None and new.get('ospf_area') is None:
                # Removing from OSPF - need to find the network statement
                # This is complex because we need the IP; skip for now
                pass
            elif old.get('ospf_area') != new.get('ospf_area'):
                # Area change requires removing old and adding new
                pass

            # Handle passive changes
            if old.get('ospf_passive') and not new.get('ospf_passive'):
                cmds.append(f" no passive-interface {iface}")
            elif not old.get('ospf_passive') and new.get('ospf_passive'):
                cmds.append(f" passive-interface {iface}")

            cmds.append("exit")
            if len(cmds) > 2:  # More than just "router ospf" and "exit"
                self.frr_batch.commands.extend(cmds)


# =============================================================================
# Live Config Applier
# =============================================================================

class LiveConfigApplier:
    """Applies configuration changes live to VPP and FRR."""

    def __init__(self, old_config: Any, new_config: Any):
        self.old_config = old_config
        self.new_config = new_config
        self.engine = ConfigDiffEngine(old_config, new_config)
        self.applied_batches: list[CommandBatch] = []
        self.failed = False

    def apply(self, dry_run: bool = False) -> tuple[bool, list[str]]:
        """
        Apply configuration changes.

        Args:
            dry_run: If True, only show what would be done

        Returns:
            (success: bool, messages: list[str])
        """
        changes = self.engine.compute_diff()

        if not changes:
            return True, ["No changes detected"]

        generator = CommandGenerator(changes, self.new_config)
        batches = generator.generate_commands()

        messages = []
        messages.append(f"Detected {len(changes)} configuration change(s):")
        for change in changes:
            messages.append(f"  - {change}")

        messages.append("")

        for batch in batches:
            if batch.is_empty():
                continue

            if dry_run:
                messages.append(f"[DRY-RUN] {batch.description} ({len(batch.commands)} commands):")
                for cmd in batch.commands:
                    messages.append(f"  {cmd}")
                messages.append("")
            else:
                messages.append(f"Applying {batch.description}...")
                success, output = self._execute_batch(batch)
                if not success:
                    messages.append(f"  ERROR: {output}")
                    self.failed = True
                    # Attempt rollback
                    if batch.rollback_commands:
                        messages.append("  Attempting rollback...")
                        self._rollback(batch)
                    return False, messages
                else:
                    messages.append(f"  OK ({len(batch.commands)} commands)")
                    self.applied_batches.append(batch)

        return True, messages

    def _execute_batch(self, batch: CommandBatch) -> tuple[bool, str]:
        """Execute a command batch."""
        if batch.target == "vpp-core":
            return self._execute_vpp(batch.commands, "core")
        elif batch.target == "vpp-nat":
            return self._execute_vpp(batch.commands, "nat")
        elif batch.target == "frr":
            return frr_exec(batch.commands)
        else:
            return False, f"Unknown target: {batch.target}"

    def _execute_vpp(self, commands: list[str], instance: str) -> tuple[bool, str]:
        """Execute VPP commands."""
        for cmd in commands:
            if not cmd:
                continue
            success, output = vpp_exec(cmd, instance)
            if not success:
                return False, f"Command failed: {cmd}\nOutput: {output}"
        return True, "OK"

    def _rollback(self, batch: CommandBatch) -> None:
        """Attempt to rollback a failed batch."""
        for cmd in reversed(batch.rollback_commands):
            if not cmd:
                continue
            if batch.target in ("vpp-core", "vpp-nat"):
                instance = "core" if batch.target == "vpp-core" else "nat"
                vpp_exec(cmd, instance)
            elif batch.target == "frr":
                frr_exec([cmd])


# =============================================================================
# Helper Functions
# =============================================================================

def requires_restart(old_config: Any, new_config: Any) -> list[str]:
    """
    Check if any changes require a full service restart.

    Returns a list of reasons why restart is needed, or empty list if
    all changes can be applied live.
    """
    reasons = []

    # CPU config changes require restart
    if old_config and new_config:
        old_cpu = asdict(old_config.cpu) if old_config.cpu else {}
        new_cpu = asdict(new_config.cpu) if new_config.cpu else {}
        if old_cpu != new_cpu:
            reasons.append("CPU allocation changed")

    # Management interface changes (handled by systemd-networkd, not VPP)
    if old_config and new_config:
        old_mgmt = asdict(old_config.management) if old_config.management else {}
        new_mgmt = asdict(new_config.management) if new_config.management else {}
        if old_mgmt != new_mgmt:
            reasons.append("Management interface changed (requires networkd restart)")

    # External interface PCI/name changes
    if old_config and new_config:
        if old_config.external and new_config.external:
            if old_config.external.pci != new_config.external.pci:
                reasons.append("External interface PCI address changed")
            if old_config.external.iface != new_config.external.iface:
                reasons.append("External interface name changed")

    # Internal interface PCI/name changes
    if old_config and new_config:
        old_internal_pcis = {i.pci for i in old_config.internal}
        new_internal_pcis = {i.pci for i in new_config.internal}
        if old_internal_pcis != new_internal_pcis:
            reasons.append("Internal interface PCI addresses changed")

    return reasons


def get_change_summary(old_config: Any, new_config: Any) -> str:
    """Get a human-readable summary of changes."""
    engine = ConfigDiffEngine(old_config, new_config)
    changes = engine.compute_diff()

    if not changes:
        return "No changes"

    lines = [f"{len(changes)} change(s):"]
    for change in changes:
        lines.append(f"  - {change}")

    return "\n".join(lines)
