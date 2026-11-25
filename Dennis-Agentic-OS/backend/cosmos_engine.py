"""COSMOS execution engine.

One shared pipeline serves every agent and both divisions:

    effective config -> role prompt -> studio_llm -> strict JSON
    -> validate/qualify -> persist execution -> persist recommendations

The engine never fabricates data. Discovery items are AI hypotheses qualified
against the active configuration; unrelated or low-relevance items are
filtered and the execution reports ``insufficient_results`` honestly instead
of padding the list. Ads affordability/affiliate claims stay unvalidated until
the owner performs the separate validation operation.
"""

from __future__ import annotations

import json
import re
import time

from . import ads_backlog
from . import cosmos_common as common
from . import cosmos_config
from . import cosmos_feedback
from . import cosmos_llm
from . import cosmos_registry
from . import cosmos_store
from . import studio_llm

MIN_RELEVANCE = 60
MAX_ITEMS = 20

_EVIDENCE = {"verified", "third_party_claim", "estimate", "hypothesis", "sourced"}
_SEARCH_INTENTS = {"informational", "commercial", "navigational", "comparison"}
_COMMERCIAL = {"high", "medium", "low", "unknown"}
_STRATEGY_VERDICTS = {"proceed", "revise", "hold", "reject"}
_RESEARCH_CLASSES = {"verified", "third_party_claim", "estimate", "hypothesis"}
_MEASUREMENT_BASES = {"measured", "estimate"}

_STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "into", "your", "you",
    "our", "their", "who", "what", "when", "where", "why", "how", "are", "was",
    "were", "will", "can", "could", "should", "would", "about", "best", "top",
    "using", "use", "make", "made", "new", "video", "product", "products",
    "topic", "topics", "content", "audience", "market", "channel",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _tokens(text):
    return {
        token for token in common.normalize_key(text).split()
        if len(token) > 2 and token not in _STOPWORDS
    }


def _overlap(text, reference):
    return len(_tokens(text) & _tokens(reference))


