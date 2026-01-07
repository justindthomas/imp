"""
imp_lib.repl - REPL components for IMP

This package contains the modular components for the IMP interactive REPL:
- context: Menu context and state tracking
- menu: Menu tree structure
- completer: Tab completion
- navigation: Menu navigation
- display/: Configuration and live state display functions
- commands/: Command handlers
- dispatcher: Main command dispatcher and REPL loop
"""

from .context import MenuContext, get_prompt_text
from .menu import build_menu_tree
from .navigation import navigate
from .completer import MenuCompleter

__all__ = [
    'MenuContext',
    'get_prompt_text',
    'build_menu_tree',
    'navigate',
    'MenuCompleter',
]
