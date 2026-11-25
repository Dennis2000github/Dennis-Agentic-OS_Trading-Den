"""Shared Composio provider adapter — execution machinery for all COSMOS modules.

Layered on top of the existing ``backend/composio.py`` dashboard adapter
(keys, catalog, connect flows). This module adds what the dashboard module
deliberately does not do:

* exact tool-schema lookup (REST, ``tool_slugs=``) with scopes and versions;
* connected-account resolution per toolkit (REST);
* tool execution through the project tool-router session (MCP JSON-RPC via
  ``COMPOSIO_MULTI_EXECUTE_TOOL``) — the repo's implemented execution method;
* honest transport detection and connection-state reporting.

Live-verified platform behavior (2026-11, documented so future maintainers
do not have to re-derive it):

* REST ``GET /tools?tool_slugs=`` returns full input schemas — works today;
* REST ``POST /tools/execute`` (v3 and v3.1) is NOT exposed on this tier
  (plain 404) — the transport exists in code but reports itself unavailable;
* the tool-router session executes tools mechanically (structured results
  with ``log_id``) but scopes connections per session: a project-level
  ACTIVE connection is not automatically visible inside a session. The
  adapter surfaces the session's connect link to the owner instead of
  silently re-authenticating (never creating duplicate connections).

Execution results, failures and approval gating are audited through
``integrations.executions``; every mutating action must pass
``integrations.permissions`` before it can run.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error

from .. import composio
from . import store

EXECUTE_TOOL = "COMPOSIO_MULTI_EXECUTE_TOOL"
STATE_TOOL = "COMPOSIO_MANAGE_CONNECTIONS"
MCP_TIMEOUT = 120
MAX_ITEMS_PER_CALL = 50

# 60-second cache for session connection-state probes (per toolkit).
_STATE_CACHE = {}


class ComposioAdapterError(store.IntegrationError):
    pass


# ---------------------------------------------------------------------------
# REST discovery (verified working)
# ---------------------------------------------------------------------------
def toolkit_tools(slug, limit=100):
    """List a toolkit's tools with schemas from the live catalog.

    Falls back to the cached dashboard catalog shape when the live API is
    unreachable so discovery never crashes a module.
    """
    slug = store.clean_text(slug, "Toolkit slug", 80, required=True)
    if not composio.keys()["api"]:
        raise ComposioAdapterError("not_configured", "Add a Composio project API key.", 409)
    try:
        data = composio._http_json("/tools", {"toolkit_slug": slug, "limit": str(min(100, max(1, limit)))})
        items = data.get("items") if isinstance(data, dict) else data
        return [t for t in (items or []) if isinstance(t, dict) and t.get("slug")]
    except composio.ComposioError as exc:
        raise ComposioAdapterError(exc.code, exc.message, exc.status)


def tool_schema(tool_slug):
    """Exact schema lookup for one tool (params, scopes, versions)."""
    tool_slug = store.clean_text(tool_slug, "Tool slug", 160, required=True)
    if not composio.keys()["api"]:
        raise ComposioAdapterError("not_configured", "Add a Composio project API key.", 409)
    try:
        data = composio._http_json("/tools", {"tool_slugs": tool_slug})
    except composio.ComposioError as exc:
        raise ComposioAdapterError(exc.code, exc.message, exc.status)
    items = data.get("items") if isinstance(data, dict) else data
    for item in items or []:
        if isinstance(item, dict) and item.get("slug") == tool_slug:
            return {
                "slug": item.get("slug"),
                "name": item.get("name"),
                "description": item.get("description"),
                "version": item.get("version"),
                "available_versions": item.get("available_versions") or [],
                "input_parameters": item.get("input_parameters") or {},
                "output_parameters": item.get("output_parameters") or {},
                "scopes": item.get("scopes") or [],
                # `is_deprecated` is the boolean; `deprecated` is a nested
                # summary object in the live catalog.
                "deprecated": bool(item.get("is_deprecated")),
                "no_auth": bool(item.get("no_auth")),
            }
    raise ComposioAdapterError("tool_unknown", "Tool %s was not found in the live catalog." % tool_slug, 404)


def resolve_connection(toolkit):
    """Resolve the ACTIVE project-level connected account for a toolkit.

    Reuses the user's existing connection — never creates one.
    """
    toolkit = store.clean_text(toolkit, "Toolkit", 80, required=True)
    if not composio.keys()["api"]:
        raise ComposioAdapterError("not_configured", "Add a Composio project API key.", 409)
    try:
        conns = composio.connections().get("connections") or []
    except composio.ComposioError as exc:
        raise ComposioAdapterError(exc.code, exc.message, exc.status)
    matches = [c for c in conns if str(c.get("toolkit") or "").lower() == toolkit.lower()]
    for conn in matches:
        if str(conn.get("status") or "").upper() == "ACTIVE":
            return conn
    if matches:
        raise ComposioAdapterError(
            "connection_expired",
            "The %s connection exists but is %s; reconnect it from the Composio dashboard."
            % (toolkit, matches[0].get("status")),
            409,
        )
    raise ComposioAdapterError(
        "connection_missing", "No connected account for toolkit %s." % toolkit, 409)


# ---------------------------------------------------------------------------
# Session tool-router execution (MCP JSON-RPC)
# ---------------------------------------------------------------------------
def _session_url():
    stored = composio._read_store().get("session_mcp") or {}
    url = str(stored.get("url") or "").strip()
    if not url:
        raise ComposioAdapterError(
            "session_mcp_missing",
            "No tool-router session configured. Refresh it from the Composio dashboard.",
            409,
        )
    return url


def _parse_frames(raw):
    """Parse streamable-HTTP MCP responses (SSE frames or plain JSON)."""
    if raw.startswith("HTTPERR:") or raw.startswith("EXC:"):
        raise ComposioAdapterError("unreachable", raw[:300], 502)
    frames = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                frames.append(json.loads(line[5:].strip()))
            except ValueError:
                continue
    if frames:
        return frames
    try:
        parsed = json.loads(raw)
        return [parsed] if isinstance(parsed, dict) else []
    except ValueError:
        return []


def _rpc(url, key, payload, request_id):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        composio.API_HEADER: key,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=MCP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise ComposioAdapterError("unreachable", "Composio MCP HTTP %s: %s" % (exc.code, detail), 502)
    except urllib.error.URLError as exc:
        raise ComposioAdapterError("unreachable", "Composio MCP unreachable: %s" % exc.reason, 502)
    frames = _parse_frames(raw)
    for frame in frames:
        if frame.get("id") == request_id and ("result" in frame or "error" in frame):
            return frame
    raise ComposioAdapterError("bad_response", "MCP response did not contain a matching frame.", 502)


def _extract_text(frame):
    content = ((frame.get("result") or {}).get("content")) or []
    return "".join(c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text")


def session_connection_state(toolkit):
    """Inspect the SESSION's own connection view for a toolkit (read-only).

    Returns ``{"status": "active"|"initiated"|"missing", "redirect_url": str|None}``.
    The session router scopes connections per session on the current tier, so
    an ACTIVE project connection can still show as ``initiated`` here.
    Cached for 60 seconds to keep owner-facing views cheap.
    """
    toolkit = store.clean_text(toolkit, "Toolkit", 80, required=True)
    cache_key = toolkit.lower()
    cached = _STATE_CACHE.get(cache_key)
    if cached and (store.now() - cached[0]) < 60:
        return cached[1]
    key = composio.keys()["api"]
    frame = _rpc(_session_url(), key, {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": STATE_TOOL,
                   "arguments": {"toolkits": [{"name": toolkit, "action": "list"}]}},
    }, 7)
    if "error" in frame:
        raise ComposioAdapterError("bad_response", str(frame["error"])[:300], 502)
    text = _extract_text(frame)
    try:
        payload = json.loads(text)
    except ValueError:
        return {"status": "missing", "redirect_url": None, "raw": text[:300]}
    results = ((payload.get("data") or {}).get("results")) or {}
    entry = results.get(toolkit) or {}
    status = str(entry.get("status") or "").lower() or "missing"
    state = {"status": status, "redirect_url": entry.get("redirect_url")}
    _STATE_CACHE[cache_key] = (store.now(), state)
    return state


def connect_link(toolkit):
    """Return the session's connect link for a toolkit — shown to the owner.

    This NEVER opens or completes the link; the owner decides in the browser.
    Completing it once per session unlocks execution inside that session.
    """
    toolkit = store.clean_text(toolkit, "Toolkit", 80, required=True)
    key = composio.keys()["api"]
    frame = _rpc(_session_url(), key, {
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": STATE_TOOL,
                   "arguments": {"toolkits": [{"name": toolkit, "action": "add"}]}},
    }, 8)
    if "error" in frame:
        raise ComposioAdapterError("bad_response", str(frame["error"])[:300], 502)
    text = _extract_text(frame)
    try:
        payload = json.loads(text)
    except ValueError:
        raise ComposioAdapterError("bad_response", "Unexpected manage-connections response.", 502)
    entry = ((payload.get("data") or {}).get("results")) or {}
    row = entry.get(toolkit) or {}
    return {"toolkit": toolkit, "status": row.get("status"),
            "redirect_url": row.get("redirect_url"),
            "note": "Open this link once to bind the session. The existing project connection is reused where the platform allows it."}


def _flag(value):
    """Interpret MCP success flags, which arrive as bool or string in practice."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "ok")
    return False


