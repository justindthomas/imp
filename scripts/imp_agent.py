#!/usr/bin/env python3
"""
imp_agent.py - LLM-powered agent for IMP configuration management

This module provides a natural language interface to router configuration
using Ollama and tool calling. Changes are staged until 'apply'.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

try:
    import requests
except ImportError:
    print("ERROR: python3-requests is required. Install with: apt install python3-requests")
    sys.exit(1)


# =============================================================================
# Configuration
# =============================================================================

DEFAULT_OLLAMA_HOST = "localhost:11434"
DEFAULT_OLLAMA_MODEL = "gpt-oss:120b"
IMP_CONFIG_FILE = Path("/persistent/config/imp.json")


class Colors:
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    MAGENTA = "\033[0;35m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    NC = "\033[0m"


def log(msg: str) -> None:
    print(f"{Colors.GREEN}[+]{Colors.NC} {msg}")


def warn(msg: str) -> None:
    print(f"{Colors.YELLOW}[!]{Colors.NC} {msg}")


def error(msg: str) -> None:
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")


def tool_log(name: str, args: dict) -> None:
    """Log a tool call."""
    print(f"{Colors.MAGENTA}[Tool: {name}]{Colors.NC}")
    for key, value in args.items():
        print(f"  {key}: {value}")


# =============================================================================
# Configuration Loading
# =============================================================================

def load_imp_config() -> dict:
    """Load IMP settings from config file."""
    if IMP_CONFIG_FILE.exists():
        try:
            with open(IMP_CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_ollama_host(arg_host: Optional[str] = None) -> str:
    """Get Ollama host from args, env, config, or default."""
    if arg_host:
        return arg_host
    if os.environ.get("OLLAMA_HOST"):
        return os.environ["OLLAMA_HOST"]
    config = load_imp_config()
    if config.get("ollama", {}).get("host"):
        return config["ollama"]["host"]
    return DEFAULT_OLLAMA_HOST


def get_ollama_model(arg_model: Optional[str] = None) -> str:
    """Get Ollama model from args, env, config, or default."""
    if arg_model:
        return arg_model
    if os.environ.get("OLLAMA_MODEL"):
        return os.environ["OLLAMA_MODEL"]
    config = load_imp_config()
    if config.get("ollama", {}).get("model"):
        return config["ollama"]["model"]
    return DEFAULT_OLLAMA_MODEL


# =============================================================================
# Ollama Client
# =============================================================================

class OllamaClient:
    """HTTP client for Ollama API."""

    def __init__(self, host: str, model: str):
        self.host = host.rstrip("/")
        if not self.host.startswith("http"):
            self.host = f"http://{self.host}"
        self.model = model
        self.url = f"{self.host}/api/chat"

    def chat(self, messages: list, tools: list) -> dict:
        """
        Send a chat request with tools.
        Returns the response dict with 'message' key.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }

        response = requests.post(self.url, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()

    def check_connection(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def check_model(self) -> bool:
        """Check if the model is available."""
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                # Check for exact match or match without tag
                return any(
                    self.model == m or self.model == m.split(":")[0]
                    for m in models
                )
        except requests.RequestException:
            pass
        return False


# =============================================================================
# Tool Definitions
# =============================================================================

def build_tools() -> list[dict]:
    """Build the list of tool definitions for Ollama."""
    return [
        # Read tools
        {
            "type": "function",
            "function": {
                "name": "get_config_summary",
                "description": "Get a summary of the current router configuration including hostname, interfaces, BGP, and NAT settings",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_interfaces",
                "description": "List all network interfaces including management, external, and internal interfaces with their IP addresses and sub-interfaces",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_interface_detail",
                "description": "Get detailed information about a specific interface",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Interface name: 'management', 'external', or 'internal0', 'internal1', etc."
                        }
                    },
                    "required": ["interface"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_loopbacks",
                "description": "List all loopback interfaces with their IP addresses",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_bvi_domains",
                "description": "List all BVI (Bridge Virtual Interface) domains",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_vlan_passthrough",
                "description": "List all VLAN passthrough rules (L2 xconnects between external and internal)",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_nat_config",
                "description": "Get NAT configuration including pool prefix, mappings, and bypass rules",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_bgp_config",
                "description": "Get BGP configuration including ASN, router ID, and peer settings",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
        # Write tools
        {
            "type": "function",
            "function": {
                "name": "add_subinterface",
                "description": "Add a VLAN sub-interface to an interface (external or internal). At least one of ipv4_cidr or ipv6_cidr must be provided.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Parent interface name (e.g., 'external', 'internal0')"
                        },
                        "vlan_id": {
                            "type": "integer",
                            "description": "VLAN ID (1-4094)"
                        },
                        "ipv4_cidr": {
                            "type": "string",
                            "description": "IPv4 address in CIDR notation (e.g., '10.0.100.1/24')"
                        },
                        "ipv6_cidr": {
                            "type": "string",
                            "description": "IPv6 address in CIDR notation (optional)"
                        },
                        "create_lcp": {
                            "type": "boolean",
                            "description": "Create linux_cp TAP for FRR visibility (default: true)"
                        }
                    },
                    "required": ["interface", "vlan_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_subinterface",
                "description": "Delete a VLAN sub-interface from an interface",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Parent interface name (e.g., 'external', 'internal0')"
                        },
                        "vlan_id": {
                            "type": "integer",
                            "description": "VLAN ID to delete"
                        }
                    },
                    "required": ["interface", "vlan_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_loopback",
                "description": "Add a loopback interface. At least one of ipv4_cidr or ipv6_cidr must be provided.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Friendly name for the loopback (e.g., 'router-id', 'services')"
                        },
                        "ipv4_cidr": {
                            "type": "string",
                            "description": "IPv4 address in CIDR notation (e.g., '10.255.255.1/32')"
                        },
                        "ipv6_cidr": {
                            "type": "string",
                            "description": "IPv6 address in CIDR notation (optional)"
                        },
                        "create_lcp": {
                            "type": "boolean",
                            "description": "Create linux_cp TAP for FRR visibility (default: true)"
                        }
                    },
                    "required": ["name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_loopback",
                "description": "Delete a loopback interface by its instance number",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instance": {
                            "type": "integer",
                            "description": "Loopback instance number (e.g., 0 for loop0)"
                        }
                    },
                    "required": ["instance"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_nat_mapping",
                "description": "Add a NAT source mapping that translates traffic from a source network to a NAT pool",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_network": {
                            "type": "string",
                            "description": "Source network in CIDR notation (e.g., '192.168.1.0/24')"
                        },
                        "nat_pool": {
                            "type": "string",
                            "description": "NAT pool in CIDR notation (e.g., '23.177.24.96/30')"
                        }
                    },
                    "required": ["source_network", "nat_pool"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_nat_mapping",
                "description": "Delete a NAT mapping by source network",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source_network": {
                            "type": "string",
                            "description": "Source network to remove from NAT"
                        }
                    },
                    "required": ["source_network"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_nat_bypass",
                "description": "Add a NAT bypass rule that allows traffic between source and destination to skip NAT",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Source network in CIDR notation"
                        },
                        "destination": {
                            "type": "string",
                            "description": "Destination network in CIDR notation"
                        }
                    },
                    "required": ["source", "destination"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_nat_bypass",
                "description": "Delete a NAT bypass rule",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "Source network of the bypass rule"
                        },
                        "destination": {
                            "type": "string",
                            "description": "Destination network of the bypass rule"
                        }
                    },
                    "required": ["source", "destination"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "set_nat_prefix",
                "description": "Set the NAT pool prefix that will be announced via BGP",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "NAT pool prefix in CIDR notation (e.g., '23.177.24.96/29')"
                        }
                    },
                    "required": ["prefix"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_vlan_passthrough",
                "description": "Add a VLAN passthrough rule to create an L2 xconnect between external and internal interfaces",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "vlan_id": {
                            "type": "integer",
                            "description": "VLAN ID to pass through (1-4094)"
                        },
                        "internal_interface": {
                            "type": "string",
                            "description": "Internal interface name (e.g., 'internal0')"
                        },
                        "vlan_type": {
                            "type": "string",
                            "description": "VLAN type: 'dot1q' (default) or 'dot1ad' for QinQ",
                            "enum": ["dot1q", "dot1ad"]
                        }
                    },
                    "required": ["vlan_id", "internal_interface"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_vlan_passthrough",
                "description": "Delete a VLAN passthrough rule",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "vlan_id": {
                            "type": "integer",
                            "description": "VLAN ID to remove"
                        }
                    },
                    "required": ["vlan_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "enable_bgp",
                "description": "Enable and configure BGP peering",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "asn": {
                            "type": "integer",
                            "description": "Local AS number"
                        },
                        "router_id": {
                            "type": "string",
                            "description": "Router ID (IPv4 address)"
                        },
                        "peer_ipv4": {
                            "type": "string",
                            "description": "Peer IPv4 address"
                        },
                        "peer_asn": {
                            "type": "integer",
                            "description": "Peer AS number"
                        },
                        "peer_ipv6": {
                            "type": "string",
                            "description": "Peer IPv6 address (optional)"
                        }
                    },
                    "required": ["asn", "router_id", "peer_ipv4", "peer_asn"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "disable_bgp",
                "description": "Disable BGP",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        },
    ]


