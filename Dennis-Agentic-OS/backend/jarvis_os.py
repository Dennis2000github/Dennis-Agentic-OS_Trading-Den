"""Jarvis OS upgrade layer — audit, connections, read adapters, tool routing.

Rules enforced here:
  * Everything binds to / contacts localhost only.
  * Secrets stay in ~/.jarvis (0700 dir, 0600 files). Nothing here returns a key.
  * Read adapters never write. The only writes are the append-only audit log and
    explicit, separately-authorized Genesis actions routed through existing APIs.
  * Missing data is reported as "unknown" with raw evidence — never invented.
"""

import datetime
import json
import os
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
COSMOS_DIR = os.path.join(DATA_DIR, "cosmos")
GENESIS_DIR = os.path.join(COSMOS_DIR, "genesis")
OPENJARVIS_DIR = os.path.normpath(os.path.join(ROOT, "..", "OpenJarvis"))
DENMA_DB = os.path.join(ROOT, "trading_den", "MMcloneLoopEngineering", "data", "denma_history.db")
LOCAL_BASE = "http://127.0.0.1:8080"

JARVIS_HOME = os.path.expanduser("~/.jarvis")
AUDIT_LOG = os.path.join(JARVIS_HOME, "audit.log")
SECRETS_FILE = os.path.join(JARVIS_HOME, "secrets.json")
MANILA = ZoneInfo("Asia/Manila")

# IDs/roles verified in backend/cosmos_registry.py:16,199-491 (Phase 2).
INTELLIGENCES = [
    ("andromeda", "ANDROMEDA", "Discovery Intelligence"),
    ("polaris", "POLARIS", "Strategy Intelligence"),
    ("pleiades", "PLEIADES", "Creative Intelligence"),
    ("quasar", "QUASAR", "Research & Evidence Intelligence"),
    ("helios", "HELIOS", "Performance & Learning Intelligence"),
]


# ---------------------------------------------------------------------------
# Private home + append-only audit log
# ---------------------------------------------------------------------------

def ensure_private_home():
    os.makedirs(JARVIS_HOME, mode=0o700, exist_ok=True)
    try:
        os.chmod(JARVIS_HOME, 0o700)
    except OSError:
        pass
    return JARVIS_HOME


def read_secret(name):
    """Read a secret from ~/.jarvis/secrets.json (0600). Never called by GETs."""
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    value = data.get(name, "")
    return str(value) if value else ""


def audit(who, what, result="unknown", when=None):
    """Append one audit entry. No truncation, no rewrite, 0600."""
    ensure_private_home()
    entry = {
        "who": str(who or "unknown"),
        "what": str(what or "unknown"),
        "when": when or datetime.datetime.now(MANILA).isoformat(),
        "result": str(result or "unknown"),
    }
    with open(AUDIT_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        os.chmod(AUDIT_LOG, 0o600)
    except OSError:
        pass
    return entry


def audit_entries(limit=100):
    """Read-only tail of the audit log (newest last)."""
    try:
        with open(AUDIT_LOG, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-max(1, min(int(limit), 500)):]:
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"raw": line.strip()})
    return out


# ---------------------------------------------------------------------------
# Small read helpers
# ---------------------------------------------------------------------------

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _now_iso():
    return datetime.datetime.now(MANILA).isoformat()


