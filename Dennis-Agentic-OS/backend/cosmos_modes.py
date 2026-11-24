"""Per-division autonomy mode for every COSMOS intelligence.

Each intelligence/division pair runs in exactly one of two modes:

* ``autonomous`` (default) — the COSMOS scheduler runs the division's own
  capability every six hours, and the division is topped back up immediately
  after one of its proposal cards is drafted away (see ``cosmos_scheduler``).
* ``override`` — nothing runs automatically. The owner supplies a task in the
  dashboard prompt box and presses Discover; only that click runs the engine.

Store: ``data/cosmos/modes.json`` (atomic write through ``cosmos_common``).
Divisions with no stored entry default to ``autonomous`` so an install keeps
behaving sensibly without a migration step.
"""

from __future__ import annotations

import os
import threading

from . import cosmos_common as common
from . import cosmos_registry

MODES_FILE = os.path.join(common.COSMOS_DIR, "modes.json")

# The autonomous cadence: one run every six hours per division.
INTERVAL_S = 6 * 60 * 60

MODES = ("autonomous", "override")
DEFAULT_MODE = "autonomous"

_lock = threading.Lock()
# Guards against double-running the same division from the scheduler and a
# draft top-up at the same time. Single-process server, so module state is safe.
_inflight = set()


class ModeError(common.CosmosError):
    pass


def _read():
    data = common.read_json(MODES_FILE, {})
    if not data:
        return {}
    if data.get("version") != common.SCHEMA_VERSION:
        raise ModeError("store_version", "modes.json uses an unsupported version.", 500)
    entries = data.get("modes")
    return entries if isinstance(entries, dict) else {}


def _write(entries):
    common.write_json(MODES_FILE, {
        "version": common.SCHEMA_VERSION,
        "modes": entries,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    })


def _clean(agent_id, division):
    agent_id = common.clean_text(agent_id, "Intelligence", 60).lower()
    division = common.clean_text(division, "Division", 20).lower()
    if agent_id not in cosmos_registry.AGENT_ORDER:
        raise ModeError("unknown_intelligence", "Unknown COSMOS intelligence.", 400)
    if division not in common.DIVISIONS:
        raise ModeError("unknown_division", "division must be ads or youtube.", 400)
    return agent_id, division


def get(agent_id, division):
    """Stored entry for one division, or the autonomous default."""
    try:
        agent_id, division = _clean(agent_id, division)
    except ModeError:
        return {"mode": DEFAULT_MODE}
    entry = _read().get(agent_id, {})
    if not isinstance(entry, dict):
        return {"mode": DEFAULT_MODE}
    value = entry.get(division)
    if not isinstance(value, dict):
        return {"mode": DEFAULT_MODE}
    mode = value.get("mode") if value.get("mode") in MODES else DEFAULT_MODE
    return {
        "mode": mode,
        "updated_iso": value.get("updated_iso"),
        "last_run_iso": value.get("last_run_iso"),
        "next_run_iso": value.get("next_run_iso"),
        "last_trigger": value.get("last_trigger"),
        "last_error": value.get("last_error"),
    }


def mode(agent_id, division):
    return get(agent_id, division).get("mode") or DEFAULT_MODE


def set_mode(agent_id, division, value):
    agent_id, division = _clean(agent_id, division)
    mode_value = common.clean_text(value, "Mode", 20).lower()
    if mode_value not in MODES:
        raise ModeError("invalid_mode", "mode must be autonomous or override.", 400)
    with _lock:
        entries = _read()
        entry = entries.get(agent_id)
        if not isinstance(entry, dict):
            entry = {}
        existing = entry.get(division)
        if not isinstance(existing, dict):
            existing = {}
        existing["mode"] = mode_value
        existing["updated_at"] = common.now()
        existing["updated_iso"] = common.iso()
        # Switching to autonomous re-arms the cycle from now.
        if mode_value == "autonomous":
            existing["next_run_at"] = common.now() + INTERVAL_S
            existing["next_run_iso"] = common.iso(common.now() + INTERVAL_S)
        else:
            existing["next_run_at"] = None
            existing["next_run_iso"] = None
        entry[division] = existing
        entries[agent_id] = entry
        _write(entries)
    return get(agent_id, division)


def is_autonomous(agent_id, division):
    return mode(agent_id, division) == "autonomous"


def record_run(agent_id, division, trigger, error=None):
    """Stamp the last/next run times after a scheduled or top-up run."""
    try:
        agent_id, division = _clean(agent_id, division)
    except ModeError:
        return
    with _lock:
        entries = _read()
        entry = entries.get(agent_id)
        if not isinstance(entry, dict):
            entry = {}
        existing = entry.get(division)
        if not isinstance(existing, dict):
            existing = {}
        stamp = common.now()
        existing["last_run_at"] = stamp
        existing["last_run_iso"] = common.iso(stamp)
        existing["last_trigger"] = common.clean_text(trigger, "Trigger", 40)
        existing["last_error"] = common.clean_text(error, "Error", 500) if error else None
        existing["next_run_at"] = stamp + INTERVAL_S
        existing["next_run_iso"] = common.iso(stamp + INTERVAL_S)
        entry[division] = existing
        entries[agent_id] = entry
        _write(entries)


def due(agent_id, division):
    """True when the division is autonomous and its six hours have elapsed."""
    if not is_autonomous(agent_id, division):
        return False
    entry = _read().get(agent_id, {})
    value = entry.get(division) if isinstance(entry, dict) else None
    if not isinstance(value, dict):
        return True  # never ran autonomously yet
    next_run_at = value.get("next_run_at")
    try:
        return common.now() >= float(next_run_at)
    except (TypeError, ValueError):
        return True


def public_selections():
    """Well-formed entries for the config API, keyed by intelligence/division."""
    result = {}
    for agent_id in cosmos_registry.AGENT_ORDER:
        entry = _read().get(agent_id, {})
        if not isinstance(entry, dict):
            continue
        clean = {}
        for division in common.DIVISIONS:
            value = entry.get(division)
            if not isinstance(value, dict):
                continue
            clean[division] = {
                "mode": value.get("mode") if value.get("mode") in MODES else DEFAULT_MODE,
                "updated_iso": value.get("updated_iso"),
                "last_run_iso": value.get("last_run_iso"),
                "next_run_iso": value.get("next_run_iso"),
                "last_trigger": value.get("last_trigger"),
                "last_error": value.get("last_error"),
            }
        if clean:
            result[agent_id] = clean
    return result


def try_claim(agent_id, division):
    """Reserve a division for one run; False when something is already running."""
    key = (agent_id, division)
    with _lock:
        if key in _inflight:
            return False
        _inflight.add(key)
        return True


def release(agent_id, division):
    with _lock:
        _inflight.discard((agent_id, division))
