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
from . import freellm_catalog

CONFIG_FILE = os.path.join(config.DATA_DIR, "freellmapi.json")
_AGENT_ENVS = {
    "claude-code": ("ANTHROPIC", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"),
    "codex": ("OPENAI", "OPENAI_BASE_URL", "OPENAI_API_KEY"),
    "codex-cli": ("OPENAI", "OPENAI_BASE_URL", "OPENAI_API_KEY"),
}
# Agents the dashboard exposes. ``codex-cli`` remains accepted as an alias for
# backward compatibility but the UI only needs one Codex entry.
SUPPORTED_AGENTS = ("claude-code", "codex")
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
    result = {k: data[k] for k in ("base_url", "api_key", "model")}
    if isinstance(data.get("context_tokens"), int) and data["context_tokens"] > 0:
        result["context_tokens"] = data["context_tokens"]
    return result


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
            existing = _agent_data(agent_id)
        # Reuse a saved key only for the same endpoint. Changing providers must
        # supply the new provider's key instead of silently reusing the old one.
        if (existing.get("base_url") or "").rstrip("/") == base_url:
            api_key = (existing.get("api_key") or "").strip()
    if not base_url or not api_key or not model:
        raise FreeLLMAPIError("Base URL, API key, and model ID are required.")
    if len(base_url) > 2048 or len(api_key) > 4096 or len(model) > 256:
        raise FreeLLMAPIError("A FreeLLMAPI setting is too long.")
    if any(ch in base_url + api_key + model for ch in ("\n", "\r", "\x00")):
        raise FreeLLMAPIError("Settings cannot contain line breaks.")
    if "{" in base_url and "}" in base_url:
        match = re.search(r"\{([^}]+)\}", base_url)
        placeholder = match.group(0) if match else "{...}"
        raise FreeLLMAPIError(f"Base URL contains placeholder '{placeholder}'. Please replace it with your account details before saving.")
    if api_key.startswith("http://") or api_key.startswith("https://"):
        raise FreeLLMAPIError("API key appears to be a URL. Please paste your actual provider API key or token.")
    parsed = urlsplit(base_url)
    local_http = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not local_http:
        raise FreeLLMAPIError("Use an HTTPS provider URL (HTTP is allowed for localhost only).")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise FreeLLMAPIError("Enter a provider base URL without embedded credentials.")
    if not re.fullmatch(r"[A-Za-z0-9._:/@_+-]+", model):
        raise FreeLLMAPIError("Model ID contains unsupported characters.")
    # Optional: the model's real context window. When set, Claude Code is
    # told the window explicitly (CLAUDE_CODE_MAX_CONTEXT_TOKENS), which
    # both silences its "doesn't recognize the model" notice and makes
    # auto-compaction correct for gateway model ids.
    context_tokens = values.get("context_tokens")
    record = {"base_url": base_url, "api_key": api_key, "model": model}
    if context_tokens not in (None, ""):
        try:
            context_tokens = int(context_tokens)
        except (TypeError, ValueError):
            raise FreeLLMAPIError("Context window must be a whole number of tokens.")
        if context_tokens <= 0 or context_tokens > 10_000_000:
            raise FreeLLMAPIError("Context window must be between 1 and 10,000,000 tokens.")
        record["context_tokens"] = context_tokens
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


def catalog_models(agent_id):
    """FreeLLMAPI catalog models switchable for this agent, catalog order.

    Excludes the saved model itself (callers inject that separately via
    ``model_entry``). With no connection saved, returns []. Entries are
    metadata only; switching persists nothing until the model is actually
    picked, at which point the caller saves it via ``set_config``.
    """
    data = get(agent_id)
    if not data:
        return []
    try:
        catalog = freellm_catalog.catalog()
    except Exception:
        return []
    if not isinstance(catalog, dict) or not catalog.get("ok"):
        return []
    base = data.get("base_url") or ""
    saved_model = data.get("model") or ""
    entries = []
    seen = set()
    for provider in catalog.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        provider_base = (provider.get("base_url") or "").strip().rstrip("/")
        for model in provider.get("models") or []:
            if not isinstance(model, dict):
                continue
            model_id = str(model.get("id") or "").strip()
            if not model_id or model_id in seen or model_id == saved_model:
                continue
            seen.add(model_id)
            entries.append({
                "id": model_id,
                "name": str(model.get("name") or model_id),
                "description": "FreeLLMAPI catalog model · "
                               + str(provider.get("name") or ""),
                "is_default": False,
                "available": True,
                "reason": None,
                "provider": "FreeLLMAPI",
                # FreeLLMAPI-only fields (no other model record carries them).
                "freellm": {
                    "catalog": True,
                    "provider_name": provider.get("name") or "",
                    "provider_id": provider.get("id") or "",
                    "base_url": provider_base,
                    # An exact base-URL match means the saved endpoint is the
                    # documented one for this model; anything else needs the
                    # user's own gateway-style endpoint.
                    "endpoint_match": bool(provider_base and provider_base == base),
                    "context": model.get("context") or "",
                    "rate_limit": model.get("rate_limit") or "",
                    "url": model.get("url") or "",
                },
            })
    return entries


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
        # Declare the gateway model id so Claude Code skips its local model
        # validation ("Model ... isn't recognized" warning). Documented
        # mechanism for gateway/custom model ids; harmless for known ids.
        result["ANTHROPIC_CUSTOM_MODEL_OPTION"] = data["model"]
        if data.get("context_tokens"):
            # Declaring the real window silences the "assumes 1m tokens"
            # notice and makes auto-compaction correct for gateway ids.
            result["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(data["context_tokens"])
    return result