def _local_get(path, timeout=3.0):
    """GET a localhost-only path. Returns (ok, payload_or_error)."""
    if not str(path).startswith("/"):
        return False, "invalid local path"
    try:
        req = urllib.request.Request(LOCAL_BASE + path, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        try:
            return True, json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return True, raw.decode("utf-8", "replace")[:2000]
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _tcp_open(host, port, timeout=1.5):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _db_count(path, table):
    if not os.path.isfile(path):
        return None
    try:
        uri = "file:" + path.replace("?", "%3f") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as conn:
            return conn.execute("select count(*) from " + table).fetchone()[0]
    except sqlite3.Error:
        return None


# ---------------------------------------------------------------------------
# Connection cards (one per source confirmed in DISCOVERY.md)
# ---------------------------------------------------------------------------

CONNECTION_SOURCES = [
    ("cosmos", "COSMOS intelligences", "data/cosmos/executions.json + GET /api/cosmos/* (localhost)"),
    ("genesis", "GENESIS production", "data/cosmos/genesis/projects.json, renders.json + GET /api/cosmos/genesis/*"),
    ("trading-den", "Trading Den (read-only)", "trading_den/MMcloneLoopEngineering/data/denma_history.db + GET /api/bots/list, /api/screener"),
    ("claude-agent-sdk", "Claude Agent SDK bridge", "sibling OpenJarvis/src/openjarvis/agents/claude_code.py (local files)"),
    ("kokoro-tts", "Kokoro local voice", "127.0.0.1:8880 health endpoint"),
    ("openclaw", "OpenClaw gateway", "launchd ai.openclaw.gateway on 127.0.0.1:18789"),
]


def _connection_check(source_id):
    """Return (status, detail). Read-only. No secrets in detail."""
    if source_id == "cosmos":
        path = os.path.join(COSMOS_DIR, "executions.json")
        if os.path.isfile(path):
            return "Connected", f"Found {os.path.relpath(path, ROOT)}"
        return "Not connected", f"Missing {os.path.relpath(path, ROOT)}"
    if source_id == "genesis":
        paths = [os.path.join(GENESIS_DIR, "projects.json"), os.path.join(GENESIS_DIR, "renders.json")]
        found = [os.path.relpath(p, ROOT) for p in paths if os.path.isfile(p)]
        if len(found) == len(paths):
            return "Connected", "Found " + ", ".join(found)
        return "Not connected", "Missing: " + (", ".join(os.path.relpath(p, ROOT) for p in paths if not os.path.isfile(p)) or "none")
    if source_id == "trading-den":
        count = _db_count(DENMA_DB, "realized_pnl")
        if count is None:
            return "Error", f"Cannot read {os.path.relpath(DENMA_DB, ROOT)}"
        ok, payload = _local_get("/api/bots/list")
        detail = f"DB read-only OK ({count} realized rows); bots endpoint " + (
            "OK" if ok else f"unavailable ({payload})")
        return "Connected", detail
    if source_id == "claude-agent-sdk":
        path = os.path.join(OPENJARVIS_DIR, "src", "openjarvis", "agents", "claude_code.py")
        if os.path.isfile(path):
            return "Connected", "Found " + os.path.relpath(path, os.path.dirname(ROOT))
        return "Not connected", f"Missing {path}"
    if source_id == "kokoro-tts":
        return ("Connected" if _tcp_open("127.0.0.1", 8880) else "Not connected",
                "TCP probe 127.0.0.1:8880")
    if source_id == "openclaw":
        return ("Connected" if _tcp_open("127.0.0.1", 18789) else "Not connected",
                "TCP probe 127.0.0.1:18789 (launchd ai.openclaw.gateway)")
    return "Error", "unknown source"


def connections():
    out = []
    for source_id, label, source in CONNECTION_SOURCES:
        status, detail = _connection_check(source_id)
        out.append({
            "id": source_id, "label": label, "source": source,
            "status": status, "detail": detail,
            "writable": False,
        })
    return out


def test_connection(source_id):
    for sid, label, source in CONNECTION_SOURCES:
        if sid == source_id:
            status, detail = _connection_check(sid)
            return {"id": sid, "label": label, "source": source,
                    "status": status, "detail": detail, "tested_at": _now_iso()}
    return {"id": source_id, "status": "Error", "detail": "unknown source", "tested_at": _now_iso()}


# ---------------------------------------------------------------------------
# COSMOS read adapter — normalized: name, status, current task, last activity, source
# ---------------------------------------------------------------------------

def cosmos_intelligences():
    store = _read_json(os.path.join(COSMOS_DIR, "executions.json"))
    executions = store.get("executions") if isinstance(store.get("executions"), list) else []
    source = "data/cosmos/executions.json"
    now = time.time()
    out = []
    for agent_id, name, role in INTELLIGENCES:
        mine = [e for e in executions if isinstance(e, dict) and e.get("agent_id") == agent_id]
        # newest first, using epoch seconds or ISO string as available
        def _key(e):
            raw = e.get("finished_at") or e.get("started_at") or 0
            if isinstance(raw, (int, float)):
                return float(raw)
            try:
                return datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except ValueError:
                return 0.0
        mine.sort(key=_key, reverse=True)
        latest = mine[0] if mine else {}
        status = "unknown"
        if latest:
            started = latest.get("started_at")
            recent = isinstance(started, (int, float)) and (now - float(started) < 600)
            if latest.get("status") == "running" and recent:
                status = "running"
            elif latest.get("status") == "running":
                status = "stopped (stale running)"
            elif latest.get("status") == "failed":
                status = "stopped"
            elif latest.get("status") == "succeeded":
                status = "idle"
        out.append({
            "id": agent_id,
            "name": name,
            "role": role,
            "status": status,
            "current_task": str(latest.get("task") or "none recorded"),
            "last_activity_time": str(latest.get("finished_iso") or latest.get("started_iso") or "unknown"),
            "source": source,
            "evidence": {
                "executions": len(mine),
                "last_error": (latest.get("error") or {}).get("message") if isinstance(latest.get("error"), dict) else None,
            },
        })
    return out


# ---------------------------------------------------------------------------
# Genesis read adapter + action mapping (actions are authorized in the UI)
# ---------------------------------------------------------------------------

def _stop_reason(record):
    """Why did work stop? From stored error/detail/warnings/history — no guessing."""
    err = record.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "")
        message = str(err.get("message") or "")
        if code or message:
            return (code + ": " + message).strip(": ")
    detail = str(record.get("detail") or "").strip()
    if detail:
        return detail
    warnings = record.get("warnings")
    if isinstance(warnings, list) and warnings:
        return str(warnings[-1])
    history = record.get("history")
    if isinstance(history, list) and history:
        last = history[-1]
        if isinstance(last, dict) and last.get("note"):
            return str(last.get("note"))
    lines = [l.strip() for l in str(record.get("log") or "").splitlines() if l.strip()]
    if lines:
        return "reason unknown; last log line: " + lines[-1]
    return "reason unknown; no log line recorded"