# =============================================================================
# Tool Execution - Read Operations
# =============================================================================

def tool_get_config_summary(config) -> str:
    """Get configuration summary."""
    if not config:
        return "No configuration loaded"

    lines = [f"Hostname: {config.hostname}"]

    if config.management:
        lines.append(f"Management: {config.management.iface} ({config.management.mode})")

    if config.external:
        ext = config.external
        lines.append(f"External: {ext.iface} -> {ext.ipv4}/{ext.ipv4_prefix}")
        if ext.subinterfaces:
            lines.append(f"  Sub-interfaces: {len(ext.subinterfaces)}")

    for iface in config.internal:
        lines.append(f"Internal: {iface.vpp_name} ({iface.iface}) -> {iface.ipv4}/{iface.ipv4_prefix}")
        if iface.subinterfaces:
            lines.append(f"  Sub-interfaces: {len(iface.subinterfaces)}")

    if config.bgp.enabled:
        lines.append(f"BGP: AS {config.bgp.asn}, peer {config.bgp.peer_ipv4} (AS {config.bgp.peer_asn})")
    else:
        lines.append("BGP: Disabled")

    lines.append(f"NAT prefix: {config.nat.bgp_prefix}")
    lines.append(f"NAT mappings: {len(config.nat.mappings)}")
    lines.append(f"NAT bypass rules: {len(config.nat.bypass_pairs)}")
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

    if config.external:
        e = config.external
        lines.append(f"external ({e.iface}, PCI {e.pci}): {e.ipv4}/{e.ipv4_prefix}, gateway {e.ipv4_gateway}")
        if e.ipv6:
            lines.append(f"  IPv6: {e.ipv6}/{e.ipv6_prefix}")
        for sub in e.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            lines.append(f"  .{sub.vlan_id}: {', '.join(ips)}{lcp}")

    for iface in config.internal:
        lines.append(f"{iface.vpp_name} ({iface.iface}, PCI {iface.pci}): {iface.ipv4}/{iface.ipv4_prefix}")
        if iface.ipv6:
            lines.append(f"  IPv6: {iface.ipv6}/{iface.ipv6_prefix}")
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

    if interface == "external":
        if not config.external:
            return "External interface not configured"
        e = config.external
        lines = [
            f"Interface: {e.iface}",
            f"PCI: {e.pci}",
            f"IPv4: {e.ipv4}/{e.ipv4_prefix}",
            f"Gateway: {e.ipv4_gateway}",
        ]
        if e.ipv6:
            lines.append(f"IPv6: {e.ipv6}/{e.ipv6_prefix}")
            if e.ipv6_gateway:
                lines.append(f"IPv6 Gateway: {e.ipv6_gateway}")
        lines.append(f"Sub-interfaces: {len(e.subinterfaces)}")
        for sub in e.subinterfaces:
            ips = []
            if sub.ipv4:
                ips.append(f"{sub.ipv4}/{sub.ipv4_prefix}")
            if sub.ipv6:
                ips.append(f"{sub.ipv6}/{sub.ipv6_prefix}")
            lcp = " (LCP)" if sub.create_lcp else ""
            lines.append(f"  .{sub.vlan_id}: {', '.join(ips)}{lcp}")
        return "\n".join(lines)

    # Check internal interfaces
    for iface in config.internal:
        if iface.vpp_name == interface:
            lines = [
                f"Interface: {iface.iface}",
                f"VPP Name: {iface.vpp_name}",
                f"PCI: {iface.pci}",
                f"IPv4: {iface.ipv4}/{iface.ipv4_prefix}",
                f"Network: {iface.network}",
            ]
            if iface.ipv6:
                lines.append(f"IPv6: {iface.ipv6}/{iface.ipv6_prefix}")
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

    return f"Interface '{interface}' not found. Available: management, external, " + \
           ", ".join(i.vpp_name for i in config.internal)


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
            lines.append(f"VLAN {v.vlan_id}.{v.inner_vlan} (QinQ) <-> {v.internal_interface}")
        elif v.vlan_type == "dot1ad":
            lines.append(f"S-VLAN {v.vlan_id} (QinQ) <-> {v.internal_interface}")
        else:
            lines.append(f"VLAN {v.vlan_id} (802.1Q) <-> {v.internal_interface}")

    return "\n".join(lines)


