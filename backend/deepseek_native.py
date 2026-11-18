"""Supervisor for DeepSeek's official Harness Web UI."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

HOST = "127.0.0.1"
PORT = 3080
URL = f"http://{HOST}:{PORT}/"
_lock = threading.Lock()
_output_lock = threading.Lock()
_proc = None
_url = None
_output = deque(maxlen=120)
# Harness prints its web URL with the launch credential either inline
# (``http://127.0.0.1:3080/?token=…``) or split across nearby lines
# (``Local: http://127.0.0.1:3080`` then a token line). Both forms are
# accepted, so a tokenless URL still works whenever Harness itself needs none.
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
_TOKEN_RE = re.compile(
    r"(?:token|access[-_]?token|key|secret|credential)[\s:=]+"
    r"([A-Za-z0-9._~+/=-]{8,})", re.I)


class HarnessError(RuntimeError):
    pass


def _reachable():
    try:
        with urlopen(URL, timeout=1.2) as response:
            return response.status < 500
    except HTTPError as response:
        # Harness can reject a tokenless probe while still being healthy.
        return response.code < 500
    except (OSError, URLError):
        return False


def _clean_url(candidate):
    """Normalise one printed URL candidate; return None when unusable."""
    candidate = candidate.rstrip(".,);]'\"")
    if not candidate.startswith(("http://", "https://")):
        return None
    # Only the local web UI is embedded; a printed docs/auth URL is not it.
    if not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", candidate):
        return None
    return candidate


def _apply_url(candidate):
    """Record a launch URL, preferring the one carrying credentials."""
    global _url
    cleaned = _clean_url(candidate)
    if not cleaned:
        return
    credentialed = ("?" in cleaned) or ("#" in cleaned)
    if _url is None:
        _url = cleaned
    elif credentialed and ("?" not in _url) and ("#" not in _url):
        _url = cleaned


def _apply_token_line(line):
    """Attach a bare credential printed on its own line to the launch URL."""
    global _url
    if _url is None:
        return
    if "token=" in _url:
        return  # the credential is already in the URL; never duplicate it
    match = _TOKEN_RE.search(line)
    if not match:
        return
    credential = match.group(1).strip()
    if not credential:
        return
    # A query always precedes the fragment, so split both off first.
    base, sep, fragment = _url.partition("#")
    joiner = "&" if "?" in base else "?"
    _url = f"{base}{joiner}token={credential}" + (f"#{fragment}" if sep else "")


def _read_output(proc):
    global _url
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            with _output_lock:
                _output.append(line[-1000:])
            for candidate in _URL_RE.findall(line):
                _apply_url(candidate)
            _apply_token_line(line)
    except (OSError, ValueError):
        pass


def _listener_pid():
    """PID of the process listening on the Harness port, or None.

    Only used to retire a stale instance the OS no longer owns: its launch URL
    -- and therefore the browser credential it carries -- left with the old
    process, so the embedded view would only meet the 401 fence.
    """
    for argv in (["lsof", "-ti", f"tcp:{PORT}", "-sTCP:LISTEN"],
                 ["fuser", f"{PORT}/tcp"]):
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except OSError:
            continue
        pids = [p for p in res.stdout.split() if p.isdigit()]
        if pids:
            return int(pids[0])
    return None


def _process_looks_like_dsh(pid):
    try:
        res = subprocess.run(["ps", "-p", str(pid), "-o", "comm="],
                             capture_output=True, text=True, timeout=10)
    except OSError:
        return False
    name = (res.stdout or "").strip().lower()
    return "node" in name or "dsh" in name or "npm" in name


def _retire_stale_listener():
    """Stop an unowned Harness listener so a managed launch can take the port."""
    pid = _listener_pid()
    if pid is None:
        return
    if not _process_looks_like_dsh(pid):
        raise HarnessError(
            f"Port {PORT} is held by an unexpected process (pid {pid}). Stop it "
            "manually, then start DeepSeek Harness from Workspace.")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise HarnessError(f"Could not stop the stale Harness process: {exc}") from exc
    for _ in range(40):  # up to 10s for the port to be released
        if not _reachable():
            return
        time.sleep(0.25)
    raise HarnessError(
        f"The stale Harness process (pid {pid}) did not stop. Kill it and start "
        "DeepSeek Harness from Workspace again.")


def status():
    running = _reachable()
    managed = bool(_proc and _proc.poll() is None)
    return {"running": running, "url": _url if running and _url else None,
            "managed": managed}


def start():
    global _proc, _url
    with _lock:
        if _reachable():
            current = status()
            if current["url"]:
                return current
            _retire_stale_listener()

        npx = shutil.which("npx")
        if not npx:
            raise HarnessError("Node.js and npm are required. Install Node.js, then restart the OS.")
        if _proc is None or _proc.poll() is not None:
            try:
                env = os.environ.copy()
                env["BROWSER"] = "none"
                _url = None
                with _output_lock:
                    _output.clear()
                _proc = subprocess.Popen(
                    [npx, "--yes", "@deepseek-ai/dsh", "web", "--no-open"],
                    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    close_fds=True,
                    env=env,
                )
                threading.Thread(target=_read_output, args=(_proc,), daemon=True,
                                 name="deepseek-harness-output").start()
            except OSError as exc:
                raise HarnessError(f"Could not launch DeepSeek Harness: {exc}") from exc

        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if _proc.poll() is not None:
                _proc = None
                with _output_lock:
                    detail = "\n".join(_output)[-1800:]
                raise HarnessError("DeepSeek Harness exited during startup. " +
                                   (detail or "Check Node.js/npm and try again."))
            if _reachable():
                if _url:
                    return status()
                # The service answers but has not printed its URL yet (the web
                # app announces after its loader settles). Give the output
                # thread a short grace period before calling it missing.
                grace = time.monotonic() + 8.0
                while time.monotonic() < grace:
                    if _url:
                        return status()
                    if _proc.poll() is not None:
                        break
                    time.sleep(0.25)
                if _url:
                    return status()
                with _output_lock:
                    detail = "\n".join(_output)[-1800:]
                raise HarnessError(
                    "DeepSeek Harness is serving but did not print its launch URL. "
                    "The embedded view needs that URL (it carries the launch "
                    "credential)." + (("\n" + detail) if detail else ""))
            time.sleep(0.25)

        with _output_lock:
            detail = "\n".join(_output)[-1800:]
        raise HarnessError("DeepSeek Harness did not provide a launch URL within 300 seconds." +
                           (("\n" + detail) if detail else ""))


def stop():
    global _proc
    with _lock:
        proc, _proc = _proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
