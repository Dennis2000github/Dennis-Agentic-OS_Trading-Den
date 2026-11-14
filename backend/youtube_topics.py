"""Topic Studio: the YouTube topic pipeline with duplicate detection.

Status pipeline (from the channel master prompt and the workspace spec):

    idea -> researching -> validated -> approved -> scripted -> in_production -> published

``rejected`` is reachable from any non-published status. Transitions are
forward-only; each step has explicit requirements:

* ``validated`` requires a linked niche validation;
* ``approved`` requires the validation score to meet the 18/25 threshold;
* ``scripted`` is set automatically when a script is created from the topic.

Duplicate detection compares normalized title token sets (Jaccard) within the
channel; near-identical topics are blocked unless ``force`` is set.
"""

from __future__ import annotations

import os
import re

from . import youtube_channels as channels
from . import youtube_common as common
from . import youtube_research as research

TOPICS_FILE_NAME = "topics.json"

STATUSES = (
    "idea",
    "researching",
    "validated",
    "approved",
    "scripted",
    "in_production",
    "published",
    "rejected",
)
FORWARD_ORDER = {status: index for index, status in enumerate(STATUSES[:7])}
PRIORITIES = ("P1", "P2", "P3")
SEARCH_INTENTS = ("informational", "commercial", "navigational", "comparison")
COMMERCIAL_RELEVANCE = ("high", "medium", "low", "unknown")

DUPLICATE_BLOCK_THRESHOLD = 0.85
DUPLICATE_WARN_THRESHOLD = 0.55

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does",
    "for", "from", "how", "in", "is", "it", "of", "on", "or", "that", "the",
    "this", "to", "was", "what", "when", "why", "with", "you", "your",
}


class TopicError(common.YoutubeError):
    pass


def _topics_file():
    return os.path.join(common.YOUTUBE_DIR, TOPICS_FILE_NAME)


def _load_topics():
    data = common.read_store(_topics_file(), "topics")
    return [item for item in data.get("topics", []) if isinstance(item, dict) and item.get("id")]


