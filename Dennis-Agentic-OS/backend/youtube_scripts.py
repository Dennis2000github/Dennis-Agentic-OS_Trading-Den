"""Script Studio: versioned long-form scripts with fact-check gating.

Conventions (channel master prompt):

* scripts are created from *approved* topics;
* every update snapshots the previous state as an immutable version;
* structure follows the six-part script skeleton seeded per channel;
* approval requires an explicit fact-check pass (every checklist item verified
  and at least one citation);
* AI generation composes channel master prompt + Scriptwriter Agent extension +
  task context and parses a strict JSON response; malformed output is an error,
  never silently rewritten.
"""

from __future__ import annotations

import json
import os
import re

from . import studio_llm
from . import youtube_channels as channels
from . import youtube_common as common
from . import youtube_topics as topics

SCRIPTS_FILE_NAME = "scripts.json"
VERSIONS_FILE_NAME = "script-versions.json"

STATUSES = ("draft", "in_review", "approved", "needs_revision")
MAX_VERSIONS = 30
MAX_SECTIONS = 12
NARRATION_WPM = 150


class ScriptError(common.YoutubeError):
    pass


def _scripts_file():
    return os.path.join(common.YOUTUBE_DIR, SCRIPTS_FILE_NAME)


def _versions_file():
    return os.path.join(common.YOUTUBE_DIR, VERSIONS_FILE_NAME)


def _load_scripts():
    data = common.read_store(_scripts_file(), "scripts")
    return [item for item in data.get("scripts", []) if isinstance(item, dict) and item.get("id")]


def _save_scripts(items):
    common.write_store(_scripts_file(), "scripts", items)


def _load_versions():
    data = common.read_store(_versions_file(), "versions")
    return [item for item in data.get("versions", []) if isinstance(item, dict) and item.get("id")]


def _save_versions(items):
    common.write_store(_versions_file(), "versions", items)


def _structure():
    return channels.seed().get("script_structure", [])


def _default_disclaimer():
    return channels.seed().get("disclaimer") or ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _section_skeleton(target_duration_min):
    total_seconds = int(target_duration_min) * 60
    sections = []
    elapsed = 0
    for index, part in enumerate(_structure()):
        duration = int(round(total_seconds * float(part.get("share") or 0)))
        if index == len(_structure()) - 1:
            duration = max(0, total_seconds - elapsed)
        sections.append(
            {
                "id": "sec_" + str(index + 1),
                "structure_id": part.get("id"),
                "heading": part.get("heading") or ("Section %d" % (index + 1)),
                "purpose": part.get("purpose") or "",
                "narration": "",
                "on_screen_text": [],
                "animation_notes": "",
                "sound_design": "",
                "start_sec": elapsed,
                "end_sec": elapsed + duration,
            }
        )
        elapsed += duration
    return sections


def _clean_sections(value, target_duration_min):
    if value is None:
        return _section_skeleton(target_duration_min)
    if not isinstance(value, list) or not value:
        raise ScriptError("invalid_script", "sections must be a non-empty list.")
    if len(value) > MAX_SECTIONS:
        raise ScriptError("invalid_script", "A script can have at most %d sections." % MAX_SECTIONS)
    skeleton = {part.get("id"): part for part in _structure()}
    cleaned = []
    elapsed = 0
    for index, section in enumerate(value):
        if not isinstance(section, dict):
            raise ScriptError("invalid_script", "Each section must be an object.")
        structure_id = common.clean_text(section.get("structure_id"), "Structure", 60) or None
        meta = skeleton.get(structure_id or "", {})
        heading = common.clean_text(section.get("heading"), "Section heading", 120) or meta.get("heading") or ("Section %d" % (index + 1))
        narration = common.clean_text(section.get("narration"), "Narration", 40_000)
        start = section.get("start_sec")
        end = section.get("end_sec")
        try:
            start = max(0, int(start)) if start is not None else elapsed
        except (TypeError, ValueError):
            start = elapsed
        try:
            end = max(start, int(end)) if end is not None else 0
        except (TypeError, ValueError):
            end = 0
        on_screen = common.clean_str_list(section.get("on_screen_text"), "On-screen text", 12, 160)
        # Timing: keep provided values, otherwise estimate from narration length
        # (channel narration speed) so the storyboard skeleton stays realistic.
        words = len(re.findall(r"[A-Za-z0-9']+", narration))
        estimated = int(round(words / float(channels.seed().get("narration_wpm") or 150) * 60))
        if narration and estimated < 2:
            estimated = 2
        if not end or end <= start:
            end = start + estimated
        cleaned.append(
            {
                "id": common.clean_text(section.get("id"), "Section id", 40) or ("sec_" + str(index + 1)),
                "structure_id": structure_id,
                "heading": heading,
                "purpose": common.clean_text(section.get("purpose"), "Purpose", 400) or meta.get("purpose") or "",
                "narration": narration,
                "on_screen_text": on_screen,
                "animation_notes": common.clean_text(section.get("animation_notes"), "Animation notes", 2000),
                "sound_design": common.clean_text(section.get("sound_design"), "Sound design", 600),
                "start_sec": start,
                "end_sec": end,
            }
        )
        elapsed = end
    return cleaned


