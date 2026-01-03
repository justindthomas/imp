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

# Optional: rich for markdown rendering
try:
    from rich.console import Console, Group
    from rich.markdown import Markdown
    from rich.table import Table
    from rich.text import Text
    import re
    RICH_AVAILABLE = True
    console = Console()

    def render_cell(cell: str) -> Text:
        """Render markdown formatting in a table cell."""
        text = Text()
        i = 0
        while i < len(cell):
            # Bold: **text**
            if cell[i:i+2] == '**':
                end = cell.find('**', i + 2)
                if end != -1:
                    text.append(cell[i+2:end], style="bold")
                    i = end + 2
                    continue
            # Code: `text`
            if cell[i] == '`':
                end = cell.find('`', i + 1)
                if end != -1:
                    text.append(cell[i+1:end], style="cyan")
                    i = end + 1
                    continue
            # Regular character
            text.append(cell[i])
            i += 1
        return text

    def fix_markdown_tables(content: str) -> str:
        """Fix markdown tables that have rows collapsed onto single lines."""
        # Fix: | ... | |--- (header followed by separator on same line)
        content = re.sub(r'\|\s*\|(\s*-+\s*\|)', r'|\n|\1', content)
        # Fix: | ... | | ... | (data rows concatenated)
        content = re.sub(r'\|\s*\|\s*(?=[^-\s\n])', '|\n| ', content)
        return content

    def parse_markdown_table(table_text: str) -> tuple[list[str], list[list[str]]]:
        """Parse a markdown table into headers and rows."""
        lines = [l.strip() for l in table_text.strip().split('\n') if l.strip()]
        if len(lines) < 2:
            return [], []

        def parse_row(line: str) -> list[str]:
            # Remove leading/trailing pipes and split
            line = line.strip()
            if line.startswith('|'):
                line = line[1:]
            if line.endswith('|'):
                line = line[:-1]
            return [cell.strip() for cell in line.split('|')]

        headers = parse_row(lines[0])
        rows = []

        for line in lines[1:]:
            # Skip separator line
            if re.match(r'^[\|\s\-:]+$', line):
                continue
            rows.append(parse_row(line))

        return headers, rows

    def render_content_with_tables(content: str) -> Group:
        """Render content, converting markdown tables to rich Tables."""
        content = fix_markdown_tables(content)

        # Pattern to match markdown tables (header + separator + rows)
        table_pattern = re.compile(
            r'(\|[^\n]+\|\s*\n\|[\s\-:|]+\|\s*\n(?:\|[^\n]+\|\s*\n?)+)',
            re.MULTILINE
        )

        parts = []
        last_end = 0

        for match in table_pattern.finditer(content):
            # Add text before the table
            before = content[last_end:match.start()].strip()
            if before:
                parts.append(Markdown(before))

            # Parse and render the table
            headers, rows = parse_markdown_table(match.group(1))
            if headers:
                table = Table(show_header=True, header_style="bold")
                for header in headers:
                    table.add_column(header)
                for row in rows:
                    # Pad row if needed
                    while len(row) < len(headers):
                        row.append("")
                    # Render markdown in cells
                    rendered = [render_cell(cell) for cell in row[:len(headers)]]
                    table.add_row(*rendered)
                parts.append(table)

            last_end = match.end()

        # Add remaining text after last table
        after = content[last_end:].strip()
        if after:
            parts.append(Markdown(after))

        return Group(*parts) if parts else Markdown(content)