def tool_get_nat_config(config) -> str:
    """Get NAT configuration."""
    if not config:
        return "No configuration loaded"

    lines = [
        f"NAT Pool Prefix: {config.nat.bgp_prefix or 'Not set'}",
        f"Mappings: {len(config.nat.mappings)}",
    ]

    for m in config.nat.mappings:
        lines.append(f"  {m.source_network} -> {m.nat_pool}")

    lines.append(f"Bypass Rules: {len(config.nat.bypass_pairs)}")
    for bp in config.nat.bypass_pairs:
        lines.append(f"  {bp.source} -> {bp.destination}")

    return "\n".join(lines)


def tool_get_bgp_config(config) -> str:
    """Get BGP configuration."""
    if not config:
        return "No configuration loaded"

    bgp = config.bgp
    if not bgp.enabled:
        return "BGP is disabled"

    lines = [
        f"Enabled: {bgp.enabled}",
        f"Local AS: {bgp.asn}",
        f"Router ID: {bgp.router_id}",
        f"Peer IPv4: {bgp.peer_ipv4}",
        f"Peer AS: {bgp.peer_asn}",
    ]
    if bgp.peer_ipv6:
        lines.append(f"Peer IPv6: {bgp.peer_ipv6}")

    return "\n".join(lines)


# =============================================================================
# Tool Execution - Write Operations
# =============================================================================

