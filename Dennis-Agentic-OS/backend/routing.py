"""Free/Paid AI access routing policy.

Global preference with per-agent and per-task overrides:

* ``free_first`` (default) - prefer connections classified Free / Free Tier /
  Local. Paid connections are used only when the user explicitly authorized
  paid fallback; credits are never consumed silently.
* ``paid_first``  - prefer official provider (paid) connections; free
  alternatives are fallbacks.
* ``manual``      - the explicitly selected connection is used, unchanged.

Pricing classification always comes from connection metadata
(``connections.PRICING_*``), never from a name or type: a gateway is not
assumed free and an official provider is not assumed paid.
"""

from __future__ import annotations

import os
import threading
import time

from . import compatibility
from . import config
from . import connections
from . import model_catalog

ROUTING_FILE = os.path.join(config.DATA_DIR, "routing.json")
_STORE_VERSION = 1

MODE_FREE_FIRST = "free_first"
MODE_PAID_FIRST = "paid_first"
MODE_MANUAL = "manual"
MODES = (MODE_FREE_FIRST, MODE_PAID_FIRST, MODE_MANUAL)

FREE_PRICING = (connections.PRICING_FREE, connections.PRICING_FREE_TIER,
                connections.PRICING_LOCAL)

_LOCK = threading.RLock()


class RoutingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = int(status or 400)


