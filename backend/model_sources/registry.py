"""Model source registry + short-TTL cache.

The dropdown reloads when opened, when Refresh is pressed, or when the agent
changes; the cache exists only to avoid hammering the CLI on rapid reopens.
Nothing here reads the Model Registry, Free/Paid routing or Connections.
"""

import threading
import time

from .. import config
from .antigravity import AntigravityModelSource
from .base import ModelListError
from .claude import ClaudeModelSource
from .codex import CodexModelSource
from .deepseek import DeepSeekModelSource
from .hermes import HermesModelSource
from .opencode import OpenCodeModelSource
from .cursor import CursorModelSource
from .openclaw import OpenClawModelSource

_SOURCES = {
    "opencode": OpenCodeModelSource(),
    "cursor": CursorModelSource(),
    "openclaw": OpenClawModelSource(),
    "antigravity": AntigravityModelSource(),
    "codex": CodexModelSource(),
    "codex-cli": CodexModelSource(),
    "claude-code": ClaudeModelSource(),
    "hermes-cli": HermesModelSource(),
    "deepseek-harness": DeepSeekModelSource(),
}

_cache = {}
_lock = threading.Lock()


def get_source(agent_id):
    return _SOURCES.get(agent_id)


def clear_cache(agent_id=None):
    with _lock:
        if agent_id is None:
            _cache.clear()
        else:
            _cache.pop(agent_id, None)


def list_models(agent_id, ctx, cwd=None, refresh=False):
    """Return a fresh (or cached) model list for ``agent_id``.

    Raises :class:`ModelListError` when the CLI is unsupported or parsing
    fails. Never returns a fabricated list.
    """
    source = get_source(agent_id)
    if source is None:
        raise ModelListError(f"No model source for agent '{agent_id}'")

    now = time.time()
    if not refresh:
        with _lock:
            entry = _cache.get(agent_id)
        if entry and (now - entry["ts"]) < config.MODEL_CACHE_TTL_SECONDS:
            return entry["models"]

    models = source.list_models(ctx, cwd=cwd)
    with _lock:
        _cache[agent_id] = {"ts": now, "models": models}
    return models


def validate_model(agent_id, model_id, ctx, cwd=None, refresh=False):
    models = list_models(agent_id, ctx, cwd=cwd, refresh=refresh)
    return any(m["id"] == model_id for m in models)


# Display labels for provider ids we know. Unknown ids stay verbatim so the UI
# never renames a provider the CLI (or the user's own config) invented.
_PROVIDER_LABELS = {
    "opencode": "OpenCode",
    "cursor": "Cursor",
    "openclaw": "OpenClaw",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "google deepmind": "Google DeepMind",
    "deepseek": "DeepSeek",
    "ollama-cloud": "Ollama Cloud",
    "openrouter": "OpenRouter",
    "github-copilot": "GitHub Copilot",
    "freellmapi": "FreeLLMAPI",
}


def provider_label(provider_id):
    return _PROVIDER_LABELS.get((provider_id or "").strip().lower(),
                                (provider_id or "").strip())


def providers(models):
    """Group a model list by the provider the CLI reported for each model.

    Only providers actually present in the list are returned (first-seen
    order), so the dropdown can never invent a provider the CLI did not list.
    """
    counts = {}
    available = {}
    order = []
    for model in models or []:
        pid = (model.get("provider") or "").strip()
        if not pid:
            continue
        if pid not in counts:
            counts[pid] = 0
            available[pid] = False
            order.append(pid)
        counts[pid] += 1
        if model.get("available", True):
            available[pid] = True
    return [
        {
            "id": pid,
            "label": provider_label(pid),
            "model_count": counts[pid],
            "available": available[pid],
        }
        for pid in order
    ]