def genesis_panel():
    projects = _read_json(os.path.join(GENESIS_DIR, "projects.json")).get("projects", [])
    renders = _read_json(os.path.join(GENESIS_DIR, "renders.json")).get("renders", [])
    publications = _read_json(os.path.join(GENESIS_DIR, "publications.json")).get("publications", [])
    projects = [p for p in projects if isinstance(p, dict)]
    renders = [r for r in renders if isinstance(r, dict)]
    publications = [p for p in publications if isinstance(p, dict)]

    working, needs_approval, stopped = [], [], []
    for p in projects:
        item = {
            "id": p.get("id"), "title": p.get("title") or "untitled",
            "status": p.get("status") or "unknown", "stage": p.get("stage") or "unknown",
            "division": p.get("division") or "unknown",
            "activity": p.get("updated_iso") or "unknown",
            "source": "data/cosmos/genesis/projects.json",
        }
        if p.get("status") == "awaiting_approval":
            approval = p.get("approval") if isinstance(p.get("approval"), dict) else {}
            review = p.get("quality_review") if isinstance(p.get("quality_review"), dict) else {}
            item["question"] = str(approval.get("note") or review.get("verdict") or
                                   "Approval requested; no question recorded")
            item["impact"] = ("Approve allows the next production/publish gate to proceed. "
                              "Reject returns the project to production; no money is spent by either action.")
            needs_approval.append(item)
        elif p.get("status") == "failed":
            item["why"] = _stop_reason(p)
            stopped.append(item)
        elif p.get("status") in ("in_production", "rendering", "quality_review", "approved", "published"):
            item["doing"] = str(p.get("stage") or p.get("status"))
            working.append(item)

    for r in renders:
        if r.get("status") in ("failed", "cancelled"):
            stopped.append({
                "id": r.get("id"), "title": "Render " + str(r.get("id") or "unknown"),
                "status": r.get("status"), "stage": r.get("stage") or "unknown",
                "why": _stop_reason(r), "activity": r.get("finished_at") or r.get("created_at") or "unknown",
                "source": "data/cosmos/genesis/renders.json",
                "restart": "render",
            })
        elif r.get("status") in ("queued", "preparing", "waiting_media", "assembling"):
            working.append({
                "id": r.get("id"), "title": "Render " + str(r.get("id") or "unknown"),
                "status": r.get("status"), "stage": r.get("stage") or "unknown",
                "doing": str(r.get("detail") or r.get("stage") or "rendering"),
                "activity": r.get("created_at") or "unknown",
                "source": "data/cosmos/genesis/renders.json",
            })

    for pub in publications:
        if pub.get("status") == "awaiting_approval":
            needs_approval.append({
                "id": pub.get("id"), "title": "Publication " + str(pub.get("id") or "unknown"),
                "status": pub.get("status"), "question": str(pub.get("approval", {}).get("note") or "Publish approval requested"),
                "impact": "Approve schedules publishing to the configured destination. Reject keeps it local.",
                "source": "data/cosmos/genesis/publications.json",
            })
        elif pub.get("status") in ("failed", "cancelled"):
            stopped.append({
                "id": pub.get("id"), "title": "Publication " + str(pub.get("id") or "unknown"),
                "status": pub.get("status"), "why": _stop_reason(pub),
                "source": "data/cosmos/genesis/publications.json", "restart": "publication",
            })

    return {"working_on": working, "needs_approval": needs_approval, "stopped": stopped,
            "read_only": True, "read_at": _now_iso()}


