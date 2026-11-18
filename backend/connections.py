"""Connection Source registry for the unified AI connection architecture.

Two connection types exist and are separate internal concepts:

* ``provider``  - direct official provider access (OpenAI, Anthropic, Google
  Gemini, Groq, Cerebras, NVIDIA, ...). Official API endpoint + credentials.
* ``gateway``   - alternative model access (OpenRouter, LiteLLM, 9Router,
  FreeLLMAPI-configured endpoints, custom OpenAI- or Anthropic-compatible
  endpoints). A gateway is NOT assumed to be free and is NOT assumed to use
  one protocol; every record carries its own protocol and pricing class.

Design rules (see docs/AI_CONNECTION_ARCHITECTURE_AUDIT.md):

* Secrets never leave this module redacted. ``public_view()`` returns a key
  hint only; the raw key is injected into agent environments at execution
  time by ``execution_router``.
* Connection health comes from actual endpoint tests (``test_connection``),
  never from the presence of saved credentials.
* Storage is a flat JSON file (``data/connections.json``) written atomically
  with owner-only permissions. No new infrastructure dependency.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import stat
import threading
import time
import uuid
from urllib.parse import urlsplit

from . import config

CONNECTIONS_FILE = os.path.join(config.DATA_DIR, "connections.json")
_STORE_VERSION = 1
_STORE_MODE = stat.S_IRUSR | stat.S_IWUSR  # 0600: the file holds API keys

# Connection source types.
PROVIDER = "provider"
GATEWAY = "gateway"

# API protocols a connection can speak. The protocol decides both the model
# discovery call and the agent environment mapping at execution time.
OPENAI_PROTOCOL = "openai"        # GET {base}/models, Bearer auth
ANTHROPIC_PROTOCOL = "anthropic"  # GET {base}/v1/models, x-api-key auth

# Pricing classifications (connection metadata, never inferred from names).
PRICING_FREE = "free"
PRICING_FREE_TIER = "free_tier"
PRICING_PAID = "paid"
PRICING_SUBSCRIPTION = "subscription"
PRICING_LOCAL = "local"
PRICING_UNKNOWN = "unknown"
PRICING_VALUES = (PRICING_FREE, PRICING_FREE_TIER, PRICING_PAID,
                  PRICING_SUBSCRIPTION, PRICING_LOCAL, PRICING_UNKNOWN)

# Connection lifecycle states (spec section 5C).
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_AUTH_REQUIRED = "auth_required"
STATUS_CONFIGURED = "configured"
STATUS_TESTING = "testing"
STATUS_CONNECTED = "connected"
STATUS_UNAVAILABLE = "unavailable"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_AUTH_FAILED = "auth_failed"
STATUS_CONFIG_ERROR = "configuration_error"
STATUS_UNKNOWN = "unknown"

_LOCK = threading.RLock()
_HEALTH_TTL = 300  # seconds a cached test result is trusted


# ---------------------------------------------------------------------------
# Presets: metadata for the add-connection form. Served via the API; the
# frontend never hardcodes this list. Base URLs are the vendors' documented
# OpenAI-/Anthropic-compatible endpoints; users can override any field.
# ---------------------------------------------------------------------------
PROVIDER_PRESETS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "OPENAI_API_KEY",
        "docs_url": "https://platform.openai.com/docs/api-reference",
        "key_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "protocol": ANTHROPIC_PROTOCOL,
        "key_env": "ANTHROPIC_API_KEY",
        "docs_url": "https://docs.anthropic.com/en/api/getting-started",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "google": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "GEMINI_API_KEY",
        "docs_url": "https://ai.google.dev/gemini-api/docs/openai",
        "key_url": "https://aistudio.google.com/app/apikey",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "GROQ_API_KEY",
        "docs_url": "https://console.groq.com/docs/openai",
        "key_url": "https://console.groq.com/keys",
    },
    "cerebras": {
        "label": "Cerebras",
        "base_url": "https://api.cerebras.ai/v1",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "CEREBRAS_API_KEY",
        "docs_url": "https://inference-docs.cerebras.ai",
        "key_url": "https://cloud.cerebras.ai",
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "NVIDIA_API_KEY",
        "docs_url": "https://docs.api.nvidia.com",
        "key_url": "https://build.nvidia.com",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "DEEPSEEK_API_KEY",
        "docs_url": "https://api-docs.deepseek.com",
        "key_url": "https://platform.deepseek.com/api_keys",
    },
}

GATEWAY_PRESETS = {
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "OPENROUTER_API_KEY",
        "docs_url": "https://openrouter.ai/docs",
        "key_url": "https://openrouter.ai/keys",
        "pricing": PRICING_UNKNOWN,  # aggregator: free and paid models mixed
        "notes": "Aggregator of many upstream providers. Model ids are "
                 "gateway-specific (e.g. deepseek/deepseek-chat) and are not "
                 "assumed to match upstream ids.",
    },
    "litellm": {
        "label": "LiteLLM proxy",
        "base_url": "http://localhost:4000",
        "protocol": OPENAI_PROTOCOL,
        "key_env": "LITELLM_KEY",
        "docs_url": "https://docs.litellm.ai/docs/proxy/quick_start",
        "pricing": PRICING_LOCAL,
        "notes": "Local/self-hosted LiteLLM proxy. Point base_url at your "
                 "proxy and give it the proxy master/virtual key.",
    },
    "router9": {
        "label": "9Router",
        "base_url": "http://localhost:20128",
        "protocol": ANTHROPIC_PROTOCOL,
        "key_env": None,
        "docs_url": "",
        "pricing": PRICING_UNKNOWN,
        "notes": "Local Anthropic-compatible router (as used by Claude Code "
                 "via ANTHROPIC_BASE_URL). Pricing and availability depend "
                 "on how the router is configured.",
    },
    "custom_openai": {
        "label": "Custom OpenAI-compatible",
        "base_url": "",
        "protocol": OPENAI_PROTOCOL,
        "key_env": None,
        "docs_url": "",
        "pricing": PRICING_UNKNOWN,
        "notes": "Any endpoint that answers GET {base}/models and POST "
                 "{base}/chat/completions with OpenAI semantics.",
    },
    "custom_anthropic": {
        "label": "Custom Anthropic-compatible",
        "base_url": "",
        "protocol": ANTHROPIC_PROTOCOL,
        "key_env": None,
        "docs_url": "",
        "pricing": PRICING_UNKNOWN,
        "notes": "Any endpoint that answers GET {base}/v1/models and POST "
                 "{base}/v1/messages with Anthropic semantics.",
    },
}

ALL_PRESETS = dict(PROVIDER_PRESETS)
ALL_PRESETS.update(GATEWAY_PRESETS)


class ConnectionError(Exception):
    """Raised for invalid connection configuration or unknown ids."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = int(status or 400)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _read_store():
    data = config.read_json(CONNECTIONS_FILE, {})
    if not isinstance(data, dict):
        return {"version": _STORE_VERSION, "connections": [], "health": {}}
    data.setdefault("version", _STORE_VERSION)
    if not isinstance(data.get("connections"), list):
        data["connections"] = []
    if not isinstance(data.get("health"), dict):
        data["health"] = {}
    return data


