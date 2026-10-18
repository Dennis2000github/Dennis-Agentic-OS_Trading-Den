"""DenMa (Trading Den) backend supervisor.

The Trading Den dashboard shipped in ``trading_den/MMcloneLoopEngineering``
serves its own REST API and WebSocket feed. It uses same-origin relative URLs
(``/api/...`` and ``/ws``), so embedding the page inside the OS origin breaks
every call. Instead of proxying, we run the DenMa ``app.py`` as its own server
and let the OS link out to it (its own origin => REST + WS just work).

This module:
  * finds a Python interpreter that has the DenMa dependencies installed,
  * spawns ``app.py --host <host> --port <port>`` in the background,
  * reports health/status and supports restart,
  * stops the child when the OS server exits.
"""

import atexit
import os
import socket
import subprocess
import sys
import threading
import time

from .config import DIRECTORY

PROJECT_DIR = os.path.join(DIRECTORY, "trading_den", "MMcloneLoopEngineering")

DEFAULT_HOST = os.environ.get("DENMA_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("DENMA_PORT", "8000"))
AUTOSTART = os.environ.get("DENNIS_NO_DENMA", "") not in ("1", "true", "yes")

# Dependencies the DenMa backend imports at startup.
_REQUIRED_MODULES = "import ccxt, ccxt.pro, aiohttp, aiodns"

_DEP_HINT = (
    "No Python interpreter with the DenMa dependencies (ccxt, aiohttp, aiodns) "
    "was found. Install them, e.g.: pip install -r "
    "trading_den/MMcloneLoopEngineering/requirements.txt — or point DENMA_PYTHON "
    "at an interpreter that already has them."
)


def _port_open(host, port, timeout=0.3):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            return sock.connect_ex((host, port)) == 0
        except OSError:
            return False


def _has_deps(python):
    try:
        proc = subprocess.run(
            [python, "-c", _REQUIRED_MODULES],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=25,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _candidate_pythons():
    """Interpreters to try, most specific first."""
    seen = set()

    def emit(path):
        if not path:
            return None
        path = os.path.expanduser(path)
        if path in seen or not os.path.exists(path):
            return None
        seen.add(path)
        return path

    for candidate in (
        os.environ.get("DENMA_PYTHON"),
        os.path.join(PROJECT_DIR, ".venv", "bin", "python"),
        os.path.expanduser("~/PycharmProjects/MarketMaking/.venv/bin/python"),
        sys.executable,
        sys.executable and os.path.join(os.path.dirname(sys.executable), "python3"),
    ):
        resolved = emit(candidate)
        if resolved:
            yield resolved


def find_python():
    for python in _candidate_pythons():
        if _has_deps(python):
            return python
    return None


class DenmaBackend:
    """Supervises the DenMa backend process."""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, project_dir=PROJECT_DIR,
                 autostart=AUTOSTART):
        self.host = host
        self.port = int(port)
        self.project_dir = project_dir
        self.autostart = autostart
        self.python = None
        self.proc = None
        self.state = "stopped"  # stopped | starting | running | unavailable
        self.error = ""
        self.started_at = None
        self.log_path = os.path.join(project_dir, "server.log")
        self._lock = threading.Lock()

    # -- urls ---------------------------------------------------------------
    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    @property
    def ws_url(self):
        return f"ws://{self.host}:{self.port}/ws"

    # -- lifecycle ----------------------------------------------------------
    def _is_managed_alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def _port_serving(self):
        return _port_open(self.host, self.port)

    def start(self):
        """Start the backend in a daemon thread. Returns immediately."""
        with self._lock:
            if self._is_managed_alive() or self._port_serving():
                self.state = "running"
                return
            if not self.autostart:
                self.state = "stopped"
                self.error = "Autostart disabled (DENNIS_NO_DENMA set)"
                return
            self.state = "starting"
            self.error = ""
        threading.Thread(target=self._start_blocking, daemon=True).start()

    def _start_blocking(self):
        python = find_python()
        if not python:
            self.state = "unavailable"
            self.error = _DEP_HINT
            return
        self.python = python
        try:
            log = open(self.log_path, "ab", buffering=0)
        except OSError:
            log = subprocess.DEVNULL
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            self.proc = subprocess.Popen(
                [python, "app.py", "--host", self.host, "--port", str(self.port)],
                cwd=self.project_dir,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        except Exception as exc:
            self.state = "unavailable"
            self.error = f"Failed to launch DenMa backend: {exc}"
            return
        self.started_at = time.time()
        if self.wait_ready(timeout=30.0):
            self.state = "running"
        elif self._is_managed_alive():
            self.state = "starting"
        else:
            self.state = "unavailable"
            self.error = f"DenMa backend exited during startup. See {self.log_path}"

    def wait_ready(self, timeout=30.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._port_serving():
                return True
            if self.proc is not None and self.proc.poll() is not None:
                return False
            time.sleep(0.4)
        return False

    def stop(self, timeout=8.0):
        with self._lock:
            proc = self.proc
            self.proc = None
        if not proc or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass

    def restart(self):
        self.stop()
        with self._lock:
            self.started_at = None
            self.state = "stopped"
        self.start()
        self.wait_ready(timeout=30.0)
        if self._port_serving():
            self.state = "running"

    # -- reporting ----------------------------------------------------------
    def status(self):
        managed = self._is_managed_alive()
        serving = self._port_serving()
        if managed or serving:
            self.state = "running"
            if serving and not managed:
                self.error = ""
        running = managed or serving
        if running and self.started_at is None:
            self.started_at = time.time()
        return {
            "running": running,
            "managed": managed,
            "external": serving and not managed,
            "state": self.state,
            "url": self.url,
            "ws_url": self.ws_url,
            "host": self.host,
            "port": self.port,
            "python": self.python or "",
            "log": self.log_path,
            "error": self.error,
            "uptime": round(time.time() - self.started_at, 1)
            if (self.started_at and running) else 0,
        }


BACKEND = DenmaBackend()


def start():
    BACKEND.start()


def stop():
    BACKEND.stop()


def shutdown_hook():
    atexit.register(BACKEND.stop)