def genesis_restart_target(item):
    """Map Restart to the nearest existing endpoint (no native restart exists)."""
    kind = str(item.get("restart") or "")
    if kind == "publication":
        return {"endpoint": f"/api/cosmos/genesis/publications/{item.get('id')}/retry", "method": "POST"}
    if kind == "render":
        return {"endpoint": f"/api/cosmos/genesis/projects/{item.get('id')}/render", "method": "POST"}
    return {"endpoint": f"/api/cosmos/genesis/projects/{item.get('id')}/reopen", "method": "POST"}


# ---------------------------------------------------------------------------
# Trading Den read-only adapter
# ---------------------------------------------------------------------------

def _pnl_demo_rows():
    if not os.path.isfile(DENMA_DB):
        return {"total": None, "by_type": {}, "max_date": "unknown", "evidence": "DB missing"}
    try:
        uri = "file:" + DENMA_DB.replace("?", "%3f") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as conn:
            total = conn.execute("select coalesce(sum(amount_usd),0) from realized_pnl").fetchone()[0]
            rows = conn.execute(
                "select strategy_type, count(*), coalesce(sum(amount_usd),0) from realized_pnl group by strategy_type order by 3 desc"
            ).fetchall()
            max_date = conn.execute("select max(date) from realized_pnl").fetchone()[0]
            today = conn.execute(
                "select count(*), coalesce(sum(amount_usd),0) from realized_pnl "
                "where date(datetime(timestamp,'unixepoch','+8 hours')) = date('now','+8 hours')"
            ).fetchone()
            fills = conn.execute("select count(*) from fills").fetchone()[0]
    except sqlite3.Error as exc:
        return {"total": None, "by_type": {}, "max_date": "unknown", "evidence": f"sqlite error: {exc}"}
    return {
        "total": total,
        "by_type": [{"type": r[0] or "unknown", "rows": r[1], "amount": r[2]} for r in rows],
        "max_date": max_date or "none",
        "today_rows": today[0], "today_amount": today[1], "fills": fills,
        "evidence": "data source: denma_history.db (read-only URI)",
    }