def _write_store(data):
    config.write_json(CONNECTIONS_FILE, data, mode=_STORE_MODE)


def _normalize_base_url(base_url):
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if "://" not in base:
        raise ConnectionError("base_url must include a scheme (http:// or https://)")
    parts = urlsplit(base)
    if parts.scheme not in ("http", "https"):
        raise ConnectionError("base_url scheme must be http or https")
    if parts.username or parts.password:
        raise ConnectionError("base_url must not embed credentials")
    if "{" in base or "}" in base:
        raise ConnectionError("base_url must not contain placeholders")
    # Anthropic-compatible callers append /v1/... themselves; a trailing
    # /v1 on an anthropic base would double it.
    if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ConnectionError("Plain http is only allowed for local endpoints "
                              "(localhost / 127.0.0.1 / ::1)")
    return base


def _clean_record(raw):
    """Return a normalized connection record (keeps the secret in memory)."""
    conn = {
        "id": (raw.get("id") or "").strip(),
        "type": raw.get("type") if raw.get("type") in (PROVIDER, GATEWAY) else GATEWAY,
        "name": (raw.get("name") or "").strip() or "Unnamed connection",
        "provider_id": (raw.get("provider_id") or "").strip(),
        "protocol": raw.get("protocol") if raw.get("protocol") in (OPENAI_PROTOCOL, ANTHROPIC_PROTOCOL) else OPENAI_PROTOCOL,
        "base_url": (raw.get("base_url") or "").strip(),
        "api_key": (raw.get("api_key") or "").strip(),
        "key_env": (raw.get("key_env") or "").strip() or None,
        "pricing": raw.get("pricing") if raw.get("pricing") in PRICING_VALUES else PRICING_UNKNOWN,
        "docs_url": (raw.get("docs_url") or "").strip(),
        "key_url": (raw.get("key_url") or "").strip(),
        "notes": (raw.get("notes") or "").strip(),
        "model_ids": [str(m).strip() for m in (raw.get("model_ids") or []) if str(m).strip()],
        "aliases": {str(k): str(v) for k, v in (raw.get("aliases") or {}).items() if str(k).strip() and str(v).strip()},
        "derived": bool(raw.get("derived")),
        "created_at": raw.get("created_at") or "",
        "updated_at": raw.get("updated_at") or "",
    }
    return conn


