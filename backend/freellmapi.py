"""Per-agent FreeLLMAPI endpoint settings.

FreeLLMAPI is a catalog, not a hosted proxy. The user supplies a compatible
provider endpoint, API key, and model from that catalog for each CLI. Secrets
are stored in ``data/freellmapi.json`` with owner-only permissions.
"""

import json
import os
import re
import stat
import tempfile
import threading
from urllib.parse import urlsplit

from . import config

CONFIG_FILE = os.path.join(config.DATA_DIR, "freellmapi.json")
_AGENT_ENVS = {
    "claude-code": ("ANTHROPIC", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"),
    "codex": ("OPENAI", "OPENAI_BASE_URL", "OPENAI_API_KEY"),
    "codex-cli": ("OPENAI", "OPENAI_BASE_URL", "OPENAI_API_KEY"),
}
_LOCK = threading.RLock()


class FreeLLMAPIError(ValueError):
    pass


def _read():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"agents": {}}
    except (OSError, ValueError) as exc:
        raise FreeLLMAPIError("FreeLLMAPI settings could not be read.") from exc
    if not isinstance(data, dict):
        return {"agents": {}}
    # Accept the initial single-agent file format if it was created during an
    # interrupted upgrade, and migrate it on the next save.
    if "base_url" in data:
        data = {"agents": {"claude-code": {
            k: data[k] for k in ("base_url", "api_key", "model") if data.get(k)
        }}}
    if not isinstance(data.get("agents"), dict):
        data["agents"] = {}
    return data


def _agent_data(agent_id, data=None):
    if agent_id not in _AGENT_ENVS:
        return {}
    data = _read() if data is None else data
    value = data.get("agents", {}).get(agent_id, {})
    return value if isinstance(value, dict) else {}


def status(agent_id):
    with _LOCK:
        data = _agent_data(agent_id)
    configured = all(data.get(k) for k in ("base_url", "api_key", "model"))
    key = data.get("api_key") or ""
    return {
        "agent_id": agent_id,
        "configured": bool(configured),
        "base_url": data.get("base_url") if configured else "",
        "model": data.get("model") if configured else "",
        "key_hint": ("…" + key[-4:]) if configured and len(key) >= 4 else None,
    }


def get(agent_id):
    with _LOCK:
        data = _agent_data(agent_id)
    if not all(data.get(k) for k in ("base_url", "api_key", "model")):
        return None
    return {k: data[k] for k in ("base_url", "api_key", "model")}


def set_config(values):
    if not isinstance(values, dict):
        raise FreeLLMAPIError("Settings must be an object.")
    agent_id = str(values.get("agent_id") or "").strip()
    if agent_id not in _AGENT_ENVS:
        raise FreeLLMAPIError("FreeLLMAPI is currently supported for Claude Code and Codex CLI.")
    base_url = str(values.get("base_url") or "").strip().rstrip("/")
    model = str(values.get("model") or "").strip()
    api_key = str(values.get("api_key") or "").strip()
    if not api_key:
        with _LOCK:
            api_key = (_agent_data(agent_id).get("api_key") or "").strip()
    if not base_url or not api_key or not model:
        raise FreeLLMAPIError("Base URL, API key, and model ID are required.")
    if len(base_url) > 2048 or len(api_key) > 4096 or len(model) > 256:
        raise FreeLLMAPIError("A FreeLLMAPI setting is too long.")
    if any(ch in base_url + api_key + model for ch in ("\n", "\r", "\x00")):
        raise FreeLLMAPIError("Settings cannot contain line breaks.")
    parsed = urlsplit(base_url)
    local_http = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not local_http:
        raise FreeLLMAPIError("Use an HTTPS provider URL (HTTP is allowed for localhost only).")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FreeLLMAPIError("Enter a provider base URL without embedded credentials.")
    if not re.fullmatch(r"[A-Za-z0-9._:/@_+-]+", model):
        raise FreeLLMAPIError("Model ID contains unsupported characters.")
    record = {"base_url": base_url, "api_key": api_key, "model": model}
    with _LOCK:
        all_data = _read()
        all_data.setdefault("agents", {})[agent_id] = record
        os.makedirs(config.DATA_DIR, mode=0o700, exist_ok=True)
        try:
            os.chmod(config.DATA_DIR, 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(prefix=".freellmapi-", suffix=".json", dir=config.DATA_DIR)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(all_data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, CONFIG_FILE)
            os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
    return status(agent_id)


def clear(agent_id):
    with _LOCK:
        all_data = _read()
        all_data.get("agents", {}).pop(agent_id, None)
        if all_data.get("agents"):
            _write(all_data)
        else:
            try:
                os.remove(CONFIG_FILE)
            except FileNotFoundError:
                pass
    return status(agent_id)


def _write(data):
    os.makedirs(config.DATA_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".freellmapi-", suffix=".json", dir=config.DATA_DIR)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, CONFIG_FILE)
        os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def model_entry(agent_id):
    data = get(agent_id)
    if not data:
        return None
    return {
        "id": data["model"],
        "name": data["model"],
        "description": "FreeLLMAPI configured model",
        "is_default": True,
        "available": True,
        "reason": None,
        "provider": "FreeLLMAPI",
    }


def apply_agent_env(env, agent_id, model_id=None):
    """Add the endpoint only when the FreeLLMAPI model is selected."""
    data = get(agent_id)
    if not data or model_id != data["model"]:
        return env
    _protocol, base_var, key_var = _AGENT_ENVS[agent_id]
    result = dict(env)
    result.update({base_var: data["base_url"], key_var: data["api_key"]})
    if agent_id == "claude-code":
        result["ANTHROPIC_API_KEY"] = ""
        result["ANTHROPIC_MODEL"] = data["model"]
    return result
