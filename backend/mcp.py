"""MCP (Model Context Protocol) connector subsystem.

Two jobs, both stdlib-only so the OS keeps its zero-dependency backend:

1. **Discovery** - search the official MCP Registry REST API
   (``GET /v0.1/servers``) so the operator can find public MCP servers
   online, plus a small curated fallback catalog when the registry is
   unreachable.

2. **Connect + policy + OAuth** - turn a discovered or hand-entered server
   into an authorized connector, following the MCP authorization spec:

   - OAuth 2.0 Protected Resource Metadata (RFC 9728) for auth-server
     discovery,
   - OAuth 2.0 Authorization Server Metadata (RFC 8414) for endpoints,
   - OAuth 2.0 Dynamic Client Registration (RFC 7591) when offered
     (pre-registered / Client ID Metadata Documents are honoured first),
   - OAuth 2.1 authorization-code + PKCE (RFC 7636) and Resource
     Indicators (RFC 8707).

   Every connector must clear the OS **policy gate** (scopes, tool
   allowlist, data access, risk tier, human approval) before it is usable.
   Tokens are never returned to the browser; only ``has_token`` and an
   expiry are exposed.

All state lives in ``data/mcp_servers.json`` (owner-only on disk).
"""

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config

MCP_FILE = os.path.join(config.DATA_DIR, "mcp_servers.json")

REGISTRY_BASE = os.environ.get(
    "DENNIS_MCP_REGISTRY", "https://registry.modelcontextprotocol.io")
REGISTRY_TIMEOUT = float(os.environ.get("DENNIS_MCP_TIMEOUT", "8"))

# ---------------------------------------------------------------------------
# Policy model
# ---------------------------------------------------------------------------

POLICY_PRESETS = {
    "read-only": {
        "label": "Read-only",
        "description": "Read resources and list tools only. No writes, no side effects.",
        "allow_write": False,
        "allow_network": True,
        "require_approval": True,
        "risk_level": "low",
        "data_classification": "internal",
    },
    "restricted": {
        "label": "Restricted",
        "description": "Read + an explicit tool allowlist. Writes require approval.",
        "allow_write": False,
        "allow_network": True,
        "require_approval": True,
        "risk_level": "medium",
        "data_classification": "internal",
    },
    "trusted": {
        "label": "Trusted",
        "description": "Full tool access including writes. Use only for first-party servers.",
        "allow_write": True,
        "allow_network": True,
        "require_approval": False,
        "risk_level": "high",
        "data_classification": "confidential",
    },
}

DEFAULT_POLICY = {
    "preset": "restricted",
    "scopes": ["mcp:tools"],
    "tools": [],
    "allow_write": False,
    "allow_network": True,
    "require_approval": True,
    "risk_level": "medium",
    "data_classification": "internal",
    "accepted": False,
}

VALID_TRANSPORTS = ("streamable-http", "sse", "stdio")
VALID_AUTH_TYPES = ("oauth", "api_key", "none")
VALID_STATUS = ("policy_review", "authorizing", "connected", "error")


def default_policy(preset="restricted"):
    policy = dict(DEFAULT_POLICY)
    policy["preset"] = preset if preset in POLICY_PRESETS else "restricted"
    policy.update(POLICY_PRESETS.get(policy["preset"], {}))
    return policy


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _new_id(prefix="mcp"):
    return f"{prefix}-{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_servers():
    data = config.read_json(MCP_FILE, default=None)
    if isinstance(data, dict) and isinstance(data.get("servers"), list):
        return data
    return {"servers": []}


def save_servers(store):
    config.write_json(MCP_FILE, store)
    try:
        os.chmod(MCP_FILE, 0o600)
    except OSError:
        pass


def get_server(server_id):
    for s in load_servers()["servers"]:
        if s.get("id") == server_id:
            return s
    return None


def _upsert(server):
    store = load_servers()
    for i, s in enumerate(store["servers"]):
        if s.get("id") == server["id"]:
            store["servers"][i] = server
            break
    else:
        store["servers"].insert(0, server)
    save_servers(store)
    return server


