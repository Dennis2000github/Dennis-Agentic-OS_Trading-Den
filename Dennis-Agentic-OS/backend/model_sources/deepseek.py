"""Model source for the native DeepSeek Harness agent.

DeepSeek Harness is not a CLI: the list comes from
:mod:`backend.deepseek_harness`, which keeps a curated set of Chat Completions
models (and honours ``DEEPSEEK_MODEL`` / ``DEEPSEEK_MODELS``). No network call is
made here, so opening the dropdown never stalls on a remote API.
"""

from urllib.parse import urlsplit

from .base import ModelSource


class DeepSeekModelSource(ModelSource):
    agent_id = "deepseek-harness"

    def list_models(self, ctx, cwd=None):
        from .. import deepseek_harness

        # A custom DEEPSEEK_BASE_URL means the endpoint is not DeepSeek itself;
        # name the provider by its host so the UI never mislabels it.
        base = deepseek_harness.base_url()
        provider = "DeepSeek"
        if base != deepseek_harness.DEFAULT_BASE_URL:
            provider = urlsplit(base).hostname or "Custom endpoint"

        models = []
        for model in deepseek_harness.models():
            entry = dict(model)
            entry.setdefault("provider", provider)
            models.append(entry)
        return models
