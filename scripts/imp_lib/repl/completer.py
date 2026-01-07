"""
Tab completion for IMP REPL.

This module provides context-aware command completion using prompt_toolkit.
"""

import json
from pathlib import Path

try:
    from prompt_toolkit.completion import Completer, Completion
except ImportError:
    # Stub for when prompt_toolkit is not available
    class Completer:
        pass
    class Completion:
        def __init__(self, text, start_position=0):
            self.text = text
            self.start_position = start_position

from .context import MenuContext

# Config file path for reading module config
CONFIG_FILE = Path("/persistent/config/router.json")

# Import module loader for show module commands
from imp_lib.modules import load_module_definition
MODULE_LOADER_AVAILABLE = True


class MenuCompleter(Completer):
    """Dynamic completer that provides context-aware completions."""

    def __init__(self, ctx: MenuContext, menus: dict):
        self.ctx = ctx
        self.menus = menus

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()

        # Determine what we're completing
        if not words:
            # Empty input - show current menu completions
            completions = self._get_menu_completions([])
            word = ""
        elif text.endswith(' '):
            # User typed a word and space - complete subcommands
            completions = self._get_menu_completions(words)
            word = ""
        else:
            # User is typing a word - complete from parent context
            completions = self._get_menu_completions(words[:-1])
            word = words[-1].lower()

        # Yield matching completions
        for item in completions:
            if item.lower().startswith(word):
                yield Completion(item, start_position=-len(word))

    def _get_menu_at_path(self, path: list[str]):
        """Navigate to a menu based on path segments."""
        menu = self.menus.get("root")
        for segment in path:
            if menu and "children" in menu:
                menu = menu["children"].get(segment)
            else:
                return None
        return menu

    def _get_module_names_with_show_commands(self) -> list[str]:
        """Get list of enabled module names that have show_commands defined."""
        if not MODULE_LOADER_AVAILABLE:
            return []
        modules = []
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    config_data = json.load(f)
                for mod in config_data.get("modules", []):
                    if mod.get("enabled") and mod.get("name"):
                        try:
                            mod_def = load_module_definition(mod["name"])
                            if mod_def.show_commands:
                                modules.append(mod["name"])
                        except Exception:
                            pass
            except Exception:
                pass
        return modules

    def _get_module_show_commands(self, module_name: str) -> list[str]:
        """Get list of show command names for a specific module."""
        if not MODULE_LOADER_AVAILABLE:
            return []
        try:
            mod_def = load_module_definition(module_name)
            return [cmd.name for cmd in mod_def.show_commands]
        except Exception:
            return []

    def _get_menu_completions(self, cmd_prefix: list[str]) -> list[str]:
        """Get available commands and submenus for given context.

        Args:
            cmd_prefix: Additional path segments typed on command line
        """
        completions = []

        # Build effective path: current menu path + typed command prefix
        effective_path = self.ctx.path + cmd_prefix

        # Handle "show" command completions (it's a global command, not a menu)
        if not self.ctx.path or self.ctx.path[0] != "config":
            # At root or non-config menu - show is for live state
            if cmd_prefix == ["show"]:
                return ["interfaces", "ip", "ipv6", "neighbors", "bgp", "ospf", "module", "config"]
            if cmd_prefix == ["show", "ip"]:
                return ["route", "fib"]
            if cmd_prefix == ["show", "ipv6"]:
                return ["route", "fib"]
            if cmd_prefix == ["show", "module"]:
                # Return enabled modules with show_commands
                return self._get_module_names_with_show_commands()
            if len(cmd_prefix) == 3 and cmd_prefix[0] == "show" and cmd_prefix[1] == "module":
                # Return show commands for the specific module
                return self._get_module_show_commands(cmd_prefix[2])
            if cmd_prefix == ["show", "config"]:
                return ["interfaces", "loopbacks", "bvi", "vlan-passthrough", "routing", "nat", "containers", "cpu"]

        # Navigate to the menu at this effective path
        menu = self._get_menu_at_path(effective_path)

        # If we couldn't navigate there, no completions
        if menu is None and cmd_prefix:
            return []

        # Only show global commands at the current menu level (not when completing subcommands)
        if not cmd_prefix:
            # Commands available everywhere
            base_commands = ["help", "exit"]

            # Navigation commands only when not at root
            if self.ctx.path:
                base_commands.extend(["back", "home"])

            # Root-only commands
            if not self.ctx.path:
                base_commands.extend(["show", "status", "reload"])
                # Apply only when there are unsaved changes
                if self.ctx.dirty:
                    base_commands.append("apply")
            # At config level, show is for viewing config sections
            elif self.ctx.path == ["config"]:
                base_commands.append("show")

            completions.extend(base_commands)

        if menu:
            # Menu-specific commands from static menu definition
            if "commands" in menu:
                completions.extend(menu["commands"])

            # Child menus from static menu definition
            if "children" in menu:
                completions.extend(menu["children"].keys())

        # Dynamic completions based on effective path
        if effective_path == ["config", "interfaces"] and self.ctx.config:
            # Add interface names from config (dynamic children)
            completions.extend(i.name for i in self.ctx.config.interfaces)

        if len(effective_path) == 3 and effective_path[:2] == ["config", "interfaces"] and self.ctx.config:
            # After selecting an interface name, show commands
            iface_name = effective_path[2]
            if iface_name not in ("management", "show", "list", "add"):
                if any(i.name == iface_name for i in self.ctx.config.interfaces):
                    completions.extend(["show", "set-ipv4", "set-ipv6", "set-mtu", "delete", "subinterfaces", "ospf", "ospf6"])

        # Routes menu
        if effective_path == ["config", "routes"] and self.ctx.config:
            completions.extend(["list", "add", "delete", "set-default-v4", "set-default-v6"])

        if len(effective_path) >= 4 and effective_path[-1] == "subinterfaces":
            # Add sub-interface commands
            completions.extend(["list", "add", "delete"])

        if len(effective_path) == 3 and effective_path[:2] == ["config", "loopbacks"]:
            # After "delete" or "edit", show loopback instance numbers
            if effective_path[2] in ("delete", "edit") and self.ctx.config:
                for lo in self.ctx.config.loopbacks:
                    completions.append(str(lo.instance))

        if len(effective_path) == 4 and effective_path[:2] == ["config", "loopbacks"]:
            # After selecting a loopback instance, show OSPF commands
            if effective_path[2] not in ("delete", "edit", "add"):
                completions.extend(["ospf", "ospf6"])

        if len(effective_path) == 3 and effective_path[:2] == ["config", "bvi"]:
            # After "delete" or "edit", show BVI bridge IDs
            if effective_path[2] in ("delete", "edit") and self.ctx.config:
                for bvi in self.ctx.config.bvi_domains:
                    completions.append(str(bvi.bridge_id))

        if len(effective_path) == 4 and effective_path[:2] == ["config", "bvi"]:
            # After selecting a BVI, show OSPF commands
            if effective_path[2] not in ("delete", "edit", "add"):
                completions.extend(["ospf", "ospf6"])

        if len(effective_path) == 3 and effective_path[:2] == ["config", "vlan-passthrough"]:
            # After "delete", show VLAN IDs
            if effective_path[2] == "delete" and self.ctx.config:
                for v in self.ctx.config.vlan_passthrough:
                    completions.append(str(v.vlan_id))

        return list(set(completions))  # Remove duplicates
