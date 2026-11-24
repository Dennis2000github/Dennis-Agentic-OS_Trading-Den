"""Owner feedback loop for COSMOS intelligence.

Every Reject / Refine carries a preset template chosen by the owner. The
preset (plus any custom note) is recorded per intelligence/division and
injected into future discovery prompts, so the agent learns what the owner
considers a miss instead of re-proposing the same shape of card.

Presets are data, shared by all five intelligences; both divisions use the
same lists. Custom owner notes are always recorded alongside the preset.

Store: ``data/cosmos/feedback.json`` (bounded to the most recent entries).
"""

from __future__ import annotations

import os

from . import cosmos_common as common
from . import cosmos_registry

FEEDBACK_FILE = os.path.join(common.COSMOS_DIR, "feedback.json")
MAX_ENTRIES = 600

ACTIONS = ("reject", "refine")

REJECT_PRESETS = [
    {"id": "not_relevant", "label": "Not relevant to my template"},
    {"id": "price_out_of_range", "label": "Price outside my range"},
    {"id": "weak_demand", "label": "Weak demand evidence"},
    {"id": "wrong_audience", "label": "Wrong audience"},
    {"id": "already_covered", "label": "Already covered / duplicated"},
    {"id": "low_viral_risk", "label": "Little viral potential"},
]

REFINE_PRESETS = [
    {"id": "seasonal_angle", "label": "Seasonal angle (Christmas, birthdays, festivals)"},
    {"id": "cheaper_alternative", "label": "Find a cheaper alternative"},
    {"id": "better_angle", "label": "Better product/topic angle"},
    {"id": "different_audience", "label": "Aim at a different audience"},
    {"id": "stronger_hook", "label": "Stronger viral hook"},
    {"id": "tighter_niche", "label": "Tighten to a narrower niche"},
]


class FeedbackError(common.CosmosError):
    pass


def _read():
    data = common.read_json(FEEDBACK_FILE, {})
    if not data:
        return []
    if data.get("version") != common.SCHEMA_VERSION:
        raise FeedbackError("store_version", "feedback.json uses an unsupported version.", 500)
    entries = data.get("entries")
    return entries if isinstance(entries, list) else []


def _write(entries):
    common.write_json(FEEDBACK_FILE, {
        "version": common.SCHEMA_VERSION,
        "entries": entries[-MAX_ENTRIES:],
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    })


def presets(action):
    """The selectable preset templates for Reject or Refine."""
    if action == "reject":
        return REJECT_PRESETS
    if action == "refine":
        return REFINE_PRESETS
    raise FeedbackError("unknown_action", "action must be reject or refine.", 400)


def preset_label(action, preset_id):
    for preset in presets(action):
        if preset["id"] == preset_id:
            return preset["label"]
    return None


def record(agent_id, division, recommendation_id, action, preset_id=None, note=None):
    """Record one owner feedback event. Unknown presets are stored as custom."""
    agent_id = common.clean_text(agent_id, "Intelligence", 60).lower()
    division = common.clean_text(division, "Division", 20).lower()
    if agent_id not in cosmos_registry.AGENT_ORDER:
        raise FeedbackError("unknown_intelligence", "Unknown COSMOS intelligence.", 400)
    if division not in common.DIVISIONS:
        raise FeedbackError("unknown_division", "division must be ads or youtube.", 400)
    if action not in ACTIONS:
        raise FeedbackError("unknown_action", "action must be reject or refine.", 400)
    preset_id = common.clean_text(preset_id, "Preset", 60) or None
    label = preset_label(action, preset_id) if preset_id else None
    if preset_id and not label:
        # Owner-supplied preset id outside the catalog: keep the id, drop the label.
        label = None
    entry = {
        "id": common.new_id("fbk_"),
        "agent_id": agent_id,
        "division": division,
        "recommendation_id": common.clean_text(recommendation_id, "Recommendation", 80) or None,
        "action": action,
        "preset_id": preset_id,
        "preset_label": label,
        "note": common.clean_text(note, "Note", 2000) or None,
        "created_at": common.now(),
        "created_iso": common.iso(),
    }
    with common.acquire():
        entries = _read()
        entries.append(entry)
        _write(entries)
    return entry


def recent_context(agent_id, division, limit=12):
    """Compact recent feedback for prompt injection, newest first."""
    entries = _read()
    entries = [entry for entry in entries
               if entry.get("agent_id") == agent_id and entry.get("division") == division]
    entries.sort(key=lambda entry: entry.get("created_at") or 0, reverse=True)
    context = []
    for entry in entries[:max(1, limit)]:
        context.append({
            "action": entry.get("action"),
            "preset": entry.get("preset_label") or entry.get("preset_id"),
            "note": entry.get("note"),
            "when_iso": entry.get("created_iso"),
        })
    return context
