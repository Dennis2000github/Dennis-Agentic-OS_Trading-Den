"""OpenJarvis digest bridge — the OS's single integration point with the
sibling OpenJarvis briefing service.

OpenJarvis (Stanford, Apache-2.0) runs its own FastAPI server
(`jarvis serve`, default http://127.0.0.1:8000) exposing the morning-digest
pipeline: digest_collect -> LLM synthesis (persona "jarvis") -> TTS ->
DigestStore, plus a cron scheduler. See docs/OPENJARVIS_VOICE_SETUP.md.

Design rules (mirroring backend/tts.py):
- Process lifecycle delegates to scripts/openjarvis_service.py so exactly one
  service process exists (same pattern as tts.service_control -> kokoro).
- Every network call has a hard timeout and degrades gracefully: when the
  service is down the proxy functions return an {"available": False, ...}
  shape instead of raising, so the dashboard can render "Briefing service
  offline" rather than breaking the JARVIS home view.
- The mic, wake word and voice stay owned by the Dennis OS (js/galaxy.js);
  OpenJarvis is a headless briefing/telemetry service.
"""

import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime

from . import config as dennis_config

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

OPENJARVIS_BASE_URL = os.environ.get("OPENJARVIS_URL", "http://127.0.0.1:8000")
# Optional bearer token — OpenJarvis only requires one when OPENJARVIS_API_KEY
# is set on its side; loopback deployments normally run without auth.
_OPENJARVIS_API_KEY = os.environ.get("OPENJARVIS_API_KEY", "")
SERVICE_SCRIPT = os.path.normpath(os.path.join(
    dennis_config.DATA_DIR, "..", "scripts", "openjarvis_service.py"))

# The digest generation runs the full collect -> LLM -> TTS lifecycle. On the
# local-first engine (qwen3:0.6b at ~4-8 tok/s on this machine) a 200-word
# briefing can take a few minutes, so generate gets a generous timeout.
_DEFAULT_TIMEOUT = 10.0
_GENERATE_TIMEOUT = 420.0


class DigestError(Exception):
    """Raised for configuration problems (missing script, bad action)."""


# ---------------------------------------------------------------------------
# Low-level proxy
# ---------------------------------------------------------------------------

def _request(method, path, body=None, timeout=_DEFAULT_TIMEOUT):
    """Call the OpenJarvis REST API. Returns (status_code, parsed_json).

    Raises DigestError only when the service cannot be reached at all (the
    caller turns that into an offline payload). HTTP error statuses are
    returned as-is so routes can forward them honestly.
    """
    url = OPENJARVIS_BASE_URL.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if _OPENJARVIS_API_KEY:
        headers["Authorization"] = f"Bearer {_OPENJARVIS_API_KEY}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8")
            try:
                return res.status, (json.loads(raw) if raw else {})
            except ValueError:
                return res.status, {"raw": raw}
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
        except (OSError, ValueError):
            parsed = {"detail": str(exc)}
        return exc.code, parsed
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise DigestError(f"OpenJarvis service unreachable at {url}: {exc}") from exc


