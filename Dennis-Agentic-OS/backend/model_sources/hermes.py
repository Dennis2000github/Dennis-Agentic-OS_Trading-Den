"""Model source for the Hermes Agent CLI (``hermes``).

Hermes exposes no machine-readable model-list command: ``hermes model`` is an
interactive picker. It does maintain two files that can be read without driving
a TUI:

* ``$HERMES_HOME/config.yaml`` (default ``~/.hermes/config.yaml``) - the active
  provider and default model in the top-level ``model:`` block.
* ``$HERMES_HOME/provider_models_cache.json`` - the per-provider model list the
  picker populated. Entries look like
  ``{provider: {"models": ["id", ...], ...}}``.

Read-only and dependency-free: we never import YAML, touch credentials or start
the CLI. When the cache is unavailable we raise an actionable error instead of
returning a fabricated list.
"""

import json
import os

from .base import ModelListError, ModelSource


def _hermes_home():
    return os.environ.get("HERMES_HOME") or os.path.join(
        os.path.expanduser("~"), ".hermes"
    )


def _unquote(value):
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _read_model_config(path):
    """Minimal parser for the top-level ``model:`` block in ``config.yaml``.

    We only need ``default`` and ``provider``; avoiding PyYAML keeps the
    backend's dependency set unchanged.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return {}

    in_model = False
    out = {}
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            if in_model:
                break
            in_model = stripped == "model:"
            continue
        if not in_model or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        out[key.strip()] = _unquote(value)
    return out


def _read_provider_models(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _models_from_entry(entry):
    if isinstance(entry, list):
        raw = entry
    elif isinstance(entry, dict):
        raw = entry.get("models") or []
    else:
        raw = []
    out = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append((item.strip(), item.strip(), ""))
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
            if not mid:
                continue
            name = str(item.get("name") or mid).strip()
            out.append((mid, name, str(item.get("description") or "")))
    return out


class HermesModelSource(ModelSource):
    agent_id = "hermes-cli"

    def list_models(self, ctx, cwd=None):
        home = _hermes_home()
        config = _read_model_config(os.path.join(home, "config.yaml"))
        cache = _read_provider_models(
            os.path.join(home, "provider_models_cache.json")
        )

        provider = (config.get("provider") or "").strip()
        default = (config.get("default") or "").strip()

        # A single configured provider is unambiguous even if the config file
        # predates the explicit key.
        if not provider and len(cache) == 1:
            provider = next(iter(cache))

        if provider and provider in cache:
            models = _models_from_entry(cache.get(provider))
        else:
            models = []

        seen = set()
        result = []
        if default:
            seen.add(default)
            result.append(self._norm(default, name=default, is_default=True,
                                     provider=provider))
        for mid, name, description in models:
            if mid in seen:
                continue
            seen.add(mid)
            result.append(
                self._norm(mid, name=name, description=description,
                           provider=provider)
            )

        if not result:
            where = os.path.join(home, "provider_models_cache.json")
            raise ModelListError(
                "No cached models for Hermes"
                + (f" provider '{provider}'" if provider else "")
                + f" ({where}). Run `hermes model --refresh` once, then Retry."
            )
        return result
