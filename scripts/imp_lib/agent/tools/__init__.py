"""
imp_lib.agent.tools - Tool implementations for IMP agent

This package contains the tool implementations that the LLM agent
can call to read and modify router configuration.
"""

from imp_lib.common import tool_log

from .definitions import build_tools

from .read import (
    get_module_config_dict,
    find_module,
    tool_get_config_summary,
    tool_get_interfaces,
    tool_get_interface_detail,
    tool_get_routes,
    tool_get_loopbacks,
    tool_get_bvi_domains,
    tool_get_vlan_passthrough,
    tool_list_modules,
    tool_get_module_config,
    tool_execute_module_command,
    tool_get_bgp_config,
    tool_get_ospf_config,
    tool_get_ospf6_config,
)

from .write import (
    tool_add_subinterface,
    tool_delete_subinterface,
    tool_add_loopback,
    tool_delete_loopback,
    tool_add_vlan_passthrough,
    tool_delete_vlan_passthrough,
    tool_add_route,
    tool_delete_route,
    tool_configure_bgp,
    tool_add_bgp_peer,
    tool_remove_bgp_peer,
    tool_disable_bgp,
    tool_add_bgp_prefix,
    tool_remove_bgp_prefix,
    tool_enable_ospf,
    tool_disable_ospf,
    tool_enable_ospf6,
    tool_disable_ospf6,
    tool_set_interface_ospf,
    tool_set_interface_ospf6,
    tool_clear_interface_ospf,
    tool_clear_interface_ospf6,
)

from .live import (
    tool_show_ip_route,
    tool_show_ipv6_route,
    tool_show_ip_fib,
    tool_show_ipv6_fib,
    tool_show_interfaces_live,
    tool_show_neighbors,
)

from .capture import (
    tool_start_capture,
    tool_stop_capture,
    tool_get_capture_status,
    tool_list_capture_files,
    tool_analyze_capture,
    tool_delete_capture,
    tool_tshark_query,
)

from .trace import (
    tool_start_trace,
    tool_show_trace,
    tool_get_trace_status,
    tool_clear_trace,
)

from .interactive import tool_ask_user


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
        if name == "get_routes":
            return tool_get_routes(config)
        if name == "get_loopbacks":
            return tool_get_loopbacks(config)
        if name == "get_bvi_domains":
            return tool_get_bvi_domains(config)
        if name == "get_vlan_passthrough":
            return tool_get_vlan_passthrough(config)
        if name == "list_modules":
            return tool_list_modules(config)
        if name == "get_module_config":
            return tool_get_module_config(config, module_name=args.get("module_name", ""))
        if name == "execute_module_command":
            return tool_execute_module_command(
                config, ctx,
                module_name=args.get("module_name", ""),
                command_path=args.get("command_path", ""),
                params=args.get("params", {})
            )
        if name == "get_bgp_config":
            return tool_get_bgp_config(config)
        if name == "get_ospf_config":
            return tool_get_ospf_config(config)
        if name == "get_ospf6_config":
            return tool_get_ospf6_config(config)

        # Live state lookup tools
        if name == "show_ip_route":
            return tool_show_ip_route(prefix=args.get("prefix"))
        if name == "show_ipv6_route":
            return tool_show_ipv6_route(prefix=args.get("prefix"))
        if name == "show_ip_fib":
            return tool_show_ip_fib(prefix=args.get("prefix"))
        if name == "show_ipv6_fib":
            return tool_show_ipv6_fib(prefix=args.get("prefix"))
        if name == "show_interfaces_live":
            return tool_show_interfaces_live()
        if name == "show_neighbors":
            return tool_show_neighbors()

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
            return tool_delete_loopback(config, ctx, name=args.get("name", ""))
        if name == "add_vlan_passthrough":
            return tool_add_vlan_passthrough(
                config, ctx,
                vlan_id=args.get("vlan_id", 0),
                from_interface=args.get("from_interface", ""),
                to_interface=args.get("to_interface", ""),
                vlan_type=args.get("vlan_type", "dot1q")
            )
        if name == "delete_vlan_passthrough":
            return tool_delete_vlan_passthrough(config, ctx, vlan_id=args.get("vlan_id", 0))
        if name == "add_route":
            return tool_add_route(
                config, ctx,
                destination=args.get("destination", ""),
                via=args.get("via", ""),
                interface=args.get("interface")
            )
        if name == "delete_route":
            return tool_delete_route(config, ctx, destination=args.get("destination", ""))
        if name == "configure_bgp":
            return tool_configure_bgp(
                config, ctx,
                asn=args.get("asn", 0),
                router_id=args.get("router_id", "")
            )
        if name == "add_bgp_peer":
            return tool_add_bgp_peer(
                config, ctx,
                name=args.get("name", ""),
                peer_ip=args.get("peer_ip", ""),
                peer_asn=args.get("peer_asn", 0),
                description=args.get("description")
            )
        if name == "remove_bgp_peer":
            return tool_remove_bgp_peer(
                config, ctx,
                peer_ip=args.get("peer_ip", "")
            )
        if name == "disable_bgp":
            return tool_disable_bgp(config, ctx)
        if name == "add_bgp_prefix":
            return tool_add_bgp_prefix(config, ctx, prefix=args.get("prefix", ""))
        if name == "remove_bgp_prefix":
            return tool_remove_bgp_prefix(config, ctx, prefix=args.get("prefix", ""))
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

        # Packet capture tools - don't need config
        if name == "start_capture":
            return tool_start_capture(
                instance=args.get("instance", "core"),
                interface=args.get("interface", "any"),
                direction=args.get("direction", "rx tx"),
                max_packets=args.get("max_packets", 10000),
                filename=args.get("filename")
            )
        if name == "stop_capture":
            return tool_stop_capture(instance=args.get("instance", "core"))
        if name == "get_capture_status":
            return tool_get_capture_status()
        if name == "list_capture_files":
            return tool_list_capture_files()
        if name == "analyze_capture":
            return tool_analyze_capture(filename=args.get("filename", ""))
        if name == "delete_capture":
            return tool_delete_capture(filename=args.get("filename", ""))
        if name == "tshark_query":
            return tool_tshark_query(
                filename=args.get("filename", ""),
                display_filter=args.get("display_filter"),
                fields=args.get("fields"),
                max_packets=args.get("max_packets", 50)
            )

        # VPP graph trace tools
        if name == "start_trace":
            return tool_start_trace(
                instance=args.get("instance", "core"),
                input_node=args.get("input_node", "dpdk-input"),
                count=args.get("count", 50)
            )
        if name == "show_trace":
            return tool_show_trace(
                instance=args.get("instance", "core"),
                max_packets=args.get("max_packets", 10)
            )
        if name == "get_trace_status":
            return tool_get_trace_status()
        if name == "clear_trace":
            return tool_clear_trace(instance=args.get("instance", "core"))

        # Interactive tool - doesn't need config
        if name == "ask_user":
            return tool_ask_user(
                question=args.get("question", ""),
                context=args.get("context")
            )

        return f"Unknown tool: {name}"

    except Exception as e:
        return f"Error executing {name}: {e}"


