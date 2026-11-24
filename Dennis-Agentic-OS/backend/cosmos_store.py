"""Execution and recommendation stores for COSMOS.

Two append-oriented JSON stores:

* ``data/cosmos/executions.json`` — one record per agent run, scoped by
  ``agent_id`` and ``division``. Contains the exact configuration snapshot,
  model/provider, timing, result cards and error.
* ``data/cosmos/recommendations.json`` — discovery output with full
  provenance and an explicit review lifecycle. Drafting is the only path into
  a backlog; rejection remains in history.

Reads are always scoped so one agent's private history can never appear as
another agent's activity. Shared backlog records are referenced by id only.
"""

from __future__ import annotations

import copy

from . import cosmos_common as common

EXECUTION_STATUSES = ("running", "succeeded", "failed")
RECOMMENDATION_STATUSES = ("proposed", "drafted", "rejected", "refined", "archived")


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------
def _load_executions():
    return common.load_store(common.EXECUTIONS_FILE, "executions")


def _save_executions(items):
    common.save_store(common.EXECUTIONS_FILE, "executions", items[-common.MAX_EXECUTIONS:])


def start_execution(agent_id, division, capability, task, model, provider, config_snapshot, prompt_sha256):
    stamp = common.now()
    record = {
        "id": common.new_id("exe_"),
        "agent_id": agent_id,
        "division": division,
        "capability": capability,
        "task": common.clean_text(task, "Task", 8000),
        "status": "running",
        "model": common.clean_text(model, "Model", 160),
        "provider": common.clean_text(provider, "Provider", 80),
        "prompt_sha256": prompt_sha256,
        "config_snapshot": copy.deepcopy(config_snapshot),
        "result": None,
        "recommendation_ids": [],
        "duplicates_skipped": 0,
        "filtered_out": 0,
        "insufficient_results": False,
        "error": None,
        "started_at": stamp,
        "started_iso": common.iso(stamp),
        "finished_at": None,
        "finished_iso": None,
        "duration_ms": None,
    }
    with common.acquire():
        items = _load_executions()
        items.append(record)
        _save_executions(items)
    return copy.deepcopy(record)


def finish_execution(execution_id, status, result=None, error=None, recommendation_ids=None,
                     duplicates_skipped=0, filtered_out=0, insufficient_results=False):
    if status not in EXECUTION_STATUSES:
        raise common.CosmosError("invalid_execution_status", "Unknown execution status.", 400)
    with common.acquire():
        items = _load_executions()
        record = next((item for item in items if item.get("id") == execution_id), None)
        if not record:
            raise common.CosmosError("execution_not_found", "Execution not found.", 404)
        stamp = common.now()
        record["status"] = status
        record["finished_at"] = stamp
        record["finished_iso"] = common.iso(stamp)
        record["duration_ms"] = int(max(0, stamp - (record.get("started_at") or stamp)) * 1000)
        record["recommendation_ids"] = list(recommendation_ids or [])
        record["duplicates_skipped"] = int(duplicates_skipped or 0)
        record["filtered_out"] = int(filtered_out or 0)
        record["insufficient_results"] = bool(insufficient_results)
        if result is not None:
            encoded = common.public(result)
            blob = common.hash_text(encoded)
            # The engine already bounds results; this is a final guard.
            if len(str(encoded)) <= common.MAX_RESULT_BYTES:
                record["result"] = encoded
                record["result_sha256"] = blob
        if error:
            record["error"] = {
                "code": common.clean_text(error.get("code"), "Error code", 80) or "execution_error",
                "message": common.clean_text(error.get("message"), "Error", 2000),
            }
        _save_executions(items)
    return copy.deepcopy(record)


def list_executions(agent_id=None, division=None, status=None, limit=20):
    with common.acquire():
        items = _load_executions()
    if agent_id:
        items = [item for item in items if item.get("agent_id") == agent_id]
    if division:
        items = [item for item in items if item.get("division") == division]
    if status:
        items = [item for item in items if item.get("status") == status]
    items.sort(key=lambda item: item.get("started_at") or 0, reverse=True)
    try:
        requested = int(limit or 20)
    except (TypeError, ValueError):
        requested = 20
    requested = max(1, min(requested, 100))
    # Keep history payloads readable: config snapshots are retained in full
    # only on the single-execution read.
    trimmed = []
    for item in items[:requested]:
        entry = copy.deepcopy(item)
        entry.pop("config_snapshot", None)
        trimmed.append(entry)
    return {
        "executions": trimmed,
        "total": len(items),
        "latest_iso": items[0].get("finished_iso") or items[0].get("started_iso") if items else None,
    }


def get_execution(execution_id):
    with common.acquire():
        items = _load_executions()
    for item in items:
        if item.get("id") == str(execution_id or ""):
            return copy.deepcopy(item)
    raise common.CosmosError("execution_not_found", "Execution not found.", 404)


