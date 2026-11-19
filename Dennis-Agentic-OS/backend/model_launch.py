"""Per-agent model launch planning.

A model switch must reach the actual CLI, not just the session record. CLIs
accept models in three different ways:

* **Startup flag** (``codex -m``, ``claude --model``, ``aider --model``) —
  handled directly by ``AgentSession.start`` via the profile's ``model_flag``.
* **Generated config file** (OpenCode v2) — the interactive TUI takes no model
  flag and its shared background service ignores client env, but a *standalone*
  server started with ``OPENCODE_CONFIG`` pointing at a config file with
  ``{"model": "provider/model"}`` boots with that model (verified live against
  opencode 2.2.2). The config file is generated per session from the user's
  global OpenCode config plus the chosen model, so nothing in the user's home
  or project folders is modified.
* **Staged TUI input** (OpenClaw) — the TUI selects models with ``/model
  <id>`` typed after the UI has rendered. Steps are scheduled with delays.

``plan()`` returns a :class:`ModelLaunch` describing extra argv, extra env and
the staged inputs to schedule. ``AgentSession.start`` applies it.
"""

import json
import os
import re

from . import config


class ModelLaunch:
    def __init__(self):
        self.extra_args = []       # prepended after the profile's base args
        self.env = {}              # merged into the process environment
        self.inputs = []           # [{"at": seconds, "text": str}]
        self.config_file = None    # generated file to delete on stop

    def empty(self):
        return not self.extra_args and not self.env and not self.inputs


def _read_global_opencode_config():
    """Best-effort read of the user's global OpenCode config (JSON or JSONC)."""
    base = os.path.join(os.path.expanduser("~"), ".config", "opencode")
    for name in ("opencode.json", "opencode.jsonc"):
        path = os.path.join(base, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = fh.read()
        except OSError:
            continue
        # Tolerate JSONC: strip // line comments and trailing commas.
        cleaned = []
        for line in raw.splitlines():
            idx = line.find("//")
            if idx >= 0 and '"' not in line[:idx]:
                line = line[:idx]
            cleaned.append(line)
        text = "\n".join(cleaned).strip()
        try:
            data = json.loads(text)
        except ValueError:
            text = json.dumps(_strip_trailing_commas(text))
            try:
                data = json.loads(text)
            except ValueError:
                continue
        return data if isinstance(data, dict) else {}
    return {}


def _strip_trailing_commas(text):
    out = []
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith(","):
            line = stripped[:-1]
        out.append(line)
    return "\n".join(out)


def _opencode_config_path(session_id):
    directory = os.path.join(config.DATA_DIR, "opencode-launch")
    return os.path.join(directory, f"{session_id}.json")


def _opencode_provider_block(model_id):
    """Endpoint block for the generated OpenCode config, or None.

    Covers connection-scoped models (``conn:<id>:<native>``) on an
    OpenAI-compatible connection. The block is only produced when the
    endpoint and key are actually configured - nothing is invented.
    """
    from . import connections as _connections
    from . import model_catalog as _model_catalog

    parsed = _model_catalog.parse_internal_id(model_id)
    if not parsed:
        return None
    connection_id, native = parsed
    conn = _connections.get_connection(connection_id)
    if not conn or conn.get("protocol") != _connections.OPENAI_PROTOCOL:
        return None
    base = conn.get("base_url") or ""
    key, _source = _connections.resolve_key(conn)
    provider_id = re.sub(r"[^a-z0-9]+", "-",
                         (conn.get("provider_id") or "custom").lower()).strip("-")
    if not base or not key or not native:
        return None
    block = {
        "api": "openai-compatible",
        "name": conn.get("name") or "Custom endpoint",
        "options": {"baseURL": base, "apiKey": key},
        "models": {native: {"name": native}},
    }
    return provider_id or "custom", native, block


def _plan_opencode(session_id, model_id):
    launch = ModelLaunch()
    if not model_id:
        return launch
    merged = _read_global_opencode_config()
    provider_block = _opencode_provider_block(model_id)
    if provider_block:
        provider_id, native, block = provider_block
        merged.setdefault("provider", {})[provider_id] = block
        merged["model"] = provider_id + "/" + native
    else:
        merged["model"] = model_id
    if "$schema" not in merged:
        merged["$schema"] = "https://opencode.ai/config.json"
    path = _opencode_config_path(session_id)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        # The config can carry an API key; write it owner-only.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
    except OSError:
        return launch
    launch.config_file = path
    # The interactive TUI connects to the shared background service, which was
    # started without this env and ignores per-client config. A standalone
    # private server loads it at boot, so the chosen model actually applies.
    launch.extra_args = ["--standalone"]
    launch.env = {"OPENCODE_CONFIG": path}
    return launch


_PLANNERS = {
    "opencode": _plan_opencode,
}


def plan(agent_id, model_id, session_id):
    planner = _PLANNERS.get(agent_id)
    if not planner:
        return ModelLaunch()
    try:
        launch = planner(session_id, model_id)
    except Exception:          # never block a session start on the hook
        return ModelLaunch()
    return launch


def cleanup(launch):
    if not launch or not launch.config_file:
        return
    try:
        os.remove(launch.config_file)
    except OSError:
        pass
