"""
Configuration constants for IMP.

Paths and default values used across the configuration system.
"""

from pathlib import Path


# Template and configuration paths
TEMPLATE_DIR = Path("/etc/imp/templates")
CONFIG_FILE = Path("/persistent/config/router.json")
GENERATED_DIR = Path("/tmp/imp-generated-config")