def trading_brief():
    """Read-only. Demo values are explicitly labelled; live values are not faked."""
    demo = _pnl_demo_rows()
    ok_bots, bots = _local_get("/api/bots/list")
    ok_screen, screen = _local_get("/api/screener")
    ok_snap, snap = _local_get("/api/live-snapshot")

    # Hard-coded figures baked into the DenMa browser template. These are the
    # "demo values" the owner approved for display, always with a DEMO DATA
    # warning; they are NOT derived from any live feed or the database.
    browser_demo = {
        "pnl_today": "+318.42 USDT",
        "realized_pnl": "+271.15 USDT",
        "nav_connected_exchanges": "38,432.77 USDT",
        "source": "trading_den/MMcloneLoopEngineering/templates/index.html:1848-1859 (hard-coded, non-live)",
    }
    pnl = {
        "value": demo.get("total"),
        "browser_demo": browser_demo,
        "label": "DEMO DATA — seeded all-history rows, NOT today's PNL",
        "today_rows": demo.get("today_rows", "unknown"),
        "today_amount": demo.get("today_amount", "unknown"),
        "realized": demo.get("total"),
        "unrealized": "unknown (seeded positions only)",
        "fees": "unknown (fills rows: %s)" % demo.get("fills", "unknown"),
        "window": "midnight Asia/Manila → now (not available in data)",
        "source": demo.get("evidence"),
        "latest_row_date": demo.get("max_date"),
    }
    bot_list = []
    if ok_bots and isinstance(bots, dict):
        for bot in (bots.get("bots") or []):
            if isinstance(bot, dict):
                bot_list.append({"name": str(bot.get("name") or bot.get("id") or "unknown"),
                                 "status": str(bot.get("status") or "unknown"), "demo": False})
    for name in ("bot_tri_demo", "bot_cross_demo"):
        bot_list.append({"name": name, "status": "Running (browser demo)", "demo": True})
    bots_out = {
        "running": sum(1 for b in bot_list if not b["demo"] and str(b["status"]).lower() == "running"),
        "stopped": sum(1 for b in bot_list if not b["demo"] and str(b["status"]).lower() != "running"),
        "names": bot_list,
        "label": "DEMO DATA — browser localStorage demo bots are labeled; backend count is live"
                 if ok_bots else "unknown — bots endpoint unavailable: %s" % bots,
    }
    arb = {
        "ranking": [{"type": r["type"], "amount": r["amount"], "rows": r["rows"]}
                    for r in (demo.get("by_type") or [])],
        "leader": (demo.get("by_type") or [{}])[0].get("type", "unknown"),
        "margin": "unknown (no margin field in schema)",
        "label": "DEMO DATA — all-history seeded totals; no today rows (today_rows=%s)" % demo.get("today_rows", "unknown"),
        "source": demo.get("evidence"),
    }
    matrix = []
    if ok_screen and isinstance(screen, dict):
        opps = screen.get("opportunities") or screen.get("opps") or []
        for o in opps[:3]:
            if not isinstance(o, dict):
                continue
            matrix.append({
                "asset": o.get("symbol") or o.get("pair") or "unknown",
                "buy_venue": o.get("buyVenue") or o.get("buy_exchange") or "unknown",
                "sell_venue": o.get("sellVenue") or o.get("sell_exchange") or "unknown",
                "gross_spread_pct": o.get("pct") if o.get("pct") is not None else "unknown",
                "net_spread_pct": ((float(o.get("pct")) - float(o.get("totalFeePct", 0)))
                                   if isinstance(o.get("pct"), (int, float)) else "unknown"),
                "timestamp": o.get("updated") or "unknown",
                "stale": bool(o.get("stale", False)),
                "source": "GET /api/screener (localhost, read-only)",
            })
    matrix_out = {
        "items": matrix,
        "stale_threshold_seconds": 10.0,
        "stale_flag": "updated older than 10s backend / 15s UI → stale and unreliable",
        "status": "live" if matrix else ("unknown — screener unavailable: %s" % screen if not ok_screen else "no opportunities returned"),
        "snapshot_time": (snap.get("timestamp") if ok_snap and isinstance(snap, dict) else "unknown"),
    }
    return {
        "pnl_today": pnl, "bots": bots_out, "arbitrage": arb, "matrix": matrix_out,
        "read_only": True, "generated_at": _now_iso(),
        "note": "No write/control action is exposed for Trading Den.",
    }