# Import validation and dataclasses when needed
def _get_config_classes():
    """Lazy import of config classes."""
    sys.path.insert(0, '/usr/local/bin')
    try:
        from configure_router import (
            SubInterface, LoopbackInterface, NATMapping, ACLBypassPair,
            VLANPassthrough, validate_ipv4_cidr, validate_ipv6_cidr, parse_cidr
        )
        return {
            'SubInterface': SubInterface,
            'LoopbackInterface': LoopbackInterface,
            'NATMapping': NATMapping,
            'ACLBypassPair': ACLBypassPair,
            'VLANPassthrough': VLANPassthrough,
            'validate_ipv4_cidr': validate_ipv4_cidr,
            'validate_ipv6_cidr': validate_ipv6_cidr,
            'parse_cidr': parse_cidr,
        }
    except ImportError:
        return None


def _get_parent_interface(config, interface: str):
    """Get the parent interface object for subinterface operations."""
    if interface == "external":
        return config.external, "external"

    for iface in config.internal:
        if iface.vpp_name == interface:
            return iface, iface.vpp_name

    return None, None


def tool_add_subinterface(config, ctx, interface: str, vlan_id: int,
                          ipv4_cidr: str = None, ipv6_cidr: str = None,
                          create_lcp: bool = True) -> str:
    """Add a sub-interface."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    parent, parent_name = _get_parent_interface(config, interface)
    if not parent:
        available = ["external"] + [i.vpp_name for i in config.internal]
        return f"Interface '{interface}' not found. Available: {', '.join(available)}"

    # Validate VLAN ID
    if vlan_id < 1 or vlan_id > 4094:
        return "Error: VLAN ID must be between 1 and 4094"

    # Check for duplicate
    if any(s.vlan_id == vlan_id for s in parent.subinterfaces):
        return f"Error: Sub-interface .{vlan_id} already exists on {parent_name}"

    # Validate and parse IPs
    ipv4, ipv4_prefix = None, None
    ipv6, ipv6_prefix = None, None

    if ipv4_cidr:
        if not classes['validate_ipv4_cidr'](ipv4_cidr):
            return f"Error: Invalid IPv4 CIDR: {ipv4_cidr}"
        ipv4, ipv4_prefix = classes['parse_cidr'](ipv4_cidr)

    if ipv6_cidr:
        if not classes['validate_ipv6_cidr'](ipv6_cidr):
            return f"Error: Invalid IPv6 CIDR: {ipv6_cidr}"
        ipv6, ipv6_prefix = classes['parse_cidr'](ipv6_cidr)

    if not ipv4 and not ipv6:
        return "Error: At least one of ipv4_cidr or ipv6_cidr is required"

    # Create and add sub-interface
    subif = classes['SubInterface'](
        vlan_id=vlan_id,
        ipv4=ipv4,
        ipv4_prefix=ipv4_prefix,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        create_lcp=create_lcp
    )
    parent.subinterfaces.append(subif)
    ctx.dirty = True

    ips = []
    if ipv4:
        ips.append(f"{ipv4}/{ipv4_prefix}")
    if ipv6:
        ips.append(f"{ipv6}/{ipv6_prefix}")

    return f"Added {parent_name}.{vlan_id} with {', '.join(ips)}"


def tool_delete_subinterface(config, ctx, interface: str, vlan_id: int) -> str:
    """Delete a sub-interface."""
    parent, parent_name = _get_parent_interface(config, interface)
    if not parent:
        available = ["external"] + [i.vpp_name for i in config.internal]
        return f"Interface '{interface}' not found. Available: {', '.join(available)}"

    sub = next((s for s in parent.subinterfaces if s.vlan_id == vlan_id), None)
    if not sub:
        return f"Sub-interface .{vlan_id} not found on {parent_name}"

    parent.subinterfaces.remove(sub)
    ctx.dirty = True
    return f"Deleted {parent_name}.{vlan_id}"


def tool_add_loopback(config, ctx, name: str, ipv4_cidr: str = None,
                      ipv6_cidr: str = None, create_lcp: bool = True) -> str:
    """Add a loopback interface."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    # Find next available instance
    used_instances = {lo.instance for lo in config.loopbacks}
    instance = 0
    while instance in used_instances:
        instance += 1

    # Validate and parse IPs
    ipv4, ipv4_prefix = None, None
    ipv6, ipv6_prefix = None, None

    if ipv4_cidr:
        if not classes['validate_ipv4_cidr'](ipv4_cidr):
            return f"Error: Invalid IPv4 CIDR: {ipv4_cidr}"
        ipv4, ipv4_prefix = classes['parse_cidr'](ipv4_cidr)

    if ipv6_cidr:
        if not classes['validate_ipv6_cidr'](ipv6_cidr):
            return f"Error: Invalid IPv6 CIDR: {ipv6_cidr}"
        ipv6, ipv6_prefix = classes['parse_cidr'](ipv6_cidr)

    if not ipv4 and not ipv6:
        return "Error: At least one of ipv4_cidr or ipv6_cidr is required"

    # Create and add loopback
    loopback = classes['LoopbackInterface'](
        instance=instance,
        name=name,
        ipv4=ipv4,
        ipv4_prefix=ipv4_prefix,
        ipv6=ipv6,
        ipv6_prefix=ipv6_prefix,
        create_lcp=create_lcp
    )
    config.loopbacks.append(loopback)
    ctx.dirty = True

    ips = []
    if ipv4:
        ips.append(f"{ipv4}/{ipv4_prefix}")
    if ipv6:
        ips.append(f"{ipv6}/{ipv6_prefix}")

    return f"Added loop{instance} ({name}) with {', '.join(ips)}"