except ImportError:
    RICH_AVAILABLE = False
    console = None
    fix_markdown_tables = None
    render_content_with_tables = None


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
        {
            "type": "function",
            "function": {
                "name": "get_ospf_config",
                "description": "Get OSPF (IPv4) configuration including router ID, default-originate setting, and interface areas",
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
                "name": "get_ospf6_config",
                "description": "Get OSPFv3 (IPv6) configuration including router ID, default-originate setting, and interface areas",
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
        {
            "type": "function",
            "function": {
                "name": "enable_ospf",
                "description": "Enable and configure OSPF (IPv4 routing protocol)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "router_id": {
                            "type": "string",
                            "description": "Router ID (IPv4 address). If not provided, uses BGP router-id if available."
                        },
                        "default_originate": {
                            "type": "boolean",
                            "description": "Inject default route into OSPF (default: false)"
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "disable_ospf",
                "description": "Disable OSPF",
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
                "name": "enable_ospf6",
                "description": "Enable and configure OSPFv3 (IPv6 routing protocol)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "router_id": {
                            "type": "string",
                            "description": "Router ID (IPv4 address). If not provided, uses OSPF or BGP router-id if available."
                        },
                        "default_originate": {
                            "type": "boolean",
                            "description": "Inject default route into OSPFv3 (default: false)"
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "disable_ospf6",
                "description": "Disable OSPFv3",
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
                "name": "set_interface_ospf",
                "description": "Set OSPF area and options for an interface. Use interface names like 'internal0', 'external', 'loop0', 'bvi1'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Interface name (e.g., 'internal0', 'external', 'loop0', 'bvi1')"
                        },
                        "area": {
                            "type": "integer",
                            "description": "OSPF area ID (e.g., 0 for backbone)"
                        },
                        "passive": {
                            "type": "boolean",
                            "description": "Make interface passive (no OSPF hellos sent)"
                        }
                    },
                    "required": ["interface", "area"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "set_interface_ospf6",
                "description": "Set OSPFv3 area and options for an interface. Use interface names like 'internal0', 'external', 'loop0', 'bvi1'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Interface name (e.g., 'internal0', 'external', 'loop0', 'bvi1')"
                        },
                        "area": {
                            "type": "integer",
                            "description": "OSPFv3 area ID (e.g., 0 for backbone)"
                        },
                        "passive": {
                            "type": "boolean",
                            "description": "Make interface passive (no OSPFv3 hellos sent)"
                        }
                    },
                    "required": ["interface", "area"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "clear_interface_ospf",
                "description": "Remove an interface from OSPF (clear OSPF area)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Interface name (e.g., 'internal0', 'external', 'loop0', 'bvi1')"
                        }
                    },
                    "required": ["interface"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "clear_interface_ospf6",
                "description": "Remove an interface from OSPFv3 (clear OSPFv3 area)",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Interface name (e.g., 'internal0', 'external', 'loop0', 'bvi1')"
                        }
                    },
                    "required": ["interface"]
                }
            }
        },
        # Interactive tool
        {
            "type": "function",
            "function": {
                "name": "ask_user",
                "description": "Ask the user a clarifying question when their request is ambiguous or missing required information. Use this to gather details needed to complete a task rather than just explaining what's needed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "A clear, specific question to ask the user"
                        },
                        "context": {
                            "type": "string",
                            "description": "Brief context explaining why you need this information (optional)"
                        }
                    },
                    "required": ["question"]
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
    # Internal interfaces
    for iface in config.internal:
        if iface.ospf_area is not None:
            passive = " (passive)" if iface.ospf_passive else ""
            lines.append(f"  {iface.vpp_name}: area {iface.ospf_area}{passive}")
            has_areas = True
    # External interface
    if config.external and config.external.ospf_area is not None:
        passive = " (passive)" if config.external.ospf_passive else ""
        lines.append(f"  external: area {config.external.ospf_area}{passive}")
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
    # Internal interfaces
    for iface in config.internal:
        if iface.ospf6_area is not None:
            passive = " (passive)" if iface.ospf6_passive else ""
            lines.append(f"  {iface.vpp_name}: area {iface.ospf6_area}{passive}")
            has_areas = True
    # External interface
    if config.external and config.external.ospf6_area is not None:
        passive = " (passive)" if config.external.ospf6_passive else ""
        lines.append(f"  external: area {config.external.ospf6_area}{passive}")
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