# ---------------------------------------------------------------------------
# Tool routing layer — adapters answer known intents; unknown goes to normal brain
# ---------------------------------------------------------------------------

def tool_route(query):
    text = str(query or "").strip().lower()
    if "genesis status" in text or ("genesis" in text and "status" in text):
        panel = genesis_panel()
        working = len(panel["working_on"])
        approvals = len(panel["needs_approval"])
        stopped = len(panel["stopped"])
        why = panel["stopped"][0].get("why") if panel["stopped"] else "none"
        speech = (f"Genesis: {working} items working on, {approvals} needing approval, "
                  f"and {stopped} stopped. {why}." if stopped else
                  f"Genesis: {working} items working on, {approvals} needing approval, and no stopped work.")
        return {"handled": True, "intent": "genesis_status", "speech": speech, "data": panel}
    if "trading den brief" in text or ("trading" in text and "brief" in text):
        brief = trading_brief()
        p = brief["pnl_today"]
        b = brief["bots"]
        bd = p.get("browser_demo") or {}
        speech = (f"Trading Den brief. DEMO DATA: the browser shows PNL today {bd.get('pnl_today', 'unknown')}, "
                  f"realized {bd.get('realized_pnl', 'unknown')}, and NAV {bd.get('nav_connected_exchanges', 'unknown')} — "
                  f"these are hard-coded, not live. Database all-history total is {p['value']} — {p['label']}. "
                  f"Running bots: {b['running']}. Arbitrage leader: {brief['arbitrage']['leader']} — {brief['arbitrage']['label']}. "
                  f"Matrix has {len(brief['matrix']['items'])} opportunities from a read-only feed.")
        return {"handled": True, "intent": "trading_brief", "speech": speech, "data": brief}
    if "cosmos status" in text or ("cosmos" in text and "status" in text):
        agents = cosmos_intelligences()
        speech = "COSMOS intelligences: " + ", ".join(
            f"{a['name']} {a['status']}" for a in agents) + "."
        return {"handled": True, "intent": "cosmos_status", "speech": speech, "data": agents}
    return {"handled": False, "intent": "unknown", "speech": "", "data": None}


def genesis_action(kind, item, token, note=""):
    """Perform an explicit, already-confirmed Genesis action via existing APIs.

    Only Approve / Reject / Restart are exposed. Restart maps to the nearest
    existing endpoint because no native restart route exists.
    """
    item = item if isinstance(item, dict) else {}
    item_id = str(item.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
        return {"ok": False, "status": 0, "error": "invalid id", "response": ""}
    kind = str(kind or "")
    if kind == "approve":
        endpoint = f"/api/cosmos/genesis/projects/{item_id}/approval"
        payload = {"approved": True, "note": str(note or "Approved via Jarvis")}
    elif kind == "reject":
        endpoint = f"/api/cosmos/genesis/projects/{item_id}/approval"
        payload = {"approved": False, "note": str(note or "Rejected via Jarvis")}
    elif kind == "restart":
        mapped = genesis_restart_target(item)
        endpoint = mapped["endpoint"]
        payload = {}
    else:
        return {"ok": False, "status": 0, "error": "unknown action", "response": ""}

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["X-Dennis-Token"] = str(token)
    try:
        req = urllib.request.Request(
            LOCAL_BASE + endpoint, data=json.dumps(payload).encode("utf-8"),
            headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "status": 0, "error": f"{type(exc).__name__}: {exc}",
                "endpoint": endpoint, "response": ""}
    text = body.decode("utf-8", "replace")[:1000]
    return {"ok": 200 <= status < 300, "status": status, "endpoint": endpoint,
            "response": text}


def health():
    return {
        "audit_writable": os.access(JARVIS_HOME, os.W_OK) or os.access(os.path.expanduser("~"), os.W_OK),
        "audit_path": AUDIT_LOG,
        "connections": len(CONNECTION_SOURCES),
        "manila_now": _now_iso(),
    }