def _clean_sources(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    sources = []
    for entry in value[:12]:
        if isinstance(entry, str):
            entry = {"label": entry, "url": None, "type": "provided"}
        if not isinstance(entry, dict):
            continue
        label = common.clean_text(entry.get("label"), "Source label", 300)
        url = common.clean_text(entry.get("url"), "Source URL", 1000) or None
        if url and not url.lower().startswith(("http://", "https://")):
            url = None
        kind = str(entry.get("type") or "").strip().lower()
        if kind not in ("provided", "observed", "third_party", "none"):
            kind = "provided" if (label or url) else "none"
        if label or url:
            sources.append({"label": label, "url": url, "type": kind})
    return sources


def _has_owner_source(sources):
    return any(source.get("type") == "provided" and (source.get("url") or source.get("label")) for source in sources)


def _price(value, currency="PHP", verified=False, owner_source=False):
    if value in (None, ""):
        return None, False
    amount = None
    if isinstance(value, (int, float)):
        amount = round(float(value), 2)
    elif isinstance(value, dict):
        amount = value.get("amount")
        currency = common.clean_text(value.get("currency"), "Currency", 8) or currency
        if amount is not None:
            try:
                amount = round(float(amount), 2)
            except (TypeError, ValueError):
                amount = None
    elif isinstance(value, str):
        cleaned = re.sub(r"[^0-9.,]", "", value).replace(",", "")
        try:
            amount = round(float(cleaned), 2)
        except (TypeError, ValueError):
            amount = None
    if amount is None:
        return None, False
    return {"amount": amount, "currency": currency or "PHP", "verified": bool(verified and owner_source)}, bool(verified and owner_source)


def _norm_confidence(value, allowed, fallback):
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return text if text in allowed else fallback


def _canonical_pillar(value, channel):
    text = common.clean_text(value, "Pillar", 120)
    labels = [str(item.get("label") or "") for item in (channel.get("content_pillars") or []) if isinstance(item, dict)]
    if not labels:
        return text or None
    wanted = common.normalize_key(text)
    for label in labels:
        if common.normalize_key(label) == wanted:
            return label
    for label in labels:
        if wanted and (wanted in common.normalize_key(label) or common.normalize_key(label) in wanted):
            return label
    return text or None


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _normalize_ads_item(raw, snapshot):
    if not isinstance(raw, dict):
        return None
    template = snapshot.get("template") or {}
    currency = (template.get("price_range") or {}).get("currency") or "PHP"
    name = common.clean_text(raw.get("name") or raw.get("title"), "Product name", 300)
    if not name:
        return None
    sources = _clean_sources(raw.get("sources"))
    price, price_verified = _price(
        raw.get("price"),
        currency,
        bool(raw.get("price_verified")),
        _has_owner_source(sources),
    )
    affiliate = _norm_confidence(raw.get("affiliate_status"), {"unvalidated", "none", "potential"}, "unvalidated")
    description = common.clean_text(raw.get("description"), "Description", 3000)
    item = {
        "name": name,
        "description": description,
        "category": common.clean_text(raw.get("category"), "Category", 200) or template.get("product_category"),
        "merchant": common.clean_text(raw.get("merchant"), "Merchant", 200) or None,
        "price": price,
        "currency": currency,
        "price_verified": price_verified,
        "product_url": common.clean_text(raw.get("product_url"), "Product URL", 1000) or None,
        "image_url": common.clean_text(raw.get("image_url"), "Image URL", 1000) or None,
        "target_audience": common.clean_text(raw.get("target_audience"), "Target audience", 600) or template.get("target_audience"),
        "reason": common.clean_text(raw.get("reason"), "Reason", 1200),
        "sources": sources,
        "relevance_score": common.clamp_int(raw.get("relevance_score"), 0, 100, 0),
        # Discovery is never an affiliate validation.
        "affiliate_status": affiliate,
        "creative_format": common.clean_text(raw.get("creative_format"), "Creative format", 300) or template.get("video_format"),
        "evidence_status": "hypothesis",
    }
    for key in ("product_url", "image_url"):
        if item[key] and not item[key].lower().startswith(("http://", "https://")):
            item[key] = None
    return item


def _normalize_youtube_item(raw, snapshot):
    if not isinstance(raw, dict):
        return None
    template = snapshot.get("template") or {}
    channel = snapshot.get("channel") or {}
    title = common.clean_text(raw.get("title") or raw.get("name"), "Topic title", 300)
    if not title:
        return None
    return {
        "title": title,
        "description": common.clean_text(raw.get("description"), "Description", 3000),
        "pillar": _canonical_pillar(raw.get("pillar"), channel),
        "target_audience": common.clean_text(raw.get("target_audience"), "Target audience", 600) or channel.get("target_audience"),
        "video_style": common.clean_text(raw.get("video_style"), "Video style", 200) or template.get("video_style"),
        "reason": common.clean_text(raw.get("reason"), "Reason", 1200),
        "sources": _clean_sources(raw.get("sources")),
        "relevance_score": common.clamp_int(raw.get("relevance_score"), 0, 100, 0),
        "opportunity": common.clean_text(raw.get("opportunity"), "Opportunity", 1000) or None,
        "competition": common.clean_text(raw.get("competition"), "Competition", 1000) or None,
        "audience_question": common.clean_text(raw.get("audience_question"), "Audience question", 600),
        "suggested_format": _norm_confidence(raw.get("suggested_format"), {"longform", "shorts"}, template.get("format") or "longform"),
        "search_intent": _norm_confidence(raw.get("search_intent"), _SEARCH_INTENTS, "informational"),
        "commercial_relevance": _norm_confidence(raw.get("commercial_relevance"), _COMMERCIAL, "unknown"),
        "evidence_status": "hypothesis",
    }


def _normalize_analysis_item(raw):
    if not isinstance(raw, dict):
        return None
    subject = common.clean_text(raw.get("subject") or raw.get("title"), "Subject", 300)
    if not subject:
        return None
    return {
        "subject": subject,
        "verdict": _norm_confidence(raw.get("verdict"), _STRATEGY_VERDICTS, "revise"),
        "analysis": common.clean_text(raw.get("analysis") or raw.get("description"), "Analysis", 4000),
        "strengths": common.clean_str_list(raw.get("strengths"), "Strengths", 12, 300),
        "risks": common.clean_str_list(raw.get("risks"), "Risks", 12, 300),
        "priority": _norm_confidence(raw.get("priority"), {"p1", "p2", "p3"}, "p2").upper(),
        "recommended_actions": common.clean_str_list(raw.get("recommended_actions"), "Actions", 12, 300),
        "evidence_status": _norm_confidence(raw.get("evidence_status"), _EVIDENCE, "hypothesis"),
    }


def _normalize_creative_item(raw):
    if not isinstance(raw, dict):
        return None
    title = common.clean_text(raw.get("concept_title") or raw.get("title"), "Concept title", 300)
    if not title:
        return None
    platform = raw.get("platform")
    if isinstance(platform, list):
        platforms = common.clean_str_list(platform, "Platforms", 8, 80)
    else:
        platforms = common.clean_str_list([platform] if platform else [], "Platforms", 8, 80)
    return {
        "concept_title": title,
        "hook": common.clean_text(raw.get("hook"), "Hook", 1000),
        "format": common.clean_text(raw.get("format"), "Format", 200),
        "platforms": platforms,
        "structure": common.clean_str_list(raw.get("structure"), "Structure", 16, 400),
        "visual_direction": common.clean_text(raw.get("visual_direction"), "Visual direction", 2000),
        "script_direction": common.clean_text(raw.get("script_direction"), "Script direction", 3000),
        "cta": common.clean_text(raw.get("cta"), "CTA", 500) or None,
        "disclosure": common.clean_text(raw.get("disclosure"), "Disclosure", 600) or None,
        "assets_needed": common.clean_str_list(raw.get("assets_needed"), "Assets", 16, 200),
        "evidence_status": "hypothesis",
    }


def _normalize_research_item(raw):
    if not isinstance(raw, dict):
        return None
    claim = common.clean_text(raw.get("claim") or raw.get("subject"), "Claim", 1200)
    if not claim:
        return None
    sources = _clean_sources(raw.get("evidence"))
    return {
        "claim": claim,
        "classification": _norm_confidence(raw.get("classification"), _RESEARCH_CLASSES, "hypothesis"),
        "evidence": sources,
        "gaps": common.clean_str_list(raw.get("gaps"), "Gaps", 12, 500),
        "reliability": _norm_confidence(raw.get("reliability"), {"high", "medium", "low", "unknown"}, "unknown"),
        "recommendation": common.clean_text(raw.get("recommendation"), "Recommendation", 1500),
        "evidence_status": _norm_confidence(raw.get("classification"), _EVIDENCE, "hypothesis"),
    }


def _normalize_performance_item(raw):
    if not isinstance(raw, dict):
        return None
    subject = common.clean_text(raw.get("subject") or raw.get("title"), "Subject", 300)
    if not subject:
        return None
    measurements = []
    dropped = 0
    for entry in (raw.get("measurements") or [])[:60]:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        name = common.clean_text(entry.get("name"), "Metric name", 200)
        basis = _norm_confidence(entry.get("basis"), _MEASUREMENT_BASES, "")
        source = common.clean_text(entry.get("source"), "Metric source", 300)
        if not name or not basis or not source or source.lower() in ("model", "assistant", "inferred", "unknown"):
            dropped += 1
            continue
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            dropped += 1
            continue
        measurements.append({
            "name": name,
            "value": value if isinstance(value, (int, float)) else common.clean_text(value, "Value", 80),
            "unit": common.clean_text(entry.get("unit"), "Unit", 60),
            "basis": basis,
            "source": source,
        })
    insights = common.clean_str_list(raw.get("insights"), "Insights", 16, 600)
    if dropped:
        insights.append("%d measurement(s) were omitted because they lacked a measured/estimate basis or a source." % dropped)
    if not measurements:
        insights.append("No valid measurements were supplied. Provide measured or estimated metrics with a source before drawing performance conclusions.")
    return {
        "subject": subject,
        "period": common.clean_text(raw.get("period"), "Period", 200) or None,
        "measurements": measurements,
        "insights": insights,
        "patterns": common.clean_str_list(raw.get("patterns"), "Patterns", 16, 500),
        "next_actions": common.clean_str_list(raw.get("next_actions"), "Next actions", 16, 500),
        "evidence_status": "estimate" if measurements and all(m["basis"] == "estimate" for m in measurements) else ("verified" if measurements else "hypothesis"),
    }


def _normalize_item(division, kind, raw, snapshot):
    if kind == "recommendations":
        return _normalize_ads_item(raw, snapshot) if division == "ads" else _normalize_youtube_item(raw, snapshot)
    if kind == "analysis":
        return _normalize_analysis_item(raw)
    if kind == "creative":
        return _normalize_creative_item(raw)
    if kind == "research":
        return _normalize_research_item(raw)
    if kind == "performance":
        return _normalize_performance_item(raw)
    return None


# ---------------------------------------------------------------------------
# Qualification
# ---------------------------------------------------------------------------
def _qualify(division, item, snapshot):
    score = item.get("relevance_score") or 0
    if score < MIN_RELEVANCE:
        return False, "relevance score %d is below the %d threshold" % (score, MIN_RELEVANCE)
    if division == "ads":
        template = snapshot.get("template") or {}
        # Only owner/model-authored fields count: target_audience and
        # creative_format may have been defaulted from the template itself.
        text = " ".join(str(item.get(field) or "") for field in ("name", "description", "category"))
        reference = " ".join([
            str(template.get("product_category") or ""),
            " ".join(template.get("product_interests") or []),
            " ".join(template.get("creative_directions") or []),
            str(template.get("target_audience") or ""),
        ])
        merchant = common.normalize_key(item.get("merchant"))
        merchants = {common.normalize_key(name) for name in (template.get("merchants") or [])}
        if merchant and merchant in merchants:
            return True, "merchant matches the active template"
        if _overlap(text, reference) >= 2:
            return True, "matches the active Ads configuration"
        return False, "does not match the active Ads category, audience or product interests"
    channel = snapshot.get("channel") or {}
    template = snapshot.get("template") or {}
    # reason is excluded: models often restate the config in it.
    text = " ".join(str(item.get(field) or "") for field in ("title", "description", "audience_question"))
    pillar = common.normalize_key(item.get("pillar"))
    pillar_labels = [common.normalize_key(entry.get("label")) for entry in (channel.get("content_pillars") or []) if isinstance(entry, dict)]
    if pillar and any(pillar == label or pillar in label or label in pillar for label in pillar_labels if label):
        return True, "content pillar matches the channel"
    reference = " ".join([
        str(channel.get("content_niche") or ""),
        " ".join(pillar_labels),
        str(channel.get("target_audience") or ""),
        " ".join(template.get("audience_interests") or []),
        " ".join(template.get("structure") or []),
    ])
    if _overlap(text, reference) >= 2:
        return True, "matches the active channel configuration"
    return False, "does not match the channel niche, pillars or audience"


# ---------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------
def _config_for_prompt(snapshot):
    if snapshot.get("domain") == "ads":
        return {
            "domain": "ads",
            "template": snapshot.get("template"),
            "workspace_settings": snapshot.get("workspace_settings"),
            "existing_backlog_titles": (snapshot.get("backlog") or {}).get("active_titles") or [],
            "source_priority": snapshot.get("source_priority"),
        }
    return {
        "domain": "youtube",
        "channel": snapshot.get("channel"),
        "template": snapshot.get("template"),
        "existing_backlog_titles": (snapshot.get("backlog") or {}).get("existing_titles") or [],
        "source_priority": snapshot.get("source_priority"),
    }


def _user_prompt(agent_id, division, capability_def, snapshot, task, source_notes, context, focus, expected_items):
    contract = capability_def.get("item_contract") or {}
    min_items = expected_items if expected_items else capability_def.get("min_items", 1)
    payload = {
        "summary": "Short honest summary. State if the configuration could not support enough qualified items.",
        "items": [contract],
    }
    sections = [
        "TASK",
        task or "Execute your %s responsibility using the active configuration." % capability_def["label"],
        "",
        "EFFECTIVE CONFIGURATION (authoritative; do not contradict it)",
        json.dumps(_config_for_prompt(snapshot), indent=2, ensure_ascii=False),
        "",
        "PROVENANCE CONTEXT",
        json.dumps(context, indent=2, ensure_ascii=False),
        "",
        "SHARED INTEGRATION CAPABILITIES (COSMOS-level; use only through /api/integrations)",
        json.dumps(context.get("shared_integrations") or {"available": False},
                   indent=2, ensure_ascii=False),
        "",
        "OWNER-SUPPLIED SOURCE NOTES",
        source_notes.strip() if source_notes and source_notes.strip() else "(none provided — do not invent sources)",
        "",
        "OUTPUT CONTRACT",
        "Return one JSON object with exactly these top-level keys:",
        json.dumps(payload, indent=2, ensure_ascii=False),
        "",
        "RULES",
        "- Produce at least %d item(s) when the configuration genuinely supports them." % min_items,
        "- relevance_score must be 0-100 and honest. Qualification threshold is %d." % MIN_RELEVANCE,
        "- Use one of: hypothesis, estimate, third_party_claim, verified, sourced for evidence fields.",
        "- If you do not have a source, leave sources empty and keep the item a hypothesis.",
        "- Never pad the list with unrelated products/topics to reach the minimum.",
        "- Do not include URLs you did not receive.",
        "- For performance work, every measurement needs basis=measured|estimate and a real source; otherwise omit it.",
    ]
    feedback = (context or {}).get("owner_feedback") or []
    if feedback:
        sections += [
            "",
            "OWNER FEEDBACK (learn from this)",
            "The owner rejected or refined recent cards for the reasons below. Avoid repeating",
            "these mistakes: do not propose items similar to rejected ones, and favour the",
            "angles the owner asked for in refinements.",
            json.dumps(feedback, indent=2, ensure_ascii=False),
        ]
    if focus:
        sections += [
            "",
            "REFINEMENT FOCUS",
            json.dumps(focus, indent=2, ensure_ascii=False),
            "Return exactly one refined item. Do not create unrelated items.",
        ]
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run(agent_id, division, task, source_notes="", template_id=None, channel_id=None,
        expected_items=None, focus=None):
    """Execute one capability and persist its execution + recommendations."""
    agent = cosmos_registry.get_agent(agent_id)
    capability_def = cosmos_registry.capability(agent_id, division)
    division = str(division).strip().lower()
    task = common.clean_text(task, "Task", 8000)
    source_notes = common.clean_text(source_notes, "Source notes", 20000)
    if not task:
        task = "Execute your %s responsibility using the active configuration." % capability_def["label"]

    snapshot = cosmos_config.effective(division, template_id=template_id, channel_id=channel_id)
    # Each intelligence/division runs with its own Agent + Model choice. A
    # division with no stored choice inherits the shared Studio selection.
    llm_choice = cosmos_llm.resolve(agent_id, division)
    model = llm_choice.get("model") or ""
    provider = llm_choice.get("agent_id") or ""

    recent = cosmos_store.list_recommendations(agent_id=agent_id, division=division, limit=10).get("recommendations", [])
    context = {
        "own_recent_recommendations": [
            {"title": item.get("title"), "status": item.get("status"), "execution_id": item.get("execution_id")}
            for item in recent
        ],
        "recent_executions": cosmos_store.recent_execution_context(agent_id, division, limit=3),
        "backlog_counts": (snapshot.get("backlog") or {}).get("counts") or {},
        "owner_feedback": cosmos_feedback.recent_context(agent_id, division),
        "shared_integrations": cosmos_registry.shared_integrations(agent_id, division),
    }
    system = cosmos_registry.system_prompt(agent_id, division)
    user = _user_prompt(agent_id, division, capability_def, snapshot, task, source_notes,
                        context, focus, expected_items)
    prompt_sha = common.hash_text(system + "\n\n" + user)
    execution = cosmos_store.start_execution(
        agent_id, division, capability_def["id"], task, model, provider, snapshot, prompt_sha,
    )

    started = time.time()
    try:
        response = studio_llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            agent_id=llm_choice.get("agent_id"),
            model_id=model or None,
            temperature=0.35,
            max_tokens=8000,
        )
    except Exception as exc:
        code = getattr(exc, "code", "llm_request_failed")
        message = getattr(exc, "message", None) or str(exc) or "The model request failed."
        status = getattr(exc, "status", 502)
        cosmos_store.finish_execution(
            execution["id"], "failed",
            error={"code": code, "message": message},
        )
        error = common.CosmosError(code, message, status if 400 <= status <= 599 else 502)
        error.execution_id = execution["id"]
        raise error

    try:
        parsed = common.extract_json(response.get("content") if isinstance(response, dict) else response)
    except common.CosmosError as exc:
        cosmos_store.finish_execution(execution["id"], "failed", error={
            "code": exc.code, "message": exc.message,
        })
        exc.execution_id = execution["id"]
        raise
    if isinstance(parsed, list):
        parsed = {"summary": "", "items": parsed}
    if not isinstance(parsed, dict):
        cosmos_store.finish_execution(execution["id"], "failed", error={
            "code": "invalid_model_output", "message": "The model returned an unsupported JSON shape.",
        })
        error = common.CosmosError("invalid_model_output", "The model returned an unsupported JSON shape.", 502)
        error.execution_id = execution["id"]
        raise error

    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        cosmos_store.finish_execution(execution["id"], "failed", error={
            "code": "invalid_model_output", "message": "The model response has no items list.",
        })
        error = common.CosmosError("invalid_model_output", "The model response has no items list.", 502)
        error.execution_id = execution["id"]
        raise error

    kind = capability_def["kind"]
    items = []
    filtered = []
    for raw in raw_items[:MAX_ITEMS]:
        try:
            item = _normalize_item(division, kind, raw, snapshot)
        except common.CosmosError as exc:
            filtered.append({"title": "Unusable item", "reason": exc.message})
            continue
        if not item:
            filtered.append({"title": "Unusable item", "reason": "missing required fields"})
            continue
        if kind == "recommendations":
            qualified, reason = _qualify(division, item, snapshot)
        else:
            qualified, reason = True, ""
        item["qualified"] = bool(qualified)
        item["filter_reason"] = reason or None
        items.append(item)
        if not qualified:
            filtered.append({
                "title": item.get("title") or item.get("name") or "Item",
                "reason": reason,
            })

    qualified_items = [item for item in items if item.get("qualified")]
    min_items = expected_items if expected_items else capability_def.get("min_items", 1)
    recommendation_ids = []
    duplicates_skipped = 0

    if kind == "recommendations":
        backlog_titles = set()
        for title in (snapshot.get("backlog") or {}).get("existing_titles") or []:
            backlog_titles.add(common.normalize_key(title))
        focus_rec = ((focus or {}).get("recommendation") or {}) if isinstance(focus, dict) else {}
        ignore_recommendation_id = focus_rec.get("id")
        ignore_title = common.normalize_key(focus_rec.get("title"))
        if division == "ads":
            try:
                for product in ads_backlog.list_products(limit=2000, include_archived=True).get("products", []):
                    backlog_titles.add(common.normalize_key(product.get("name")))
            except Exception:
                pass
        seen = set()
        for item in qualified_items:
            label = item.get("title") or item.get("name")
            key = cosmos_store.recommendation_key(division, item)
            if key in seen:
                duplicates_skipped += 1
                item["qualified"] = False
                item["filter_reason"] = "duplicate within this run"
                continue
            seen.add(key)
            existing = cosmos_store.find_active_recommendation(key)
            if existing and existing.get("id") != ignore_recommendation_id:
                duplicates_skipped += 1
                item["qualified"] = False
                item["filter_reason"] = "already proposed or drafted"
                continue
            if common.normalize_key(label) in backlog_titles and common.normalize_key(label) != ignore_title:
                duplicates_skipped += 1
                item["qualified"] = False
                item["filter_reason"] = "already exists in the active backlog"
                continue
            record = cosmos_store.create_recommendation(
                agent_id=agent_id,
                division=division,
                execution_id=execution["id"],
                capability=capability_def["id"],
                template_id=snapshot.get("template_id"),
                config_snapshot=snapshot,
                title=label,
                summary=item.get("reason") or item.get("description") or "",
                payload=item,
                sources=item.get("sources") or [],
                relevance_score=item.get("relevance_score") or 0,
                opportunity=item.get("opportunity") if division == "youtube" else None,
                competition=item.get("competition") if division == "youtube" else None,
                evidence_status=item.get("evidence_status") or "hypothesis",
            )
            recommendation_ids.append(record["id"])
        qualified_items = [item for item in qualified_items if item.get("qualified")]

    insufficient = kind == "recommendations" and len(qualified_items) < min_items
    summary = str(parsed.get("summary") or "")[:3000].strip()
    if not summary:
        summary = "Produced %d item(s); %d qualified." % (len(items), len(qualified_items))
    if insufficient:
        summary += (
            " Only %d of the %d required qualified items were produced. "
            "No unrelated filler was added." % (len(qualified_items), min_items)
        )

    result = {
        "summary": summary,
        "kind": kind,
        "items": items,
        "qualified_count": len(qualified_items),
        "required_items": min_items,
        "insufficient_results": insufficient,
        "filtered": filtered[:20],
        "filtered_out": len(filtered),
        "duplicates_skipped": duplicates_skipped,
        "model": model,
        "provider": provider,
        "duration_ms": int((time.time() - started) * 1000),
    }
    _bound_result(result)
    finished = cosmos_store.finish_execution(
        execution["id"], "succeeded", result=result,
        recommendation_ids=recommendation_ids,
        duplicates_skipped=duplicates_skipped,
        filtered_out=len(filtered),
        insufficient_results=insufficient,
    )
    return finished


def _bound_result(result):
    encoded = json.dumps(result)
    if len(encoded) <= common.MAX_RESULT_BYTES:
        return result
    result["items"] = result["items"][:10]
    for item in result["items"]:
        for key, value in list(item.items()):
            if isinstance(value, str) and len(value) > 1200:
                item[key] = value[:1200] + "…"
    result["truncated"] = True
    return result
