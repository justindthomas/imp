"""
Configuration utilities for IMP agent.

This module handles loading configuration and resolving Ollama settings.
"""

import json
import os
from pathlib import Path
from typing import Optional


DEFAULT_OLLAMA_HOST = "localhost:11434"
DEFAULT_OLLAMA_MODEL = "gpt-oss:120b"
IMP_CONFIG_FILE = Path("/persistent/config/imp.json")


def load_imp_config() -> dict:
    """Load IMP settings from config file."""
    if IMP_CONFIG_FILE.exists():
        try:
            with open(IMP_CONFIG_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def get_ollama_host(arg_host: Optional[str] = None) -> str:
    """Get Ollama host from args, env, config, or default."""
    if arg_host:
        return arg_host
    if os.environ.get("OLLAMA_HOST"):
        return os.environ["OLLAMA_HOST"]
    config = load_imp_config()
    if config.get("ollama", {}).get("host"):
        return config["ollama"]["host"]
    return DEFAULT_OLLAMA_HOST


def get_ollama_model(arg_model: Optional[str] = None) -> str:
    """Get Ollama model from args, env, config, or default."""
    if arg_model:
        return arg_model
    if os.environ.get("OLLAMA_MODEL"):
        return os.environ["OLLAMA_MODEL"]
    config = load_imp_config()
    if config.get("ollama", {}).get("model"):
        return config["ollama"]["model"]
    return DEFAULT_OLLAMA_MODEL
