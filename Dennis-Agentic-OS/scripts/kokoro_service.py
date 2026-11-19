"""Manage the local Kokoro TTS inference service (start/stop/restart/status).

Guarantees exactly ONE shared inference process:
- probes 127.0.0.1:8880 first — if a healthy service answers, does nothing,
- otherwise checks the PID file for a live process before spawning,
- `start` is a no-op when the service is already up (never double-starts).

Usage (from the repo root):
    python3 scripts/kokoro_service.py start|stop|restart|status|health
"""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "tts_service", "server.py")
VENV_PY = os.path.join(REPO, "tts_service", ".venv", "bin", "python")
RUN_DIR = os.path.join(REPO, "data", "tts")
PID_FILE = os.path.join(RUN_DIR, "service.pid")
LOG_FILE = os.path.join(RUN_DIR, "service.log")
HOST = os.environ.get("KOKORO_HOST", "127.0.0.1")
PORT = int(os.environ.get("KOKORO_PORT", "8880"))
URL = f"http://{HOST}:{PORT}"


def _health(timeout=2.0):
    """HTTP 200 on /health = healthy (schema-agnostic: any local variant)."""
    try:
        with urllib.request.urlopen(f"{URL}/health", timeout=timeout) as r:
            body = r.read().decode("utf-8")
            data = json.loads(body)
            return data if isinstance(data, dict) else {"ok": True}
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _read_pid():
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status():
    health = _health()
    pid = _read_pid()
    return {
        "url": URL,
        "healthy": health is not None,
        "health": health,
        "pid": pid,
        "pid_alive": bool(pid and _pid_alive(pid)),
        "log": LOG_FILE,
    }


def start(wait_ready_s=90):
    st = status()
    if st["healthy"]:
        print(json.dumps({"action": "noop", "reason": "service already healthy",
                          "url": URL, "health": st["health"]}, indent=2))
        return 0
    if st["pid"] and st["pid_alive"]:
        # Process exists but not healthy yet (still loading) or wedged.
        deadline = time.time() + wait_ready_s
        while time.time() < deadline:
            health = _health()
            if health is not None:
                print(json.dumps({"action": "started", "url": URL,
                                  "health": health}, indent=2))
                return 0
            time.sleep(1.0)
        print(json.dumps({"action": "timeout", "pid": st["pid"]}, indent=2))
        return 1

    os.makedirs(RUN_DIR, exist_ok=True)
    if not os.path.isfile(VENV_PY):
        print(json.dumps({"action": "error", "error": (
            f"venv python missing at {VENV_PY} — run tts_service/setup.sh first")},
            indent=2))
        return 1
    env = dict(os.environ)
    env.setdefault("KOKORO_HOST", HOST)
    env.setdefault("KOKORO_PORT", str(PORT))
    with open(LOG_FILE, "ab") as log:
        log.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
        log.flush()
        proc = subprocess.Popen(
            [VENV_PY, SERVER],
            cwd=REPO, env=env,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    deadline = time.time() + wait_ready_s
    while time.time() < deadline:
        if proc.poll() is not None:
            print(json.dumps({"action": "error",
                              "error": f"service exited rc={proc.returncode}",
                              "log": LOG_FILE}, indent=2))
            return 1
        health = _health()
        if health is not None:
            print(json.dumps({"action": "started", "pid": proc.pid, "url": URL,
                              "health": health}, indent=2))
            return 0
        time.sleep(1.0)
    print(json.dumps({"action": "timeout", "pid": proc.pid, "log": LOG_FILE}, indent=2))
    return 1


def stop():
    pid = _read_pid()
    if not pid or not _pid_alive(pid):
        print(json.dumps({"action": "noop", "reason": "not running"}))
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.time() + 15
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.3)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass
    print(json.dumps({"action": "stopped", "pid": pid}))
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        sys.exit(start())
    elif cmd == "stop":
        sys.exit(stop())
    elif cmd == "restart":
        stop()
        sys.exit(start())
    elif cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "health":
        print(json.dumps(_health(5.0), indent=2))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
