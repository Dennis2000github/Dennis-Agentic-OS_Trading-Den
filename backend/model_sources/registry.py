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
from .opencode import OpenCodeModelSource

_SOURCES = {
    "opencode": OpenCodeModelSource(),
    "antigravity": AntigravityModelSource(),
    "codex": CodexModelSource(),
    "codex-cli": CodexModelSource(),
    "claude-code": ClaudeModelSource(),
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
