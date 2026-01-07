"""
Interactive tool implementations for IMP agent.

This module contains tools that interact with the user,
such as asking clarifying questions.
"""

from imp_lib.common import Colors


def tool_ask_user(question: str, context: str = None) -> str:
    """Ask the user a clarifying question and return their answer."""
    print()
    if context:
        print(f"{Colors.DIM}{context}{Colors.NC}")
    print(f"{Colors.CYAN}Question:{Colors.NC} {question}")
    try:
        answer = input(f"{Colors.CYAN}Answer:{Colors.NC} ").strip()
        if not answer:
            return "(User provided no answer)"
        return f"User's answer: {answer}"
    except (KeyboardInterrupt, EOFError):
        print()
        return "(User cancelled the question)"
