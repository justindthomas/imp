"""
Tool definitions for IMP agent.

This module contains the JSON schema definitions for all agent tools
that are passed to the Ollama API.
"""


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
                "description": "List all network interfaces including management and dataplane interfaces with their IP addresses and sub-interfaces",
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
                            "description": "Interface name: 'management' or a dataplane interface name (e.g., 'wan', 'lan')"
                        }
                    },
                    "required": ["interface"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_routes",
                "description": "List all configured static routes including default routes",
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
                "name": "list_modules",
                "description": "List available IMP VPP modules and their status. Shows which modules are installed, enabled, and what commands they provide.",
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
                "name": "get_module_config",
                "description": "Get configuration for a specific IMP VPP module (e.g., 'nat', 'nat64'). Shows current settings from router.json.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "module_name": {
                            "type": "string",
                            "description": "Module name (e.g., 'nat', 'nat64')"
                        }
                    },
                    "required": ["module_name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "execute_module_command",
                "description": "Execute a command defined by an IMP VPP module. Use list_modules to see available commands for each module. Commands can add/remove array items, set values, or list configurations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "module_name": {
                            "type": "string",
                            "description": "Module name (e.g., 'nat', 'nat64')"
                        },
                        "command_path": {
                            "type": "string",
                            "description": "Command path as defined in module YAML (e.g., 'mappings/add', 'bypass/delete', 'set-prefix')"
                        },
                        "params": {
                            "type": "object",
                            "description": "Parameters for the command as key-value pairs (e.g., {\"source_network\": \"192.168.1.0/24\", \"nat_pool\": \"23.177.24.96/30\"})"
                        }
                    },
                    "required": ["module_name", "command_path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_bgp_config",
                "description": "Get BGP configuration including ASN, router ID, and all configured peers. Check this before adding or removing peers.",
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
        # Live state lookup tools
        {
            "type": "function",
            "function": {
                "name": "show_ip_route",
                "description": "Show IPv4 routing table from FRR. Optionally filter by prefix to show routes within that prefix (e.g., '10.0.0.0/8' shows all routes within 10.0.0.0/8).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "Optional prefix filter (e.g., '192.168.0.0/16' to show routes within that range)"
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "show_ipv6_route",
                "description": "Show IPv6 routing table from FRR. Optionally filter by prefix to show routes within that prefix.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "Optional prefix filter (e.g., '2001:db8::/32' to show routes within that range)"
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "show_ip_fib",
                "description": "Show IPv4 forwarding table (FIB) from VPP dataplane. Without a prefix, shows all FIB entries. With a prefix, filters to show only FIB entries within that prefix range (same behavior as FRR's 'longer-prefixes').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "Optional prefix filter (e.g., '10.0.0.0/8' to show all entries within that range)"
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "show_ipv6_fib",
                "description": "Show IPv6 forwarding table (FIB) from VPP dataplane. Without a prefix, shows all FIB entries. With a prefix, filters to show only FIB entries within that prefix range (same behavior as FRR's 'longer-prefixes').",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prefix": {
                            "type": "string",
                            "description": "Optional prefix filter (e.g., '2001:db8::/32' to show all entries within that range)"
                        }
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "show_interfaces_live",
                "description": "Show live interface state and counters from VPP dataplane (not staged config)",
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
                "name": "show_neighbors",
                "description": "Show ARP (IPv4) and NDP (IPv6) neighbor tables from VPP dataplane",
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
                "description": "Add a VLAN sub-interface to a dataplane interface. At least one of ipv4_cidr or ipv6_cidr must be provided.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Parent interface name (e.g., 'wan', 'lan')"
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
                "description": "Delete a VLAN sub-interface from a dataplane interface",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Parent interface name (e.g., 'wan', 'lan')"
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
                "description": "Delete a loopback interface",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Loopback name (e.g., 'loop0') or instance number (e.g., '0')"
                        }
                    },
                    "required": ["name"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_vlan_passthrough",
                "description": "Add a VLAN passthrough rule to create an L2 xconnect between two interfaces",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "vlan_id": {
                            "type": "integer",
                            "description": "VLAN ID to pass through (1-4094)"
                        },
                        "from_interface": {
                            "type": "string",
                            "description": "Source interface name (e.g., 'wan')"
                        },
                        "to_interface": {
                            "type": "string",
                            "description": "Destination interface name (e.g., 'lan')"
                        },
                        "vlan_type": {
                            "type": "string",
                            "description": "VLAN type: 'dot1q' (default) or 'dot1ad' for QinQ",
                            "enum": ["dot1q", "dot1ad"]
                        }
                    },
                    "required": ["vlan_id", "from_interface", "to_interface"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_route",
                "description": "Add a static route",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "destination": {
                            "type": "string",
                            "description": "Destination network in CIDR notation (e.g., '0.0.0.0/0' for default, '10.0.0.0/8')"
                        },
                        "via": {
                            "type": "string",
                            "description": "Next-hop IP address"
                        },
                        "interface": {
                            "type": "string",
                            "description": "Optional: force route via specific interface"
                        }
                    },
                    "required": ["destination", "via"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_route",
                "description": "Delete a static route",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "destination": {
                            "type": "string",
                            "description": "Destination network in CIDR notation (e.g., '0.0.0.0/0' for default)"
                        }
                    },
                    "required": ["destination"]
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
                "name": "configure_bgp",
                "description": "Enable and configure BGP with local ASN and router-id. Does not modify existing peers. Use add_bgp_peer to add peers.",
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
                        }
                    },
                    "required": ["asn", "router_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "add_bgp_peer",
                "description": "Add a BGP peer to the configuration. Supports both IPv4 and IPv6 peers. BGP must be enabled first.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Friendly name for this peer (e.g., 'upstream', 'ix-peer-1')"
                        },
                        "peer_ip": {
                            "type": "string",
                            "description": "Peer IP address (IPv4 or IPv6)"
                        },
                        "peer_asn": {
                            "type": "integer",
                            "description": "Peer AS number"
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional description for FRR config (defaults to name)"
                        }
                    },
                    "required": ["name", "peer_ip", "peer_asn"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "remove_bgp_peer",
                "description": "Remove a BGP peer by IP address",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "peer_ip": {
                            "type": "string",
                            "description": "IP address of peer to remove"
                        }
                    },
                    "required": ["peer_ip"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "disable_bgp",
                "description": "Disable BGP and remove all peers",
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
                "description": "Set OSPF area and options for an interface. Use interface names like 'wan', 'lan', 'loop0', 'bvi1'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Interface name (e.g., 'wan', 'lan', 'loop0', 'bvi1')"
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
                "description": "Set OSPFv3 area and options for an interface. Use interface names like 'wan', 'lan', 'loop0', 'bvi1'.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interface": {
                            "type": "string",
                            "description": "Interface name (e.g., 'wan', 'lan', 'loop0', 'bvi1')"
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
                            "description": "Interface name (e.g., 'wan', 'lan', 'loop0', 'bvi1')"
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
                            "description": "Interface name (e.g., 'wan', 'lan', 'loop0', 'bvi1')"
                        }
                    },
                    "required": ["interface"]
                }
            }
        },
        # Packet capture tools
        {
            "type": "function",
            "function": {
                "name": "start_capture",
                "description": "Start a packet capture on a VPP instance. Captures are written to /tmp as .pcap files.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instance": {
                            "type": "string",
                            "description": "VPP instance: 'core' for main routing, or module name (e.g., 'nat', 'nat64')"
                        },
                        "interface": {
                            "type": "string",
                            "description": "Interface name to capture on, or 'any' for all interfaces (default: 'any')"
                        },
                        "direction": {
                            "type": "string",
                            "description": "Capture direction: 'rx', 'tx', 'drop', 'rx tx', or 'rx tx drop' (default: 'rx tx')"
                        },
                        "max_packets": {
                            "type": "integer",
                            "description": "Maximum packets to capture. 0 for unlimited (default: 10000)"
                        },
                        "filename": {
                            "type": "string",
                            "description": "Output filename (without .pcap extension). Auto-generated if not specified."
                        }
                    },
                    "required": ["instance"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "stop_capture",
                "description": "Stop an active packet capture and write the pcap file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instance": {
                            "type": "string",
                            "description": "VPP instance: 'core' for main routing, or module name (e.g., 'nat', 'nat64')"
                        }
                    },
                    "required": ["instance"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_capture_status",
                "description": "Show active captures on all running VPP instances (core and modules)",
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
                "name": "list_capture_files",
                "description": "List all pcap capture files in /tmp with size and modification time",
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
                "name": "analyze_capture",
                "description": "Analyze a pcap file using tshark. Returns file info, protocol hierarchy, and top conversations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Pcap filename (in /tmp) or full path. .pcap extension is optional."
                        }
                    },
                    "required": ["filename"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "delete_capture",
                "description": "Delete a pcap capture file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Pcap filename to delete (in /tmp) or full path"
                        }
                    },
                    "required": ["filename"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "tshark_query",
                "description": "Run a tshark query on a pcap file for detailed packet analysis. Use this to inspect specific protocols, filter packets, or extract field values.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filename": {
                            "type": "string",
                            "description": "Pcap filename (in /tmp) or full path"
                        },
                        "display_filter": {
                            "type": "string",
                            "description": "Wireshark display filter (e.g., 'dns', 'tcp.port==80', 'ip.addr==10.0.0.1')"
                        },
                        "fields": {
                            "type": "string",
                            "description": "Comma-separated fields to extract (e.g., 'dns.qry.name,dns.a'). If omitted, shows packet summary."
                        },
                        "max_packets": {
                            "type": "integer",
                            "description": "Maximum packets to return (default: 50)"
                        }
                    },
                    "required": ["filename"]
                }
            }
        },
        # VPP graph trace tools
        {
            "type": "function",
            "function": {
                "name": "start_trace",
                "description": "Start VPP graph tracing to see how packets flow through VPP's processing nodes. Useful for debugging packet drops or routing issues.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instance": {
                            "type": "string",
                            "description": "VPP instance: 'core' for main routing, or module name (e.g., 'nat', 'nat64')"
                        },
                        "input_node": {
                            "type": "string",
                            "description": "Input node to trace from. Common: 'dpdk-input' (physical NICs), 'memif-input' (inter-VPP), 'host-interface-input' (veth/tap)"
                        },
                        "count": {
                            "type": "integer",
                            "description": "Number of packets to trace (default: 50)"
                        }
                    },
                    "required": ["instance", "input_node"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "show_trace",
                "description": "Show VPP graph trace output - displays how traced packets flowed through VPP's processing nodes",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instance": {
                            "type": "string",
                            "description": "VPP instance: 'core' for main routing, or module name (e.g., 'nat', 'nat64')"
                        },
                        "max_packets": {
                            "type": "integer",
                            "description": "Maximum packets to show (default: 10)"
                        }
                    },
                    "required": ["instance"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_trace_status",
                "description": "Show trace status on all running VPP instances (how many packets have been traced)",
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
                "name": "clear_trace",
                "description": "Clear the trace buffer on a VPP instance",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instance": {
                            "type": "string",
                            "description": "VPP instance: 'core' for main routing, or module name (e.g., 'nat', 'nat64')"
                        }
                    },
                    "required": ["instance"]
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
