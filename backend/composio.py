"""Composio integration for the Plugins tab.

Composio gives an agent one connection to 1,600+ app toolkits. This module is
the OS-side adapter; it never vendors the SDK and adds no third-party imports:

* **Keys** - consumer keys (``ck_...``, MCP / For You) and project API keys
  (``COMPOSIO_API_KEY``, Platform) are read from the environment first, then
  from a private ``data/composio.json`` (mode ``0600``). They are never returned
  by the HTTP API.
* **Catalog** - toolkits, tools and connected accounts come from the REST API
  (``/api/v3.1``) with a local cache. Without a key the panel uses the curated
  seed in ``public-plugins/composio/toolkits.json`` so setup still works.
* **MCP** - generates and optionally wires the streamable-HTTP MCP server
  (``https://connect.composio.dev/mcp``) into local agent configs, always with
  a timestamped backup, and is idempotent.
* **Plugin** - installs the portable pack in ``public-plugins/composio`` through
  the existing agent-agnostic plugin manager.

Credentials stay local; every outbound request is a direct HTTPS call with a
short timeout and no shell.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config
from . import plugins

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_DIR = os.path.join(REPO_ROOT, "public-plugins", "composio")
PACK_MANIFEST = os.path.join(PACK_DIR, "plugin.json")
PACK_TOOLKITS = os.path.join(PACK_DIR, "toolkits.json")
STORE_FILE = os.path.join(config.DATA_DIR, "composio.json")
CACHE_FILE = os.path.join(config.DATA_DIR, "composio_cache.json")
VENV_DIR = os.path.join(REPO_ROOT, ".cache", "composio-venv")
CLI_DIR = os.path.join(os.path.expanduser("~"), ".composio")

SOURCE_REPO = "https://github.com/ComposioHQ/composio"
DOCS_URL = "https://docs.composio.dev"
DASHBOARD_URL = "https://dashboard.composio.dev"
MCP_URL = "https://connect.composio.dev/mcp"
API_BASE = "https://backend.composio.dev/api/v3.1"
CONSUMER_HEADER = "x-consumer-api-key"
API_HEADER = "x-api-key"
CONSUMER_ENV = "COMPOSIO_CONSUMER_KEY"
API_ENV = "COMPOSIO_API_KEY"

CACHE_TTL_SECONDS = 6 * 60 * 60
HTTP_TIMEOUT = 25
MAX_PAYLOAD = 4 * 1024 * 1024
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")

_detect_cache = {"at": 0.0, "data": None}


class ComposioError(Exception):
    def __init__(self, message, code="composio_error", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Key storage
# ---------------------------------------------------------------------------
def _read_store():
    data = config.read_json(STORE_FILE, default=None)
    return data if isinstance(data, dict) else {}


def _write_store(data):
    os.makedirs(os.path.dirname(STORE_FILE), exist_ok=True)
    config.write_json(STORE_FILE, data)
    try:
        os.chmod(STORE_FILE, 0o600)
    except OSError:
        pass


def _clean_key(value, kind):
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) > 300 or any(ch in value for ch in ("\n", "\r", "\x00", " ")):
        raise ComposioError("That %s key contains invalid characters." % kind, "key_invalid")
    return value


def keys():
    """Return the effective keys without ever exposing their values."""
    store = _read_store()
    env_consumer = (os.environ.get(CONSUMER_ENV) or "").strip()
    env_api = (os.environ.get(API_ENV) or "").strip()
    consumer = env_consumer or str(store.get("consumer_key") or "")
    api = env_api or str(store.get("api_key") or "")
    return {
        "consumer": consumer,
        "api": api,
        "consumer_source": (CONSUMER_ENV if env_consumer else ("store" if consumer else "none")),
        "api_source": (API_ENV if env_api else ("store" if api else "none")),
        "updated_at": store.get("updated_at") or "",
    }


def public_keys():
    data = keys()
    return {
        "consumer_key_set": bool(data["consumer"]),
        "api_key_set": bool(data["api"]),
        "consumer_key_source": data["consumer_source"],
        "api_key_source": data["api_source"],
        "updated_at": data["updated_at"],
    }


def save_keys(consumer_key=None, api_key=None):
    store = _read_store()
    changed = False
    if consumer_key is not None:
        value = _clean_key(consumer_key, "consumer")
        if value:
            store["consumer_key"] = value
            changed = True
    if api_key is not None:
        value = _clean_key(api_key, "api")
        if value:
            store["api_key"] = value
            changed = True
    if not changed:
        raise ComposioError("Enter a consumer key (ck_...) or a project API key.", "key_missing")
    store["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_store(store)
    return public_keys()


def clear_keys():
    if os.path.isfile(STORE_FILE):
        try:
            os.remove(STORE_FILE)
        except OSError as exc:
            raise ComposioError("Could not remove the stored keys: %s" % exc, "key_clear_failed", 500)
    return public_keys()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _http_json(path, params=None, key_kind="api"):
    data = keys()
    key = data["api"] if key_kind == "api" else data["consumer"]
    if not key:
        raise ComposioError(
            "Add a Composio API key in the Plugins tab first.", "not_configured", 409)
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Accept": "application/json",
        "User-Agent": "DENNIS-OS-Composio/1.0",
        API_HEADER if key_kind == "api" else CONSUMER_HEADER: key,
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            body = response.read(MAX_PAYLOAD + 1)
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        message = _server_message(body) or ("Composio returned HTTP %s." % exc.code)
        status = 409 if exc.code in (401, 403) else (502 if exc.code >= 500 else 400)
        code = "unauthorized" if exc.code in (401, 403) else "api_error"
        raise ComposioError(message, code, status)
    except urllib.error.URLError as exc:
        raise ComposioError("Cannot reach Composio: %s" % exc.reason, "unreachable", 502)
    except TimeoutError:
        raise ComposioError("Composio did not respond in time.", "timeout", 504)
    if len(body) > MAX_PAYLOAD:
        raise ComposioError("Composio returned more data than the panel can display.", "too_large", 413)
    try:
        parsed = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        raise ComposioError("Composio returned an unreadable response.", "bad_response", 502)
    return parsed if isinstance(parsed, (dict, list)) else {}


def _server_message(body):
    try:
        parsed = json.loads((body or b"").decode("utf-8", "replace"))
    except ValueError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    error = parsed.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("slug") or "")[:300]
    if isinstance(error, str):
        return error[:300]
    return str(parsed.get("message") or "")[:300]


def _items(parsed):
    if isinstance(parsed, list):
        return parsed
    if not isinstance(parsed, dict):
        return []
    for key in ("items", "toolkits", "tools", "data", "results"):
        value = parsed.get(key)
        if isinstance(value, list):
            return value
    return []


def _paginate(path, params, max_pages=8):
    """Follow Composio's cursor pagination, stopping on a short/absent cursor."""
    items, cursor, pages = [], "", 0
    while pages < max_pages:
        query = dict(params)
        query["limit"] = min(int(query.get("limit") or 100), 100)
        if cursor:
            query["cursor"] = cursor
        parsed = _http_json(path, query)
        items.extend(_items(parsed))
        cursor = ""
        if isinstance(parsed, dict):
            cursor = str(parsed.get("next_cursor") or parsed.get("next") or "")
        pages += 1
        if not cursor:
            break
    return items


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
def seed_catalog():
    data = config.read_json(PACK_TOOLKITS, default=None)
    if not isinstance(data, dict):
        return {"toolkits": [], "meta_tools": []}
    toolkits = []
    for entry in data.get("toolkits") or []:
        if not isinstance(entry, dict) or not entry.get("slug"):
            continue
        item = dict(entry)
        auth = item.get("auth")
        if isinstance(auth, str):
            item["auth"] = [auth] if auth else []
        elif not isinstance(auth, list):
            item["auth"] = []
        toolkits.append(item)
    meta = [t for t in data.get("meta_tools") or [] if isinstance(t, dict) and t.get("slug")]
    return {"toolkits": toolkits, "meta_tools": meta}


