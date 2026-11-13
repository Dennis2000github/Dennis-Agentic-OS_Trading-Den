"""Supervisor for DeepSeek's official Harness Web UI."""
from __future__ import annotations

import os
import re
import shutil
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


def _read_output(proc):
    global _url
    try:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            with _output_lock:
                _output.append(line[-1000:])
            for candidate in re.findall(r"https?://[^\s\"'<>]+", line):
                candidate = candidate.rstrip(".,);]")
                if candidate.startswith(URL) and ("?" in candidate or "#" in candidate):
                    _url = candidate
    except (OSError, ValueError):
        pass


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
            raise HarnessError(
                "DeepSeek Harness is already running outside the OS, but its launch URL "
                "is unavailable. Stop that process and start it from Workspace."
            )

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
            if _reachable() and _url:
                return status()
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
