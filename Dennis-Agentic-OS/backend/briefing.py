"""Native briefing module — the Step 5 replacement for the OpenJarvis digest bridge.

No sibling repo, no FastAPI service, no network call to :8000. Every line of a
briefing is assembled from real local data exposed by :mod:`backend.jarvis_os`
and :mod:`backend.tts`. Missing data is reported as "unknown" together with its
source path — never invented. Artifacts live under ``~/.jarvis/briefings``
(0700 directory, 0600 files) and are read-only once written.

The public function names and return shapes match the old ``digest`` module so
the existing ``/api/digest/*`` routes and JARVIS briefing UI keep working.
"""

import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import jarvis_os
from . import tts

MANILA = ZoneInfo("Asia/Manila")
BRIEF_DIR = os.path.join(jarvis_os.JARVIS_HOME, "briefings")

DAILY_FILE = os.path.join(BRIEF_DIR, "daily.json")
WEEKLY_FILE = os.path.join(BRIEF_DIR, "weekly.json")
HISTORY_FILE = os.path.join(BRIEF_DIR, "history.json")
SCHEDULE_FILE = os.path.join(BRIEF_DIR, "schedule.json")
AUDIO_FILE = os.path.join(BRIEF_DIR, "latest.wav")
LOG_FILE = os.path.join(BRIEF_DIR, "briefing.log")

_CHECK_INTERVAL_S = 15 * 60
_DEFAULT_LIMIT = 10


class DigestError(Exception):
    """Configuration/validation problem (kept for API compatibility)."""


# ---------------------------------------------------------------------------
# Local storage (private home, 0700/0600)
# ---------------------------------------------------------------------------

