"""Unified execution router: turn a selected connection + model into the
concrete configuration each agent runtime needs.

Three execution mechanisms stay distinct:

* ``cli_native``   - the agent's own CLI keeps its own auth (default).
* ``api_via_cli``  - the agent's CLI is pointed at a connection endpoint via
  environment variables (the mechanism FreeLLMAPI pioneered for Claude Code
  and Codex; generalized here to every registered connection).
* ``api``          - in-process API execution (DeepSeek Harness chat client).

The router never executes inference itself; it resolves *configuration*:
environment variables, endpoint URLs and the native model id the runtime
expects. Connection-scoped model ids (``conn:<id>:<native>``) are resolved
here and translated back to the runtime's native id.
"""

from __future__ import annotations

from contextlib import contextmanager

from . import compatibility
from . import connections
from . import deepseek_harness
from . import model_catalog


class RoutingError(Exception):
    """Raised when a route cannot be resolved; message is user-facing."""

    def __init__(self, message, status=400, diagnostic=None):
        super().__init__(message)
        self.status = int(status or 400)
        self.diagnostic = diagnostic or {}


def apply_env(env, agent_id, model_id):
    """Return ``env`` plus the connection variables needed for this model.

    Resolution order (exactly one binding wins):

    1. Connection-scoped model (``conn:...``) -> connection env template.
    2. FreeLLMAPI per-agent settings (existing behaviour, unchanged).
    3. Otherwise the environment passes through untouched (CLI-native auth).
    """
    if not agent_id or not model_id:
        return env
    parsed = model_catalog.parse_internal_id(model_id)
    if parsed:
        conn = connections.get_connection(parsed[0])
        if not conn:
            raise RoutingError("Selected model's connection is missing.", status=400)
        endpoint, _entry = compatibility.endpoint_for(agent_id, conn["protocol"])
        if endpoint is None or endpoint.status not in (compatibility.COMPATIBLE,):
            diagnostic = compatibility.evaluate(agent_id, model_id, connection=conn)
            raise RoutingError(
                diagnostic.get("reason") or "Agent cannot execute this model.",
                status=400, diagnostic=diagnostic)
        key, key_source = connections.resolve_key(conn)
        if not key and "api_key" in endpoint.env_template.values():
            raise RoutingError("No API key configured for connection '%s'." % conn["name"],
                               status=400)
        result = dict(env)
        for var_name, kind in endpoint.env_template.items():
            if kind == "base_url":
                result[var_name] = conn["base_url"]
            elif kind == "api_key":
                result[var_name] = key or ""
            elif kind == "model":
                # Declares the custom model to the runtime (e.g. Claude
                # Code's ANTHROPIC_CUSTOM_MODEL_OPTION skips its local model
                # validation for gateway ids).
                result[var_name] = parsed[1]
            elif kind == "model_context":
                # The model's real context window (Claude Code sizes
                # auto-compaction with it). Skipped when unknown.
                record, _conn2 = model_catalog.get_record(model_id)
                window = (record or {}).get("context_window")
                if isinstance(window, int) and window > 0:
                    result[var_name] = str(window)
            elif isinstance(kind, str):
                result[var_name] = kind  # literal override (e.g. clearing a var)
        if endpoint.model_env:
            result[endpoint.model_env] = parsed[1]
        return result
    return env


def native_model_id(model_id):
    """The model id the target runtime actually expects."""
    parsed = model_catalog.parse_internal_id(model_id)
    if not parsed:
        return model_id
    connection_id, native_id = parsed
    # If a gateway->upstream alias is known, the native runtime id may
    # differ from the gateway's id; the runtime gets the *gateway's* id
    # because that is what the gateway expects on the wire. Aliases are
    # metadata, not rewrites.
    return native_id


def validate_model_for_agent(agent_id, model_id):
    """Full compatibility gate used by session model switching.

    Returns ``(ok, payload)`` where payload carries the diagnostic and, for
    connection models, the resolved connection.
    """
    if not model_catalog.is_connection_model(model_id):
        return True, {"status": compatibility.NOT_TESTED, "execution": "cli_native"}
    diagnostic = compatibility.evaluate(agent_id, model_id)
    status = diagnostic.get("status")
    if status == compatibility.COMPATIBLE:
        return True, diagnostic
    if status in (compatibility.AUTH_REQUIRED, compatibility.CONNECTION_UNAVAILABLE,
                  compatibility.INCOMPATIBLE, compatibility.NOT_TESTED,
                  compatibility.PARTIAL):
        return False, diagnostic
    return False, diagnostic


# ---------------------------------------------------------------------------
# In-process API execution (DeepSeek Harness chat client)
# ---------------------------------------------------------------------------
@contextmanager
def api_endpoint_override(model_id):
    """Route in-process API calls through a connection for this block.

    Yields a dict ``{base_url, api_key, model, record_as}`` when ``model_id``
    is a connection model whose protocol is OpenAI-compatible and the target
    in-process client can execute it; otherwise yields ``None`` and the
    caller keeps its default behaviour. The override is activated here, so
    callers only need the ``with`` block.
    """
    parsed = model_catalog.parse_internal_id(model_id)
    if not parsed:
        yield None
        return
    conn = connections.get_connection(parsed[0])
    if not conn or conn["protocol"] != connections.OPENAI_PROTOCOL:
        yield None
        return
    endpoint, _entry = compatibility.endpoint_for("deepseek-harness", conn["protocol"])
    if endpoint is None or endpoint.status != compatibility.COMPATIBLE:
        yield None
        return
    key, _source = connections.resolve_key(conn)
    # OpenAI-style callers append /chat/completions themselves. ``record_as``
    # keeps the connection-scoped id on persisted conversation state while
    # the wire call uses the native id.
    override = {
        "base_url": conn["base_url"],
        "api_key": key,
        "model": parsed[1],
        "record_as": model_id,
    }
    with deepseek_harness.request_override(
            base_url=override["base_url"],
            api_key=override["api_key"],
            model=override["model"],
            record_as=override["record_as"]):
        yield override


def route_summary(agent_id, model_id):
    """Diagnostics for the routing decision (no secrets)."""
    if not model_catalog.is_connection_model(model_id):
        return {
            "execution": "cli_native",
            "connection": None,
            "source": "agent_native",
        }
    _ok, diagnostic = validate_model_for_agent(agent_id, model_id)
    conn = diagnostic.get("connection") or {}
    return {
        "execution": diagnostic.get("execution"),
        "connection": {"id": conn.get("id"), "name": conn.get("name"),
                       "type": conn.get("type"), "protocol": conn.get("protocol")},
        "compatibility": diagnostic.get("status"),
        "compatibility_reason": diagnostic.get("reason"),
    }
