"""
Menu tree definition for IMP REPL.

This module contains the hierarchical menu structure that defines
available commands and navigation paths.
"""


def build_menu_tree() -> dict:
    """Build the hierarchical menu structure."""
    return {
        "root": {
            "children": {
                # Configuration submenu - all config items moved here
                "config": {
                    "children": {
                        "interfaces": {
                            "children": {
                                "management": {"commands": ["show", "set-dhcp", "set-static"]},
                            },
                            "commands": ["show", "list", "add"],
                            "dynamic": True,  # Interface names are generated from config
                        },
                        "routes": {
                            "commands": ["list", "add", "delete", "set-default-v4", "set-default-v6"],
                        },
                        "loopbacks": {
                            "commands": ["list", "add", "edit", "delete"],
                        },
                        "bvi": {
                            "commands": ["list", "add", "edit", "delete"],
                        },
                        "vlan-passthrough": {
                            "commands": ["list", "add", "delete"],
                        },
                        "routing": {
                            "children": {
                                "bgp": {
                                    "commands": ["show", "enable", "disable"],
                                    "children": {
                                        "peers": {"commands": ["list", "add", "remove"]},
                                    },
                                },
                                "ospf": {"commands": ["show", "enable", "disable", "set"]},
                                "ospf6": {"commands": ["show", "enable", "disable", "set"]},
                            },
                            "commands": ["show"],
                        },
                        "modules": {
                            "commands": ["available", "list", "install", "enable", "disable"],
                        },
                        "nat": {
                            "children": {
                                "mappings": {"commands": ["list", "add", "delete"]},
                                "bypass": {"commands": ["list", "add", "delete"]},
                            },
                            "commands": ["show", "set-prefix"],
                        },
                        "containers": {
                            "commands": ["show", "set"],
                        },
                        "cpu": {
                            "commands": ["show"],
                        },
                    },
                    "commands": ["show"],
                },
                # Operational commands remain at root
                "shell": {
                    "children": {
                        "routing": {"commands": []},
                        "core": {"commands": []},
                        "nat": {"commands": []},
                    },
                    "commands": [],
                },
                "capture": {
                    "commands": ["start", "stop", "status", "files", "analyze", "export", "delete"],
                },
                "trace": {
                    "commands": ["start", "stop", "status", "show", "clear"],
                },
                "snapshot": {
                    "commands": ["list", "create", "delete", "export", "import", "rollback"],
                },
                "agent": {
                    "commands": [],
                },
            },
            "commands": ["show", "status"],
        }
    }
