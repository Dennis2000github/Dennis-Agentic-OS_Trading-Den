"""Cross-module execution audit for shared integrations.

Every provider action COSMOS modules take through the integration router
ends up here: the requesting module/agent, provider, capability domain,
tool/action, target resource, status, approval reference and error details.
This is the single audit trail that lets any authorized module (current or
future) act through shared services without losing track of who did what.

Credential-looking values are redacted before anything reaches the store
(same rules as the Studio approval queue), payloads are bounded, and the
store is capped so it cannot grow without limit.
"""

from __future__ import annotations

import os

from .. import studio_contracts as contracts
from . import store

EXECUTIONS_FILE = os.path.join(store.INTEGRATIONS_DIR, "executions.json")
MAX_EXECUTIONS = 1000
MAX_TARGET_JSON = 2 * 1024
MAX_DETAIL_JSON = 8 * 1024

# Lifecycle of one integration action. ``approval_required`` marks actions
# waiting on the owner; ``rejected`` marks owner denials (kept for audit).
STATUSES = ("running", "succeeded", "failed", "rejected", "approval_required", "expired")


def record(
    provider,
    action,
    status,
    *,
    domain="",
    tool="",
    target=None,
    agent=None,
    module=None,
    approval_id=None,
    error=None,
    detail=None,
    started_at=None,
    finished_at=None,
):
    """Append one execution row and return its public copy.

    ``provider`` is the registry provider id (e.g. ``composio``); ``action``
    is the capability action (e.g. ``upload_video``); ``tool`` optionally
    carries the provider-native tool slug (e.g. ``YOUTUBE_UPLOAD_VIDEO``).
    """
    provider_id = store.clean_text(provider, "Provider", 60, required=True)
    action_id = store.clean_text(action, "Action", 120, required=True)
    status_id = store.clean_text(status, "Status", 30, required=True)
    if status_id not in STATUSES:
        raise store.IntegrationError(
            "invalid_status", "Execution status must be one of: %s." % ", ".join(STATUSES)
        )
    domain_id = store.clean_text(domain, "Domain", 60)
    tool_id = store.clean_text(tool, "Tool", 160)
    agent_id = store.clean_text(agent, "Agent", 60) or None
    module_id = store.clean_text(module, "Module", 120) or None
    approval = store.clean_text(approval_id, "Approval id", 60) or None

    error_row = None
    if error:
        if not isinstance(error, dict):
            error_row = {"code": "execution_error", "message": store.clean_text(error, "Error", 500)}
        else:
            error_row = {
                "code": store.clean_text(error.get("code") or "execution_error", "Error code", 60),
                "message": store.clean_text(error.get("message") or "", "Error message", 500),
            }

    row = {
        "id": store.new_id("intx_"),
        "provider": provider_id,
        "domain": domain_id,
        "action": action_id,
        "tool": tool_id,
        "target": _bounded(target, MAX_TARGET_JSON),
        "agent": agent_id,
        "module": module_id,
        "approval_id": approval,
        "status": status_id,
        "error": error_row,
        "detail": _bounded(detail, MAX_DETAIL_JSON),
        "started_at": float(started_at) if started_at is not None else store.now(),
        "started_iso": store.iso(started_at),
        "finished_at": float(finished_at) if finished_at is not None else None,
        "finished_iso": store.iso(finished_at) if finished_at is not None else None,
        "duration_ms": _duration(started_at, finished_at),
    }
    if row["finished_at"] is not None:
        row["finished_iso"] = store.iso(row["finished_at"])
    with store.lock():
        items = store.load_store(EXECUTIONS_FILE, "executions")
        items.insert(0, row)
        store.save_store(EXECUTIONS_FILE, "executions", items[:MAX_EXECUTIONS])
    return public(row)


