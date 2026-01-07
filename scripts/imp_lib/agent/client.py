"""
Ollama API client for IMP agent.

This module provides an HTTP client for communicating with the Ollama API.
"""

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    requests = None


class OllamaClient:
    """HTTP client for Ollama API."""

    def __init__(self, host: str, model: str):
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests library is required")

        self.host = host.rstrip("/")
        if not self.host.startswith("http"):
            self.host = f"http://{self.host}"
        self.model = model
        self.url = f"{self.host}/api/chat"

    def chat(self, messages: list, tools: list) -> dict:
        """
        Send a chat request with tools.
        Returns the response dict with 'message' key.
        """
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
        }

        response = requests.post(self.url, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()

    def check_connection(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            return response.status_code == 200
        except requests.RequestException:
            return False

    def check_model(self) -> bool:
        """Check if the model is available."""
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                # Check for exact match or match without tag
                return any(
                    self.model == m or self.model == m.split(":")[0]
                    for m in models
                )
        except requests.RequestException:
            pass
        return False
