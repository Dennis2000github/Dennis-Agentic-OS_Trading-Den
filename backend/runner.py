"""Generic project server runner for the Code & Preview panel.

The Code & Preview pane doubles as a small IDE: you can edit and save files,
then press Run to start the open project's dev server. This module detects a
plausible command for a project folder, supervises it as a subprocess, captures
its output, and parses a URL from that output so the Preview pane can auto-load
the running app.

Dependency-free (stdlib only), mirroring the rest of ``backend/``.
"""

import os
import re
import subprocess
import sys
import threading
import time

_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?[/\w\-\.?=&%#]*", re.I)
_MAX_LINES = 400

_RUNNERS = {}
_REGISTRY_LOCK = threading.Lock()


def _python_for(project_path):
    for rel in (os.path.join(".venv", "bin", "python"),
                os.path.join("venv", "bin", "python")):
        candidate = os.path.join(project_path, rel)
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def detect_command(project_path):
    """Return ``(argv, url_hint)`` for the project, or ``(None, None)``.

    ``url_hint`` is used when no process is needed (a static site) or to seed
    the preview before the server prints its own URL.
    """
    py = _python_for(project_path)

    def exists(name):
        return os.path.isfile(os.path.join(project_path, name))

    if exists("app.py"):
        return [py, "app.py"], None
    if exists("main.py"):
        return [py, "main.py"], None
    if exists("manage.py"):
        return [py, "manage.py", "runserver"], "http://127.0.0.1:8000/"

    pkg = os.path.join(project_path, "package.json")
    if os.path.isfile(pkg):
        try:
            import json
            with open(pkg, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            scripts = data.get("scripts") or {}
            if "dev" in scripts:
                return ["npm", "run", "dev"], None
            if "start" in scripts:
                return ["npm", "start"], None
        except Exception:
            return ["npm", "start"], None

    index = os.path.join(project_path, "index.html")
    if os.path.isfile(index):
        rel = os.path.relpath(index, project_path).replace(os.sep, "/")
        return None, rel or "index.html"
    return None, None


class ProjectRun:
    def __init__(self, project_path):
        self.path = os.path.realpath(project_path)
        self.proc = None
        self.command = None
        self.url = None
        self.started_at = None
        self.error = ""
        self.output = []
        self.static = False
        self._lock = threading.Lock()

    # -- helpers ------------------------------------------------------------
    def _alive(self):
        return bool(self.proc and self.proc.poll() is None)

    def _append(self, line):
        self.output.append(line)
        if len(self.output) > _MAX_LINES:
            del self.output[0:len(self.output) - _MAX_LINES]
        if not self.url:
            match = _URL_RE.search(line)
            if match:
                self.url = match.group(0).rstrip(".,)")

    def _pump(self, stream):
        try:
            for raw in iter(stream.readline, b""):
                self._append(raw.decode("utf-8", "replace").rstrip("\n"))
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    # -- lifecycle ----------------------------------------------------------
    def start(self):
        with self._lock:
            if self._alive() or self.static:
                return
            argv, url_hint = detect_command(self.path)
            self.url = url_hint or self.url
            if argv is None:
                if url_hint:
                    self.static = True
                    self.command = "(static site)"
                    self.started_at = time.time()
                    self.error = ""
                    return
                self.error = "No runnable entry point found (looked for app.py, main.py, manage.py, package.json, index.html)."
                return
            self.output = []
            self.command = " ".join(argv)
            self.error = ""
            env = dict(os.environ)
            env.setdefault("PYTHONUNBUFFERED", "1")
            try:
                self.proc = subprocess.Popen(
                    argv, cwd=self.path, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                    env=env, start_new_session=True)
            except Exception as exc:
                self.error = f"Failed to start '{self.command}': {exc}"
                self.proc = None
                return
            self.started_at = time.time()
            threading.Thread(target=self._pump, args=(self.proc.stdout,), daemon=True).start()

    def stop(self, timeout=8.0):
        with self._lock:
            proc = self.proc
            self.proc = None
            self.static = False
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
            except Exception:
                pass

    def status(self):
        running = self._alive() or self.static
        if not running and self.command:
            tail = "\n".join(self.output[-8:])
            if not self.error and tail:
                self.error = "Process exited. " + tail[-400:]
        return {
            "running": running,
            "command": self.command or "",
            "url": self.url or "",
            "pid": self.proc.pid if self._alive() else 0,
            "uptime": round(time.time() - self.started_at, 1) if (running and self.started_at) else 0,
            "error": self.error,
            "output": self.output[-120:],
        }


def runner_for(project_path):
    key = os.path.realpath(project_path)
    with _REGISTRY_LOCK:
        runner = _RUNNERS.get(key)
        if runner is None:
            runner = ProjectRun(key)
            _RUNNERS[key] = runner
        return runner


def status_for(project_path):
    return runner_for(project_path).status()


def start_for(project_path):
    runner = runner_for(project_path)
    runner.start()
    return runner.status()


def stop_for(project_path):
    runner = runner_for(project_path)
    runner.stop()
    return runner.status()
