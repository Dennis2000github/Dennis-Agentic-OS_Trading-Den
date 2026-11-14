"""Niche research and competitor intelligence for YouTube Studio.

Implements the channel master prompt's validation engine: five criteria scored
1-5 (max 25) with an 18/25 approval threshold and uncertainty flags.

Honesty rules enforced here:

* every validation carries a fixed disclaimer stating scores are estimates;
* competitor observations must be marked ``observed`` or ``assumption``;
* sources are stored with an explicit ``sourced`` flag and optional URL;
* nothing in this module invents search volume, views, subscribers or RPM.
"""

from __future__ import annotations

import os

from . import youtube_channels as channels
from . import youtube_common as common

RESEARCH_FILE_NAME = "research.json"
COMPETITORS_FILE_NAME = "competitors.json"

CRITERIA = ("rpm_potential", "proven_demand", "geographic_audience", "content_sustainability", "content_gap")
MIN_SCORE = 1
MAX_SCORE = 5
MAX_TOTAL = 25
APPROVAL_THRESHOLD = 18
FLAGS = ("legal_uncertainty", "factual_uncertainty", "geographic_uncertainty", "source_missing")
OBSERVATION_BASES = ("observed", "assumption")

VALIDATION_DISCLAIMER = (
    "Validation scores are analyst estimates, not measured platform data. "
    "Search volume, competitor views, and RPM figures are never invented and must come from a recorded source."
)


class ResearchError(common.YoutubeError):
    pass


def _research_file():
    return os.path.join(common.YOUTUBE_DIR, RESEARCH_FILE_NAME)


def _competitors_file():
    return os.path.join(common.YOUTUBE_DIR, COMPETITORS_FILE_NAME)


def criteria_meta():
    """Criteria metadata from the pinned seed (labels + guiding questions)."""
    meta = {item["id"]: item for item in channels.seed().get("validation", {}).get("criteria", [])}
    return [
        {
            "id": criterion,
            "label": (meta.get(criterion) or {}).get("label") or criterion.replace("_", " ").title(),
            "question": (meta.get(criterion) or {}).get("question") or "",
            "min_score": MIN_SCORE,
            "max_score": MAX_SCORE,
        }
        for criterion in CRITERIA
    ]


def validation_rules():
    return {
        "criteria": criteria_meta(),
        "min_score": MIN_SCORE,
        "max_score": MAX_SCORE,
        "max_total": MAX_TOTAL,
        "approval_threshold": APPROVAL_THRESHOLD,
        "flags": list(FLAGS),
        "disclaimer": VALIDATION_DISCLAIMER,
    }


# ---------------------------------------------------------------------------
# Validations
# ---------------------------------------------------------------------------
def _load_validations():
    data = common.read_store(_research_file(), "validations")
    return [item for item in data.get("validations", []) if isinstance(item, dict) and item.get("id")]


def _save_validations(items):
    common.write_store(_research_file(), "validations", items)


def _clean_criteria(value):
    if not isinstance(value, dict):
        raise ResearchError("invalid_validation", "criteria must be an object with the five scored criteria.")
    cleaned = {}
    for criterion in CRITERIA:
        entry = value.get(criterion)
        if not isinstance(entry, dict):
            raise ResearchError("invalid_validation", "Score every criterion (missing: %s)." % criterion)
        try:
            score = int(entry.get("score"))
        except (TypeError, ValueError):
            raise ResearchError("invalid_validation", "%s score must be a number 1-5." % criterion)
        if not MIN_SCORE <= score <= MAX_SCORE:
            raise ResearchError("invalid_validation", "%s score must be between 1 and 5." % criterion)
        cleaned[criterion] = {
            "score": score,
            "note": common.clean_text(entry.get("note"), "%s note" % criterion, 600),
            "basis": "estimate" if str(entry.get("basis") or "estimate") != "sourced" else "sourced",
            "source_label": common.clean_text(entry.get("source_label"), "%s source" % criterion, 160) or None,
        }
    return cleaned


def _score_total(criteria):
    return sum(int(criteria[c]["score"]) for c in CRITERIA)


def _verdict(total, flags):
    if flags:
        return "review"
    if total < APPROVAL_THRESHOLD:
        return "fail"
    return "pass"