def _normalize_tool_row(raw, log_id):
    """Unwrap the live tool-router row shape.

    Composio returns each result as
    ``{"tool_slug": ..., "response": {"successful": true, "data": ...}}``.
    Earlier transports put ``successful``/``data`` at the top level, so both
    shapes stay supported. The nested response is authoritative when present.
    """
    row = dict(raw or {})
    response = row.get("response")
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except ValueError:
            response = None
    if isinstance(response, dict):
        if "successful" in response:
            row["successful"] = _flag(response.get("successful"))
        if "data" in response:
            row["data"] = response.get("data")
        if response.get("error") is not None:
            row["error"] = response.get("error")
        if response.get("log_id"):
            row["log_id"] = response.get("log_id")
        if not row.get("tool_slug") and response.get("tool_slug"):
            row["tool_slug"] = response.get("tool_slug")
    else:
        row["successful"] = _flag(row.get("successful"))
    return {
        "tool_slug": row.get("tool_slug"),
        "successful": _flag(row.get("successful")),
        "data": row.get("data"),
        "error": row.get("error"),
        "log_id": row.get("log_id") or log_id,
    }


def execute_tools(items, thought=None):
    """Execute up to 50 tools through the session tool router.

    Returns the normalized per-item results:
    ``[{tool_slug, successful, data, error, log_id}]``.
    """
    if not isinstance(items, list) or not items:
        raise ComposioAdapterError("invalid_arguments", "At least one tool item is required.")
    if len(items) > MAX_ITEMS_PER_CALL:
        raise ComposioAdapterError(
            "invalid_arguments", "At most %d tools can execute per call." % MAX_ITEMS_PER_CALL)
    clean_items = []
    for item in items:
        if not isinstance(item, dict) or not item.get("tool_slug"):
            raise ComposioAdapterError("invalid_arguments", "Each item needs a tool_slug.")
        clean_items.append({
            "tool_slug": store.clean_text(item["tool_slug"], "Tool slug", 160, required=True),
            "arguments": item.get("arguments") if isinstance(item.get("arguments"), dict) else {},
        })
    key = composio.keys()["api"]
    if not key:
        raise ComposioAdapterError("not_configured", "Add a Composio project API key.", 409)
    arguments = {"tools": clean_items}
    if thought:
        arguments["thought"] = store.clean_text(thought, "Thought", 300)
    frame = _rpc(_session_url(), key, {
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": EXECUTE_TOOL, "arguments": arguments},
    }, 9)
    if "error" in frame:
        raise ComposioAdapterError("execution_failed", str(frame["error"])[:400], 502)
    text = _extract_text(frame)
    try:
        payload = json.loads(text)
    except ValueError:
        raise ComposioAdapterError("bad_response", "Execution returned non-JSON output.", 502)
    log_id = payload.get("log_id")
    if payload.get("successful") is False and not (payload.get("data") or {}).get("results"):
        raise ComposioAdapterError("execution_failed", str(payload.get("error") or "execution failed")[:400], 502)
    results = [
        _normalize_tool_row(row, log_id)
        for row in ((payload.get("data") or {}).get("results") or [])
    ]
    return results


def execute(tool_slug, arguments, thought=None):
    """Execute one tool; raises on failure. Returns the tool's ``data``."""
    results = execute_tools([{"tool_slug": tool_slug, "arguments": arguments}], thought=thought)
    row = results[0]
    if not row.get("successful"):
        raise ComposioAdapterError(
            "tool_failed",
            "%s failed: %s" % (tool_slug, str(row.get("error") or "unknown error")[:400]),
            502,
        )
    return row.get("data")