def _doc_url(slug):
    return "%s/toolkits/%s" % (DOCS_URL, slug)


def _normalize_toolkit(item):
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    slug = str(item.get("slug") or meta.get("slug") or "").strip().lower()
    if not slug:
        return None
    categories = item.get("categories") or meta.get("categories") or []
    if isinstance(categories, str):
        categories = [categories]
    categories = [str(c) for c in categories if c]
    auth = item.get("auth_schemes") or meta.get("auth_schemes") or []
    if isinstance(auth, str):
        auth = [auth]
    count = item.get("tools_count", meta.get("tools_count"))
    return {
        "slug": slug,
        "name": str(item.get("name") or meta.get("name") or slug),
        "description": str(meta.get("description") or item.get("description") or "")[:400],
        "logo": str(meta.get("logo") or item.get("logo") or ""),
        "category": categories[0] if categories else "Other",
        "categories": categories,
        "tools_count": count if isinstance(count, int) else None,
        "auth": [str(a) for a in auth if isinstance(a, (str, int, float))][:4],
        "no_auth": bool(item.get("no_auth")),
        "docs": _doc_url(slug),
        "source": "live",
    }


def _normalize_tool(item):
    toolkit = item.get("toolkit") if isinstance(item.get("toolkit"), dict) else {}
    return {
        "slug": str(item.get("slug") or ""),
        "name": str(item.get("name") or item.get("slug") or ""),
        "description": str(item.get("description") or "")[:600],
        "deprecated": bool(item.get("is_deprecated") or item.get("deprecated")),
        "toolkit": str(toolkit.get("slug") or ""),
    }


