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

    # Special case: internal interfaces are dynamic
    if ctx.path == ["config", "interfaces", "internal"] and ctx.config:
        if any(i.vpp_name == target for i in ctx.config.internal):
            ctx.path.append(target)
            return True

    # Special case: subinterfaces on dynamic internal interfaces
    if len(ctx.path) == 4 and ctx.path[:3] == ["config", "interfaces", "internal"] and ctx.config:
        iface_name = ctx.path[3]
        if any(i.vpp_name == iface_name for i in ctx.config.internal):
            if target == "subinterfaces":
                ctx.path.append(target)
                return True

    # Special case: subinterfaces on external interface
    if ctx.path == ["config", "interfaces", "external"] and target == "subinterfaces":
        ctx.path.append(target)
        return True

    return False