def recent_execution_context(agent_id, division, limit=4):
    with common.acquire():
        items = _load_executions()
    rows = [
        item for item in items
        if item.get("agent_id") == agent_id and item.get("division") == division
        and item.get("status") == "succeeded"
    ]
    rows.sort(key=lambda item: item.get("started_at") or 0, reverse=True)
    context = []
    for item in rows[:limit]:
        result = item.get("result") or {}
        context.append({
            "execution_id": item.get("id"),
            "task": item.get("task"),
            "summary": result.get("summary"),
            "finished_iso": item.get("finished_iso"),
            "recommendation_ids": item.get("recommendation_ids") or [],
        })
    return context


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------
def _load_recommendations():
    return common.load_store(common.RECOMMENDATIONS_FILE, "recommendations")


def _save_recommendations(items):
    common.save_store(common.RECOMMENDATIONS_FILE, "recommendations", items[-common.MAX_RECOMMENDATIONS:])


def recommendation_key(division, payload):
    if division == "youtube":
        label = payload.get("title") or ""
    else:
        label = payload.get("name") or ""
    parts = [common.normalize_key(label)]
    for field in ("merchant", "product_url"):
        value = common.normalize_key(payload.get(field))
        if value:
            parts.append(value)
    return "|".join(part for part in parts if part)


def find_active_recommendation(dedupe_key):
    """A proposed/drafted card with the same key blocks duplicates."""
    if not dedupe_key:
        return None
    with common.acquire():
        items = _load_recommendations()
    for item in items:
        if item.get("dedupe_key") == dedupe_key and item.get("status") in ("proposed", "drafted"):
            return copy.deepcopy(item)
    return None


def create_recommendation(agent_id, division, execution_id, capability, template_id,
                          config_snapshot, title, summary, payload, sources,
                          relevance_score, opportunity=None, competition=None,
                          evidence_status="hypothesis", dedupe_key=None):
    stamp = common.now()
    record = {
        "id": common.new_id("rec_"),
        "agent_id": agent_id,
        "division": division,
        "execution_id": execution_id,
        "capability": capability,
        "template_id": template_id,
        "config_snapshot": copy.deepcopy(config_snapshot),
        "title": common.clean_text(title, "Title", 300, required=True),
        "summary": common.clean_text(summary, "Summary", 2000),
        "payload": common.public(payload),
        "sources": copy.deepcopy(sources or []),
        "relevance_score": common.clamp_int(relevance_score, 0, 100, 0),
        "opportunity": common.clean_text(opportunity, "Opportunity", 800) or None,
        "competition": common.clean_text(competition, "Competition", 800) or None,
        "evidence_status": common.clean_text(evidence_status, "Evidence", 40) or "hypothesis",
        "status": "proposed",
        "dedupe_key": dedupe_key or recommendation_key(division, payload),
        "user_decision": None,
        "destination": None,
        "affiliate_validation": None,
        "refined_from": None,
        "refined_to": None,
        "last_draft_error": None,
        "created_at": stamp,
        "created_iso": common.iso(stamp),
        "updated_at": stamp,
        "updated_iso": common.iso(stamp),
    }
    with common.acquire():
        items = _load_recommendations()
        items.append(record)
        _save_recommendations(items)
    return copy.deepcopy(record)


def list_recommendations(agent_id=None, division=None, status=None, limit=100):
    with common.acquire():
        items = _load_recommendations()
    if agent_id:
        items = [item for item in items if item.get("agent_id") == agent_id]
    if division:
        items = [item for item in items if item.get("division") == division]
    if status:
        items = [item for item in items if item.get("status") == status]
    items.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    try:
        requested = int(limit or 100)
    except (TypeError, ValueError):
        requested = 100
    requested = max(1, min(requested, 300))
    return {
        "recommendations": [copy.deepcopy(item) for item in items[:requested]],
        "total": len(items),
        "counts": counts_for(items),
    }


def counts_for(items):
    result = {status: 0 for status in RECOMMENDATION_STATUSES}
    for item in items:
        status = item.get("status") or "proposed"
        result[status] = result.get(status, 0) + 1
    result["total"] = len(items)
    return result


def recommendation_counts(agent_id=None, division=None):
    with common.acquire():
        items = _load_recommendations()
    if agent_id:
        items = [item for item in items if item.get("agent_id") == agent_id]
    if division:
        items = [item for item in items if item.get("division") == division]
    return counts_for(items)


def get_recommendation(recommendation_id):
    with common.acquire():
        items = _load_recommendations()
    for item in items:
        if item.get("id") == str(recommendation_id or ""):
            return copy.deepcopy(item)
    raise common.CosmosError("recommendation_not_found", "Recommendation not found.", 404)


