"""
ANSI color codes and logging utilities for IMP CLI tools.
"""


class Colors:
    """ANSI color escape codes for terminal output."""
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    YELLOW = "\033[1;33m"
    CYAN = "\033[0;36m"
    MAGENTA = "\033[0;35m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    NC = "\033[0m"  # No Color / Reset


def log(msg: str) -> None:
    """Log a success/info message in green."""
    print(f"{Colors.GREEN}[+]{Colors.NC} {msg}")


def warn(msg: str) -> None:
    """Log a warning message in yellow."""
    print(f"{Colors.YELLOW}[!]{Colors.NC} {msg}")


def error(msg: str) -> None:
    """Log an error message in red."""
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")


def info(msg: str) -> None:
    """Log an informational message in cyan."""
    print(f"{Colors.CYAN}[i]{Colors.NC} {msg}")


def tool_log(name: str, args: dict = None) -> None:
    """Log an agent tool call in magenta."""
    print(f"{Colors.MAGENTA}[Tool: {name}]{Colors.NC}")
    if args:
        for key, value in args.items():
            print(f"  {key}: {value}")
