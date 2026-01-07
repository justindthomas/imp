"""
Agent loop for IMP.

This module contains the main agent loop that handles user interaction
with the Ollama LLM and tool execution.
"""

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    requests = None

from imp_lib.common import Colors, log, warn, error

from .config import get_ollama_host, get_ollama_model
from .client import OllamaClient
from .ui import RICH_AVAILABLE, console, render_content_with_tables
from .prompts import build_system_prompt
from .tools import build_tools, execute_tool


def run_agent(ctx, host: str = None, model: str = None) -> None:
    """
    Run the agent loop.

    Args:
        ctx: MenuContext from imp_repl with config and dirty flag
        host: Ollama host override
        model: Ollama model override
    """
    if not REQUESTS_AVAILABLE:
        error("python3-requests is required for agent mode")
        print("  Install with: apt install python3-requests")
        return

    host = get_ollama_host(host)
    model = get_ollama_model(model)

    client = OllamaClient(host, model)

    # Check connection
    print()
    if not client.check_connection():
        error(f"Cannot connect to Ollama at {host}")
        print(f"  Make sure Ollama is running: ollama serve")
        print(f"  Or set OLLAMA_HOST environment variable")
        return

    if not client.check_model():
        warn(f"Model '{model}' may not be available")
        print(f"  Run: ollama pull {model}")
        print(f"  Or set OLLAMA_MODEL environment variable")
        print()

    log(f"Connected to Ollama ({model})")
    print("Type your request, or 'exit' to return")
    print()

    # Build tools
    tools = build_tools()

    # Conversation history
    messages = [
        {"role": "system", "content": build_system_prompt(ctx.config)}
    ]

    while True:
        try:
            user_input = input(f"{Colors.CYAN}agent>{Colors.NC} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            break

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            break

        # Add user message
        messages.append({"role": "user", "content": user_input})

        try:
            # Call Ollama
            print(f"{Colors.DIM}Thinking...{Colors.NC}")
            response = client.chat(messages, tools)

            message = response.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])

            # Process tool calls
            if tool_calls:
                # Append assistant message with tool calls to history
                messages.append({
                    "role": "assistant",
                    "tool_calls": tool_calls
                })

                # Execute each tool and collect results
                for tool_call in tool_calls:
                    func = tool_call.get("function", {})
                    tool_name = func.get("name", "")
                    tool_args = func.get("arguments", {})

                    result = execute_tool(tool_name, tool_args, ctx.config, ctx)
                    print(f"  {Colors.DIM}→ {result}{Colors.NC}")

                    # Add tool result to messages
                    messages.append({
                        "role": "tool",
                        "content": result
                    })

                # Get final response after tool execution
                print(f"{Colors.DIM}Thinking...{Colors.NC}")
                response = client.chat(messages, tools)
                message = response.get("message", {})
                content = message.get("content", "")

                # Check for more tool calls
                more_tool_calls = message.get("tool_calls", [])
                while more_tool_calls:
                    messages.append({
                        "role": "assistant",
                        "tool_calls": more_tool_calls
                    })

                    for tool_call in more_tool_calls:
                        func = tool_call.get("function", {})
                        tool_name = func.get("name", "")
                        tool_args = func.get("arguments", {})

                        result = execute_tool(tool_name, tool_args, ctx.config, ctx)
                        print(f"  {Colors.DIM}→ {result}{Colors.NC}")

                        messages.append({
                            "role": "tool",
                            "content": result
                        })

                    print(f"{Colors.DIM}Thinking...{Colors.NC}")
                    response = client.chat(messages, tools)
                    message = response.get("message", {})
                    content = message.get("content", "")
                    more_tool_calls = message.get("tool_calls", [])

            # Display final response
            if content:
                print()
                if RICH_AVAILABLE:
                    console.print(render_content_with_tables(content))
                else:
                    print(content)
                print()

            # Add assistant response to history
            messages.append({"role": "assistant", "content": content})

        except requests.exceptions.Timeout:
            error("Request timed out. The model may be slow or unresponsive.")
            messages.pop()  # Remove failed user message
        except requests.exceptions.RequestException as e:
            error(f"Request failed: {e}")
            messages.pop()  # Remove failed user message
        except Exception as e:
            error(f"Error: {e}")
            messages.pop()  # Remove failed user message

    print("Returning to IMP REPL...")
