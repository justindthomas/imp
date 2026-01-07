#!/usr/bin/env python3
"""
imp_agent.py - LLM-powered agent for IMP configuration management

This module provides a natural language interface to router configuration
using Ollama and tool calling. Changes are staged until 'apply'.

This is now a thin wrapper that imports from imp_lib.agent.
"""

import sys
from pathlib import Path

# Add script directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, '/usr/local/bin')

# Re-export everything from imp_lib.agent for backward compatibility
from imp_lib.agent import (
    # Config
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    IMP_CONFIG_FILE,
    load_imp_config,
    get_ollama_host,
    get_ollama_model,
    # Client
    OllamaClient,
    # UI
    RICH_AVAILABLE,
    console,
    print_response,
    # Prompts
    build_system_prompt,
    # Tools
    build_tools,
    execute_tool,
    # Loop
    run_agent,
)


if __name__ == "__main__":
    # For testing
    print("This module should be called from imp_repl.py")
    print("Use: imp agent")