def _public(server):
    """Browser-safe view: strips tokens/secrets, exposes status only."""
    policy = server.get("policy") or default_policy()
    oauth = server.get("oauth") or {}
    out = {
        "id": server.get("id"),
        "name": server.get("name"),
        "title": server.get("title") or server.get("name"),
        "description": server.get("description", ""),
        "transport": server.get("transport"),
        "url": server.get("url", ""),
        "command": server.get("command", ""),
        "auth_type": server.get("auth_type"),
        "status": server.get("status", "policy_review"),
        "source": server.get("source", "custom"),
        "repository": server.get("repository", ""),
        "policy": policy,
        "scopes": oauth.get("scopes", []),
        "authorization_endpoint": oauth.get("authorization_endpoint", ""),
        "token_endpoint": oauth.get("token_endpoint", ""),
        "has_token": bool(oauth.get("access_token")),
        "token_expires_at": oauth.get("expires_at"),
        "client_id": oauth.get("client_id", ""),
        "last_error": server.get("last_error", ""),
        "created_at": server.get("created_at"),
        "updated_at": server.get("updated_at"),
    }
    if oauth.get("access_token"):
        out["access_token_preview"] = "••••" + oauth["access_token"][-4:]
    return out


def list_public():
    return [_public(s) for s in load_servers()["servers"]]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _http_json(url, method="GET", data=None, headers=None, timeout=None):
    body = None
    hdrs = {"Accept": "application/json", "User-Agent": "DENNIS-Agentic-OS"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=timeout or REGISTRY_TIMEOUT) as resp:
        raw = resp.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _same_host(paths, base):
    """Return the first URL under ``base``'s origin, or ``base`` if none apply."""
    parsed = urllib.parse.urlparse(base)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for p in paths:
        return origin + p
    return base


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

FALLBACK_CATALOG = [
    {
        "id": "io.modelcontextprotocol/filesystem",
        "title": "Filesystem",
        "description": "Secure file operations with configurable access controls.",
        "version": "0.6.2",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
        "auth_type": "none",
        "repository": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "io.modelcontextprotocol/github",
        "title": "GitHub",
        "description": "Repositories, issues, pull requests and code search.",
        "version": "0.6.2",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "auth_type": "api_key",
        "repository": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "io.modelcontextprotocol/slack",
        "title": "Slack",
        "description": "Channel history and messaging for workspaces.",
        "version": "0.6.2",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "auth_type": "api_key",
        "repository": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "io.modelcontextprotocol/sentry",
        "title": "Sentry",
        "description": "Retrieve and analyse issues from Sentry.",
        "version": "0.6.2",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sentry"],
        "auth_type": "api_key",
        "repository": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "id": "io.modelcontextprotocol/notion",
        "title": "Notion",
        "description": "Read and search Notion pages and databases.",
        "version": "1.0.0",
        "transport": "streamable-http",
        "url": "https://mcp.notion.com/mcp",
        "auth_type": "oauth",
        "repository": "https://developers.notion.com/docs/mcp",
    },
    {
        "id": "com.linear/linear",
        "title": "Linear",
        "description": "Issues, projects and cycles for your Linear workspace.",
        "version": "1.0.0",
        "transport": "streamable-http",
        "url": "https://mcp.linear.app/mcp",
        "auth_type": "oauth",
        "repository": "https://linear.app/docs/mcp",
    },
    {
        "id": "com.stripe/stripe",
        "title": "Stripe",
        "description": "Payments, customers and billing objects.",
        "version": "1.0.0",
        "transport": "streamable-http",
        "url": "https://mcp.stripe.com",
        "auth_type": "oauth",
        "repository": "https://docs.stripe.com/mcp",
    },
    {
        "id": "com.context7/context7",
        "title": "Context7",
        "description": "Up-to-date library documentation for coding agents.",
        "version": "1.0.0",
        "transport": "streamable-http",
        "url": "https://mcp.context7.com/mcp",
        "auth_type": "none",
        "repository": "https://context7.com",
    },
]


def _simplify_registry_server(entry):
    server = entry.get("server", entry)
    remotes = server.get("remotes") or []
    url = ""
    transport = ""
    if remotes:
        remote = remotes[0]
        url = remote.get("url", "")
        transport = remote.get("type") or remote.get("transport") or "streamable-http"
    packages = server.get("packages") or []
    command = ""
    args = []
    stdio = False
    if packages:
        pkg = packages[0]
        transport_cfg = pkg.get("transport") or {}
        if transport_cfg.get("type") == "stdio":
            stdio = True
            command = pkg.get("identifier", "")
            args = transport_cfg.get("args") or []
    if stdio:
        transport = "stdio"
    elif not transport:
        transport = "stdio" if not url else "streamable-http"
    return {
        "id": server.get("name", ""),
        "title": server.get("title") or server.get("name", ""),
        "description": server.get("description", ""),
        "version": server.get("version", ""),
        "transport": transport,
        "url": url,
        "command": command,
        "args": args,
        "auth_type": "oauth" if url else "none",
        "repository": (server.get("repository") or {}).get("url", "")
                        if isinstance(server.get("repository"), dict)
                        else server.get("repository", ""),
        "source": "registry",
    }