def tool_delete_loopback(config, ctx, instance: int) -> str:
    """Delete a loopback interface."""
    lo = next((l for l in config.loopbacks if l.instance == instance), None)
    if not lo:
        return f"Loopback loop{instance} not found"

    config.loopbacks.remove(lo)
    ctx.dirty = True
    return f"Deleted loop{instance} ({lo.name})"


def tool_add_nat_mapping(config, ctx, source_network: str, nat_pool: str) -> str:
    """Add a NAT mapping."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    if not classes['validate_ipv4_cidr'](source_network):
        return f"Error: Invalid source network: {source_network}"
    if not classes['validate_ipv4_cidr'](nat_pool):
        return f"Error: Invalid NAT pool: {nat_pool}"

    # Check for duplicate
    if any(m.source_network == source_network for m in config.nat.mappings):
        return f"Error: Mapping for {source_network} already exists"

    config.nat.mappings.append(classes['NATMapping'](
        source_network=source_network,
        nat_pool=nat_pool
    ))
    ctx.dirty = True
    return f"Added NAT mapping: {source_network} -> {nat_pool}"


def tool_delete_nat_mapping(config, ctx, source_network: str) -> str:
    """Delete a NAT mapping."""
    mapping = next((m for m in config.nat.mappings if m.source_network == source_network), None)
    if not mapping:
        return f"NAT mapping for {source_network} not found"

    config.nat.mappings.remove(mapping)
    ctx.dirty = True
    return f"Deleted NAT mapping for {source_network}"


def tool_add_nat_bypass(config, ctx, source: str, destination: str) -> str:
    """Add a NAT bypass rule."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    if not classes['validate_ipv4_cidr'](source):
        return f"Error: Invalid source network: {source}"
    if not classes['validate_ipv4_cidr'](destination):
        return f"Error: Invalid destination network: {destination}"

    # Check for duplicate
    if any(b.source == source and b.destination == destination for b in config.nat.bypass_pairs):
        return f"Error: Bypass rule {source} -> {destination} already exists"

    config.nat.bypass_pairs.append(classes['ACLBypassPair'](
        source=source,
        destination=destination
    ))
    ctx.dirty = True
    return f"Added NAT bypass: {source} -> {destination}"


