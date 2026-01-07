"""
imp_lib.agent - LLM Agent components for IMP

This package contains the modular components for the IMP Ollama agent:
- config: Ollama host/model configuration resolution
- client: OllamaClient HTTP client
- ui: Rich markdown rendering utilities
- tools/: Tool definitions and implementations
- prompts: System prompt builder
- loop: Main agent loop
"""

from .config import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    IMP_CONFIG_FILE,
    load_imp_config,
    get_ollama_host,
    get_ollama_model,
)

from .client import OllamaClient

from .ui import (
    RICH_AVAILABLE,
    console,
    print_response,
)

from .prompts import build_system_prompt

from .tools import build_tools, execute_tool

from .loop import run_agent

__all__ = [
    # Config
    'DEFAULT_OLLAMA_HOST',
    'DEFAULT_OLLAMA_MODEL',
    'IMP_CONFIG_FILE',
    'load_imp_config',
    'get_ollama_host',
    'get_ollama_model',
    # Client
    'OllamaClient',
    # UI
    'RICH_AVAILABLE',
    'console',
    'print_response',
    # Prompts
    'build_system_prompt',
    # Tools
    'build_tools',
    'execute_tool',
    # Loop
    'run_agent',
]
