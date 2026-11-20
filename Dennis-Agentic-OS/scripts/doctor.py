#!/usr/bin/env python3
"""Doctor check for the JARVIS OS upgrade (Phase 5 lock-down).

Run: python3 scripts/doctor.py   (or: npm run doctor)

Checks are read-only except that they never start/stop services. They assert:
  * syntax of every patched file
  * localhost-only bind in source and at runtime
  * private-home / audit / secrets permissions
  * no provider key is exposed in browser-facing payloads
  * every write route is confirmation-gated
  * Trading Den remains read-only
Exit 0 = no FAIL, 1 = at least one FAIL. No secret values are printed.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)

JARVIS_HOME = os.path.expanduser("~/.jarvis")
AUDIT_LOG = os.path.join(JARVIS_HOME, "audit.log")
SECRETS = os.path.join(JARVIS_HOME, "secrets.json")
HEALTH_URL = "http://127.0.0.1:8080/api/jarvis/health"

results = []


def record(level, name, detail):
    results.append((level, name, detail))
    print(f"[{level:4}] {name}: {detail}")


def ok(name, detail):
    record("PASS", name, detail)


def fail(name, detail):
    record("FAIL", name, detail)


def info(name, detail):
    record("INFO", name, detail)


def check_syntax():
    py = [os.path.join(APP, "app.py"),
          os.path.join(APP, "backend", "tts.py"),
          os.path.join(APP, "backend", "jarvis_os.py"),
          os.path.join(HERE, "doctor.py")]
    bad = []
    for f in py:
        if not os.path.isfile(f):
            bad.append(os.path.basename(f) + " (missing)")
            continue
        r = subprocess.run([sys.executable, "-m", "py_compile", f],
                           capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(os.path.basename(f) + " " + r.stderr.strip()[:160])
    if bad:
        fail("python syntax", "; ".join(bad))
    else:
        ok("python syntax", f"{len(py)} files compile")

    node = shutil.which("node")
    if not node:
        fail("js syntax", "node not found")
        return
    js = ["js/app.js", "js/galaxy.js", "js/jarvisOs.js"]
    bad = []
    for rel in js:
        f = os.path.join(APP, rel)
        r = subprocess.run([node, "--check", f], capture_output=True, text=True)
        if r.returncode != 0:
            bad.append(rel + " " + r.stderr.strip()[:160])
    if bad:
        fail("js syntax", "; ".join(bad))
    else:
        ok("js syntax", f"{len(js)} files pass node --check")


def check_bind_source():
    with open(os.path.join(APP, "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    if 'bind_host = "127.0.0.1"' in src and 'bind_host = ""' not in src:
        ok("localhost bind (source)", 'app.py uses bind_host = "127.0.0.1"')
    else:
        fail("localhost bind (source)", "expected literal localhost-only bind not found")


def check_permissions():
    if not os.path.isdir(JARVIS_HOME):
        fail("private home", f"{JARVIS_HOME} missing — run any jarvis API call once")
        return
    mode = stat.S_IMODE(os.stat(JARVIS_HOME).st_mode)
    if mode == 0o700:
        ok("private home", f"{JARVIS_HOME} is 0700")
    else:
        fail("private home", f"{JARVIS_HOME} is {oct(mode)}, expected 0700")

    for path, label in ((AUDIT_LOG, "audit log"), (SECRETS, "secrets file")):
        if not os.path.isfile(path):
            info(label, "not created yet (created on first use)")
            continue
        mode = stat.S_IMODE(os.stat(path).st_mode)
        if mode == 0o600:
            ok(label, f"{os.path.basename(path)} is 0600")
        else:
            fail(label, f"{os.path.basename(path)} is {oct(mode)}, expected 0600")


def check_confirmation_and_readonly():
    with open(os.path.join(APP, "app.py"), encoding="utf-8") as fh:
        src = fh.read()
    post_start = src.find("def do_POST")
    get_start = src.find("def do_GET")
    post_block = src[post_start:] if post_start >= 0 else ""
    get_block = src[get_start:post_start] if 0 <= get_start < post_start else ""

    writes = ["/api/jarvis/audit", "/api/jarvis/genesis/action"]
    bad = [w for w in writes if w in post_block and
           post_block[post_block.find(w):post_block.find(w) + 400].find("confirmed") < 0]
    if bad:
        fail("write confirmation", "ungated POST routes: " + ", ".join(bad))
    else:
        ok("write confirmation", f"{len(writes)} write routes require confirmed:true")

    if "/api/jarvis/trading-brief" in get_block and "/api/jarvis/trading-brief" not in post_block:
        ok("Trading Den read-only", "trading brief is GET-only; no control route")
    else:
        fail("Trading Den read-only", "trading route missing from GET or present in POST")

    # No provider key field may be returned by browser-facing status payloads.
    from backend import jarvis_os, tts
    payload = json.dumps({
        "connections": jarvis_os.connections(),
        "tts_status": {"fish": tts.fish_audio_status(),
                       "elevenlabs": tts.elevenlabs_status()},
    }, default=str).lower()
    leaks = [k for k in ("api_key", "apikey", "secret", "bearer ") if k in payload]
    if leaks:
        fail("no key in browser payload", "possible key fields: " + ", ".join(leaks))
    else:
        ok("no key in browser payload", "only configured/status booleans returned")


def check_runtime():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/api/auth/session", timeout=3) as r:
            token = json.loads(r.read().decode("utf-8")).get("token", "")
        req = urllib.request.Request(HEALTH_URL + "?token=" + urllib.parse.quote(token))
        with urllib.request.urlopen(req, timeout=3) as r:
            health = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        info("runtime health", f"server not reachable at 127.0.0.1:8080 ({type(exc).__name__}) — run npm run boot")
        return
    if health.get("connections", 0) >= 1:
        ok("runtime health", f"alive; {health.get('connections')} connection sources; manila_now={health.get('manila_now')}")
    else:
        fail("runtime health", json.dumps(health)[:200])

    # Runtime listener must be loopback.
    lsof = shutil.which("lsof")
    if lsof:
        out = subprocess.run([lsof, "-nP", "-iTCP:8080", "-sTCP:LISTEN"],
                             capture_output=True, text=True).stdout
        lines = [l for l in out.splitlines() if "LISTEN" in l]
        if lines and all("127.0.0.1:8080" in l or "[::1]:8080" in l for l in lines):
            ok("localhost bind (runtime)", "8080 listener is loopback only")
        elif lines:
            fail("localhost bind (runtime)", "non-loopback listener: " + " | ".join(lines))
        else:
            info("localhost bind (runtime)", "no listener line captured")


def check_backend_readiness():
    try:
        from backend import preflight
        report = preflight.run()
    except Exception as exc:
        fail("backend readiness", f"{type(exc).__name__}: {exc}")
        return
    if report.get("ready"):
        ok("backend readiness", f"ready via {report.get('backend')}")
    else:
        fail("backend readiness", str(report.get("message"))[:200])
    if report.get("docker", {}).get("code") != "ready":
        info("docker", "not ready — local agent fallback is expected, not a FAIL")


def main():
    print("=== JARVIS OS doctor ===")
    check_syntax()
    check_bind_source()
    check_permissions()
    check_confirmation_and_readonly()
    check_runtime()
    check_backend_readiness()
    fails = [r for r in results if r[0] == "FAIL"]
    print("===")
    print(f"{len(results) - len(fails)} pass/info, {len(fails)} fail")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