def finish(row_id, status, *, error=None, detail=None):
    """Mark a previously recorded (``running``) execution as finished."""
    status_id = store.clean_text(status, "Status", 30, required=True)
    if status_id not in STATUSES:
        raise store.IntegrationError(
            "invalid_status", "Execution status must be one of: %s." % ", ".join(STATUSES)
        )
    with store.lock():
        items = store.load_store(EXECUTIONS_FILE, "executions")
        for item in items:
            if item.get("id") == row_id:
                item["status"] = status_id
                item["finished_at"] = store.now()
                item["finished_iso"] = store.iso(item["finished_at"])
                item["duration_ms"] = _duration(item.get("started_at"), item["finished_at"])
                if error:
                    item["error"] = (
                        error
                        if isinstance(error, dict) and "code" in error
                        else {
                            "code": "execution_error",
                            "message": store.clean_text(
                                error.get("message") if isinstance(error, dict) else error,
                                "Error",
                                500,
                            ),
                        }
                    )
                else:
                    item["error"] = None
                if detail is not None:
                    item["detail"] = _bounded(detail, MAX_DETAIL_JSON)
                store.save_store(EXECUTIONS_FILE, "executions", items)
                return public(item)
    raise store.IntegrationError("execution_not_found", "Execution record not found.", 404)


def get(row_id):
    row_id = store.clean_text(row_id, "Execution id", 60, required=True)
    with store.lock():
        for item in store.load_store(EXECUTIONS_FILE, "executions"):
            if item.get("id") == row_id:
                return public(item)
    return None


def list(limit=50, provider=None, status=None, domain=None, module=None):
    with store.lock():
        items = store.load_store(EXECUTIONS_FILE, "executions")
    wanted_provider = store.clean_text(provider, "Provider", 60)
    wanted_status = store.clean_text(status, "Status", 30)
    wanted_domain = store.clean_text(domain, "Domain", 60)
    wanted_module = store.clean_text(module, "Module", 120)
    if wanted_status and wanted_status not in STATUSES:
        raise store.IntegrationError(
            "invalid_status", "Execution status must be one of: %s." % ", ".join(STATUSES)
        )
    rows = [
        item
        for item in items
        if (not wanted_provider or item.get("provider") == wanted_provider)
        and (not wanted_status or item.get("status") == wanted_status)
        and (not wanted_domain or item.get("domain") == wanted_domain)
        and (not wanted_module or item.get("module") == wanted_module)
    ]
    try:
        cap = max(1, min(int(limit or 50), 200))
    except (TypeError, ValueError):
        cap = 50
    return [public(item) for item in rows[:cap]]


def summary():
    """Health-oriented rollup: counts plus the last success/failure per provider."""
    with store.lock():
        items = store.load_store(EXECUTIONS_FILE, "executions")
    by_status = {}
    by_provider = {}
    last_success = None
    last_failure = None
    for item in items:
        status = item.get("status") or "unknown"
        provider = item.get("provider") or "unknown"
        by_status[status] = by_status.get(status, 0) + 1
        bucket = by_provider.setdefault(provider, {"total": 0, "succeeded": 0, "failed": 0})
        bucket["total"] += 1
        if status == "succeeded":
            bucket["succeeded"] += 1
            if last_success is None:
                last_success = _brief(item)
        elif status in ("failed", "rejected"):
            bucket["failed"] += 1
            if last_failure is None:
                last_failure = _brief(item)
    return {
        "total": len(items),
        "by_status": by_status,
        "by_provider": by_provider,
        "last_success": last_success,
        "last_failure": last_failure,
    }


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------
def _bounded(value, limit_bytes):
    """Redact credential-looking values and bound a JSON-ish payload."""
    if value is None:
        return None
    safe = contracts.redact(value)
    try:
        import json

        text = json.dumps(safe)
    except (TypeError, ValueError):
        return {"unserializable": True}
    if len(text) <= limit_bytes:
        return safe
    return {"truncated": True, "preview": text[:limit_bytes]}


def _duration(started_at, finished_at):
    try:
        if started_at is None or finished_at is None:
            return None
        return max(0, int(round((float(finished_at) - float(started_at)) * 1000)))
    except (TypeError, ValueError):
        return None


def _brief(item):
    return {
        "id": item.get("id"),
        "provider": item.get("provider"),
        "domain": item.get("domain"),
        "action": item.get("action"),
        "status": item.get("status"),
        "finished_iso": item.get("finished_iso"),
        "error": item.get("error"),
    }


def public(item):
    """Deep copy through JSON so callers cannot mutate stored rows."""
    import json

    return json.loads(json.dumps(item))
