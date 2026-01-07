"""
Interactive prompt utilities for REPL.

Provides wrapper functions around prompt_toolkit for collecting
user input with validation.
"""

from typing import Optional, Callable, Any

try:
    from prompt_toolkit import prompt
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


def prompt_value(
    label: str,
    default: str = "",
    validator: Optional[Callable[[str], bool]] = None,
    error_msg: str = "Invalid input"
) -> Optional[str]:
    """
    Prompt for a value with optional validation.

    Args:
        label: Prompt text to display
        default: Default value (shown in prompt, returned if empty input)
        validator: Optional function that returns True if input is valid
        error_msg: Message to show if validation fails

    Returns:
        User input string, or None if cancelled (Ctrl+C/Ctrl+D)
    """
    if not PROMPT_TOOLKIT_AVAILABLE:
        # Fallback to basic input
        try:
            if default:
                result = input(f"{label} [{default}]: ").strip()
                return result if result else default
            else:
                return input(f"{label}: ").strip()
        except (KeyboardInterrupt, EOFError):
            return None

    try:
        suffix = f" [{default}]" if default else ""
        result = prompt(f"{label}{suffix}: ").strip()

        if not result and default:
            return default

        if validator and result:
            if not validator(result):
                print(f"  {error_msg}")
                return prompt_value(label, default, validator, error_msg)

        return result
    except (KeyboardInterrupt, EOFError):
        return None


def prompt_yes_no(question: str, default: bool = False) -> Optional[bool]:
    """
    Prompt for yes/no confirmation.

    Args:
        question: Question to ask
        default: Default answer (True=yes, False=no)

    Returns:
        True for yes, False for no, None if cancelled
    """
    suffix = " [Y/n]" if default else " [y/N]"
    try:
        if PROMPT_TOOLKIT_AVAILABLE:
            answer = prompt(f"{question}{suffix}: ").strip().lower()
        else:
            answer = input(f"{question}{suffix}: ").strip().lower()

        if not answer:
            return default

        return answer in ('y', 'yes')
    except (KeyboardInterrupt, EOFError):
        return None