def _save_topics(items):
    common.write_store(_topics_file(), "topics", items)


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------
def normalize_title(title):
    text = str(title or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [token for token in text.split() if token and token not in _STOPWORDS]
    return tokens


def similarity(title_a, title_b):
    tokens_a = set(normalize_title(title_a))
    tokens_b = set(normalize_title(title_b))
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return round(len(intersection) / len(union), 3)


def find_duplicates(channel_id, title, exclude_id=None):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_topics()
    matches = []
    for item in items:
        if item.get("channel_id") != channel["id"] or item.get("deleted_at"):
            continue
        if exclude_id and item["id"] == exclude_id:
            continue
        score = similarity(title, item.get("title") or "")
        if score >= DUPLICATE_WARN_THRESHOLD:
            matches.append(
                {
                    "topic_id": item["id"],
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "similarity": score,
                    "blocks": score >= DUPLICATE_BLOCK_THRESHOLD,
                }
            )
    matches.sort(key=lambda match: match["similarity"], reverse=True)
    return matches[:5]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def _clean_status(value):
    status = str(value or "").strip().lower()
    if status not in STATUSES:
        raise TopicError("invalid_status", "Unknown topic status.")
    return status


def create_topic(channel_id, payload, force=False):
    if not isinstance(payload, dict):
        raise TopicError("invalid_topic", "Topic payload must be a JSON object.")
    channel = channels.get_channel(channel_id)
    title = common.clean_text(payload.get("title"), "Title", 160, required=True)
    duplicates = find_duplicates(channel["id"], title)
    blocked = [match for match in duplicates if match["blocks"]]
    if blocked and not force:
        raise TopicError(
            "duplicate_topic",
            "A very similar topic already exists: %s. Review it or create with force."
            % ", ".join('"%s"' % match["title"] for match in blocked[:2]),
            409,
        )
    priority = str(payload.get("priority") or "P2").strip().upper()
    if priority not in PRIORITIES:
        raise TopicError("invalid_topic", "Priority must be one of %s." % ", ".join(PRIORITIES))
    intent = str(payload.get("search_intent") or "informational").strip().lower()
    if intent not in SEARCH_INTENTS:
        raise TopicError("invalid_topic", "Unknown search intent.")
    relevance = str(payload.get("commercial_relevance") or "unknown").strip().lower()
    if relevance not in COMMERCIAL_RELEVANCE:
        raise TopicError("invalid_topic", "Unknown commercial relevance.")
    status = _clean_status(payload.get("status") or "idea")
    validation_id = common.clean_text(payload.get("validation_id"), "Validation", 64) or None
    score = None
    validation = None
    if validation_id:
        validation = research.get_validation(channel["id"], validation_id)
        score = validation.get("total_score")
        if status == "idea":
            status = "validated"
    stamp = common.now()
    topic = {
        "id": common.new_id("ytt_"),
        "channel_id": channel["id"],
        "project_id": common.clean_text(payload.get("project_id"), "Project", 64) or None,
        "title": title,
        "angle": common.clean_text(payload.get("angle"), "Angle", 500),
        "audience_question": common.clean_text(payload.get("audience_question"), "Audience question", 300),
        "pillar": common.clean_text(payload.get("pillar"), "Pillar", 80) or None,
        "cluster": common.clean_text(payload.get("cluster"), "Cluster", 80) or None,
        "priority": priority,
        "status": status,
        "search_intent": intent,
        "commercial_relevance": relevance,
        "score": score,
        "validation_id": validation_id,
        "validation_subject": (validation.get("subject") if validation else None),
        "script_id": None,
        "competitor_coverage": _clean_coverage(payload.get("competitor_coverage")),
        "unanswered_questions": common.clean_str_list(payload.get("unanswered_questions"), "Question", 12, 300),
        "notes": common.clean_text(payload.get("notes"), "Notes", 2000),
        "similar_to": [match for match in duplicates if not match["blocks"]][:3],
        "rejected_reason": None,
        "published_video_id": None,
        "archived": False,
        "deleted_at": None,
        "created_at": stamp,
        "created_iso": common.iso(stamp),
        "updated_at": stamp,
        "updated_iso": common.iso(stamp),
    }
    with common.acquire():
        items = _load_topics()
        items.append(topic)
        _save_topics(items)
    return common.public(topic)


def _clean_coverage(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise TopicError("invalid_topic", "competitor_coverage must be a list of at most 10 entries.")
    coverage = []
    for entry in value:
        if not isinstance(entry, dict):
            raise TopicError("invalid_topic", "Each coverage entry must be an object.")
        coverage.append(
            {
                "competitor": common.clean_text(entry.get("competitor"), "Competitor", 120, required=True),
                "note": common.clean_text(entry.get("note"), "Coverage note", 400),
                "basis": "observed" if str(entry.get("basis") or "observed") == "observed" else "assumption",
            }
        )
    return coverage


def list_topics(channel_id, status=None, pillar=None, priority=None, min_score=None, query=None, include_deleted=False):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_topics()
    items = [t for t in items if t.get("channel_id") == channel["id"]]
    if not include_deleted:
        items = [t for t in items if not t.get("deleted_at")]
    if status:
        wanted = _clean_status(status)
        items = [t for t in items if t.get("status") == wanted]
    if pillar:
        items = [t for t in items if t.get("pillar") == pillar]
    if priority:
        wanted = str(priority).strip().upper()
        if wanted not in PRIORITIES:
            raise TopicError("invalid_topic", "Unknown priority.")
        items = [t for t in items if t.get("priority") == wanted]
    if min_score is not None:
        try:
            floor = int(min_score)
        except (TypeError, ValueError):
            raise TopicError("invalid_topic", "min_score must be a number.")
        items = [t for t in items if (t.get("score") or 0) >= floor]
    needle = str(query or "").strip().lower()
    if needle:
        items = [
            t for t in items
            if needle in ("%s %s %s" % (t.get("title", ""), t.get("angle", ""), t.get("cluster", ""))).lower()
        ]
    priority_rank = {priority: index for index, priority in enumerate(PRIORITIES)}
    items.sort(key=lambda t: (priority_rank.get(t.get("priority"), 9), -(t.get("score") or 0), -(t.get("updated_at") or 0)))
    counts = {status_name: 0 for status_name in STATUSES}
    with common.acquire():
        all_items = _load_topics()
    for item in all_items:
        if item.get("channel_id") == channel["id"] and not item.get("deleted_at"):
            counts[item.get("status") or "idea"] = counts.get(item.get("status") or "idea", 0) + 1
    return {
        "topics": [common.public(t) for t in items],
        "total": len(items),
        "counts": counts,
        "statuses": list(STATUSES),
        "backlog_target": 50,
    }


def get_topic(channel_id, topic_id, include_deleted=False):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_topics()
    item = common.find(items, str(topic_id or ""))
    if not item or item.get("channel_id") != channel["id"] or (item.get("deleted_at") and not include_deleted):
        raise TopicError("topic_not_found", "Topic not found.", 404)
    return common.public(item)


def update_topic(channel_id, topic_id, patch):
    if not isinstance(patch, dict):
        raise TopicError("invalid_topic", "Topic payload must be a JSON object.")
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_topics()
        item = common.find(items, str(topic_id or ""))
        if not item or item.get("channel_id") != channel["id"] or item.get("deleted_at"):
            raise TopicError("topic_not_found", "Topic not found.", 404)
        if "title" in patch:
            title = common.clean_text(patch.get("title"), "Title", 160, required=True)
            duplicates = [
                match for match in find_duplicates(channel["id"], title, exclude_id=item["id"])
                if match["blocks"]
            ]
            if duplicates and not common.clean_bool(patch.get("force")):
                raise TopicError(
                    "duplicate_topic",
                    "Another topic already uses this title: %s." % duplicates[0]["title"],
                    409,
                )
            item["title"] = title
        for field, limit in (("angle", 500), ("audience_question", 300), ("pillar", 80), ("cluster", 80), ("notes", 2000)):
            if field in patch:
                item[field] = common.clean_text(patch.get(field), field.replace("_", " ").title(), limit) or None
        if "priority" in patch:
            priority = str(patch.get("priority") or "").strip().upper()
            if priority not in PRIORITIES:
                raise TopicError("invalid_topic", "Unknown priority.")
            item["priority"] = priority
        if "search_intent" in patch:
            intent = str(patch.get("search_intent") or "").strip().lower()
            if intent not in SEARCH_INTENTS:
                raise TopicError("invalid_topic", "Unknown search intent.")
            item["search_intent"] = intent
        if "commercial_relevance" in patch:
            relevance = str(patch.get("commercial_relevance") or "").strip().lower()
            if relevance not in COMMERCIAL_RELEVANCE:
                raise TopicError("invalid_topic", "Unknown commercial relevance.")
            item["commercial_relevance"] = relevance
        if "competitor_coverage" in patch:
            item["competitor_coverage"] = _clean_coverage(patch.get("competitor_coverage"))
        if "unanswered_questions" in patch:
            item["unanswered_questions"] = common.clean_str_list(patch.get("unanswered_questions"), "Question", 12, 300)
        if "validation_id" in patch:
            validation_id = common.clean_text(patch.get("validation_id"), "Validation", 64) or None
            if validation_id:
                validation = research.get_validation(channel["id"], validation_id)
                item["score"] = validation.get("total_score")
                item["validation_subject"] = validation.get("subject")
            item["validation_id"] = validation_id
        stamp = common.now()
        item["updated_at"] = stamp
        item["updated_iso"] = common.iso(stamp)
        _save_topics(items)
    return common.public(item)


def set_status(channel_id, topic_id, status, force=False):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_topics()
        item = common.find(items, str(topic_id or ""))
        if not item or item.get("channel_id") != channel["id"] or item.get("deleted_at"):
            raise TopicError("topic_not_found", "Topic not found.", 404)
        wanted = _clean_status(status)
        current = item.get("status") or "idea"
        if wanted == current:
            return common.public(item)
        if wanted == "rejected":
            if current == "published":
                raise TopicError("invalid_transition", "Published topics cannot be rejected.")
            item["status"] = "rejected"
            item["rejected_reason"] = common.clean_text(force, "Reason", 300) if isinstance(force, str) else None
        elif wanted == "validated":
            if not item.get("validation_id"):
                raise TopicError(
                    "validation_required",
                    "Link a niche validation (five scored criteria) before marking a topic validated.",
                )
            if FORWARD_ORDER.get(current, 0) > FORWARD_ORDER["validated"]:
                raise TopicError("invalid_transition", "This topic already moved past validation.")
            item["status"] = "validated"
        elif wanted == "approved":
            if FORWARD_ORDER.get(current, 0) > FORWARD_ORDER["approved"]:
                raise TopicError("invalid_transition", "This topic already moved past approval.")
            if current not in ("researching", "validated"):
                raise TopicError("invalid_transition", "Validate the topic before approving it.")
            if not item.get("validation_id"):
                raise TopicError("validation_required", "Approval requires a linked validation.")
            if (item.get("score") or 0) < research.APPROVAL_THRESHOLD:
                raise TopicError(
                    "below_threshold",
                    "Approval requires a validation score of at least %d/25 (this topic: %s)."
                    % (research.APPROVAL_THRESHOLD, item.get("score")),
                )
            item["status"] = "approved"
        elif wanted == "scripted":
            if current not in ("approved", "scripted"):
                raise TopicError("invalid_transition", "Approve the topic before scripting it.")
            item["status"] = "scripted"
        elif wanted == "in_production":
            if current not in ("scripted", "in_production"):
                raise TopicError("invalid_transition", "Script the topic before moving it into production.")
            item["status"] = "in_production"
        elif wanted == "published":
            if current != "in_production":
                raise TopicError("invalid_transition", "Only topics in production can be published.")
            item["status"] = "published"
        elif wanted in ("idea", "researching"):
            if FORWARD_ORDER.get(current, 0) > FORWARD_ORDER.get(wanted, 0):
                raise TopicError("invalid_transition", "Topics cannot move backwards to %s." % wanted)
            item["status"] = wanted
        else:
            raise TopicError("invalid_transition", "Unsupported status change.")
        stamp = common.now()
        item["updated_at"] = stamp
        item["updated_iso"] = common.iso(stamp)
        _save_topics(items)
    return common.public(item)


def attach_script(channel_id, topic_id, script_id):
    """Called by the script store when a script is created from a topic."""
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_topics()
        item = common.find(items, str(topic_id or ""))
        if not item or item.get("channel_id") != channel["id"] or item.get("deleted_at"):
            raise TopicError("topic_not_found", "Topic not found.", 404)
        item["script_id"] = script_id
        if item.get("status") in ("approved", "scripted"):
            item["status"] = "scripted"
        stamp = common.now()
        item["updated_at"] = stamp
        item["updated_iso"] = common.iso(stamp)
        _save_topics(items)
    return common.public(item)


def delete_topic(channel_id, topic_id):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_topics()
        item = common.find(items, str(topic_id or ""))
        if not item or item.get("channel_id") != channel["id"] or item.get("deleted_at"):
            raise TopicError("topic_not_found", "Topic not found.", 404)
        common.soft_delete(item)
        _save_topics(items)
    return {"deleted": item["id"]}
