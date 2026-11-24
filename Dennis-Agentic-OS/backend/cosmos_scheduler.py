"""Autonomous COSMOS discovery scheduler.

Two run triggers, both honoring the division's autonomy mode:

* **Six-hour cycle** — a daemon thread polls every 30 seconds and runs each
  autonomous division's own capability when ``cosmos_modes.due()`` fires
  (once every ``INTERVAL_S`` = six hours).
* **Draft top-up** — the moment an owner drafts a proposal card away, the
  division immediately runs again so the five-card board refills (autonomous
  divisions only). See ``request_topup``.

All runs go through ``cosmos_engine.run`` — the scheduler never generates
anything itself. For recommendation capabilities the board is kept at five
cards: after every run ``cosmos_store.enforce_proposed_cap`` archives the
oldest proposals beyond five. Runs are serialized per division so the cycle
and a top-up can never double-fire the same division.
"""

from __future__ import annotations

import queue
import threading
import traceback

from . import cosmos_common as common
from . import cosmos_engine
from . import cosmos_llm
from . import cosmos_modes
from . import cosmos_registry
from . import cosmos_store

POLL_S = 30
WORKERS = 2
BOARD_SIZE = 5

_started = False
_start_lock = threading.Lock()
_work: "queue.Queue[tuple[str, str, str]]" = queue.Queue()


def _llm_ready(agent_id, division):
    """A division only runs when its resolved Agent/Model choice is usable."""
    try:
        choice = cosmos_llm.resolve(agent_id, division)
    except Exception:
        return False
    return bool(choice and choice.get("agent_id"))


def _expected_items(agent_id, division):
    """Top-up target: refill only the missing proposal cards."""
    capability = cosmos_registry.capability(agent_id, division)
    if capability.get("kind") != "recommendations":
        return None
    try:
        listed = cosmos_store.list_recommendations(agent_id=agent_id, division=division,
                                                   status="proposed", limit=BOARD_SIZE)
        missing = BOARD_SIZE - len(listed.get("recommendations", []))
    except Exception:
        return None
    return missing if missing > 0 else None


def _task_for(agent_id, division, trigger):
    """The task text an autonomous run carries; visible in execution history."""
    label = (cosmos_registry.capability(agent_id, division).get("label") or "discovery")
    if trigger.startswith("topup"):
        return ("Execute your %s responsibility using the active configuration. "
                "(Autonomous top-up: a proposal card was drafted away; refill the board.)" % label)
    return ("Execute your %s responsibility using the active configuration. "
            "(Autonomous scheduled run — every 6 hours.)" % label)


def run_division(agent_id, division, trigger):
    """Run one division's capability once, then stamp the mode store."""
    if not cosmos_modes.try_claim(agent_id, division):
        return None
    try:
        if not _llm_ready(agent_id, division):
            cosmos_modes.record_run(agent_id, division, trigger,
                                    error="Model not configured for this division; run skipped.")
            return None
        expected = _expected_items(agent_id, division)
        if expected == 0:
            # Board already full; nothing to refill.
            cosmos_modes.record_run(agent_id, division, trigger)
            return None
        execution = cosmos_engine.run(agent_id, division, _task_for(agent_id, division, trigger),
                                      expected_items=expected)
        capability = cosmos_registry.capability(agent_id, division)
        if capability.get("kind") == "recommendations":
            cosmos_store.enforce_proposed_cap(agent_id, division, BOARD_SIZE)
        cosmos_modes.record_run(agent_id, division, trigger)
        return execution
    except Exception as exc:
        message = getattr(exc, "message", None) or str(exc) or "Autonomous run failed."
        traceback.print_exc()
        try:
            cosmos_modes.record_run(agent_id, division, trigger, error=message)
        except Exception:
            traceback.print_exc()
        return None
    finally:
        cosmos_modes.release(agent_id, division)


def _worker():
    while True:
        try:
            agent_id, division, trigger = _work.get()
        except (KeyboardInterrupt, SystemExit):
            return
        run_division(agent_id, division, trigger)
        _work.task_done()


def _cycle():
    while True:
        try:
            for agent_id in cosmos_registry.AGENT_ORDER:
                for division in common.DIVISIONS:
                    if cosmos_modes.due(agent_id, division) and (agent_id, division) not in [
                        (a, d) for a, d, _ in list(_work.queue)
                    ]:
                        _work.put((agent_id, division, "cycle"))
        except Exception:
            traceback.print_exc()
        threading.Event().wait(POLL_S)


def start_scheduler():
    """Start the cycle thread + workers once per process."""
    global _started
    with _start_lock:
        if _started:
            return False
        _started = True
    for _ in range(WORKERS):
        thread = threading.Thread(target=_worker, name="cosmos-scheduler-worker", daemon=True)
        thread.start()
    threading.Thread(target=_cycle, name="cosmos-scheduler-cycle", daemon=True).start()
    print("  COSMOS scheduler: autonomous discovery every "
          f"{cosmos_modes.INTERVAL_S // 3600}h (override divisions stay manual)")
    return True


def request_topup(agent_id, division, reason="draft"):
    """Queue an immediate refill for an autonomous division (draft top-up)."""
    if not cosmos_modes.is_autonomous(agent_id, division):
        return False
    _work.put((agent_id, division, "topup:%s" % reason))
    return True