def _read_store():
    data = config.read_json(ROUTING_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", _STORE_VERSION)
    if not isinstance(data.get("global"), dict):
        data["global"] = {}
    if not data["global"].get("mode"):
        data["global"] = {"mode": MODE_FREE_FIRST,
                          "allow_paid_fallback": False,
                          "updated_at": config.now_iso()}
    if not isinstance(data.get("agents"), dict):
        data["agents"] = {}
    if not isinstance(data.get("tasks"), dict):
        data["tasks"] = {}
    if not isinstance(data.get("authorizations"), list):
        data["authorizations"] = []
    return data


def _write_store(data):
    config.write_json(ROUTING_FILE, data)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------
def preferences():
    with _LOCK:
        data = _read_store()
    return {
        "global": {
            "mode": data["global"].get("mode", MODE_FREE_FIRST),
            "allow_paid_fallback": bool(data["global"].get("allow_paid_fallback")),
            "updated_at": data["global"].get("updated_at"),
        },
        "agents": {k: {"mode": v.get("mode"), "updated_at": v.get("updated_at")}
                   for k, v in data["agents"].items()},
        "tasks": {k: {"mode": v.get("mode"), "updated_at": v.get("updated_at")}
                  for k, v in data["tasks"].items()},
    }


def set_global(mode, allow_paid_fallback=None):
    if mode not in MODES:
        raise RoutingError("mode must be one of %s" % ", ".join(MODES))
    with _LOCK:
        data = _read_store()
        data["global"]["mode"] = mode
        data["global"]["updated_at"] = config.now_iso()
        if allow_paid_fallback is not None:
            data["global"]["allow_paid_fallback"] = bool(allow_paid_fallback)
        _write_store(data)
        return preferences()["global"]


def set_agent_override(agent_id, mode):
    if mode is not None and mode not in MODES:
        raise RoutingError("mode must be one of %s or null" % ", ".join(MODES))
    with _LOCK:
        data = _read_store()
        if mode is None:
            data["agents"].pop(agent_id, None)
        else:
            data["agents"][agent_id] = {"mode": mode, "updated_at": config.now_iso()}
        _write_store(data)
        return preferences()


def set_task_override(task_key, mode):
    if mode is not None and mode not in MODES:
        raise RoutingError("mode must be one of %s or null" % ", ".join(MODES))
    task_key = (task_key or "").strip()
    if not task_key:
        raise RoutingError("task key is required")
    with _LOCK:
        data = _read_store()
        if mode is None:
            data["tasks"].pop(task_key, None)
        else:
            data["tasks"][task_key] = {"mode": mode, "updated_at": config.now_iso()}
        _write_store(data)
        return preferences()


def mode_for(agent_id=None, task=None):
    """Resolved routing mode plus where it came from."""
    with _LOCK:
        data = _read_store()
    if task and task in data["tasks"]:
        return data["tasks"][task]["mode"], "task:%s" % task
    if agent_id and agent_id in data["agents"]:
        return data["agents"][agent_id]["mode"], "agent:%s" % agent_id
    return data["global"].get("mode", MODE_FREE_FIRST), "global"


def paid_fallback_allowed(agent_id=None, task=None):
    mode, _source = mode_for(agent_id, task)
    with _LOCK:
        data = _read_store()
    return bool(data["global"].get("allow_paid_fallback")) or mode == MODE_PAID_FIRST


# ---------------------------------------------------------------------------
# Paid fallback authorization (explicit consent, auditable)
# ---------------------------------------------------------------------------
_AUTHORIZE_TTL = 24 * 3600


def authorize_paid_fallback(connection_id, reason="", agent_id=None):
    """Record explicit user consent to use a paid connection as fallback."""
    record = {
        "at": config.now_iso(),
        "connection_id": connection_id,
        "agent_id": agent_id,
        "reason": (reason or "")[:300],
    }
    with _LOCK:
        data = _read_store()
        data["authorizations"].insert(0, record)
        del data["authorizations"][100:]
        _write_store(data)
    return record


def _recent_authorization(connection_id, agent_id=None):
    with _LOCK:
        data = _read_store()
    now = config.now_epoch()
    for record in data["authorizations"]:
        if record.get("connection_id") != connection_id:
            continue
        if agent_id and record.get("agent_id") and record.get("agent_id") != agent_id:
            continue
        if now - _parse_epoch(record.get("at")) < _AUTHORIZE_TTL:
            return record
    return None


def _parse_epoch(iso):
    import calendar
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Route resolution
# ---------------------------------------------------------------------------
def _usable_connections(agent_id, conn_type=None):
    """Connections the agent can actually execute through, with diagnostics."""
    return compatibility.compatible_connections(agent_id, conn_type=conn_type)


def _is_paid(conn):
    return (conn.get("pricing") or connections.PRICING_UNKNOWN) not in FREE_PRICING


def resolve_route(agent_id, model_id=None, connection_id=None, task=None):
    """Resolve the execution route for an agent (optionally task-scoped).

    Returns a decision dict. ``status`` is one of:
    ``resolved``            - a compatible route is available;
    ``authorization_required`` - only paid routes remain and policy/consent
                                does not permit them yet;
    ``no_route``            - nothing compatible is configured.
    Never raises for routing outcomes; invalid input raises RoutingError.
    """
    if not agent_id:
        raise RoutingError("agent_id is required")
    mode, source = mode_for(agent_id, task)
    decision = {
        "agent_id": agent_id,
        "task": task,
        "mode": mode,
        "mode_source": source,
        "status": "no_route",
        "connection": None,
        "model": None,
        "candidates": [],
        "reason": "",
        "paid_fallback_allowed": paid_fallback_allowed(agent_id, task),
    }

    # Explicit selection always wins (manual semantics), but must validate.
    if model_id or connection_id:
        if connection_id and not model_id:
            conn = connections.get_connection(connection_id)
            if not conn:
                raise RoutingError("Unknown connection: %s" % connection_id, status=404)
            candidates = [c for c in _usable_connections(agent_id) if c["id"] == connection_id]
            if not candidates or candidates[0]["compatibility"] == compatibility.INCOMPATIBLE:
                decision["reason"] = candidates[0].get("compatibility_reason") if candidates else \
                    "Connection is not compatible with this agent."
                return decision
            decision["status"] = "resolved"
            decision["connection"] = candidates[0]
            return decision
        diagnostic = compatibility.evaluate(agent_id, model_id)
        if diagnostic.get("status") == compatibility.COMPATIBLE:
            decision["status"] = "resolved"
            decision["connection"] = diagnostic.get("connection")
            decision["model"] = diagnostic.get("model")
            return decision
        decision["reason"] = diagnostic.get("reason") or "Selection is not compatible."
        return decision

    # Preference-driven selection.
    candidates = _usable_connections(agent_id)
    compatible = [c for c in candidates if c.get("compatibility") in
                  (compatibility.COMPATIBLE, compatibility.PARTIAL)]
    free = [c for c in compatible
            if (c.get("pricing") or connections.PRICING_UNKNOWN) in FREE_PRICING]
    paid = [c for c in compatible if _is_paid(c)]

    if mode == MODE_PAID_FIRST:
        ordered = paid + free
    else:  # free_first and manual both start from free when nothing explicit
        ordered = free + paid
    decision["candidates"] = [{
        "id": c["id"], "name": c["name"], "type": c["type"],
        "pricing": c.get("pricing"), "compatibility": c.get("compatibility"),
    } for c in ordered]

    if not compatible:
        decision["reason"] = ("No compatible connection for agent '%s'. "
                              "Configure one in AI Connections." % agent_id)
        return decision

    preferred = free if (mode == MODE_FREE_FIRST and free) else (paid if mode == MODE_PAID_FIRST and paid else compatible)
    chosen = preferred[0]
    if _is_paid(chosen) and mode != MODE_PAID_FIRST and not decision["paid_fallback_allowed"]:
        if _recent_authorization(chosen["id"], agent_id):
            decision["status"] = "resolved"
            decision["connection"] = chosen
            decision["reason"] = "Using recently authorized paid fallback."
            return decision
        decision["status"] = "authorization_required"
        decision["connection"] = chosen
        decision["reason"] = ("Only paid connections are available for '%s'. "
                              "Approve paid fallback to proceed." % agent_id)
        return decision
    decision["status"] = "resolved"
    decision["connection"] = chosen
    return decision


def propose_fallback(agent_id, failed_connection_id=None, task=None):
    """Ordered fallback plan after a failure - never executes anything.

    The caller must obtain explicit approval (``authorize_paid_fallback``)
    before a paid fallback is used.
    """
    decision = resolve_route(agent_id, task=task)
    candidates = [c for c in decision.get("candidates", [])
                  if failed_connection_id and c["id"] != failed_connection_id]
    return {
        "agent_id": agent_id,
        "failed_connection_id": failed_connection_id,
        "mode": decision.get("mode"),
        "candidates": candidates,
        "paid_fallback_allowed": decision.get("paid_fallback_allowed"),
        "authorization_required": any(
            _is_paid(c) and not decision.get("paid_fallback_allowed")
            for c in candidates[:1]),
    }