def _offline(reason):
    return {
        "available": False,
        "service": "openjarvis",
        "url": OPENJARVIS_BASE_URL,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Public API used by app.py routes and tests
# ---------------------------------------------------------------------------

def status():
    """Service reachability + current schedule (cheap probe, no LLM call)."""
    try:
        code, schedule = _request("GET", "/api/digest/schedule")
        reachable = True
    except DigestError as exc:
        return _offline(str(exc))
    if code == 401:
        return _offline("OpenJarvis requires an API key (OPENJARVIS_API_KEY)")
    out = {
        "available": reachable and code < 500,
        "service": "openjarvis",
        "url": OPENJARVIS_BASE_URL,
        "schedule": schedule if isinstance(schedule, dict) else {},
        "weekly": weekly_schedule_info(),
    }
    out.update(_service_pid_status())
    return out


def _service_pid_status():
    """Best-effort PID/log info from the service manager (never raises)."""
    pid_file = os.path.join(dennis_config.DATA_DIR, "openjarvis", "service.pid")
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        alive = True
    except (OSError, ValueError):
        pid, alive = None, False
    return {"pid": pid, "pid_alive": alive,
            "log": os.path.join(dennis_config.DATA_DIR, "openjarvis", "service.log")}


def latest(kind="daily"):
    """Today's digest artifact, or {"digest": None} when none exists yet."""
    try:
        code, body = _request("GET", f"/api/digest?kind={kind}")
    except DigestError as exc:
        return _offline(str(exc))
    if code == 404:
        return {"available": True, "digest": None}
    if code != 200:
        return {**_offline(f"OpenJarvis returned HTTP {code}"), "detail": body}
    return {"available": True, "digest": body}


def weekly():
    """The most recent weekly briefing (full text), or {"weekly": None}."""
    try:
        code, body = _request("GET", "/api/digest/latest?kind=weekly")
    except DigestError as exc:
        return _offline(str(exc))
    if code == 404:
        return {"available": True, "weekly": None}
    if code != 200:
        return {**_offline(f"OpenJarvis returned HTTP {code}"), "detail": body}
    return {"available": True, "weekly": body}


def generate(kind="daily"):
    """Force a fresh digest (collect -> LLM -> TTS). Slow by design."""
    if kind not in ("daily", "weekly"):
        raise DigestError(f"unknown digest kind: {kind}")
    try:
        code, body = _request("POST", "/api/digest/generate", body={"kind": kind},
                              timeout=_GENERATE_TIMEOUT)
    except DigestError as exc:
        return _offline(str(exc))
    if code != 200:
        return {**_offline(f"OpenJarvis returned HTTP {code}"), "detail": body}
    return {"available": True, "kind": body.get("kind", kind),
            "text": body.get("text", ""), "status": body.get("status", "ok")}


def history(limit=10):
    """Past digests (OpenJarvis caps text previews at 200 chars)."""
    try:
        code, body = _request("GET", "/api/digest/history")
    except DigestError as exc:
        return _offline(str(exc))
    if code != 200:
        return {**_offline(f"OpenJarvis returned HTTP {code}"), "detail": body}
    items = body if isinstance(body, list) else []
    return {"available": True, "items": items[:max(1, int(limit))]}


def schedule():
    try:
        code, body = _request("GET", "/api/digest/schedule")
    except DigestError as exc:
        return _offline(str(exc))
    if code != 200:
        return {**_offline(f"OpenJarvis returned HTTP {code}"), "detail": body}
    return {"available": True, "schedule": body}


def save_schedule(enabled, cron=None):
    body = {"enabled": bool(enabled)}
    if cron:
        body["cron"] = str(cron)
    try:
        code, resp = _request("POST", "/api/digest/schedule", body=body)
    except DigestError as exc:
        return _offline(str(exc))
    if code != 200:
        return {**_offline(f"OpenJarvis returned HTTP {code}"), "detail": resp}
    return {"available": True, "schedule": resp}


def audio():
    """Raw bytes of today's digest audio, or None when unavailable.

    Returns (data, content_type). Raises DigestError only when the service
    is unreachable.
    """
    url = OPENJARVIS_BASE_URL.rstrip("/") + "/api/digest/audio"
    req = urllib.request.Request(url, headers={"Accept": "audio/mpeg"})
    if _OPENJARVIS_API_KEY:
        req.add_header("Authorization", f"Bearer {_OPENJARVIS_API_KEY}")
    try:
        with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as res:
            return res.read(), res.headers.get("Content-Type", "audio/mpeg")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise DigestError(f"OpenJarvis returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise DigestError(f"OpenJarvis service unreachable at {url}: {exc}") from exc


def service_control(action, wait_s=90):
    """start/stop/restart/status the sibling service (single process)."""
    if action not in ("start", "stop", "restart", "status"):
        raise DigestError(f"unknown service action: {action}")
    if not os.path.isfile(SERVICE_SCRIPT):
        raise DigestError("service manager script not found")
    cmd = [sys.executable, SERVICE_SCRIPT, action]
    env = dict(os.environ)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=max(120, wait_s + 30), env=env)
    except subprocess.TimeoutExpired:
        raise DigestError("service control timed out") from None
    out = proc.stdout.strip() or proc.stderr.strip() or "{}"
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = {"raw": out}
    parsed["action_requested"] = action
    parsed["ok"] = proc.returncode == 0
    return parsed


# ---------------------------------------------------------------------------
# Weekly briefing scheduler (OS-side; OpenJarvis owns the daily cron)
# ---------------------------------------------------------------------------

_WEEKLY_STATE_FILE = os.path.join(dennis_config.DATA_DIR, "digest", "weekly_state.json")
_CHECK_INTERVAL_S = 15 * 60


def _weekly_config():
    """Auto-run settings. Enabled by default; DENNIS_WEEKLY_BRIEFING=0 opts out."""
    return {
        "enabled": os.environ.get("DENNIS_WEEKLY_BRIEFING", "1") != "0",
        "day": os.environ.get("DENNIS_WEEKLY_BRIEFING_DAY", "monday").lower(),
        "hour": int(os.environ.get("DENNIS_WEEKLY_BRIEFING_HOUR", "8")),
    }


def _weekly_state():
    try:
        with open(_WEEKLY_STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_weekly_state(state):
    os.makedirs(os.path.dirname(_WEEKLY_STATE_FILE), exist_ok=True)
    with open(_WEEKLY_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def weekly_schedule_info():
    """What the dashboard status endpoint reports about the weekly auto-run."""
    cfg = _weekly_config()
    state = _weekly_state()
    return {
        "auto_enabled": cfg["enabled"],
        "day": cfg["day"],
        "hour": cfg["hour"],
        "last_run": state.get("last_run", ""),
        "last_iso_week": state.get("iso_week", ""),
    }


def _run_weekly_once():
    """Generate the weekly briefing if it is due; returns True when it ran."""
    cfg = _weekly_config()
    if not cfg["enabled"]:
        return False
    now = datetime.now()
    if now.strftime("%A").lower() != cfg["day"] or now.hour < cfg["hour"]:
        return False
    iso_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    state = _weekly_state()
    if state.get("iso_week") == iso_week:
        return False  # already briefed this week
    try:
        out = generate("weekly")
    except DigestError as exc:
        print(f"[digest] weekly briefing failed: {exc}", flush=True)
        return False
    if out.get("available"):
        _save_weekly_state({"iso_week": iso_week, "last_run": now.isoformat(),
                            "kind": "weekly"})
        print("[digest] weekly briefing generated", flush=True)
        return True
    print(f"[digest] weekly briefing skipped: {out.get('reason', 'unavailable')}",
          flush=True)
    return False


def _weekly_loop():
    import time as _time
    while True:
        try:
            _run_weekly_once()
        except Exception as exc:  # never let the loop die
            print(f"[digest] weekly scheduler error: {exc}", flush=True)
        _time.sleep(_CHECK_INTERVAL_S)


def start_weekly_scheduler():
    """Start the daemon loop (called once from app.run())."""
    cfg = _weekly_config()
    if not cfg["enabled"]:
        return None
    thread = threading.Thread(target=_weekly_loop, name="dennis-weekly-briefing",
                              daemon=True)
    thread.start()
    return thread
