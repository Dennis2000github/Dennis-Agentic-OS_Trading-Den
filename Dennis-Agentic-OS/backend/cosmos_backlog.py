"""Recommendation review and backlog routing for COSMOS.

Three explicit owner operations:

* **draft**  — insert into the correct backlog (Ads Product Backlog or the
  existing YouTube Topic Backlog). Idempotent per recommendation.
* **reject** — keep the recommendation in history, never insert it.
* **refine** — run the same agent/division again with the recommendation as
  focus; the refined result becomes a new card linked to the original.

Affiliate validation is a separate operation. Discovery never validates an
affiliate relationship, and this module never contacts a provider: it records
what the owner explicitly confirmed (or that confirmation is still pending).
"""

from __future__ import annotations

import json

from . import ads_backlog
from . import cosmos_common as common
from . import cosmos_engine
from . import cosmos_store
from . import youtube_channels
from . import youtube_topics


def _provenance_note(recommendation):
    sources = recommendation.get("sources") or []
    source_text = "; ".join(
        source.get("url") or source.get("label") or ""
        for source in sources[:6]
        if source.get("url") or source.get("label")
    ) or "none supplied"
    note = (
        "COSMOS %(agent)s · %(division)s · execution %(execution)s · recommendation %(recommendation)s · template %(template)s\n"
        "Reason: %(reason)s\nSources: %(sources)s"
        % {
            "agent": str(recommendation.get("agent_id") or "").upper(),
            "division": recommendation.get("division"),
            "execution": recommendation.get("execution_id"),
            "recommendation": recommendation.get("id"),
            "template": recommendation.get("template_id"),
            "reason": (recommendation.get("payload") or {}).get("reason")
            or recommendation.get("summary")
            or "",
            "sources": source_text,
        }
    )
    return note[:2000]


def _channel_for(recommendation):
    snapshot = recommendation.get("config_snapshot") or {}
    channel_id = snapshot.get("channel_id") or (snapshot.get("channel") or {}).get("id")
    if channel_id:
        return youtube_channels.get_channel(channel_id)["id"]
    return youtube_channels.active_channel()["id"]


def draft(recommendation_id):
    recommendation = cosmos_store.get_recommendation(recommendation_id)
    if recommendation.get("status") == "rejected":
        raise common.CosmosError("recommendation_rejected", "Rejected recommendations cannot be drafted.", 409)
    if recommendation.get("status") == "drafted" and recommendation.get("destination"):
        return {
            "recommendation": recommendation,
            "destination": recommendation["destination"],
            "already_drafted": True,
        }
    division = recommendation.get("division")
    payload = recommendation.get("payload") or {}
    if division == "ads":
        try:
            product = ads_backlog.create_from_recommendation(recommendation)
        except ads_backlog.BacklogError as exc:
            cosmos_store.mark_draft_error(recommendation_id, exc.message)
            error = common.CosmosError(exc.code, exc.message, exc.status)
            error.recommendation_id = recommendation_id
            raise error
        destination_type = "ads_product"
        destination_id = product["id"]
        destination = {"type": destination_type, "id": destination_id, "product": product}
    else:
        channel_id = _channel_for(recommendation)
        intent = payload.get("search_intent") or "informational"
        if intent not in youtube_topics.SEARCH_INTENTS:
            intent = "informational"
        relevance = payload.get("commercial_relevance") or "unknown"
        if relevance not in youtube_topics.COMMERCIAL_RELEVANCE:
            relevance = "unknown"
        topic_payload = {
            "title": payload.get("title") or recommendation.get("title"),
            "angle": payload.get("description") or payload.get("reason") or "",
            "audience_question": payload.get("audience_question") or "",
            "pillar": payload.get("pillar") or "",
            "priority": "P2",
            "search_intent": intent,
            "commercial_relevance": relevance,
            "notes": _provenance_note(recommendation),
        }
        try:
            topic = youtube_topics.create_topic(channel_id, topic_payload)
        except youtube_topics.TopicError as exc:
            cosmos_store.mark_draft_error(recommendation_id, exc.message)
            error = common.CosmosError(exc.code, exc.message, exc.status)
            error.recommendation_id = recommendation_id
            raise error
        destination_type = "youtube_topic"
        destination_id = topic["id"]
        destination = {"type": destination_type, "id": destination_id, "topic": topic}

    updated = cosmos_store.mark_drafted(recommendation_id, destination_type, destination_id)
    return {
        "recommendation": updated,
        "destination": {
            "type": destination_type,
            "id": destination_id,
        },
        "record": destination.get("product") or destination.get("topic"),
        "already_drafted": False,
    }


