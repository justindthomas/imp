"""
Menu context and prompt utilities for IMP REPL.

This module contains:
- MenuContext: Tracks current position in menu hierarchy and configuration state
- get_prompt_text: Generates the prompt string based on current menu path
"""

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class MenuContext:
    """Tracks current position in menu hierarchy and configuration state."""
    path: list[str] = field(default_factory=list)
    config: Optional[Any] = None  # RouterConfig when available
    dirty: bool = False
    original_json: str = ""  # For detecting changes


def get_prompt_text(ctx: MenuContext) -> str:
    """Generate the prompt string based on current menu path."""
    if ctx.path:
        path_str = ".".join(ctx.path)
        dirty_marker = "*" if ctx.dirty else ""
        return f"imp.{path_str}{dirty_marker}> "
    else:
        dirty_marker = "*" if ctx.dirty else ""
        return f"imp{dirty_marker}> "
