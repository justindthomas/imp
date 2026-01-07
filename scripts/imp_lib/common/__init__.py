"""
imp_lib.common - Shared utilities for IMP tools

This module provides:
- colors: ANSI color codes and logging functions
- vpp: VPP command execution utilities
- prompts: Interactive prompt utilities
"""

from .colors import Colors, log, warn, error, info, tool_log
from .vpp import get_vpp_socket, get_available_vpp_instances, vpp_exec

__all__ = [
    'Colors', 'log', 'warn', 'error', 'info', 'tool_log',
    'get_vpp_socket', 'get_available_vpp_instances', 'vpp_exec',
]