def discover(query="", limit=20):
    """Search the official MCP registry; fall back to the curated catalog."""
    query = (query or "").strip()
    limit = max(1, min(int(limit or 20), 50))
    params = {"limit": str(limit)}
    if query:
        params["search"] = query
    url = f"{REGISTRY_BASE}/v0.1/servers?" + urllib.parse.urlencode(params)
    try:
        payload = _http_json(url)
        results = [
            _simplify_registry_server(e)
            for e in (payload.get("servers") or [])
        ]
        results = [r for r in results if r.get("id")]
        if results:
            return {"results": results, "source": "registry", "error": ""}
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        error = f"Registry unreachable ({exc}); showing offline catalog."
    else:
        error = ""
    catalog = FALLBACK_CATALOG
    if query:
        q = query.lower()
        catalog = [
            c for c in catalog
            if q in c["title"].lower()
            or q in c["id"].lower()
            or q in c["description"].lower()
        ]
    return {"results": catalog[:limit], "source": "builtin", "error": error}


# ---------------------------------------------------------------------------
# Connector creation
# ---------------------------------------------------------------------------

def create_server(payload):
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("A connector name is required")
    transport = (payload.get("transport") or "streamable-http").strip()
    if transport not in VALID_TRANSPORTS:
        raise ValueError(f"transport must be one of {', '.join(VALID_TRANSPORTS)}")
    auth_type = (payload.get("auth_type") or "oauth").strip()
    if auth_type not in VALID_AUTH_TYPES:
        raise ValueError(f"auth_type must be one of {', '.join(VALID_AUTH_TYPES)}")

    url = (payload.get("url") or "").strip()
    command = (payload.get("command") or "").strip()
    if transport == "stdio" and not command:
        raise ValueError("A command is required for a stdio connector")
    if transport != "stdio" and not url:
        raise ValueError("An endpoint URL is required for an HTTP connector")
    if url and not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("Endpoint URL must be http(s)")

    args = payload.get("args") or []
    if isinstance(args, str):
        args = [a for a in args.replace(",", " ").split() if a]

    preset = payload.get("preset") or "restricted"
    policy = default_policy(preset)
    server = {
        "id": payload.get("id") or _new_id(),
        "name": name,
        "title": payload.get("title") or name,
        "description": (payload.get("description") or "").strip(),
        "transport": transport,
        "url": url,
        "command": command,
        "args": args,
        "env": payload.get("env") or {},
        "auth_type": auth_type,
        "source": payload.get("source") or "custom",
        "repository": payload.get("repository", ""),
        "status": "policy_review",
        "policy": policy,
        "oauth": {},
        "last_error": "",
        "created_at": _now(),
        "updated_at": _now(),
    }
    _upsert(server)
    return server


def update_policy(server_id, policy_patch):
    server = get_server(server_id)
    if not server:
        raise KeyError(server_id)
    policy = dict(server.get("policy") or default_policy())
    preset = policy_patch.get("preset")
    if preset:
        policy.update(default_policy(preset))
    for key in ("scopes", "tools", "allow_write", "allow_network",
                "require_approval", "risk_level", "data_classification",
                "accepted"):
        if key in policy_patch:
            policy[key] = policy_patch[key]
    if not isinstance(policy.get("scopes"), list):
        policy["scopes"] = [s for s in str(policy.get("scopes", "")).split() if s]
    if not isinstance(policy.get("tools"), list):
        policy["tools"] = [s for s in str(policy.get("tools", "")).split() if s]
    policy["accepted"] = bool(policy.get("accepted"))
    server["policy"] = policy
    server["updated_at"] = _now()
    if server.get("status") == "connected":
        server["status"] = "connected"
    elif policy["accepted"]:
        server["status"] = "authorizing" if server.get("auth_type") == "oauth" else "policy_review"
    _upsert(server)
    return server