def tool_enable_ospf(config, ctx, router_id: str = None, default_originate: bool = False) -> str:
    """Enable OSPF."""
    if config.ospf.enabled:
        return "OSPF is already enabled"

    # Use BGP router-id as fallback if not provided
    if not router_id:
        router_id = config.bgp.router_id if config.bgp.enabled else None

    if not router_id:
        return "Error: router_id is required (no BGP router-id available as fallback)"

    # Validate router_id
    try:
        import ipaddress
        ipaddress.IPv4Address(router_id)
    except Exception as e:
        return f"Error: Invalid router ID: {e}"

    config.ospf.enabled = True
    config.ospf.router_id = router_id
    config.ospf.default_originate = default_originate
    ctx.dirty = True

    return f"Enabled OSPF with router-id {router_id}"


def tool_disable_ospf(config, ctx) -> str:
    """Disable OSPF."""
    if not config.ospf.enabled:
        return "OSPF is already disabled"

    config.ospf.enabled = False
    ctx.dirty = True
    return "Disabled OSPF"


def tool_enable_ospf6(config, ctx, router_id: str = None, default_originate: bool = False) -> str:
    """Enable OSPFv3."""
    if config.ospf6.enabled:
        return "OSPFv3 is already enabled"

    # Use OSPF or BGP router-id as fallback if not provided
    if not router_id:
        router_id = config.ospf.router_id or (config.bgp.router_id if config.bgp.enabled else None)

    if not router_id:
        return "Error: router_id is required (no OSPF/BGP router-id available as fallback)"

    # Validate router_id
    try:
        import ipaddress
        ipaddress.IPv4Address(router_id)
    except Exception as e:
        return f"Error: Invalid router ID: {e}"

    config.ospf6.enabled = True
    config.ospf6.router_id = router_id
    config.ospf6.default_originate = default_originate
    ctx.dirty = True

    return f"Enabled OSPFv3 with router-id {router_id}"


def tool_disable_ospf6(config, ctx) -> str:
    """Disable OSPFv3."""
    if not config.ospf6.enabled:
        return "OSPFv3 is already disabled"

    config.ospf6.enabled = False
    ctx.dirty = True
    return "Disabled OSPFv3"


def _find_interface_for_ospf(config, interface: str):
    """Find an interface by name for OSPF configuration.

    Returns (interface_obj, interface_type) where interface_type is one of:
    'internal', 'external', 'loopback', 'bvi'
    """
    # Check loopbacks: loop0, loop1, etc.
    if interface.startswith("loop"):
        try:
            instance = int(interface[4:])
            for loop in config.loopbacks:
                if loop.instance == instance:
                    return loop, "loopback"
        except ValueError:
            pass

    # Check BVIs: bvi1, bvi2, etc.
    if interface.startswith("bvi"):
        try:
            bridge_id = int(interface[3:])
            for bvi in config.bvi_domains:
                if bvi.bridge_id == bridge_id:
                    return bvi, "bvi"
        except ValueError:
            pass

    # Check internal interfaces: internal0, internal1, etc.
    for iface in config.internal:
        if iface.vpp_name == interface:
            return iface, "internal"

    # Check external interface
    if interface == "external" and config.external:
        return config.external, "external"

    return None, None


def tool_set_interface_ospf(config, ctx, interface: str, area: int, passive: bool = False) -> str:
    """Set OSPF area for an interface."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    iface.ospf_area = area
    iface.ospf_passive = passive
    ctx.dirty = True

    passive_str = " (passive)" if passive else ""
    return f"Set {interface} OSPF area to {area}{passive_str}"


def tool_set_interface_ospf6(config, ctx, interface: str, area: int, passive: bool = False) -> str:
    """Set OSPFv3 area for an interface."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    iface.ospf6_area = area
    iface.ospf6_passive = passive
    ctx.dirty = True

    passive_str = " (passive)" if passive else ""
    return f"Set {interface} OSPFv3 area to {area}{passive_str}"