def _normalize_connection(item):
    toolkit = item.get("toolkit") if isinstance(item.get("toolkit"), dict) else {}
    return {
        "id": str(item.get("id") or item.get("connected_account_id") or ""),
        "toolkit": str(toolkit.get("slug") or item.get("toolkit_slug") or ""),
        "status": str(item.get("status") or ""),
        "created_at": str(item.get("created_at") or ""),
    }


def _read_cache():
    data = config.read_json(CACHE_FILE, default=None)
    return data if isinstance(data, dict) else {}


def _write_cache(payload):
    os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
    config.write_json(CACHE_FILE, payload)
    try:
        os.chmod(CACHE_FILE, 0o600)
    except OSError:
        pass


def _cache_age(cache):
    fetched = cache.get("fetched_at")
    if not isinstance(fetched, (int, float)):
        return None
    return max(0, int(time.time() - fetched))


def _live_toolkits():
    items = []
    for raw in _paginate("/toolkits", {"limit": 100}, max_pages=20):
        if not isinstance(raw, dict):
            continue
        item = _normalize_toolkit(raw)
        if item:
            items.append(item)
    items.sort(key=lambda t: (t["category"].casefold(), t["name"].casefold()))
    return items


def _live_tools(slug):
    tools = []
    for raw in _paginate("/tools", {"toolkit_slug": slug, "limit": 100}, max_pages=5):
        if isinstance(raw, dict):
            tool = _normalize_tool(raw)
            if tool["slug"]:
                tools.append(tool)
    return tools


def _live_connections():
    return [
        _normalize_connection(raw)
        for raw in _paginate("/connected_accounts", {"limit": 100}, max_pages=5)
        if isinstance(raw, dict)
    ]


