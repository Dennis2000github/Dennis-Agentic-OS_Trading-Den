"""Real backend signals for the Code & Preview panel handoff.

Two signals are produced here and nowhere else:

* **File changes** -- the project folder is polled (cheap mtime walk, bounded
  depth) and a ``handoff`` event is emitted when the agent creates or edits
  files. Events carry a *burst id*: the id increments only after a quiet gap,
  so the first file change of a task is one handoff and a long run of edits
  stays in the same burst. The UI opens the panel once per burst.
* **Preview URLs** -- PTY output is scanned for ``http://localhost:PORT`` /
  ``127.0.0.1`` / ``0.0.0.0`` URLs; the first sighting emits a ``preview``
  event in the same burst.

No timers fake anything: both events come from real filesystem/process state.
"""

import os
import re
import threading
import time

_IGNORED_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".idea", ".mypy_cache", ".pytest_cache", ".ruff_cache", "tool-output",
    "target", "vendor", ".next", ".cache",
}
_WATCH_DEPTH = 4
_POLL_SECONDS = 1.5
_BURST_GAP_SECONDS = 10.0        # quiet time that starts a new handoff burst
_MAX_FILES = 4000

_URL_RE = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?(?:/[^\s'\"\x1b<>)]*)?"
)


class ProjectWatcher:
    """Polls one project folder for changes and reports handoff bursts."""

    def __init__(self, path, on_handoff=None, on_change=None, ignore_paths=None):
        self.path = path
        self.on_handoff = on_handoff or (lambda event: None)
        self.on_change = on_change or (lambda event: None)
        self._ignore = {os.path.realpath(p) for p in (ignore_paths or []) if p}
        self._stop = threading.Event()
        self._thread = None
        self._snapshot = {}
        self._burst_id = 0
        self._last_change_at = 0.0

    # -- snapshot --------------------------------------------------------
    def _scan(self):
        found = {}
        count = 0
        for root, dirs, files in os.walk(self.path):
            dirs[:] = [
                d for d in dirs
                if d not in _IGNORED_DIRS and not d.startswith(".tmp")
                and os.path.realpath(os.path.join(root, d)) not in self._ignore
            ]
            depth = root[len(self.path):].count(os.sep)
            if depth >= _WATCH_DEPTH:
                dirs[:] = []
            for name in files:
                count += 1
                if count > _MAX_FILES:
                    return found
                full = os.path.join(root, name)
                try:
                    found[full] = os.stat(full).st_mtime_ns
                except OSError:
                    continue
        return found

    # -- lifecycle -------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._snapshot = self._scan()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(_POLL_SECONDS):
            try:
                current = self._scan()
            except Exception:
                continue
            changes = []
            for path, mtime in current.items():
                if self._snapshot.get(path) != mtime:
                    changes.append(path)
            for path in self._snapshot:
                if path not in current:
                    changes.append(path)
            self._snapshot = current
            if not changes:
                continue
            now = time.monotonic()
            if now - self._last_change_at > _BURST_GAP_SECONDS:
                self._burst_id += 1
            self._last_change_at = now
            event = {
                "reason": "file_change",
                "path": self._rel(changes[0]),
                "count": len(changes),
                "burst": self._burst_id,
            }
            try:
                self.on_change(event)
                self.on_handoff(event)
            except Exception:
                pass

    def _rel(self, path):
        try:
            return os.path.relpath(path, self.path)
        except ValueError:
            return path


def find_preview_url(text):
    """Return the first local preview URL in a chunk of terminal output."""
    if not text:
        return None
    match = _URL_RE.search(text)
    return match.group(0) if match else None


class PreviewScanner:
    """Deduplicates preview URLs seen in one session's PTY output."""

    def __init__(self):
        self._seen = set()

    def scan(self, text):
        url = find_preview_url(text)
        if not url:
            return None
        url = url.rstrip(".,;:")
        if url in self._seen:
            return None
        self._seen.add(url)
        return url