def tool_delete_nat_bypass(config, ctx, source: str, destination: str) -> str:
    """Delete a NAT bypass rule."""
    bypass = next((b for b in config.nat.bypass_pairs
                   if b.source == source and b.destination == destination), None)
    if not bypass:
        return f"NAT bypass rule {source} -> {destination} not found"

    config.nat.bypass_pairs.remove(bypass)
    ctx.dirty = True
    return f"Deleted NAT bypass: {source} -> {destination}"


def tool_set_nat_prefix(config, ctx, prefix: str) -> str:
    """Set NAT pool prefix."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    if not classes['validate_ipv4_cidr'](prefix):
        return f"Error: Invalid prefix: {prefix}"

    old_prefix = config.nat.bgp_prefix
    config.nat.bgp_prefix = prefix
    ctx.dirty = True

    if old_prefix:
        return f"Changed NAT prefix from {old_prefix} to {prefix}"
    return f"Set NAT prefix to {prefix}"


def tool_add_vlan_passthrough(config, ctx, vlan_id: int, internal_interface: str,
                               vlan_type: str = "dot1q") -> str:
    """Add a VLAN passthrough rule."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    # Validate VLAN ID
    if vlan_id < 1 or vlan_id > 4094:
        return "Error: VLAN ID must be between 1 and 4094"

    # Check internal interface exists
    if not any(i.vpp_name == internal_interface for i in config.internal):
        available = [i.vpp_name for i in config.internal]
        return f"Internal interface '{internal_interface}' not found. Available: {', '.join(available)}"

    # Check for duplicate
    if any(v.vlan_id == vlan_id for v in config.vlan_passthrough):
        return f"Error: VLAN passthrough {vlan_id} already exists"

    config.vlan_passthrough.append(classes['VLANPassthrough'](
        vlan_id=vlan_id,
        internal_interface=internal_interface,
        vlan_type=vlan_type
    ))
    ctx.dirty = True
    return f"Added VLAN passthrough: {vlan_id} ({vlan_type}) <-> {internal_interface}"


def tool_delete_vlan_passthrough(config, ctx, vlan_id: int) -> str:
    """Delete a VLAN passthrough rule."""
    vlan = next((v for v in config.vlan_passthrough if v.vlan_id == vlan_id), None)
    if not vlan:
        return f"VLAN passthrough {vlan_id} not found"

    config.vlan_passthrough.remove(vlan)
    ctx.dirty = True
    return f"Deleted VLAN passthrough {vlan_id}"


def tool_enable_bgp(config, ctx, asn: int, router_id: str, peer_ipv4: str,
                    peer_asn: int, peer_ipv6: str = None) -> str:
    """Enable BGP."""
    classes = _get_config_classes()
    if not classes:
        return "Error: Configuration module not available"

    # Simple validation (could be more thorough)
    try:
        import ipaddress
        ipaddress.IPv4Address(router_id)
        ipaddress.IPv4Address(peer_ipv4)
        if peer_ipv6:
            ipaddress.IPv6Address(peer_ipv6)
    except Exception as e:
        return f"Error: Invalid IP address: {e}"

    config.bgp.enabled = True
    config.bgp.asn = asn
    config.bgp.router_id = router_id
    config.bgp.peer_ipv4 = peer_ipv4
    config.bgp.peer_asn = peer_asn
    config.bgp.peer_ipv6 = peer_ipv6
    ctx.dirty = True

    return f"Enabled BGP: AS {asn} peering with {peer_ipv4} (AS {peer_asn})"