def _new_id():
    return "conn-" + uuid.uuid4().hex[:12]


def list_connections(include_derived=True):
    """All configured connections."""
    with _LOCK:
        conns = [_clean_record(c) for c in _read_store()["connections"]]
    return conns


def get_connection(connection_id, include_derived=True):
    for conn in list_connections(include_derived=include_derived):
        if conn["id"] == connection_id:
            return conn
    return None


def require_connection(connection_id):
    conn = get_connection(connection_id)
    if not conn:
        raise ConnectionError("Unknown connection: %s" % connection_id, status=404)
    return conn


# ---------------------------------------------------------------------------
# CRUD operations
# ---------------------------------------------------------------------------
def create_connection(payload):
    if (payload.get("type") or "").strip() not in (PROVIDER, GATEWAY):
        raise ConnectionError("type must be 'provider' or 'gateway'")
    with _LOCK:
        data = _read_store()
        preset = ALL_PRESETS.get((payload.get("preset_id") or "").strip())
        conn = _clean_record({
            "id": _new_id(),
            "type": payload.get("type") if payload.get("type") in (PROVIDER, GATEWAY) else None,
            "name": payload.get("name") or (preset or {}).get("label"),
            "provider_id": payload.get("provider_id") or (payload.get("preset_id") if preset else ""),
            "protocol": payload.get("protocol") or (preset or {}).get("protocol"),
            "base_url": payload.get("base_url") if (payload.get("base_url") or "").strip() else (preset or {}).get("base_url", ""),
            "api_key": payload.get("api_key") or "",
            "key_env": payload.get("key_env") if payload.get("key_env") is not None else (preset or {}).get("key_env"),
            "pricing": payload.get("pricing") or (preset or {}).get("pricing", PRICING_UNKNOWN),
            "docs_url": (preset or {}).get("docs_url", ""),
            "key_url": (preset or {}).get("key_url", ""),
            "notes": payload.get("notes") or (preset or {}).get("notes", ""),
            "model_ids": payload.get("model_ids") or [],
            "aliases": payload.get("aliases") or {},
            "created_at": config.now_iso(),
            "updated_at": config.now_iso(),
        })
        if conn["type"] not in (PROVIDER, GATEWAY):
            raise ConnectionError("type must be 'provider' or 'gateway'")
        if not conn["base_url"]:
            raise ConnectionError("base_url is required")
        if not conn["provider_id"]:
            raise ConnectionError("provider_id is required (e.g. 'openai' or a gateway label)")
        conn["base_url"] = _normalize_base_url(conn["base_url"])
        data["connections"].insert(0, conn)
        _write_store(data)
        return conn


