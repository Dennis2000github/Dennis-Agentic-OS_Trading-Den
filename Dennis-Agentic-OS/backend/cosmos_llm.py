"""Per-intelligence, per-division Agent/Model selections for COSMOS.

Each of the five intelligences has two independent divisions (ads, youtube).
A selection stored here overrides the shared Studio text-generation selection
for that division's runs. When a division has no selection yet, it inherits the
Studio choice so existing installs keep working without a migration.

Store: ``data/cosmos/llm.json`` (atomic write, owner-only permissions).
"""

from __future__ import annotations

import os

from . import agent_registry
from . import cosmos_common as common
from . import cosmos_registry
from . import studio_llm

LLM_FILE = os.path.join(common.COSMOS_DIR, "llm.json")


def _read():
    data = common.read_json(LLM_FILE, {})
    if not data:
        return {}
    if data.get("version") != common.SCHEMA_VERSION:
        raise common.CosmosError("store_version", "llm.json uses an unsupported version.", 500)
    selections = data.get("selections")
    return selections if isinstance(selections, dict) else {}


def _write(selections):
    common.write_json(LLM_FILE, {
        "version": common.SCHEMA_VERSION,
        "selections": selections,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    })


def public_selections():
    """Return only well-formed entries, keyed by intelligence then division."""
    result = {}
    for intelligence, divisions in _read().items():
        if not isinstance(divisions, dict):
            continue
        clean = {}
        for division, entry in divisions.items():
            if not isinstance(entry, dict):
                continue
            agent_id = common.clean_text(entry.get("agent_id"), "Agent", 120)
            if not agent_id:
                continue
            clean[division] = {
                "agent_id": agent_id,
                "model": common.clean_text(entry.get("model"), "Model", 120),
            }
        if clean:
            result[intelligence] = clean
    return result


def get(intelligence, division):
    """Return the stored selection for one division, or ``None``."""
    intelligence = common.clean_text(intelligence, "Intelligence", 60).lower()
    division = common.clean_text(division, "Division", 20).lower()
    entry = _read().get(intelligence, {})
    if not isinstance(entry, dict) or division not in entry:
        return None
    value = entry[division]
    if not isinstance(value, dict):
        return None
    agent_id = common.clean_text(value.get("agent_id"), "Agent", 120)
    if not agent_id:
        return None
    return {
        "agent_id": agent_id,
        "model": common.clean_text(value.get("model"), "Model", 120),
    }


def resolve(intelligence, division):
    """Selection to run with: division-specific first, Studio default second."""
    own = get(intelligence, division)
    if own:
        return own
    inherited = studio_llm.selection()
    return {"agent_id": inherited["agent_id"], "model": inherited["model"]}


def configure(intelligence, division, agent_id, model):
    """Validate and persist one division's Agent/Model choice."""
    intelligence = common.clean_text(intelligence, "Intelligence", 60).lower()
    division = common.clean_text(division, "Division", 20).lower()
    if intelligence not in cosmos_registry.AGENT_ORDER:
        raise common.CosmosError("unknown_intelligence", "Unknown COSMOS intelligence.", 400)
    if division not in common.DIVISIONS:
        raise common.CosmosError("unknown_division", "division must be ads or youtube.", 400)
    agent_id = common.clean_text(agent_id, "Agent", 120).strip().lower()
    if not agent_id or not agent_registry.get_agent(agent_id):
        raise common.CosmosError("invalid_agent", "Choose an agent from the Agent menu.", 400)
    selection = {
        "agent_id": agent_id,
        "model": common.clean_text(model, "Model", 120),
    }
    with common.acquire():
        selections = _read()
        entry = selections.get(intelligence)
        if not isinstance(entry, dict):
            entry = {}
        entry[division] = selection
        selections[intelligence] = entry
        _write(selections)
    return selection
