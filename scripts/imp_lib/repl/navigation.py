"""
Navigation utilities for IMP REPL.

This module handles menu navigation within the hierarchical menu system.
"""

from .context import MenuContext


def navigate(ctx: MenuContext, target: str, menus: dict) -> bool:
    """
    Navigate to a menu. Returns True if navigation succeeded.

    Args:
        ctx: Current menu context
        target: Target menu name to navigate to
        menus: Menu tree dictionary

    Returns:
        True if navigation succeeded, False otherwise
    """
    # Get current menu
    menu = menus.get("root")
    for segment in ctx.path:
        if menu and "children" in menu:
            menu = menu["children"].get(segment, {})

    # Check if target is a valid child
    if menu and "children" in menu and target in menu["children"]:
        ctx.path.append(target)
        return True

    # Dynamic interface navigation: config interfaces <name>
    if ctx.path == ["config", "interfaces"] and ctx.config:
        if any(i.name == target for i in ctx.config.interfaces):
            ctx.path.append(target)
            return True

    # Subinterfaces on a dynamic interface
    if len(ctx.path) == 3 and ctx.path[:2] == ["config", "interfaces"] and ctx.config:
        iface_name = ctx.path[2]
        if any(i.name == iface_name for i in ctx.config.interfaces):
            if target == "subinterfaces":
                ctx.path.append(target)
                return True

    # Dynamic module navigation: config modules <name>
    if ctx.path == ["config", "modules"] and ctx.config:
        for m in ctx.config.modules:
            if m.get('name') == target:
                ctx.path.append(target)
                return True

    # Subpaths within a module (e.g., config modules nat mappings)
    if len(ctx.path) >= 3 and ctx.path[:2] == ["config", "modules"] and ctx.config:
        # Allow any navigation within a module - the command handler will validate
        ctx.path.append(target)
        return True

    return False