def update_connection(connection_id, payload):
    with _LOCK:
        data = _read_store()
        target = None
        for raw in data["connections"]:
            if raw.get("id") == connection_id:
                target = raw
                break
        if target is None:
            raise ConnectionError("Unknown connection: %s" % connection_id, status=404)
        merged = dict(target)
        for field in ("name", "provider_id", "protocol", "base_url", "api_key",
                      "key_env", "pricing", "notes", "docs_url", "key_url",
                      "model_ids", "aliases", "type"):
            if field in payload:
                merged[field] = payload[field]
        merged["updated_at"] = config.now_iso()
        cleaned = _clean_record(merged)
        if not cleaned["base_url"]:
            raise ConnectionError("base_url is required")
        if cleaned["type"] not in (PROVIDER, GATEWAY):
            raise ConnectionError("type must be 'provider' or 'gateway'")
        cleaned["base_url"] = _normalize_base_url(cleaned["base_url"])
        idx = data["connections"].index(target)
        data["connections"][idx] = cleaned
        # A changed endpoint/key invalidates cached health.
        data["health"].pop(connection_id, None)
        _write_store(data)
        return cleaned


def delete_connection(connection_id):
    with _LOCK:
        data = _read_store()
        remaining = [c for c in data["connections"] if c.get("id") != connection_id]
        if len(remaining) == len(data["connections"]):
            raise ConnectionError("Unknown connection: %s" % connection_id, status=404)
        data["connections"] = remaining
        data["health"].pop(connection_id, None)
        _write_store(data)
        return {"deleted": connection_id}


# ---------------------------------------------------------------------------
# Secrets & public views
# ---------------------------------------------------------------------------
def resolve_key(conn):
    """Return ``(key, source)``; environment variable wins over the store."""
    env_name = conn.get("key_env") or ""
    if env_name:
        env_key = (os.environ.get(env_name) or "").strip()
        if env_key:
            return env_key, "env:" + env_name
    stored = (conn.get("api_key") or "").strip()
    if stored:
        return stored, "stored"
    return None, "none"


def public_view(conn):
    """Connection without secrets - safe to return to the frontend."""
    view = {k: v for k, v in conn.items() if k not in ("api_key",)}
    key, source = resolve_key(conn)
    view["auth"] = {
        "kind": "api_key",
        "configured": bool(key),
        "source": source,
        "hint": ("…" + key[-4:]) if key and len(key) > 4 else ("••••" if key else None),
        "env_var": conn.get("key_env") or None,
    }
    view["health"] = get_health(conn["id"])
    return view


def public_views(connections=None):
    return [public_view(c) for c in (connections if connections is not None else list_connections())]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
def get_health(connection_id):
    with _LOCK:
        health = _read_store()["health"].get(connection_id)
    if not health:
        return {"status": STATUS_UNKNOWN, "checked_at": None, "latency_ms": None, "detail": ""}
    return health


def set_health(connection_id, result):
    with _LOCK:
        data = _read_store()
        data["health"][connection_id] = result
        _write_store(data)


def effective_status(conn):
    """Lifecycle status for UI display, from the last real test."""
    conn = conn if isinstance(conn, dict) else require_connection(conn)
    if conn.get("derived") and not conn.get("base_url"):
        return STATUS_NOT_CONFIGURED
    key, _source = resolve_key(conn)
    health = get_health(conn["id"])
    status = health.get("status") or STATUS_UNKNOWN
    fresh = health.get("checked_at") and (
        time.time() - _parse_epoch(health.get("checked_at")) < _HEALTH_TTL)
    if not fresh:
        # Never present a stale success as a live connection.
        if status == STATUS_CONNECTED:
            status = STATUS_CONFIGURED
        elif status == STATUS_UNKNOWN:
            status = STATUS_CONFIGURED if (key or conn.get("base_url")) else STATUS_NOT_CONFIGURED
    if not key and status in (STATUS_UNKNOWN, STATUS_CONFIGURED):
        # No credential and no proof the endpoint is open: ask for one.
        preset = ALL_PRESETS.get(conn.get("provider_id") or "")
        if (preset or {}).get("key_env"):
            status = STATUS_AUTH_REQUIRED
    return status