def _clean_sources(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ResearchError("invalid_validation", "sources must be a list.")
    if len(value) > 20:
        raise ResearchError("invalid_validation", "A validation can reference at most 20 sources.")
    sources = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ResearchError("invalid_validation", "Each source must be an object.")
        label = common.clean_text(entry.get("label"), "Source label", 200, required=True)
        url = common.clean_text(entry.get("url"), "Source URL", 500)
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise ResearchError("invalid_validation", "Source URLs must be http(s).")
        sources.append(
            {
                "label": label,
                "url": url or None,
                "sourced": common.clean_bool(entry.get("sourced"), bool(url)),
                "note": common.clean_text(entry.get("note"), "Source note", 300) or None,
            }
        )
    return sources


def create_validation(channel_id, payload):
    if not isinstance(payload, dict):
        raise ResearchError("invalid_validation", "Validation payload must be a JSON object.")
    channel = channels.get_channel(channel_id)
    subject = common.clean_text(payload.get("subject"), "Subject", 160, required=True)
    pillar = common.clean_text(payload.get("pillar"), "Pillar", 80)
    criteria = _clean_criteria(payload.get("criteria"))
    flags = [flag for flag in common.clean_str_list(payload.get("flags"), "Flag", len(FLAGS), 60) if flag in FLAGS]
    sources = _clean_sources(payload.get("sources"))
    summary = common.clean_text(payload.get("summary"), "Summary", 4000)
    stamp = common.now()
    total = _score_total(criteria)
    validation = {
        "id": common.new_id("ytr_"),
        "channel_id": channel["id"],
        "kind": "niche_validation",
        "subject": subject,
        "pillar": pillar or None,
        "criteria": criteria,
        "total_score": total,
        "max_total": MAX_TOTAL,
        "approval_threshold": APPROVAL_THRESHOLD,
        "verdict": _verdict(total, flags),
        "flags": flags,
        "sources": sources,
        "summary": summary,
        "disclaimer": VALIDATION_DISCLAIMER,
        "status": "saved",
        "topic_id": None,
        "archived": False,
        "deleted_at": None,
        "created_at": stamp,
        "created_iso": common.iso(stamp),
        "updated_at": stamp,
        "updated_iso": common.iso(stamp),
    }
    with common.acquire():
        items = _load_validations()
        items.append(validation)
        _save_validations(items)
    return common.public(validation)


def list_validations(channel_id, pillar=None, verdict=None, query=None, include_deleted=False):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_validations()
    items = [v for v in items if v.get("channel_id") == channel["id"]]
    if not include_deleted:
        items = [v for v in items if not v.get("deleted_at")]
    if pillar:
        items = [v for v in items if v.get("pillar") == pillar]
    if verdict:
        items = [v for v in items if v.get("verdict") == verdict]
    needle = str(query or "").strip().lower()
    if needle:
        items = [v for v in items if needle in ("%s %s" % (v.get("subject", ""), v.get("summary", ""))).lower()]
    items.sort(key=lambda v: v.get("updated_at") or 0, reverse=True)
    return {"validations": [common.public(v) for v in items], "total": len(items), "rules": validation_rules()}


def get_validation(channel_id, validation_id, include_deleted=False):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_validations()
    item = common.find(items, str(validation_id or ""))
    if not item or item.get("channel_id") != channel["id"] or (item.get("deleted_at") and not include_deleted):
        raise ResearchError("validation_not_found", "Research validation not found.", 404)
    return common.public(item)


def update_validation(channel_id, validation_id, patch):
    if not isinstance(patch, dict):
        raise ResearchError("invalid_validation", "Validation payload must be a JSON object.")
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_validations()
        item = common.find(items, str(validation_id or ""))
        if not item or item.get("channel_id") != channel["id"] or item.get("deleted_at"):
            raise ResearchError("validation_not_found", "Research validation not found.", 404)
        if "subject" in patch:
            item["subject"] = common.clean_text(patch.get("subject"), "Subject", 160, required=True)
        if "pillar" in patch:
            item["pillar"] = common.clean_text(patch.get("pillar"), "Pillar", 80) or None
        if "criteria" in patch:
            item["criteria"] = _clean_criteria(patch.get("criteria"))
            item["total_score"] = _score_total(item["criteria"])
        if "flags" in patch:
            item["flags"] = [f for f in common.clean_str_list(patch.get("flags"), "Flag", len(FLAGS), 60) if f in FLAGS]
        if "sources" in patch:
            item["sources"] = _clean_sources(patch.get("sources"))
        if "summary" in patch:
            item["summary"] = common.clean_text(patch.get("summary"), "Summary", 4000)
        item["verdict"] = _verdict(item.get("total_score") or _score_total(item["criteria"]), item.get("flags") or [])
        stamp = common.now()
        item["updated_at"] = stamp
        item["updated_iso"] = common.iso(stamp)
        _save_validations(items)
    return common.public(item)


def delete_validation(channel_id, validation_id):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_validations()
        item = common.find(items, str(validation_id or ""))
        if not item or item.get("channel_id") != channel["id"] or item.get("deleted_at"):
            raise ResearchError("validation_not_found", "Research validation not found.", 404)
        common.soft_delete(item)
        _save_validations(items)
    return {"deleted": item["id"]}


# ---------------------------------------------------------------------------
# Competitors
# ---------------------------------------------------------------------------
def _load_competitors():
    data = common.read_store(_competitors_file(), "competitors")
    return [item for item in data.get("competitors", []) if isinstance(item, dict) and item.get("id")]


def _save_competitors(items):
    common.write_store(_competitors_file(), "competitors", items)


def ensure_competitor_seed():
    """Create the master prompt's reference competitors once (empty observations)."""
    with common.acquire():
        items = _load_competitors()
        if items:
            return items
        stamp = common.now()
        for spec in channels.seed().get("competitors", []):
            items.append(
                {
                    "id": common.new_id("ytc_"),
                    "name": common.clean_text(spec.get("name"), "Competitor name", 120, required=True),
                    "url": common.clean_text(spec.get("url"), "URL", 500) or None,
                    "reference": True,
                    "positioning": "",
                    "audience": "",
                    "video_length": "",
                    "storytelling_style": "",
                    "observations": [],
                    "content_gaps": [],
                    "unanswered_questions": [],
                    "note": channels.seed().get("competitor_note") or "",
                    "archived": False,
                    "deleted_at": None,
                    "created_at": stamp,
                    "created_iso": common.iso(stamp),
                    "updated_at": stamp,
                    "updated_iso": common.iso(stamp),
                }
            )
        _save_competitors(items)
        return items


def list_competitors(channel_id=None, query=None, include_deleted=False):
    """Competitors are shared reference data (not channel-scoped)."""
    with common.acquire():
        ensure_competitor_seed()
        items = _load_competitors()
    if not include_deleted:
        items = [c for c in items if not c.get("deleted_at")]
    needle = str(query or "").strip().lower()
    if needle:
        items = [
            c for c in items
            if needle in ("%s %s %s" % (c.get("name", ""), c.get("positioning", ""), c.get("audience", ""))).lower()
        ]
    items.sort(key=lambda c: (not c.get("reference"), (c.get("name") or "").lower()))
    return {"competitors": [common.public(c) for c in items], "total": len(items), "note": channels.seed().get("competitor_note")}


def get_competitor(competitor_id, include_deleted=False):
    with common.acquire():
        items = _load_competitors()
    item = common.find(items, str(competitor_id or ""))
    if not item or (item.get("deleted_at") and not include_deleted):
        raise ResearchError("competitor_not_found", "Competitor not found.", 404)
    return common.public(item)


def create_competitor(payload):
    if not isinstance(payload, dict):
        raise ResearchError("invalid_competitor", "Competitor payload must be a JSON object.")
    name = common.clean_text(payload.get("name"), "Competitor name", 120, required=True)
    url = common.clean_text(payload.get("url"), "URL", 500)
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise ResearchError("invalid_competitor", "Competitor URLs must be http(s).")
    with common.acquire():
        items = _load_competitors()
        for item in items:
            if not item.get("deleted_at") and item.get("name", "").lower() == name.lower():
                raise ResearchError("duplicate_competitor", "This competitor already exists.", 409)
        stamp = common.now()
        item = {
            "id": common.new_id("ytc_"),
            "name": name,
            "url": url or None,
            "reference": False,
            "positioning": common.clean_text(payload.get("positioning"), "Positioning", 600),
            "audience": common.clean_text(payload.get("audience"), "Audience", 600),
            "video_length": common.clean_text(payload.get("video_length"), "Video length", 80),
            "storytelling_style": common.clean_text(payload.get("storytelling_style"), "Style", 600),
            "observations": [],
            "content_gaps": common.clean_str_list(payload.get("content_gaps"), "Content gap", 20, 300),
            "unanswered_questions": common.clean_str_list(payload.get("unanswered_questions"), "Question", 20, 300),
            "note": common.clean_text(payload.get("note"), "Note", 600),
            "archived": False,
            "deleted_at": None,
            "created_at": stamp,
            "created_iso": common.iso(stamp),
            "updated_at": stamp,
            "updated_iso": common.iso(stamp),
        }
        items.append(item)
        _save_competitors(items)
    return common.public(item)


def update_competitor(competitor_id, patch):
    if not isinstance(patch, dict):
        raise ResearchError("invalid_competitor", "Competitor payload must be a JSON object.")
    with common.acquire():
        items = _load_competitors()
        item = common.find(items, str(competitor_id or ""))
        if not item or item.get("deleted_at"):
            raise ResearchError("competitor_not_found", "Competitor not found.", 404)
        for field in ("positioning", "audience", "video_length", "storytelling_style", "note"):
            if field in patch:
                item[field] = common.clean_text(patch.get(field), field.replace("_", " ").title(), 600)
        if "url" in patch:
            url = common.clean_text(patch.get("url"), "URL", 500)
            if url and not (url.startswith("http://") or url.startswith("https://")):
                raise ResearchError("invalid_competitor", "Competitor URLs must be http(s).")
            item["url"] = url or None
        if "content_gaps" in patch:
            item["content_gaps"] = common.clean_str_list(patch.get("content_gaps"), "Content gap", 20, 300)
        if "unanswered_questions" in patch:
            item["unanswered_questions"] = common.clean_str_list(patch.get("unanswered_questions"), "Question", 20, 300)
        if "archived" in patch:
            item["archived"] = common.clean_bool(patch.get("archived"))
        stamp = common.now()
        item["updated_at"] = stamp
        item["updated_iso"] = common.iso(stamp)
        _save_competitors(items)
    return common.public(item)


def add_observation(competitor_id, payload):
    if not isinstance(payload, dict):
        raise ResearchError("invalid_observation", "Observation payload must be a JSON object.")
    note = common.clean_text(payload.get("note"), "Observation", 600, required=True)
    basis = str(payload.get("basis") or "observed").strip().lower()
    if basis not in OBSERVATION_BASES:
        raise ResearchError("invalid_observation", "Observations must be marked observed or assumption.")
    with common.acquire():
        items = _load_competitors()
        item = common.find(items, str(competitor_id or ""))
        if not item or item.get("deleted_at"):
            raise ResearchError("competitor_not_found", "Competitor not found.", 404)
        if len(item.get("observations") or []) >= 40:
            raise ResearchError("observation_limit", "This competitor already has 40 observations.", 409)
        stamp = common.now()
        observation = {
            "id": common.new_id("ytob_"),
            "note": note,
            "basis": basis,
            "created_iso": common.iso(stamp),
        }
        item.setdefault("observations", []).append(observation)
        item["updated_at"] = stamp
        item["updated_iso"] = common.iso(stamp)
        _save_competitors(items)
    return common.public(item)


def delete_observation(competitor_id, observation_id):
    with common.acquire():
        items = _load_competitors()
        item = common.find(items, str(competitor_id or ""))
        if not item or item.get("deleted_at"):
            raise ResearchError("competitor_not_found", "Competitor not found.", 404)
        before = len(item.get("observations") or [])
        item["observations"] = [o for o in (item.get("observations") or []) if o.get("id") != observation_id]
        if len(item["observations"]) == before:
            raise ResearchError("observation_not_found", "Observation not found.", 404)
        stamp = common.now()
        item["updated_at"] = stamp
        item["updated_iso"] = common.iso(stamp)
        _save_competitors(items)
    return common.public(item)


def delete_competitor(competitor_id):
    with common.acquire():
        items = _load_competitors()
        item = common.find(items, str(competitor_id or ""))
        if not item or item.get("deleted_at"):
            raise ResearchError("competitor_not_found", "Competitor not found.", 404)
        common.soft_delete(item)
        _save_competitors(items)
    return {"deleted": item["id"]}