def _update(recommendation_id, mutate):
    with common.acquire():
        items = _load_recommendations()
        item = next((entry for entry in items if entry.get("id") == str(recommendation_id or "")), None)
        if not item:
            raise common.CosmosError("recommendation_not_found", "Recommendation not found.", 404)
        mutate(item)
        item["updated_at"] = common.now()
        item["updated_iso"] = common.iso()
        _save_recommendations(items)
        return copy.deepcopy(item)


def mark_drafted(recommendation_id, destination_type, destination_id):
    def mutate(item):
        if item.get("status") == "rejected":
            raise common.CosmosError("recommendation_rejected", "Rejected recommendations cannot be drafted.", 409)
        item["status"] = "drafted"
        item["user_decision"] = {
            "decision": "draft",
            "at": common.now(),
            "iso": common.iso(),
            "note": "Drafted to %s." % destination_type,
        }
        item["destination"] = {
            "type": destination_type,
            "id": destination_id,
            "created_at": common.now(),
            "created_iso": common.iso(),
        }
        item["last_draft_error"] = None

    return _update(recommendation_id, mutate)


def mark_draft_error(recommendation_id, message):
    def mutate(item):
        item["last_draft_error"] = common.clean_text(message, "Draft error", 1000)
    return _update(recommendation_id, mutate)


def mark_rejected(recommendation_id, note=None):
    def mutate(item):
        if item.get("status") == "drafted":
            raise common.CosmosError("recommendation_drafted", "Already drafted; manage it in the backlog.", 409)
        if item.get("status") == "rejected":
            return
        item["status"] = "rejected"
        item["user_decision"] = {
            "decision": "reject",
            "at": common.now(),
            "iso": common.iso(),
            "note": common.clean_text(note, "Note", 1000),
        }

    return _update(recommendation_id, mutate)


def mark_refined(recommendation_id, new_recommendation_id):
    def mutate(item):
        item["status"] = "refined"
        item["refined_to"] = new_recommendation_id
        item["user_decision"] = {
            "decision": "refine",
            "at": common.now(),
            "iso": common.iso(),
            "note": "Refined into %s." % new_recommendation_id,
        }

    return _update(recommendation_id, mutate)


def enforce_proposed_cap(agent_id, division, cap=5):
    """Keep a division's proposal board at ``cap`` cards.

    Autonomous discovery is expected to maintain a full five-card board. When
    a run pushes the number of live proposals past the cap, the oldest extra
    proposals are archived out of the board (kept in history with an explicit
    note, never deleted).
    """
    if cap < 1:
        return []
    with common.acquire():
        items = _load_recommendations()
        live = [item for item in items
                if item.get("agent_id") == agent_id
                and item.get("division") == division
                and item.get("status") == "proposed"
                and not item.get("archived")]
        live.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
        overflow = {item["id"] for item in live[cap:]}
        if not overflow:
            return []
        archived = []
        for item in items:
            if item.get("id") in overflow:
                item["status"] = "archived"
                item["archived"] = True
                item["user_decision"] = {
                    "decision": "archived",
                    "at": common.now(),
                    "iso": common.iso(),
                    "note": "Board cap: replaced by newer proposals (five-card board).",
                }
                item["updated_at"] = common.now()
                item["updated_iso"] = common.iso()
                archived.append(item["id"])
        _save_recommendations(items)
        return archived


def set_affiliate_validation(recommendation_id, payload):
    def mutate(item):
        item["affiliate_validation"] = {
            "status": payload.get("status") or "requested",
            "confirmed": bool(payload.get("confirmed")),
            "url": payload.get("url"),
            "program": payload.get("program"),
            "evidence": payload.get("evidence"),
            "note": payload.get("note"),
            "at": common.now(),
            "iso": common.iso(),
        }
        if payload.get("status") == "validated":
            item["payload"]["affiliate_status"] = "validated"
        elif payload.get("status") == "requested":
            item["payload"]["affiliate_status"] = "pending"
        else:
            item["payload"]["affiliate_status"] = payload.get("affiliate_status") or item["payload"].get("affiliate_status") or "unvalidated"

    return _update(recommendation_id, mutate)


# ---------------------------------------------------------------------------
# Overview scoping
# ---------------------------------------------------------------------------
def agent_activity(agent_id, limit=8):
    """Dashboard activity feed for one agent only (both divisions)."""
    with common.acquire():
        executions = _load_executions()
        recommendations = _load_recommendations()
    rows = [item for item in executions if item.get("agent_id") == agent_id]
    rows.sort(key=lambda item: item.get("started_at") or 0, reverse=True)
    recs = [item for item in recommendations if item.get("agent_id") == agent_id]
    return {
        "executions": [
            {
                "id": item.get("id"),
                "division": item.get("division"),
                "capability": item.get("capability"),
                "status": item.get("status"),
                "task": item.get("task"),
                "started_iso": item.get("started_iso"),
                "finished_iso": item.get("finished_iso"),
                "duration_ms": item.get("duration_ms"),
                "error": item.get("error"),
            }
            for item in rows[:limit]
        ],
        "recommendations": counts_for(recs),
    }