def tool_clear_interface_ospf(config, ctx, interface: str) -> str:
    """Remove interface from OSPF."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    if iface.ospf_area is None:
        return f"Interface '{interface}' is not in OSPF"

    iface.ospf_area = None
    iface.ospf_passive = False
    ctx.dirty = True

    return f"Removed {interface} from OSPF"


def tool_clear_interface_ospf6(config, ctx, interface: str) -> str:
    """Remove interface from OSPFv3."""
    iface, iface_type = _find_interface_for_ospf(config, interface)
    if iface is None:
        return f"Error: Interface '{interface}' not found"

    if iface.ospf6_area is None:
        return f"Interface '{interface}' is not in OSPFv3"

    iface.ospf6_area = None
    iface.ospf6_passive = False
    ctx.dirty = True

    return f"Removed {interface} from OSPFv3"


def tool_ask_user(question: str, context: str = None) -> str:
    """Ask the user a clarifying question and return their answer."""
    print()
    if context:
        print(f"{Colors.DIM}{context}{Colors.NC}")
    print(f"{Colors.CYAN}Question:{Colors.NC} {question}")
    try:
        answer = input(f"{Colors.CYAN}Answer:{Colors.NC} ").strip()
        if not answer:
            return "(User provided no answer)"
        return f"User's answer: {answer}"
    except (KeyboardInterrupt, EOFError):
        print()
        return "(User cancelled the question)"


# =============================================================================
# Tool Dispatcher
# =============================================================================

def execute_tool(name: str, args: dict, config, ctx) -> str:
    """Execute a tool and return result string."""
    # Don't log ask_user - it has its own display
    if name != "ask_user":
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
        if name == "get_ospf_config":
            return tool_get_ospf_config(config)
        if name == "get_ospf6_config":
            return tool_get_ospf6_config(config)

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
        if name == "enable_ospf":
            return tool_enable_ospf(
                config, ctx,
                router_id=args.get("router_id"),
                default_originate=args.get("default_originate", False)
            )
        if name == "disable_ospf":
            return tool_disable_ospf(config, ctx)
        if name == "enable_ospf6":
            return tool_enable_ospf6(
                config, ctx,
                router_id=args.get("router_id"),
                default_originate=args.get("default_originate", False)
            )
        if name == "disable_ospf6":
            return tool_disable_ospf6(config, ctx)
        if name == "set_interface_ospf":
            return tool_set_interface_ospf(
                config, ctx,
                interface=args.get("interface", ""),
                area=args.get("area", 0),
                passive=args.get("passive", False)
            )
        if name == "set_interface_ospf6":
            return tool_set_interface_ospf6(
                config, ctx,
                interface=args.get("interface", ""),
                area=args.get("area", 0),
                passive=args.get("passive", False)
            )
        if name == "clear_interface_ospf":
            return tool_clear_interface_ospf(config, ctx, interface=args.get("interface", ""))
        if name == "clear_interface_ospf6":
            return tool_clear_interface_ospf6(config, ctx, interface=args.get("interface", ""))

        # Interactive tool - doesn't need config
        if name == "ask_user":
            return tool_ask_user(
                question=args.get("question", ""),
                context=args.get("context")
            )

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
1. If the request is missing required information, use ask_user to gather it - offer sensible options when appropriate
2. Use read tools to understand current state if needed
3. Use write tools to make the requested changes
4. Confirm what you did concisely

Interface types - choose the right one:
- **Loopback**: Virtual interface for service IPs, router-id, or any IP that doesn't need L2 connectivity to a physical port. Use add_loopback. Good default if user just wants "an IP address on the router."
- **Sub-interface**: VLAN on a physical port. Requires parent interface (external/internal0) + VLAN ID. Use add_subinterface. Use when traffic needs to arrive on a specific port with a VLAN tag.
- **BVI**: IP interface on a bridge domain that bridges multiple L2 members. Use when bridging ports together with a gateway IP.

When the user asks for "a VLAN interface" or "BVI" without specifying details, ask what they need:
- If they just need an IP address on the router → suggest loopback
- If they need it connected to a physical port → ask which interface and VLAN ID
- If they need to bridge multiple ports → that's a BVI with members

Important notes:
- Changes are staged until 'apply'. You cannot apply changes directly.
- At least one IP address (IPv4 or IPv6) is required for interfaces.
- VLAN IDs must be 1-4094.

Available physical interfaces: {', '.join(interfaces)}

Be helpful and concise. Use ask_user with clear options when gathering information."""


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
                if RICH_AVAILABLE:
                    console.print(render_content_with_tables(content))
                else:
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