__all__ = [
    # Tool definitions
    'build_tools',
    # Tool dispatcher
    'execute_tool',
    # Module helpers
    'get_module_config_dict',
    'find_module',
    # Read tools
    'tool_get_config_summary',
    'tool_get_interfaces',
    'tool_get_interface_detail',
    'tool_get_routes',
    'tool_get_loopbacks',
    'tool_get_bvi_domains',
    'tool_get_vlan_passthrough',
    'tool_list_modules',
    'tool_get_module_config',
    'tool_execute_module_command',
    'tool_get_bgp_config',
    'tool_get_ospf_config',
    'tool_get_ospf6_config',
    # Write tools
    'tool_add_subinterface',
    'tool_delete_subinterface',
    'tool_add_loopback',
    'tool_delete_loopback',
    'tool_add_vlan_passthrough',
    'tool_delete_vlan_passthrough',
    'tool_add_route',
    'tool_delete_route',
    'tool_configure_bgp',
    'tool_add_bgp_peer',
    'tool_remove_bgp_peer',
    'tool_disable_bgp',
    'tool_add_bgp_prefix',
    'tool_remove_bgp_prefix',
    'tool_enable_ospf',
    'tool_disable_ospf',
    'tool_enable_ospf6',
    'tool_disable_ospf6',
    'tool_set_interface_ospf',
    'tool_set_interface_ospf6',
    'tool_clear_interface_ospf',
    'tool_clear_interface_ospf6',
    # Live tools
    'tool_show_ip_route',
    'tool_show_ipv6_route',
    'tool_show_ip_fib',
    'tool_show_ipv6_fib',
    'tool_show_interfaces_live',
    'tool_show_neighbors',
    # Capture tools
    'tool_start_capture',
    'tool_stop_capture',
    'tool_get_capture_status',
    'tool_list_capture_files',
    'tool_analyze_capture',
    'tool_delete_capture',
    'tool_tshark_query',
    # Trace tools
    'tool_start_trace',
    'tool_show_trace',
    'tool_get_trace_status',
    'tool_clear_trace',
    # Interactive tools
    'tool_ask_user',
]