def catalog(force=False):
    """Toolkits from the live API, else cache, else the curated seed."""
    data = keys()
    warning = ""
    cache = _read_cache()
    age = _cache_age(cache)

    if not data["api"]:
        seed = seed_catalog()
        warning = ("Add a project API key to load the live catalog. "
                   "Showing the curated seed.")
        return _with_seed(seed["toolkits"], "seed", warning, age, cache.get("fetched_at"))

    if not force and cache.get("toolkits") and age is not None and age < CACHE_TTL_SECONDS:
        return _with_seed(cache["toolkits"], "cache", "", age, cache.get("fetched_at"))

    try:
        toolkits = _live_toolkits()
    except ComposioError as exc:
        if cache.get("toolkits"):
            return _with_seed(cache["toolkits"], "cache",
                              "Live refresh failed (%s). Showing cached catalog." % exc,
                              age, cache.get("fetched_at"))
        seed = seed_catalog()
        return _with_seed(seed["toolkits"], "seed",
                          "Live refresh failed (%s). Showing the curated seed." % exc,
                          age, cache.get("fetched_at"))
    if not toolkits:
        seed = seed_catalog()
        return _with_seed(seed["toolkits"], "seed",
                          "The live catalog is empty. Showing the curated seed.",
                          age, cache.get("fetched_at"))

    _write_cache({
        "toolkits": toolkits,
        "fetched_at": int(time.time()),
        "fetched_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    return _with_seed(toolkits, "live", "", 0, int(time.time()))


def _with_seed(toolkits, source, warning, age, fetched_at):
    normalized = []
    for item in toolkits:
        if not isinstance(item, dict) or not item.get("slug"):
            continue
        entry = dict(item)
        entry.setdefault("category", "Other")
        entry.setdefault("docs", _doc_url(entry["slug"]))
        entry.setdefault("source", source)
        normalized.append(entry)
    normalized.sort(key=lambda t: (str(t.get("category") or "Other").casefold(),
                                   str(t.get("name") or t["slug"]).casefold()))
    counts = {}
    for item in normalized:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    categories = [{"name": name, "count": count}
                  for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return {
        "toolkits": normalized,
        "categories": categories,
        "count": len(normalized),
        "source": source,
        "warning": warning,
        "cache_age_seconds": age,
        "fetched_at": fetched_at,
    }


def toolkit(slug):
    slug = (slug or "").strip().lower()
    if not slug or not NAME_RE.match(slug):
        raise ComposioError("A toolkit slug is required.", "slug_invalid")
    data = keys()
    catalog_data = catalog()
    entry = next((t for t in catalog_data["toolkits"] if t["slug"] == slug), None)
    if entry is None:
        entry = {
            "slug": slug, "name": slug, "category": "Other",
            "description": "", "docs": _doc_url(slug),
            "source": catalog_data["source"],
        }
    result = {
        "toolkit": entry,
        "tools": [],
        "source": "seed",
        "warning": "",
        "connections": [],
    }
    seed = seed_catalog()
    if slug == "composio":
        result["tools"] = [dict(t, toolkit="composio") for t in seed["meta_tools"]]
        result["source"] = "meta"
        return result
    if not data["api"]:
        result["warning"] = "Add a project API key to load this toolkit's tools."
        return result
    try:
        result["tools"] = _live_tools(slug)
        result["source"] = "live"
        try:
            result["connections"] = [c for c in _live_connections() if c["toolkit"] == slug]
        except ComposioError:
            result["connections"] = []
    except ComposioError as exc:
        result["warning"] = "Could not load tools: %s" % exc
    return result


def connections():
    if not keys()["api"]:
        return {"connections": [], "warning": "Add a project API key to list connected accounts."}
    try:
        return {"connections": _live_connections(), "warning": ""}
    except ComposioError as exc:
        return {"connections": [], "warning": str(exc)}


# ---------------------------------------------------------------------------
# Local runtime detection (CLI + Python SDK)
# ---------------------------------------------------------------------------
def _run_version(command):
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr or "").strip().splitlines()[-1][:120] or "installed"


def _sdk_status():
    """Read the SDK version from the venv without importing it (fast)."""
    if not os.path.isdir(VENV_DIR):
        return {"installed": False, "version": "", "venv": ""}
    patterns = (
        os.path.join(VENV_DIR, "lib", "python*", "site-packages", "composio-*.dist-info"),
        os.path.join(VENV_DIR, "Lib", "site-packages", "composio-*.dist-info"),
    )
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            version = ""
            try:
                with open(os.path.join(path, "METADATA"), encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.startswith("Version:"):
                            version = line.split(":", 1)[1].strip()[:40]
                            break
            except OSError:
                continue
            return {"installed": True, "version": version, "venv": VENV_DIR}
    return {"installed": False, "version": "", "venv": VENV_DIR}


def runtime_status(force=False):
    if not force and _detect_cache["data"] and time.time() - _detect_cache["at"] < 60:
        return dict(_detect_cache["data"])
    cli = shutil.which("composio") or ""
    if not cli and os.path.isfile(os.path.join(CLI_DIR, "composio")):
        cli = os.path.join(CLI_DIR, "composio")
    cli_version = _run_version([cli, "--version"]) if cli else None
    data = {
        "cli": {"installed": bool(cli), "path": cli, "version": cli_version or ""},
        "sdk": _sdk_status(),
        "install": {
            "cli": "curl -fsSL https://composio.dev/install | sh && composio login",
            "sdk": "python3 -m venv .cache/composio-venv && .cache/composio-venv/bin/pip install composio",
        },
    }
    _detect_cache["data"] = data
    _detect_cache["at"] = time.time()
    return dict(data)


# ---------------------------------------------------------------------------
# MCP definitions + wiring
# ---------------------------------------------------------------------------
def _mcp_key(client):
    data = keys()
    if data["consumer"]:
        return data["consumer"]
    if client == "opencode":
        return "{env:%s}" % CONSUMER_ENV
    if client == "cursor":
        return "${env:%s}" % CONSUMER_ENV
    return "${%s}" % CONSUMER_ENV


def mcp_block(client="generic"):
    client = (client or "generic").strip().lower()
    key = _mcp_key(client)
    headers = {CONSUMER_HEADER: key}
    if client == "opencode":
        return {"mcp": {"composio": {
            "type": "remote", "url": MCP_URL, "headers": headers, "enabled": True}}}
    if client == "gemini-cli":
        return {"mcpServers": {"composio": {"httpUrl": MCP_URL, "headers": headers}}}
    if client == "antigravity":
        return {"mcpServers": {"composio": {"serverUrl": MCP_URL, "headers": headers}}}
    if client == "cursor":
        return {"mcpServers": {"composio": {"url": MCP_URL, "headers": headers}}}
    return {"mcpServers": {"composio": {
        "type": "http", "url": MCP_URL, "headers": headers}}}


def mcp_snippet(client="generic"):
    client = (client or "generic").strip().lower()
    if client == "codex":
        text = ("[mcp_servers.composio]\n"
                "url = \"%s\"\n"
                "http_headers = { \"%s\" = \"%s\" }\n"
                % (MCP_URL, CONSUMER_HEADER, _mcp_key(client)))
        return {"client": client, "language": "toml", "snippet": text, "path": ""}
    target = _CLIENTS.get(client)
    return {
        "client": client,
        "language": "json",
        "snippet": json.dumps(mcp_block(client), indent=2) + "\n",
        "path": target["path"] if target else "",
    }


_CLIENTS = {
    "opencode": {
        "label": "OpenCode",
        "path": os.path.join(os.path.expanduser("~"), ".config", "opencode", "opencode.jsonc"),
        "format": "jsonc",
        "nested": ["mcp"],
    },
    "claude-code": {
        "label": "Claude Code",
        "path": os.path.join(os.path.expanduser("~"), ".claude.json"),
        "format": "json",
        "nested": ["mcpServers"],
    },
    "gemini-cli": {
        "label": "Gemini CLI",
        "path": os.path.join(os.path.expanduser("~"), ".gemini", "settings.json"),
        "format": "json",
        "nested": ["mcpServers"],
    },
    "antigravity": {
        "label": "Antigravity",
        "path": os.path.join(os.path.expanduser("~"), ".gemini", "config", "mcp_config.json"),
        "format": "json",
        "nested": ["mcpServers"],
    },
    "cursor": {
        "label": "Cursor",
        "path": os.path.join(os.path.expanduser("~"), ".cursor", "mcp.json"),
        "format": "json",
        "nested": ["mcpServers"],
    },
}


def clients():
    out = []
    for client_id, spec in _CLIENTS.items():
        path = spec["path"]
        wired = False
        try:
            data = _load_config(path, spec["format"])
            node = data
            for key in spec["nested"]:
                node = node.get(key) if isinstance(node, dict) else None
            wired = isinstance(node, dict) and "composio" in node
        except ComposioError:
            wired = False
        out.append({
            "id": client_id,
            "label": spec["label"],
            "path": path,
            "present": os.path.exists(path) or os.path.isdir(os.path.dirname(path)),
            "wired": wired,
        })
    return out


def _load_config(path, fmt):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise ComposioError("Cannot read %s: %s" % (path, exc), "config_unreadable", 500)
    if not text.strip():
        return {}
    if fmt == "jsonc":
        text = _strip_jsonc(text)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ComposioError("%s is not valid JSON: %s" % (path, exc), "config_invalid", 409)
    if not isinstance(data, dict):
        raise ComposioError("%s must contain a JSON object." % path, "config_invalid", 409)
    return data


def _strip_jsonc(text):
    out = []
    index, length = 0, len(text)
    in_string = escaped = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue
        out.append(char)
        index += 1
    return _drop_trailing_commas("".join(out))


def _drop_trailing_commas(text):
    out = []
    index, length = 0, len(text)
    in_string = escaped = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == ",":
            lookahead = index + 1
            while lookahead < length and text[lookahead] in " \t\r\n":
                lookahead += 1
            if lookahead < length and text[lookahead] in "}]":
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out)


def _backup(path):
    if not os.path.isfile(path):
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = "%s.dennis-backup-%s" % (path, stamp)
    try:
        shutil.copy2(path, target)
    except OSError:
        return ""
    return target


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".composio.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _wire_one(client_id, remove=False):
    spec = _CLIENTS[client_id]
    path, fmt = spec["path"], spec["format"]
    try:
        data = _load_config(path, fmt)
    except ComposioError as exc:
        return {"client": client_id, "ok": False, "error": str(exc), "path": path}
    node = data
    for key in spec["nested"][:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    container_key = spec["nested"][-1]
    container = node.get(container_key)
    if not isinstance(container, dict):
        container = {}
        node[container_key] = container
    entry = mcp_block(client_id)
    for key in spec["nested"]:
        entry = entry[key]
    value = entry["composio"]

    if remove:
        if "composio" not in container:
            return {"client": client_id, "ok": True, "action": "absent", "path": path}
        container.pop("composio", None)
        action = "removed"
    else:
        if container.get("composio") == value:
            return {"client": client_id, "ok": True, "action": "unchanged", "path": path}
        existed = "composio" in container
        container["composio"] = value
        action = "updated" if existed else "added"

    backup = _backup(path)
    text = json.dumps(data, indent=2) + "\n"
    try:
        _atomic_write(path, text)
        os.chmod(path, 0o600)
    except OSError as exc:
        return {"client": client_id, "ok": False, "error": str(exc), "path": path}
    return {"client": client_id, "ok": True, "action": action, "path": path, "backup": backup}


def wire(clients_list=None, remove=False):
    chosen = [c for c in (clients_list or list(_CLIENTS)) if c in _CLIENTS]
    if not chosen:
        raise ComposioError("Select at least one MCP client.", "client_missing")
    results = [_wire_one(client_id, remove=remove) for client_id in chosen]
    return {
        "results": results,
        "ok": all(item.get("ok") for item in results),
        "clients": clients(),
    }


# ---------------------------------------------------------------------------
# Plugin pack install
# ---------------------------------------------------------------------------
def plugin_status():
    try:
        summary = plugins.read_plugin("composio")
    except plugins.PluginError:
        return {"installed": False}
    return {
        "installed": True,
        "name": summary["name"],
        "version": summary["version"],
        "tier": summary["tier"],
        "enabled": summary["enabled"],
        "agents": summary["agents"],
        "path": summary["path"],
        "skills": [s["name"] for s in summary["skills"]],
    }


def install_plugin(force=False, link_agents=None):
    if not os.path.isfile(PACK_MANIFEST):
        raise ComposioError("The bundled Composio pack is missing.", "pack_missing", 500)
    summary = plugins.install_plugin(
        PACK_DIR, name="composio", force=force, tier="public", source_url=SOURCE_REPO)
    selected = link_agents
    if selected is None:
        selected = []
        for agent, root in plugins.AGENT_SKILL_DIRS.items():
            parent = os.path.dirname(root)
            if os.path.isdir(root) or os.path.isdir(parent):
                selected.append(agent)
        if not selected:
            selected = ["generic"]
    links = plugins.install_skills("composio", selected_agents=selected, force=force)
    return {"plugin": summary, "links": links, "status": plugin_status()}


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def overview():
    catalog_data = catalog()
    key_state = public_keys()
    data = keys()
    warning = catalog_data.get("warning") or ""
    conns = {"connections": [], "warning": ""}
    if data["api"] and catalog_data.get("source") == "live":
        conns = connections()
    if conns.get("warning") and not warning:
        warning = conns["warning"]
    return {
        "status": key_state,
        "catalog": catalog_data,
        "connections": conns.get("connections", []),
        "warning": warning,
        "mcp": {
            "url": MCP_URL,
            "header": CONSUMER_HEADER,
            "snippet": mcp_snippet("generic"),
        },
        "clients": clients(),
        "plugin": plugin_status(),
        "runtime": runtime_status(),
        "meta_tools": seed_catalog()["meta_tools"],
        "links": {
            "dashboard": DASHBOARD_URL,
            "docs": DOCS_URL,
            "repository": SOURCE_REPO,
            "toolkits": DOCS_URL + "/toolkits",
            "api_keys": DASHBOARD_URL + "/~/project/settings/api-keys",
        },
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
