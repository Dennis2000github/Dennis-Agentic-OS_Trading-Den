"""HTTP dispatcher for the YouTube capability over shared integrations.

``integrations.api`` forwards the ``youtube/`` subtree here. Pure:
``(method, path, query, body) -> (status, payload)``.

* ``GET  /api/integrations/youtube``                 — capability overview + state
* ``GET  /api/integrations/youtube/discover``        — live op availability
* ``GET  /api/integrations/youtube/connection``      — connection + session state
* ``GET  /api/integrations/youtube/channel``         — live connected-channel identity
* ``GET  /api/integrations/youtube/ops``             — op table
* ``GET  /api/integrations/youtube/ops/{op}``        — live tool schema for one op
* ``GET  /api/integrations/youtube/run/{op}``        — read-only execution (query args)
* ``POST /api/integrations/youtube/run/{op}``        — read: execute; write: approval flow
* ``POST /api/integrations/youtube/session/link``    — disclose the session connect link (owner-gated upstream)
"""

from __future__ import annotations

from . import youtube
from . import store


def _query(query, name, default=None):
    try:
        values = query.get(name, [default])
        return values[0] if values else default
    except AttributeError:
        return default


def _attribution(params, from_query=False):
    """Optional agent/module identity forwarded by an authorized COSMOS caller."""
    def value(name):
        raw = _query(params, name) if from_query else (
            params.get(name) if isinstance(params, dict) else None)
        return store.clean_text(raw, "Agent" if name == "agent" else "Module",
                                60 if name == "agent" else 120) or None
    return value("agent"), value("module") or "integrations.youtube"


def _fail(exc):
    if isinstance(exc, store.IntegrationError):
        return exc.status if 400 <= exc.status <= 599 else 400, {
            "error": exc.message, "code": exc.code}
    return 500, {"error": store.clean_text(str(exc), "Error", 500) or "youtube_error",
                 "code": "youtube_error"}


def handle(method, path, query=None, body=None):
    subpath = path[len("/api/integrations/youtube"):].strip("/")
    parts = [part for part in subpath.split("/") if part]
    try:
        if method == "GET":
            return _get(parts, query or {})
        if method == "POST":
            return _post(parts, body or {})
        return 405, {"error": "Method not allowed.", "code": "method_not_allowed"}
    except Exception as exc:  # noqa: BLE001 - dispatcher converts to JSON errors
        return _fail(exc)


def _get(parts, query):
    if not parts:
        state = youtube.connection_state()
        return 200, {
            "capability": "composio.youtube",
            "connection": state["connection"],
            "session": state["session"],
            "execution_available": state["execution_available"],
            "blocked_reason": state["blocked_reason"],
            "ops": {"read": sorted(youtube.READ_OPS), "write": sorted(youtube.WRITE_OPS)},
        }
    if parts[0] == "discover":
        return 200, youtube.discover()
    if parts[0] == "connection":
        return 200, youtube.connection_state()
    if parts[0] == "channel":
        refresh = str(_query(query, "refresh") or "").lower() in ("1", "true", "yes")
        return 200, youtube.connected_channel(refresh=refresh)
    if parts[0] == "ops" and len(parts) == 1:
        return 200, {"ops": youtube.OPS, "read": sorted(youtube.READ_OPS),
                     "write": sorted(youtube.WRITE_OPS)}
    if parts[0] == "ops" and len(parts) == 2:
        return 200, youtube.op_schema(parts[1])
    if parts[0] == "run" and len(parts) == 2:
        op = parts[1]
        if op not in youtube.READ_OPS:
            return 405, {"error": "Use POST for mutating operations.", "code": "method_not_allowed"}
        args = {k: v[-1] for k, v in (query or {}).items()
                if k not in ("agent", "module")}
        agent, module = _attribution(query, from_query=True)
        result = youtube.run(op, args, agent=agent, module=module)
        return 200, result
    raise store.IntegrationError("not_found", "Unknown YouTube path.", 404)


def _post(parts, body):
    if parts[0] == "run" and len(parts) == 2:
        op = parts[1]
        arguments = {k: v for k, v in (body.get("arguments") or {}).items()
                     if k not in ("agent", "module")}
        agent, module = _attribution(body)
        result = youtube.run(op, arguments, agent=agent, module=module)
        status = 202 if isinstance(result, dict) and result.get("status") == "approval_required" else 200
        return status, result
    if parts == ["session", "link"]:
        from . import composio_adapter as adapter

        return 200, adapter.connect_link(youtube.TOOLKIT)
    raise store.IntegrationError("not_found", "Unknown YouTube path.", 404)