def reject(recommendation_id, note=None):
    updated = cosmos_store.mark_rejected(recommendation_id, note)
    return {"recommendation": updated, "rejected": True}


def refine(recommendation_id, instruction=None):
    recommendation = cosmos_store.get_recommendation(recommendation_id)
    if recommendation.get("status") == "rejected":
        raise common.CosmosError("recommendation_rejected", "Rejected recommendations cannot be refined.", 409)
    instruction = common.clean_text(instruction, "Refinement instruction", 2000)
    focus = {
        "recommendation": {
            "id": recommendation.get("id"),
            "title": recommendation.get("title"),
            "payload": recommendation.get("payload"),
            "sources": recommendation.get("sources"),
            "evidence_status": recommendation.get("evidence_status"),
        },
        "owner_instruction": instruction or "Tighten the recommendation while keeping it inside the active configuration.",
    }
    snapshot = recommendation.get("config_snapshot") or {}
    task = (
        "Refine the focused recommendation below. Keep the same division and capability. "
        "Return one improved recommendation with a clearer reason, stronger configuration fit, "
        "and honest evidence. Do not create unrelated items.\n\n"
        + json.dumps(focus["recommendation"], indent=2, ensure_ascii=False)
        + "\n\nOwner refinement instruction: "
        + focus["owner_instruction"]
    )
    execution = cosmos_engine.run(
        recommendation.get("agent_id"),
        recommendation.get("division"),
        task,
        source_notes="",
        template_id=recommendation.get("template_id"),
        channel_id=snapshot.get("channel_id") or (snapshot.get("channel") or {}).get("id"),
        expected_items=1,
        focus=focus,
    )
    new_ids = execution.get("recommendation_ids") or []
    if not new_ids:
        raise common.CosmosError(
            "refine_no_result",
            "The refinement run did not produce a qualified recommendation. Nothing was changed.",
            409,
        )
    refined = cosmos_store.get_recommendation(new_ids[0])
    original = cosmos_store.mark_refined(recommendation_id, refined["id"])
    return {"execution": execution, "recommendation": refined, "original": original}


def validate_affiliate(recommendation_id, payload):
    recommendation = cosmos_store.get_recommendation(recommendation_id)
    if recommendation.get("division") != "ads":
        raise common.CosmosError("wrong_division", "Affiliate validation applies to Ads recommendations only.", 400)
    data = payload if isinstance(payload, dict) else {}
    confirmed = data.get("confirmed") is True
    url = common.clean_text(data.get("url"), "Affiliate URL", 1000) or None
    program = common.clean_text(data.get("program"), "Affiliate program", 200) or None
    evidence = common.clean_text(data.get("evidence"), "Evidence", 1000) or None
    status = "requested"
    if confirmed:
        if not url or not url.lower().startswith(("http://", "https://")):
            raise common.CosmosError("affiliate_url_required", "A valid affiliate tracking URL is required to record validation.", 400)
        if not program:
            raise common.CosmosError("affiliate_program_required", "The affiliate program name is required.", 400)
        status = "validated"
    updated = cosmos_store.set_affiliate_validation(recommendation_id, {
        "status": status,
        "confirmed": confirmed,
        "url": url,
        "program": program,
        "evidence": evidence,
        "note": data.get("note"),
    })
    return {
        "recommendation": updated,
        "status": status,
        "note": (
            "Owner confirmation recorded. No external provider was contacted."
            if status == "validated"
            else "Validation requested. Add the program and tracking URL to confirm."
        ),
    }