def _parse_epoch(iso):
    try:
        import calendar
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Live connection test (the only source of health truth)
# ---------------------------------------------------------------------------
def _models_request(conn, key, timeout):
    """Issue the protocol's model-listing probe. Returns (status_code, body)."""
    base = conn["base_url"]
    if conn["protocol"] == ANTHROPIC_PROTOCOL:
        root = base[:-3] if base.endswith("/v1") else base
        path = root + "/v1/models"
        headers = {"accept": "application/json"}
        if key:
            headers["x-api-key"] = key
            headers["authorization"] = "Bearer " + key
        headers["anthropic-version"] = "2023-06-01"
    else:
        path = base + "/models"
        headers = {"accept": "application/json"}
        if key:
            headers["authorization"] = "Bearer " + key
    parts = urlsplit(path)
    host = parts.hostname or "localhost"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    if parts.scheme == "https":
        http_conn = http.client.HTTPSConnection(host, port, timeout=timeout)
    else:
        http_conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        http_conn.request("GET", target, headers=headers)
        response = http_conn.getresponse()
        body = response.read(65536)
        return response.status, body
    finally:
        try:
            http_conn.close()
        except Exception:
            pass


def test_connection(connection_id, timeout=10.0):
    """Probe the endpoint and persist honest health. Returns the health dict."""
    conn = require_connection(connection_id)
    key, key_source = resolve_key(conn)
    started = time.time()
    result = {"status": STATUS_UNKNOWN, "checked_at": config.now_iso(),
              "latency_ms": None, "detail": "", "model_count": None,
              "key_source": key_source}
    if not conn.get("base_url"):
        result["status"] = STATUS_NOT_CONFIGURED
        result["detail"] = "No base_url configured."
        if not conn.get("derived"):
            set_health(connection_id, result)
        return result
    try:
        status_code, body = _models_request(conn, key, timeout)
    except (socket.timeout, TimeoutError):
        result["status"] = STATUS_UNAVAILABLE
        result["detail"] = "Timed out after %.0fs." % timeout
    except (ConnectionError, OSError) as exc:
        result["status"] = STATUS_UNAVAILABLE
        result["detail"] = "Unreachable: %s" % type(exc).__name__
    except Exception as exc:  # keep testing non-fatal
        result["status"] = STATUS_UNKNOWN
        result["detail"] = "Probe failed: %s" % type(exc).__name__
    else:
        result["latency_ms"] = int((time.time() - started) * 1000)
        if status_code in (200, 201):
            result["status"] = STATUS_CONNECTED
            try:
                payload = json.loads(body.decode("utf-8", "replace"))
                items = payload.get("data") if isinstance(payload, dict) else payload
                if isinstance(items, list):
                    result["model_count"] = len(items)
                    result["detail"] = "%d models listed." % len(items)
                else:
                    result["detail"] = "Endpoint reachable."
            except Exception:
                result["detail"] = "Endpoint reachable (unparsed model list)."
        elif status_code in (401, 403):
            result["status"] = STATUS_AUTH_FAILED
            result["detail"] = "Authentication rejected (HTTP %d)." % status_code
        elif status_code == 404:
            # Endpoint is alive but does not implement model listing. That is
            # common for minimal gateways; the route itself may still work.
            result["status"] = STATUS_CONFIGURED
            result["detail"] = "Endpoint reachable; no model-listing API (HTTP 404). " \
                               "Register models manually."
        elif status_code == 429:
            result["status"] = STATUS_RATE_LIMITED
            result["detail"] = "Rate limited (HTTP 429)."
        else:
            result["status"] = STATUS_UNAVAILABLE
            result["detail"] = "HTTP %d from endpoint." % status_code
    if not conn.get("derived"):
        set_health(connection_id, result)
    return result


# ---------------------------------------------------------------------------
# Compatibility helpers (used by backend/compatibility.py)
# ---------------------------------------------------------------------------
def connections_for_protocol(protocol, conn_type=None):
    """Connections that speak the given protocol, optionally by type."""
    out = []
    for conn in list_connections():
        if conn["protocol"] != protocol:
            continue
        if conn_type and conn["type"] != conn_type:
            continue
        out.append(conn)
    return out
