"""Generic OpenAI-compatible model source for OmniRoute and other providers."""

from .base import ModelSource

class OmniRouteSource(ModelSource):
    agent_id = "omniroute"

    def list_models(self, ctx, cwd=None):
        """
        OmniRoute typically uses a standard /v1/models endpoint.
        Since we are implementing this as a source for the OS, 
        we will return a standard list of compatible models if the 
        API call is not yet fully integrated into the CLI runner.
        """
        # In a full implementation, this would call the API.
        # For now, we provide a set of common high-performance placeholders.
        return [
            self._norm("gpt-4o", "GPT-4o", "OmniRoute High Intelligence", is_default=True, provider="OmniRoute"),
            self._norm("claude-3-5-sonnet", "Claude 3.5 Sonnet", "OmniRoute Coding Expert", provider="OmniRoute"),
            self._norm("deepseek-chat", "DeepSeek Chat", "OmniRoute Efficient Reasoner", provider="OmniRoute"),
        ]

    def current_model(self, session):
        return (session or {}).get("model_id", "gpt-4o")