def delete_server(server_id):
    store = load_servers()
    before = len(store["servers"])
    store["servers"] = [s for s in store["servers"] if s.get("id") != server_id]
    if len(store["servers"]) == before:
        return False
    save_servers(store)
    return True


# ---------------------------------------------------------------------------
# OAuth 2.1 (discovery + PKCE + token exchange)
# ---------------------------------------------------------------------------

def _protected_resource_metadata(resource_url):
    parsed = urllib.parse.urlparse(resource_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path
    candidates = []
    if path and path != "/":
        candidates.append(origin + "/.well-known/oauth-protected-resource" + path)
    candidates.append(origin + "/.well-known/oauth-protected-resource")
    last_exc = None
    for candidate in candidates:
        try:
            return _http_json(candidate)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            last_exc = exc
    raise RuntimeError(f"Could not fetch protected resource metadata: {last_exc}")


def _authorization_server_metadata(issuer):
    parsed = urllib.parse.urlparse(issuer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    candidates = []
    if path:
        candidates.append(origin + "/.well-known/oauth-authorization-server" + path)
    candidates.append(origin + "/.well-known/oauth-authorization-server")
    candidates.append(origin + "/.well-known/openid-configuration")
    last_exc = None
    for candidate in candidates:
        try:
            return _http_json(candidate)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            last_exc = exc
    raise RuntimeError(f"Could not fetch authorization server metadata: {last_exc}")


def _pkce_pair():
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _redirect_uri():
    port = os.environ.get("DENNIS_PORT", "8080")
    return f"http://127.0.0.1:{port}/api/mcp/oauth/callback"


def begin_oauth(server_id, client_id=""):
    """Discover authorization metadata and build the authorization URL.

    Follows the MCP client-registration priority: pre-registered client id
    first, then Dynamic Client Registration (RFC 7591) when the AS offers it,
    otherwise the operator is asked to paste a client id.
    """
    server = get_server(server_id)
    if not server:
        raise KeyError(server_id)
    if server.get("transport") == "stdio":
        raise ValueError("stdio connectors use environment credentials, not OAuth")
    resource = server.get("url", "")
    if not resource:
        raise ValueError("This connector has no endpoint URL to authorize")

    prm = _protected_resource_metadata(resource)
    auth_servers = prm.get("authorization_servers") or []
    if not auth_servers:
        raise RuntimeError("Protected resource metadata listed no authorization servers")
    issuer = auth_servers[0]
    asm = _authorization_server_metadata(issuer)

    authorization_endpoint = asm.get("authorization_endpoint", "")
    token_endpoint = asm.get("token_endpoint", "")
    registration_endpoint = asm.get("registration_endpoint", "")
    if not authorization_endpoint or not token_endpoint:
        raise RuntimeError("Authorization server metadata is missing endpoints")

    scopes_supported = (
        prm.get("scopes_supported")
        or asm.get("scopes_supported")
        or ["mcp:tools"]
    )
    policy = server.get("policy") or default_policy()
    requested_scopes = policy.get("scopes") or scopes_supported
    requested_scopes = [s for s in requested_scopes if s in scopes_supported] or scopes_supported

    registered_client = client_id.strip() or server.get("oauth", {}).get("client_id", "")
    registration_note = "Using pre-registered client id."
    if not registered_client and registration_endpoint:
        try:
            reg = _http_json(registration_endpoint, method="POST", data={
                "client_name": "DENNIS Agentic OS",
                "redirect_uris": [_redirect_uri()],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": " ".join(requested_scopes),
            })
            registered_client = reg.get("client_id", "")
            registration_note = "Registered a dynamic client (RFC 7591)."
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            registration_note = f"Dynamic registration failed ({exc}); provide a client id."

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    oauth = dict(server.get("oauth") or {})
    oauth.update({
        "issuer": issuer,
        "resource": resource,
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
        "registration_endpoint": registration_endpoint,
        "scopes": requested_scopes,
        "client_id": registered_client,
        "code_verifier": verifier,
        "state": state,
        "redirect_uri": _redirect_uri(),
    })
    if registered_client:
        oauth["token_endpoint_auth_method"] = "none"
    server["oauth"] = oauth
    server["status"] = "authorizing" if registered_client else "policy_review"
    server["last_error"] = registration_note if not registered_client else ""
    server["updated_at"] = _now()
    _upsert(server)

    params = {
        "response_type": "code",
        "client_id": registered_client,
        "redirect_uri": _redirect_uri(),
        "scope": " ".join(requested_scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    authorization_url = authorization_endpoint + "?" + urllib.parse.urlencode(params)
    return {
        "server": _public(server),
        "authorization_url": authorization_url,
        "state": state,
        "redirect_uri": _redirect_uri(),
        "scopes": requested_scopes,
        "issuer": issuer,
        "client_id": registered_client,
        "note": registration_note,
        "needs_client_id": not registered_client,
    }


def _extract_code(value):
    value = (value or "").strip()
    if not value:
        return "", ""
    if "://" in value or value.startswith("/"):
        parsed = urllib.parse.urlparse(value)
        qs = urllib.parse.parse_qs(parsed.query)
        return (qs.get("code", [""])[0], qs.get("state", [""])[0])
    return value, ""


def complete_oauth(server_id, code="", state=""):
    server = get_server(server_id)
    if not server:
        raise KeyError(server_id)
    oauth = server.get("oauth") or {}
    if not oauth.get("token_endpoint"):
        raise ValueError("Authorization has not been started for this connector")

    code, embedded_state = _extract_code(code)
    state = state or embedded_state
    if not code:
        raise ValueError("An authorization code is required")
    if oauth.get("state") and state and state != oauth["state"]:
        raise ValueError("OAuth state mismatch - restart authorization")
    if not oauth.get("client_id"):
        raise ValueError("A client id is required before exchanging the code")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": oauth.get("redirect_uri", _redirect_uri()),
        "client_id": oauth["client_id"],
        "code_verifier": oauth.get("code_verifier", ""),
        "resource": oauth.get("resource", server.get("url", "")),
    }
    try:
        token = _http_json(oauth["token_endpoint"], method="POST", data=data)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise RuntimeError(f"Token exchange failed ({exc.code}): {detail}") from exc

    if not token.get("access_token"):
        raise RuntimeError("Authorization server returned no access token")

    expires_in = token.get("expires_in")
    oauth["access_token"] = token["access_token"]
    if token.get("refresh_token"):
        oauth["refresh_token"] = token["refresh_token"]
    oauth["token_type"] = token.get("token_type", "Bearer")
    oauth["expires_at"] = (
        int(time.time() + int(expires_in)) if expires_in else None)
    oauth.pop("code_verifier", None)
    oauth.pop("state", None)
    server["oauth"] = oauth
    server["status"] = "connected"
    server["last_error"] = ""
    server["updated_at"] = _now()
    _upsert(server)
    return server


def set_client_id(server_id, client_id):
    server = get_server(server_id)
    if not server:
        raise KeyError(server_id)
    oauth = dict(server.get("oauth") or {})
    oauth["client_id"] = client_id.strip()
    server["oauth"] = oauth
    server["updated_at"] = _now()
    _upsert(server)
    return server


def complete_connection(server_id):
    """Finish connecting a connector that needs no OAuth code (stdio/API key)."""
    server = get_server(server_id)
    if not server:
        raise KeyError(server_id)
    policy = server.get("policy") or default_policy()
    if not policy.get("accepted"):
        raise ValueError("Accept the policy before connecting this connector")
    server["status"] = "connected"
    server["last_error"] = ""
    server["updated_at"] = _now()
    _upsert(server)
    return server


def refresh_token(server_id):
    server = get_server(server_id)
    if not server:
        raise KeyError(server_id)
    oauth = server.get("oauth") or {}
    if not oauth.get("refresh_token") or not oauth.get("token_endpoint"):
        raise ValueError("No refresh token is stored for this connector")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": oauth["refresh_token"],
        "client_id": oauth.get("client_id", ""),
        "resource": oauth.get("resource", server.get("url", "")),
    }
    token = _http_json(oauth["token_endpoint"], method="POST", data=data)
    if not token.get("access_token"):
        raise RuntimeError("Refresh returned no access token")
    oauth["access_token"] = token["access_token"]
    if token.get("refresh_token"):
        oauth["refresh_token"] = token["refresh_token"]
    expires_in = token.get("expires_in")
    oauth["expires_at"] = int(time.time() + int(expires_in)) if expires_in else None
    server["oauth"] = oauth
    server["status"] = "connected"
    server["updated_at"] = _now()
    _upsert(server)
    return server