def _clean_citations(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 30:
        raise ScriptError("invalid_script", "citations must be a list of at most 30 entries.")
    cleaned = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ScriptError("invalid_script", "Each citation must be an object.")
        label = common.clean_text(entry.get("label"), "Citation label", 300, required=True)
        url = common.clean_text(entry.get("url"), "Citation URL", 500)
        if url and not (url.startswith("http://") or url.startswith("https://")):
            raise ScriptError("invalid_script", "Citation URLs must be http(s).")
        cleaned.append(
            {
                "id": common.clean_text(entry.get("id"), "Citation id", 40) or common.new_id("cit_"),
                "label": label,
                "url": url or None,
                "verified": common.clean_bool(entry.get("verified")),
                "note": common.clean_text(entry.get("note"), "Citation note", 300) or None,
            }
        )
    return cleaned


def _clean_checklist(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 40:
        raise ScriptError("invalid_script", "fact_check.items must be a list of at most 40 entries.")
    cleaned = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ScriptError("invalid_script", "Each fact-check item must be an object.")
        cleaned.append(
            {
                "id": common.clean_text(entry.get("id"), "Item id", 40) or common.new_id("fc_"),
                "claim": common.clean_text(entry.get("claim"), "Claim", 600, required=True),
                "source_label": common.clean_text(entry.get("source_label"), "Source", 300) or None,
                "verified": common.clean_bool(entry.get("verified")),
                "note": common.clean_text(entry.get("note"), "Note", 400) or None,
            }
        )
    return cleaned


def _compute(script):
    words = 0
    for section in script.get("sections") or []:
        words += len(re.findall(r"[A-Za-z0-9']+", section.get("narration") or ""))
    script["word_count"] = words
    script["est_duration_sec"] = int(round(words / float(
        channels.seed().get("narration_wpm") or NARRATION_WPM
    ) * 60))
    unchecked = [item for item in (script.get("fact_check") or {}).get("items", []) if not item.get("verified")]
    script["fact_check"] = script.get("fact_check") or {}
    script["fact_check"]["unchecked_count"] = len(unchecked)
    script["fact_check"]["complete"] = bool((script.get("fact_check") or {}).get("items")) and not unchecked
    return script


def _snapshot(script, note):
    with common.acquire():
        versions = _load_versions()
        number = len([v for v in versions if v.get("script_id") == script["id"]]) + 1
        record = {
            "id": common.new_id("ytsv_"),
            "script_id": script["id"],
            "channel_id": script.get("channel_id"),
            "version": script.get("version") or number,
            "note": common.clean_text(note, "Note", 300) or ("Autosave before revision %d" % (number + 1)),
            "snapshot": common.public(script),
            "created_at": common.now(),
            "created_iso": common.iso(),
        }
        versions.append(record)
        # Bound history: drop the oldest snapshots beyond the cap.
        own = [v for v in versions if v.get("script_id") == script["id"]]
        if len(own) > MAX_VERSIONS:
            drop_ids = {v["id"] for v in sorted(own, key=lambda v: v.get("created_at") or 0)[: len(own) - MAX_VERSIONS]}
            versions = [v for v in versions if v["id"] not in drop_ids]
        _save_versions(versions)
    return record


def _status(value):
    status = str(value or "draft").strip().lower()
    if status not in STATUSES:
        raise ScriptError("invalid_status", "Unknown script status.")
    return status


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def create_script(channel_id, payload):
    if not isinstance(payload, dict):
        raise ScriptError("invalid_script", "Script payload must be a JSON object.")
    channel = channels.get_channel(channel_id)
    topic_record = None
    topic_id = common.clean_text(payload.get("topic_id"), "Topic", 64) or None
    if topic_id:
        topic_record = topics.get_topic(channel["id"], topic_id)
        if topic_record.get("status") not in ("approved", "scripted", "in_production"):
            raise ScriptError(
                "topic_not_approved",
                "Scripts are written from approved topics. This topic is '%s'." % topic_record.get("status"),
            )
    title = common.clean_text(
        payload.get("title") or (topic_record or {}).get("title"),
        "Title", 160, required=True,
    )
    target = payload.get("target_duration_min")
    if target is None:
        target = (channel.get("publishing") or {}).get("default_duration_min") or 12
    try:
        target = int(target)
    except (TypeError, ValueError):
        raise ScriptError("invalid_script", "target_duration_min must be a number.")
    if not 1 <= target <= 60:
        raise ScriptError("invalid_script", "target_duration_min must be between 1 and 60.")
    stamp = common.now()
    script = {
        "id": common.new_id("yts_"),
        "channel_id": channel["id"],
        "topic_id": topic_id,
        "project_id": common.clean_text(payload.get("project_id"), "Project", 64) or None,
        "title": title,
        "alt_titles": common.clean_str_list(payload.get("alt_titles"), "Alternative title", 3, 160),
        "thumbnail_concept": common.clean_text(payload.get("thumbnail_concept"), "Thumbnail concept", 600),
        "thumbnail_text": common.clean_text(payload.get("thumbnail_text"), "Thumbnail text", 120),
        "pillar": common.clean_text(payload.get("pillar"), "Pillar", 80) or (topic_record or {}).get("pillar"),
        "target_audience": common.clean_text(payload.get("target_audience"), "Audience", 300)
        or channel.get("target_audience"),
        "target_duration_min": target,
        "hook": common.clean_text(payload.get("hook"), "Hook", 1200),
        "sections": _clean_sections(payload.get("sections"), target),
        "citations": _clean_citations(payload.get("citations")),
        "fact_check": {"items": _clean_checklist((payload.get("fact_check") or {}).get("items") if isinstance(payload.get("fact_check"), dict) else payload.get("fact_check"))},
        "description": common.clean_text(payload.get("description"), "Description", 5000),
        "chapters": common.clean_text(payload.get("chapters"), "Chapters", 2000),
        "disclaimer": common.clean_text(payload.get("disclaimer"), "Disclaimer", 2000) or _default_disclaimer(),
        "generation": None,
        "status": "draft",
        "version": 1,
        "archived": False,
        "deleted_at": None,
        "created_at": stamp,
        "created_iso": common.iso(stamp),
        "updated_at": stamp,
        "updated_iso": common.iso(stamp),
    }
    _compute(script)
    with common.acquire():
        items = _load_scripts()
        items.append(script)
        _save_scripts(items)
    if topic_id:
        topics.attach_script(channel["id"], topic_id, script["id"])
    return common.public(script)


def list_scripts(channel_id, status=None, topic_id=None, query=None, include_deleted=False):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_scripts()
    items = [s for s in items if s.get("channel_id") == channel["id"]]
    if not include_deleted:
        items = [s for s in items if not s.get("deleted_at")]
    if status:
        wanted = _status(status)
        items = [s for s in items if s.get("status") == wanted]
    if topic_id:
        items = [s for s in items if s.get("topic_id") == topic_id]
    needle = str(query or "").strip().lower()
    if needle:
        items = [s for s in items if needle in (s.get("title", "")).lower()]
    items.sort(key=lambda s: -(s.get("updated_at") or 0))
    counts = {name: 0 for name in STATUSES}
    with common.acquire():
        all_items = _load_scripts()
    for item in all_items:
        if item.get("channel_id") == channel["id"] and not item.get("deleted_at"):
            counts[item.get("status") or "draft"] = counts.get(item.get("status") or "draft", 0) + 1
    return {"scripts": [common.public(s) for s in items], "total": len(items), "counts": counts}


def get_script(channel_id, script_id, include_deleted=False):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_scripts()
    item = common.find(items, str(script_id or ""))
    if not item or item.get("channel_id") != channel["id"] or (item.get("deleted_at") and not include_deleted):
        raise ScriptError("script_not_found", "Script not found.", 404)
    return common.public(item)


def update_script(channel_id, script_id, patch, note=None):
    if not isinstance(patch, dict):
        raise ScriptError("invalid_script", "Script payload must be a JSON object.")
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_scripts()
        script = common.find(items, str(script_id or ""))
        if not script or script.get("channel_id") != channel["id"] or script.get("deleted_at"):
            raise ScriptError("script_not_found", "Script not found.", 404)
        changed, narrative_changed = _patch_script(script, patch)
        if not changed:
            return common.public(script)
        if narrative_changed:
            # Only narrative edits (sections, citations, fact-check) create an
            # immutable version; metadata edits just update the record.
            _snapshot(script, note)
            script["version"] = (script.get("version") or 1) + 1
        stamp = common.now()
        script["updated_at"] = stamp
        script["updated_iso"] = common.iso(stamp)
        _compute(script)
        _save_scripts(items)
    return common.public(script)


def _patch_script(script, patch):
    """Apply a patch. Returns ``(changed, narrative_changed)``."""
    changed = False
    narrative_changed = False
    text_fields = (
        "title", "thumbnail_concept", "thumbnail_text", "hook", "description",
        "chapters", "disclaimer", "target_audience", "pillar",
    )
    for field in text_fields:
        if field in patch:
            limit = 160 if field == "title" else 2000 if field in ("thumbnail_text", "pillar") else 5000
            value = common.clean_text(patch.get(field), field.replace("_", " ").title(), limit)
            if field == "title" and not value:
                raise ScriptError("invalid_script", "Title is required.")
            if script.get(field) != value:
                script[field] = value
                changed = True
    if "alt_titles" in patch:
        value = common.clean_str_list(patch.get("alt_titles"), "Alternative title", 3, 160)
        if value != script.get("alt_titles"):
            script["alt_titles"] = value
            changed = True
    if "target_duration_min" in patch:
        try:
            target = int(patch.get("target_duration_min"))
        except (TypeError, ValueError):
            raise ScriptError("invalid_script", "target_duration_min must be a number.")
        if not 1 <= target <= 60:
            raise ScriptError("invalid_script", "target_duration_min must be between 1 and 60.")
        if target != script.get("target_duration_min"):
            script["target_duration_min"] = target
            changed = True
    if "sections" in patch:
        value = _clean_sections(patch.get("sections"), script.get("target_duration_min") or 12)
        if value != script.get("sections"):
            script["sections"] = value
            changed = True
            narrative_changed = True
    if "citations" in patch:
        value = _clean_citations(patch.get("citations"))
        if value != script.get("citations"):
            script["citations"] = value
            changed = True
            narrative_changed = True
    if "fact_check" in patch:
        patch_fc = patch.get("fact_check") or {}
        items_value = patch_fc.get("items") if isinstance(patch_fc, dict) else patch_fc
        value = _clean_checklist(items_value)
        existing = (script.get("fact_check") or {}).get("items") or []
        if value != existing:
            script["fact_check"] = {"items": value}
            changed = True
            narrative_changed = True
    if "status" in patch:
        raise ScriptError(
            "use_status_endpoint",
            "Script approval state changes through POST /scripts/<id>/status.",
        )
    return changed, narrative_changed


def set_status(channel_id, script_id, status):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_scripts()
        script = common.find(items, str(script_id or ""))
        if not script or script.get("channel_id") != channel["id"] or script.get("deleted_at"):
            raise ScriptError("script_not_found", "Script not found.", 404)
        wanted = _status(status)
        current = script.get("status") or "draft"
        if wanted == current:
            return common.public(script)
        if wanted == "approved":
            if current not in ("in_review", "needs_revision", "approved"):
                raise ScriptError("review_required", "Submit the script for review before approving it.")
            fact_check = script.get("fact_check") or {}
            if not fact_check.get("items"):
                raise ScriptError(
                    "fact_check_required",
                    "Add fact-check checklist items (and verify them) before approving.",
                )
            if not fact_check.get("complete"):
                raise ScriptError(
                    "fact_check_incomplete",
                    "%d fact-check item(s) are still unverified." % (fact_check.get("unchecked_count") or 0),
                )
            if not script.get("citations"):
                raise ScriptError("citations_required", "Approval requires at least one recorded source.")
            script["status"] = "approved"
        elif wanted == "in_review":
            script["status"] = "in_review"
        elif wanted in ("draft", "needs_revision"):
            script["status"] = wanted
        stamp = common.now()
        script["updated_at"] = stamp
        script["updated_iso"] = common.iso(stamp)
        _save_scripts(items)
    return common.public(script)


def delete_script(channel_id, script_id):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        items = _load_scripts()
        script = common.find(items, str(script_id or ""))
        if not script or script.get("channel_id") != channel["id"] or script.get("deleted_at"):
            raise ScriptError("script_not_found", "Script not found.", 404)
        common.soft_delete(script)
        _save_scripts(items)
    return {"deleted": script["id"]}


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------
def list_versions(channel_id, script_id):
    channel = channels.get_channel(channel_id)
    script = get_script(channel["id"], script_id)
    with common.acquire():
        versions = [v for v in _load_versions() if v.get("script_id") == script["id"]]
    versions.sort(key=lambda v: v.get("version") or 0, reverse=True)
    return {
        "script_id": script["id"],
        "current_version": script.get("version"),
        "versions": [
            {
                "id": v["id"],
                "version": v.get("version"),
                "note": v.get("note"),
                "created_iso": v.get("created_iso"),
                "word_count": (v.get("snapshot") or {}).get("word_count"),
            }
            for v in versions
        ],
    }


def restore_version(channel_id, script_id, version_id):
    channel = channels.get_channel(channel_id)
    with common.acquire():
        script = get_script(channel["id"], script_id)
        record = common.find(_load_versions(), str(version_id or ""))
        if not record or record.get("script_id") != script["id"]:
            raise ScriptError("version_not_found", "Script version not found.", 404)
        snapshot = record.get("snapshot") or {}
        _snapshot(script, "Autosave before restoring v%s" % record.get("version"))
        for field in (
            "title", "alt_titles", "thumbnail_concept", "thumbnail_text", "pillar",
            "target_audience", "target_duration_min", "hook", "sections", "citations",
            "fact_check", "description", "chapters", "disclaimer",
        ):
            if field in snapshot:
                script[field] = snapshot[field]
        script["status"] = "draft"
        script["version"] = (script.get("version") or 1) + 1
        stamp = common.now()
        script["updated_at"] = stamp
        script["updated_iso"] = common.iso(stamp)
        _compute(script)
        items = _load_scripts()
        for index, item in enumerate(items):
            if item["id"] == script["id"]:
                items[index] = script
                break
        _save_scripts(items)
    return common.public(script)


# ---------------------------------------------------------------------------
# AI generation
# ---------------------------------------------------------------------------
def _extract_json(text):
    """Parse the model response as JSON; tolerate a fenced ```json block."""
    text = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidates = [fenced.group(1).strip() if fenced else text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


def build_generation_prompt(channel_id, payload):
    channel = channels.get_channel(channel_id)
    composed = channels.compose_agent_prompt(channel["id"], "scriptwriter")
    topic_record = None
    topic_id = common.clean_text(payload.get("topic_id"), "Topic", 64) or None
    if topic_id:
        topic_record = topics.get_topic(channel["id"], topic_id)
    subject = common.clean_text(payload.get("subject") or (topic_record or {}).get("title"), "Subject", 200, required=True)
    angle = common.clean_text(payload.get("angle") or (topic_record or {}).get("angle"), "Angle", 500)
    pillar = common.clean_text(payload.get("pillar") or (topic_record or {}).get("pillar"), "Pillar", 80)
    audience_question = common.clean_text(
        payload.get("audience_question") or (topic_record or {}).get("audience_question"), "Question", 300
    )
    duration = payload.get("target_duration_min") or (channel.get("publishing") or {}).get("default_duration_min") or 12
    try:
        duration = max(5, min(30, int(duration)))
    except (TypeError, ValueError):
        duration = 12
    task = [
        "Write one complete long-form YouTube script.",
        "Subject: %s" % subject,
        "Content pillar: %s" % (pillar or "unspecified"),
        "Target narration duration: about %d minutes (%d words at %d wpm)."
        % (duration, int(duration * (channels.seed().get("narration_wpm") or NARRATION_WPM)), channels.seed().get("narration_wpm") or NARRATION_WPM),
    ]
    if angle:
        task.append("Educational angle: %s" % angle)
    if audience_question:
        task.append("Audience question to answer: %s" % audience_question)
    task.append(
        "Follow the channel script structure sections in order: "
        + ", ".join(part.get("heading") or "" for part in _structure())
        + "."
    )
    task.append(
        "Respond with ONLY a JSON object (no prose, no markdown fences) with keys: "
        "title, alt_titles (3 strings), thumbnail_concept, thumbnail_text, hook, "
        "sections (array of {heading, narration, on_screen_text (array), animation_notes, sound_design}), "
        "citations (array of {label, url, note} - only real, verifiable sources such as IRS.gov pages; "
        "use an empty array when unsure), description, chapters, fact_check_claims "
        "(array of {claim, source_label}) and disclaimer."
    )
    task.append(
        "Never fabricate statistics, case studies, credentials or testimonials. "
        "Calculations must be arithmetically correct and clearly hypothetical."
    )
    return {
        "messages": [
            {"role": "system", "content": composed["prompt"]},
            {"role": "user", "content": "\n".join(task)},
        ],
        "duration": duration,
        "topic_id": topic_id,
        "subject": subject,
    }


def generate_script(channel_id, payload):
    if not isinstance(payload, dict):
        raise ScriptError("invalid_script", "Generation payload must be a JSON object.")
    channel = channels.get_channel(channel_id)
    plan = build_generation_prompt(channel["id"], payload)
    result = studio_llm.complete(plan["messages"], temperature=0.6)
    data = _extract_json(result["content"])
    if not data:
        raise ScriptError(
            "generation_format",
            "The model response was not valid JSON, so nothing was saved. "
            "Preview: %s" % result["content"][:240],
        )
    sections_payload = []
    for section in data.get("sections") or []:
        if isinstance(section, dict):
            sections_payload.append(
                {
                    "heading": section.get("heading"),
                    "narration": section.get("narration"),
                    "on_screen_text": section.get("on_screen_text"),
                    "animation_notes": section.get("animation_notes"),
                    "sound_design": section.get("sound_design"),
                }
            )
    checklist = [
        {"claim": claim.get("claim"), "source_label": claim.get("source_label")}
        for claim in (data.get("fact_check_claims") or [])
        if isinstance(claim, dict) and claim.get("claim")
    ]
    citations = []
    for citation in data.get("citations") or []:
        if isinstance(citation, dict) and citation.get("label"):
            citations.append(
                {
                    "label": citation.get("label"),
                    "url": citation.get("url"),
                    "note": citation.get("note"),
                    "verified": False,
                }
            )
    script = create_script(channel["id"], {
        "topic_id": plan["topic_id"],
        "title": data.get("title") or plan["subject"],
        "alt_titles": data.get("alt_titles"),
        "thumbnail_concept": data.get("thumbnail_concept"),
        "thumbnail_text": data.get("thumbnail_text"),
        "pillar": payload.get("pillar") or (topics.get_topic(channel["id"], plan["topic_id"]).get("pillar") if plan["topic_id"] else None),
        "target_audience": channel.get("target_audience"),
        "target_duration_min": plan["duration"],
        "hook": data.get("hook"),
        "sections": sections_payload,
        "citations": citations,
        "fact_check": {"items": checklist},
        "description": data.get("description"),
        "chapters": data.get("chapters"),
        "disclaimer": data.get("disclaimer"),
    })
    script["generation"] = {
        "model": result.get("model"),
        "usage": result.get("usage") or {},
        "generated_iso": common.iso(),
        "parsed": True,
    }
    with common.acquire():
        items = _load_scripts()
        for index, item in enumerate(items):
            if item["id"] == script["id"]:
                items[index] = script
                break
        _save_scripts(items)
    return script