def tool_disable_bgp(config, ctx) -> str:
    """Disable BGP."""
    if not config.bgp.enabled:
        return "BGP is already disabled"

    config.bgp.enabled = False
    ctx.dirty = True
    return "Disabled BGP"


# =============================================================================
# Tool Dispatcher
# =============================================================================

def execute_tool(name: str, args: dict, config, ctx) -> str:
    """Execute a tool and return result string."""
    tool_log(name, args)

    try:
        # Read tools
        if name == "get_config_summary":
            return tool_get_config_summary(config)
        if name == "get_interfaces":
            return tool_get_interfaces(config)
        if name == "get_interface_detail":
            return tool_get_interface_detail(config, args.get("interface", ""))
        if name == "get_loopbacks":
            return tool_get_loopbacks(config)
        if name == "get_bvi_domains":
            return tool_get_bvi_domains(config)
        if name == "get_vlan_passthrough":
            return tool_get_vlan_passthrough(config)
        if name == "get_nat_config":
            return tool_get_nat_config(config)
        if name == "get_bgp_config":
            return tool_get_bgp_config(config)

        # Write tools
        if name == "add_subinterface":
            return tool_add_subinterface(
                config, ctx,
                interface=args.get("interface", ""),
                vlan_id=args.get("vlan_id", 0),
                ipv4_cidr=args.get("ipv4_cidr"),
                ipv6_cidr=args.get("ipv6_cidr"),
                create_lcp=args.get("create_lcp", True)
            )
        if name == "delete_subinterface":
            return tool_delete_subinterface(
                config, ctx,
                interface=args.get("interface", ""),
                vlan_id=args.get("vlan_id", 0)
            )
        if name == "add_loopback":
            return tool_add_loopback(
                config, ctx,
                name=args.get("name", ""),
                ipv4_cidr=args.get("ipv4_cidr"),
                ipv6_cidr=args.get("ipv6_cidr"),
                create_lcp=args.get("create_lcp", True)
            )
        if name == "delete_loopback":
            return tool_delete_loopback(config, ctx, instance=args.get("instance", 0))
        if name == "add_nat_mapping":
            return tool_add_nat_mapping(
                config, ctx,
                source_network=args.get("source_network", ""),
                nat_pool=args.get("nat_pool", "")
            )
        if name == "delete_nat_mapping":
            return tool_delete_nat_mapping(config, ctx, source_network=args.get("source_network", ""))
        if name == "add_nat_bypass":
            return tool_add_nat_bypass(
                config, ctx,
                source=args.get("source", ""),
                destination=args.get("destination", "")
            )
        if name == "delete_nat_bypass":
            return tool_delete_nat_bypass(
                config, ctx,
                source=args.get("source", ""),
                destination=args.get("destination", "")
            )
        if name == "set_nat_prefix":
            return tool_set_nat_prefix(config, ctx, prefix=args.get("prefix", ""))
        if name == "add_vlan_passthrough":
            return tool_add_vlan_passthrough(
                config, ctx,
                vlan_id=args.get("vlan_id", 0),
                internal_interface=args.get("internal_interface", ""),
                vlan_type=args.get("vlan_type", "dot1q")
            )
        if name == "delete_vlan_passthrough":
            return tool_delete_vlan_passthrough(config, ctx, vlan_id=args.get("vlan_id", 0))
        if name == "enable_bgp":
            return tool_enable_bgp(
                config, ctx,
                asn=args.get("asn", 0),
                router_id=args.get("router_id", ""),
                peer_ipv4=args.get("peer_ipv4", ""),
                peer_asn=args.get("peer_asn", 0),
                peer_ipv6=args.get("peer_ipv6")
            )
        if name == "disable_bgp":
            return tool_disable_bgp(config, ctx)

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error executing {name}: {e}"


# =============================================================================
# System Prompt
# =============================================================================