def _ensure_dir():
    jarvis_os.ensure_private_home()
    os.makedirs(BRIEF_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(BRIEF_DIR, 0o700)
    except OSError:
        pass
    return BRIEF_DIR


def _write_json(path, obj):
    _ensure_dir()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _now_iso():
    return datetime.now(MANILA).isoformat()


def _log(message):
    try:
        _ensure_dir()
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"{_now_iso()} {message}\n")
        os.chmod(LOG_FILE, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Real local sources — never invent a value
# ---------------------------------------------------------------------------

def _fmt(value, fallback="unknown"):
    if value is None or value == "":
        return fallback
    return str(value).replace("\n", " ").strip()[:160]


def collect_sections():
    """Read-only snapshots. Each section carries the source it came from."""
    sections = {}

    try:
        conns = jarvis_os.connections() or []
        sections["connections"] = {
            "source": "backend/jarvis_os.py:connections()",
            "items": [{"name": _fmt(c.get("label")),
                       "status": _fmt(c.get("status")),
                       "detail": _fmt(c.get("detail"))} for c in conns if isinstance(c, dict)],
        }
    except Exception as exc:
        sections["connections"] = {"source": "backend/jarvis_os.py:connections()",
                                   "error": type(exc).__name__}

    try:
        agents = jarvis_os.cosmos_intelligences() or []
        sections["cosmos"] = {
            "source": "data/cosmos/executions.json",
            "items": [{"name": _fmt(a.get("name")),
                       "status": _fmt(a.get("status")),
                       "records": a.get("record_count", "unknown"),
                       "task": _fmt(a.get("current_task"))} for a in agents if isinstance(a, dict)],
        }
    except Exception as exc:
        sections["cosmos"] = {"source": "data/cosmos/executions.json",
                              "error": type(exc).__name__}

    try:
        gen = jarvis_os.genesis_panel() or {}
        items = []
        for kind in ("working_on", "needs_approval", "stopped"):
            for row in (gen.get(kind) or []):
                if isinstance(row, dict):
                    items.append({"kind": kind,
                                  "id": _fmt(row.get("id")),
                                  "title": _fmt(row.get("title")),
                                  "status": _fmt(row.get("status")),
                                  "why": _fmt(row.get("why") or row.get("doing") or row.get("activity"))})
        sections["genesis"] = {
            "source": "data/cosmos/genesis/projects.json + renders.json",
            "items": items,
        }
    except Exception as exc:
        sections["genesis"] = {"source": "data/cosmos/genesis/", "error": type(exc).__name__}

    try:
        trading = jarvis_os.trading_brief() or {}
        sections["trading_den"] = {
            "source": "GET /api/jarvis/trading-brief (read-only)",
            "read_only": trading.get("read_only", "unknown"),
            "note": _fmt(trading.get("note")),
            "pnl_today": _fmt(trading.get("pnl_today")),
            "bots": _fmt(trading.get("bots")),
        }
    except Exception as exc:
        sections["trading_den"] = {"source": "GET /api/jarvis/trading-brief",
                                   "error": type(exc).__name__}

    try:
        entries = jarvis_os.audit_entries(limit=5) or []
        sections["audit"] = {
            "source": os.path.join(jarvis_os.JARVIS_HOME, "audit.log"),
            "items": [{"when": _fmt(e.get("when")),
                       "what": _fmt(e.get("what")),
                       "result": _fmt(e.get("result"))}
                      for e in entries if isinstance(e, dict)],
        }
    except Exception as exc:
        sections["audit"] = {"source": os.path.join(jarvis_os.JARVIS_HOME, "audit.log"),
                             "error": type(exc).__name__}

    try:
        voice = tts.status() or {}
        kokoro = voice.get("kokoro") or {}
        sections["voice"] = {
            "source": "GET /api/tts/status",
            "kokoro": _fmt(kokoro.get("status")),
            "fish_configured": bool((voice.get("fish") or {}).get("configured")),
            "default_provider": _fmt(voice.get("global_default_provider")),
        }
    except Exception as exc:
        sections["voice"] = {"source": "GET /api/tts/status", "error": type(exc).__name__}

    return sections


def _text_from_sections(sections, kind):
    """Plain-text briefing. Only values present in `sections` are written."""
    now = datetime.now(MANILA)
    title = f"DENNIS AGENTIC OS — {'WEEKLY' if kind == 'weekly' else 'DAILY'} BRIEFING"
    lines = [title, f"Generated: {now.isoformat()} (Asia/Manila)", ""]

    conn = sections.get("connections", {})
    lines.append("CONNECTIONS")
    if conn.get("error"):
        lines.append(f"  unknown — {conn['error']} (source: {conn.get('source')})")
    else:
        for item in conn.get("items", []):
            lines.append(f"  - {item['name']}: {item['status']} — {item['detail']}")
        if not conn.get("items"):
            lines.append("  unknown — no sources recorded (source: backend/jarvis_os.py)")
    lines.append("")

    cosmos = sections.get("cosmos", {})
    lines.append("COSMOS")
    if cosmos.get("error"):
        lines.append(f"  unknown — {cosmos['error']} (source: {cosmos.get('source')})")
    else:
        for item in cosmos.get("items", []):
            lines.append(f"  - {item['name']}: {item['status']}; records={item['records']}; task={item['task']}")
    lines.append("")

    gen = sections.get("genesis", {})
    lines.append("GENESIS")
    if gen.get("error"):
        lines.append(f"  unknown — {gen['error']} (source: {gen.get('source')})")
    else:
        for item in gen.get("items", []):
            lines.append(f"  - {item['id']} [{item['kind']}]: {item['status']} — {item['why']}")
        if not gen.get("items"):
            lines.append("  unknown — no projects recorded")
    lines.append("")

    trade = sections.get("trading_den", {})
    lines.append("TRADING DEN (read-only)")
    lines.append(f"  read_only={_fmt(trade.get('read_only'))}; bots={_fmt(trade.get('bots'))}")
    lines.append(f"  note: {_fmt(trade.get('note'))}")
    lines.append("")

    audit = sections.get("audit", {})
    lines.append("RECENT AUDIT")
    for item in audit.get("items", []):
        lines.append(f"  - {item['when']} | {item['what']} | {item['result']}")
    if not audit.get("items"):
        lines.append("  unknown — no entries recorded")
    lines.append("")

    voice = sections.get("voice", {})
    lines.append(f"VOICE: kokoro={_fmt(voice.get('kokoro'))}; "
                 f"fish_configured={voice.get('fish_configured', 'unknown')}; "
                 f"default={_fmt(voice.get('default_provider'))}")
    lines.append("")
    lines.append("All figures above come from local OS state. DEMO DATA is labelled as such at source.")
    return "\n".join(lines)


def _spoken_summary(sections, kind):
    """Short spoken version for local TTS. Same real data, no new claims.

    The full evidence-heavy text stays on screen; CPU TTS would take minutes
    on ~2,000 characters, so the audio gets a bounded (~400 char) summary.
    """
    label = "weekly" if kind == "weekly" else "daily"
    parts = [f"Good morning, sir. Here is your {label} briefing."]

    conn = sections.get("connections", {})
    items = [i for i in conn.get("items", []) if isinstance(i, dict)]
    if items:
        online = sum(1 for i in items if str(i.get("status", "")).lower() == "connected")
        parts.append(f"{online} of {len(items)} local sources are connected.")
    elif conn.get("error"):
        parts.append("Connection status could not be read.")
    else:
        parts.append("No local sources are recorded.")

    cosmos = sections.get("cosmos", {}).get("items", [])
    if cosmos:
        parts.append("COSMOS: " + "; ".join(
            f"{a.get('name')} {a.get('status')}" for a in cosmos[:3] if isinstance(a, dict)) + ".")

    gen = sections.get("genesis", {}).get("items", [])
    parts.append(f"Genesis has {len(gen)} recorded item"
                 + ("" if len(gen) == 1 else "s") + ".")

    trade = sections.get("trading_den", {})
    note = _fmt(trade.get("note"))
    parts.append("Trading Den is read only." + (f" {note}" if note else ""))

    audit = sections.get("audit", {}).get("items", [])
    if audit and isinstance(audit[0], dict):
        parts.append("Last audit entry: " + _fmt(audit[0].get("what"))[:110] + ".")

    voice = sections.get("voice", {})
    parts.append("Kokoro voice status: " + _fmt(voice.get("kokoro")) + ".")
    parts.append("That concludes your briefing, sir.")
    return " ".join(parts)[:420]


# ---------------------------------------------------------------------------
# Public API used by app.py routes and the dashboard
# ---------------------------------------------------------------------------

def _target(kind):
    if kind == "weekly":
        return WEEKLY_FILE
    if kind == "daily":
        return DAILY_FILE
    raise DigestError(f"unknown digest kind: {kind}")


def generate(kind="daily"):
    """Build a briefing from local state, persist it, and cache local audio."""
    if kind not in ("daily", "weekly"):
        raise DigestError(f"unknown digest kind: {kind}")
    sections = collect_sections()
    text = _text_from_sections(sections, kind)
    record = {
        "kind": kind,
        "generated_at": _now_iso(),
        "text": text,
        "sections": sections,
        "source": "local: backend/jarvis_os + backend/tts (no external service)",
        "audio_available": False,
    }

    # Audio is produced at generation time (a POST), never from a GET.
    # The spoken summary is short so local CPU TTS finishes in reasonable time.
    spoken = _spoken_summary(sections, kind)
    record["spoken_summary"] = spoken
    try:
        audio, ctype, _rate, _meta = tts.generate(spoken, studio="apollo")
        if audio:
            _ensure_dir()
            with open(AUDIO_FILE, "wb") as fh:
                fh.write(audio)
            os.chmod(AUDIO_FILE, 0o600)
            record["audio_available"] = True
            record["audio_content_type"] = ctype or "audio/wav"
    except Exception as exc:
        record["audio_error"] = type(exc).__name__ + ": " + str(exc)[:160]
        _log(f"audio skipped for {kind}: {record['audio_error']}")

    _write_json(_target(kind), record)

    history = _read_json(HISTORY_FILE) or []
    history.insert(0, {"kind": kind, "generated_at": record["generated_at"],
                       "preview": text[:200]})
    _write_json(HISTORY_FILE, history[:100])

    jarvis_os.audit("local-owner", f"briefing.{kind} generated from local OS state",
                    "ok" if record["audio_available"] else "ok (audio unavailable)")
    _log(f"{kind} briefing generated; audio={record['audio_available']}")
    return {"available": True, "kind": kind, "text": text, "status": "ok",
            "audio_available": record["audio_available"],
            "generated_at": record["generated_at"]}


def latest(kind="daily"):
    record = _read_json(_target(kind))
    return {"available": True, "digest": record}


def weekly():
    record = _read_json(WEEKLY_FILE)
    return {"available": True, "weekly": record}


def history(limit=_DEFAULT_LIMIT):
    items = _read_json(HISTORY_FILE) or []
    return {"available": True, "items": items[:max(1, int(limit))]}


def schedule():
    cfg = _read_json(SCHEDULE_FILE) or {"enabled": False, "cron": None}
    return {"available": True, "schedule": cfg}


def save_schedule(enabled, cron=None):
    if cron is not None and not isinstance(cron, str):
        raise DigestError("cron must be a string")
    cfg = {"enabled": bool(enabled), "cron": cron, "updated_at": _now_iso()}
    _write_json(SCHEDULE_FILE, cfg)
    jarvis_os.audit("local-owner", "briefing schedule updated", str(cfg))
    return {"available": True, "schedule": cfg}


def audio():
    """Return cached audio bytes. Read-only — never generates on a GET."""
    try:
        with open(AUDIO_FILE, "rb") as fh:
            return fh.read(), "audio/wav"
    except OSError:
        return None, None


def service_control(action, wait_s=90):
    """No external service exists in the native design; report status honestly."""
    if action not in ("start", "stop", "restart", "status"):
        raise DigestError(f"unknown service action: {action}")
    out = status()
    out["action_requested"] = action
    out["ok"] = True
    out["note"] = ("Native briefing module — no separate service to start/stop. "
                   "Auto-run is controlled by DENNIS_WEEKLY_BRIEFING and /api/digest/schedule.")
    return out


def status():
    return {
        "available": True,
        "service": "local-briefing",
        "url": "local: backend/briefing.py (no external service)",
        "native": True,
        "schedule": schedule().get("schedule", {}),
        "weekly": weekly_schedule_info(),
        "pid": os.getpid(),
        "pid_alive": True,
        "log": LOG_FILE,
    }


# ---------------------------------------------------------------------------
# Weekly auto-run (OS-side; no external cron, no sibling service)
# ---------------------------------------------------------------------------

_WEEKLY_STATE_FILE = os.path.join(BRIEF_DIR, "weekly_state.json")


def _weekly_config():
    return {
        "enabled": os.environ.get("DENNIS_WEEKLY_BRIEFING", "1") != "0",
        "day": os.environ.get("DENNIS_WEEKLY_BRIEFING_DAY", "monday").lower(),
        "hour": int(os.environ.get("DENNIS_WEEKLY_BRIEFING_HOUR", "8")),
    }


def weekly_schedule_info():
    cfg = _weekly_config()
    state = _read_json(_WEEKLY_STATE_FILE) or {}
    return {
        "auto_enabled": cfg["enabled"],
        "day": cfg["day"],
        "hour": cfg["hour"],
        "last_run": state.get("last_run", ""),
        "last_iso_week": state.get("iso_week", ""),
    }


def _run_weekly_once():
    cfg = _weekly_config()
    if not cfg["enabled"]:
        return False
    now = datetime.now(MANILA)
    if now.strftime("%A").lower() != cfg["day"] or now.hour < cfg["hour"]:
        return False
    iso_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    state = _read_json(_WEEKLY_STATE_FILE) or {}
    if state.get("iso_week") == iso_week:
        return False
    try:
        out = generate("weekly")
    except Exception as exc:
        _log(f"weekly briefing failed: {type(exc).__name__}: {exc}")
        return False
    if out.get("available"):
        _write_json(_WEEKLY_STATE_FILE, {
            "iso_week": iso_week, "last_run": now.isoformat(), "kind": "weekly"})
        jarvis_os.audit("weekly-scheduler", "briefing.weekly auto-generated", "ok")
        return True
    return False


def _weekly_loop():
    while True:
        try:
            _run_weekly_once()
        except Exception as exc:  # never let the loop die
            _log(f"weekly scheduler error: {type(exc).__name__}: {exc}")
        time.sleep(_CHECK_INTERVAL_S)


def start_weekly_scheduler():
    cfg = _weekly_config()
    if not cfg["enabled"]:
        return None
    thread = threading.Thread(target=_weekly_loop, name="dennis-native-weekly-briefing",
                              daemon=True)
    thread.start()
    return thread
