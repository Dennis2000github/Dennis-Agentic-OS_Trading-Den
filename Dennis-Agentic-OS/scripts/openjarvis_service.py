"""Manage the sibling OpenJarvis briefing service (start/stop/restart/status).

OpenJarvis (Stanford, Apache-2.0) is installed as a sibling repo and provides
the OS's daily-briefing pipeline (morning digest agent + REST API). This
script guarantees exactly ONE shared `jarvis serve` process:

- probes the digest API first — if the service already answers, does nothing,
- otherwise checks the PID file for a live process before spawning,
- `start` is a no-op when the service is already up (never double-starts).

Usage (from the repo root):
    python3 scripts/openjarvis_service.py start|stop|restart|status

Configuration (environment):
    OPENJARVIS_HOME   sibling checkout (default: ../OpenJarvis next to this repo)
    OPENJARVIS_URL    service base URL (default: http://127.0.0.1:8000)
    OPENJARVIS_CMD    override launch command (shell string; default uses
                      the sibling's run-jarvis.sh wrapper, which loads .env
                      and execs `uv run jarvis serve`)
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
DEFAULT_HOME = os.environ.get(
    "OPENJARVIS_HOME", os.path.normpath(os.path.join(REPO, "..", "OpenJarvis")))
RUN_DIR = os.path.join(REPO, "data", "openjarvis")
PID_FILE = os.path.join(RUN_DIR, "service.pid")
LOG_FILE = os.path.join(RUN_DIR, "service.log")
OLLAMA_PID_FILE = os.path.join(RUN_DIR, "ollama.pid")
OLLAMA_LOG_FILE = os.path.join(RUN_DIR, "ollama.log")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
HOST = "127.0.0.1"
PORT = int(os.environ.get("OPENJARVIS_PORT", "8000"))
URL = os.environ.get("OPENJARVIS_URL", f"http://{HOST}:{PORT}")


def _probe(timeout=2.0):
    """True when the OpenJarvis HTTP server answers at all.

    Deliberately NOT /api/health: that endpoint reports the LLM engine's
    health and answers 503 while Ollama is cold. Any HTTP response — even a
    401/503 — proves the server process is up, which is all this manager
    cares about. The cheap /api/digest/schedule route is the probe target.
    """
    try:
        urllib.request.urlopen(f"{URL}/api/digest/schedule", timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # the server answered; the engine may still be warming
    except (urllib.error.URLError, OSError):
        return False


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


def _ollama_healthy(timeout=2.0):
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _ensure_ollama(wait_s=30):
    """Start `ollama serve` on demand (loopback).

    OpenJarvis's engine discovery falls back to the NIM cloud engine when
    Ollama is down, which fails without a valid cloud key — so the local
    brain must be up before the digest pipeline runs. An already-running
    Ollama (shared system service) is left untouched.
    """
    if _ollama_healthy():
        return True, "already-running"
    import shutil
    ollama_bin = shutil.which("ollama")
    if not ollama_bin:
        return False, "ollama binary not found"
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(OLLAMA_LOG_FILE, "ab") as log:
        log.write(f"\n===== ollama start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
        log.flush()
        proc = subprocess.Popen(
            [ollama_bin, "serve"], cwd=REPO,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    with open(OLLAMA_PID_FILE, "w") as f:
        f.write(str(proc.pid))
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if _ollama_healthy():
            return True, f"started (pid {proc.pid})"
        if proc.poll() is not None:
            return False, f"ollama exited rc={proc.returncode}"
        time.sleep(0.5)
    return _ollama_healthy(), "timeout waiting for ollama"


def _launch_cmd():
    """The command that runs `jarvis serve` in the sibling checkout."""
    override = os.environ.get("OPENJARVIS_CMD", "").strip()
    if override:
        import shlex
        return shlex.split(override)
    wrapper = os.path.join(DEFAULT_HOME, "run-jarvis.sh")
    if os.path.isfile(wrapper):
        return ["/bin/bash", wrapper, "serve", "--host", HOST, "--port", str(PORT)]
    # No wrapper (uv not at ~/.local/bin, etc.) — let the env provide it.
    raise FileNotFoundError(
        f"OpenJarvis launcher not found at {wrapper} — set OPENJARVIS_CMD")


def status():
    return {
        "url": URL,
        "home": DEFAULT_HOME,
        "reachable": _probe(),
        "pid": _read_pid(),
        "pid_alive": bool(_read_pid() and _pid_alive(_read_pid())),
        "ollama": {"url": OLLAMA_URL, "healthy": _ollama_healthy()},
        "log": LOG_FILE,
    }


def start(wait_ready_s=90):
    st = status()
    if st["reachable"]:
        print(json.dumps({"action": "noop", "reason": "service already up",
                          "url": URL}, indent=2))
        return 0
    if st["pid"] and st["pid_alive"]:
        # Process exists but not answering yet (still importing) or wedged.
        deadline = time.time() + wait_ready_s
        while time.time() < deadline:
            if _probe():
                print(json.dumps({"action": "started", "url": URL}, indent=2))
                return 0
            time.sleep(1.0)
        print(json.dumps({"action": "timeout", "pid": st["pid"]}, indent=2))
        return 1

    try:
        cmd = _launch_cmd()
    except FileNotFoundError as exc:
        print(json.dumps({"action": "error", "error": str(exc)}, indent=2))
        return 1
    if not os.path.isdir(DEFAULT_HOME):
        print(json.dumps({"action": "error", "error": (
            f"OpenJarvis checkout missing at {DEFAULT_HOME} — "
            "set OPENJARVIS_HOME")}, indent=2))
        return 1

    # The local brain must be up: engine discovery otherwise falls back to
    # the NIM cloud engine, which fails without a valid key.
    ollama_ok, ollama_detail = _ensure_ollama()
    if not ollama_ok:
        print(json.dumps({"action": "error",
                          "error": f"could not start Ollama: {ollama_detail}"},
                         indent=2))
        return 1

    os.makedirs(RUN_DIR, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("OPENJARVIS_URL", URL)
    # Hand the OS vault to the briefing connector (obsidian). digest_collect
    # builds fresh connector instances, so the path must arrive via env.
    env.setdefault("OBSIDIAN_VAULT_PATH", os.path.join(REPO, "vault"))
    # The Dennis workspace venv must not leak into uv's project resolution
    # (uv otherwise warns and risks resolving the wrong environment).
    env.pop("VIRTUAL_ENV", None)
    env.pop("PYTHONHOME", None)
    with open(LOG_FILE, "ab") as log:
        log.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
        log.flush()
        proc = subprocess.Popen(
            cmd, cwd=DEFAULT_HOME, env=env,
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
        if _probe():
            print(json.dumps({"action": "started", "pid": proc.pid, "url": URL,
                              "ollama": ollama_detail}, indent=2))
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
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