def build_system_prompt(config) -> str:
    """Build the system prompt with current context."""
    interfaces = ["management", "external"]
    if config:
        interfaces.extend(i.vpp_name for i in config.internal)

    return f"""You are an AI assistant for configuring an IMP router. You have access to tools that can read and modify the router configuration.

When the user asks for changes:
1. Use read tools to understand current state if needed
2. Use write tools to make the requested changes
3. Confirm what you did in a concise way

Important notes:
- Changes are staged until the user runs 'apply' in the main REPL. You cannot apply changes directly.
- At least one IP address (IPv4 or IPv6) is required when adding sub-interfaces or loopbacks.
- VLAN IDs must be between 1 and 4094.
- When adding sub-interfaces, specify the parent interface name.

Available interfaces: {', '.join(interfaces)}

Be helpful and concise. If something fails, explain why and suggest alternatives."""


# =============================================================================
# Agent Loop
# =============================================================================

def run_agent(ctx, host: str = None, model: str = None) -> None:
    """
    Run the agent loop.

    Args:
        ctx: MenuContext from imp_repl with config and dirty flag
        host: Ollama host override
        model: Ollama model override
    """
    host = get_ollama_host(host)
    model = get_ollama_model(model)

    client = OllamaClient(host, model)

    # Check connection
    print()
    if not client.check_connection():
        error(f"Cannot connect to Ollama at {host}")
        print(f"  Make sure Ollama is running: ollama serve")
        print(f"  Or set OLLAMA_HOST environment variable")
        return

    if not client.check_model():
        warn(f"Model '{model}' may not be available")
        print(f"  Run: ollama pull {model}")
        print(f"  Or set OLLAMA_MODEL environment variable")
        print()

    log(f"Connected to Ollama ({model})")
    print("Type your request, or 'exit' to return")
    print()

    # Build tools
    tools = build_tools()

    # Conversation history
    messages = [
        {"role": "system", "content": build_system_prompt(ctx.config)}
    ]

    while True:
        try:
            user_input = input(f"{Colors.CYAN}agent>{Colors.NC} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            break

        # Add user message
        messages.append({"role": "user", "content": user_input})

        try:
            # Call Ollama
            print(f"{Colors.DIM}Thinking...{Colors.NC}")
            response = client.chat(messages, tools)

            message = response.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])

            # Process tool calls
            if tool_calls:
                # Append assistant message with tool calls to history
                messages.append({
                    "role": "assistant",
                    "tool_calls": tool_calls
                })

                # Execute each tool and collect results
                for tool_call in tool_calls:
                    func = tool_call.get("function", {})
                    tool_name = func.get("name", "")
                    tool_args = func.get("arguments", {})

                    result = execute_tool(tool_name, tool_args, ctx.config, ctx)
                    print(f"  {Colors.DIM}→ {result}{Colors.NC}")

                    # Add tool result to messages
                    messages.append({
                        "role": "tool",
                        "content": result
                    })

                # Get final response after tool execution
                print(f"{Colors.DIM}Thinking...{Colors.NC}")
                response = client.chat(messages, tools)
                message = response.get("message", {})
                content = message.get("content", "")

                # Check for more tool calls
                more_tool_calls = message.get("tool_calls", [])
                while more_tool_calls:
                    messages.append({
                        "role": "assistant",
                        "tool_calls": more_tool_calls
                    })

                    for tool_call in more_tool_calls:
                        func = tool_call.get("function", {})
                        tool_name = func.get("name", "")
                        tool_args = func.get("arguments", {})

                        result = execute_tool(tool_name, tool_args, ctx.config, ctx)
                        print(f"  {Colors.DIM}→ {result}{Colors.NC}")

                        messages.append({
                            "role": "tool",
                            "content": result
                        })

                    print(f"{Colors.DIM}Thinking...{Colors.NC}")
                    response = client.chat(messages, tools)
                    message = response.get("message", {})
                    content = message.get("content", "")
                    more_tool_calls = message.get("tool_calls", [])

            # Display final response
            if content:
                print()
                print(content)
                print()

            # Add assistant response to history
            messages.append({"role": "assistant", "content": content})

        except requests.exceptions.Timeout:
            error("Request timed out. The model may be slow or unresponsive.")
            messages.pop()  # Remove failed user message
        except requests.exceptions.RequestException as e:
            error(f"Request failed: {e}")
            messages.pop()  # Remove failed user message
        except Exception as e:
            error(f"Error: {e}")
            messages.pop()  # Remove failed user message

    print("Returning to IMP REPL...")


if __name__ == "__main__":
    # For testing
    print("This module should be called from imp_repl.py")
    print("Use: imp agent")
