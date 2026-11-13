"""Live agent sessions: PTY/Docker lifecycle, streaming fan-out, status.

One :class:`AgentSession` exists per project-session id. Sessions are kept alive
independently of which panel/dropdown is visible, so switching agents or
sidebar sessions never interrupts a running task.

This module owns *processes only*. Session metadata lives in ``session_store``,
model listing lives in ``model_sources`` and transport lives in ``ws``.
"""

import os
import threading
import time
import unicodedata

from . import agent_registry, config, filewatch, sandbox

_BUFFER_LIMIT = 200_000  # characters of scrollback replayed to new clients
_BUSY_WINDOW = 1.5       # seconds after the last byte before we call it idle
_HANDOFF_GAP = 10.0      # quiet time that starts a new handoff burst
_HISTORY_LIMIT = 200_000  # characters exposed to the History drawer
_HISTORY_IDLE = 0.6       # quiet time before a screen snapshot is archived


class _Screen:
    """Just enough VT100 to keep a readable copy of a full-screen TUI.

    Agent TUIs run in the terminal's alternate screen buffer, where xterm.js
    keeps no scrollback. This grid replays the PTY stream so the History drawer
    can show what the CLI actually displayed, without escape codes or the
    cursor-addressing whitespace that makes a raw dump unreadable.

    Deliberately small: cursor addressing, erase, newline/CR/backspace and the
    alternate-screen switch. Styling is ignored — history is plain text.
    """

    def __init__(self, rows=30, cols=100):
        self.rows = 0
        self.cols = 0
        self.grid = []
        self.row = 0
        self.col = 0
        self._pending = ""
        self.resize(rows, cols)

    def resize(self, rows, cols):
        rows = max(1, int(rows or 30))
        cols = max(1, int(cols or 100))
        if rows == self.rows and cols == self.cols:
            return
        self.rows, self.cols = rows, cols
        self.grid = [[" "] * cols for _ in range(rows)]
        self.row = self.col = 0
        self._pending = ""

    def clear(self):
        self.grid = [[" "] * self.cols for _ in range(self.rows)]
        self.row = self.col = 0

    def _scroll(self):
        self.grid.pop(0)
        self.grid.append([" "] * self.cols)

    def _print(self, ch):
        width = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if self.col + width > self.cols:
            self.col = 0
            self.row += 1
            if self.row >= self.rows:
                self._scroll()
                self.row = self.rows - 1
        self.grid[self.row][self.col] = ch
        if width == 2:
            self.grid[self.row][self.col + 1] = ""
        self.col += width

    def _newline(self):
        self.row += 1
        if self.row >= self.rows:
            self._scroll()
            self.row = self.rows - 1

    @staticmethod
    def _arg(params, index=0, default=1):
        try:
            return params[index] or default
        except IndexError:
            return default

    @staticmethod
    def _params(raw):
        raw = raw.lstrip("?><!")
        if not raw:
            return []
        out = []
        for part in raw.split(";"):
            try:
                out.append(int(part) if part else 0)
            except ValueError:
                out.append(0)
        return out

    def _csi(self, raw, final):
        params = self._params(raw)
        if final in ("H", "f"):
            self.row = min(self.rows - 1, max(0, self._arg(params, 0) - 1))
            self.col = min(self.cols - 1, max(0, self._arg(params, 1) - 1))
        elif final == "A":
            self.row = max(0, self.row - self._arg(params))
        elif final == "B":
            self.row = min(self.rows - 1, self.row + self._arg(params))
        elif final == "C":
            self.col = min(self.cols - 1, self.col + self._arg(params))
        elif final == "D":
            self.col = max(0, self.col - self._arg(params))
        elif final == "G":
            self.col = min(self.cols - 1, max(0, self._arg(params) - 1))
        elif final == "d":
            self.row = min(self.rows - 1, max(0, self._arg(params) - 1))
        elif final == "K":
            mode = params[0] if params else 0
            if mode == 2:
                self.grid[self.row] = [" "] * self.cols
            elif mode == 1:
                for c in range(0, min(self.col + 1, self.cols)):
                    self.grid[self.row][c] = " "
            else:
                for c in range(self.col, self.cols):
                    self.grid[self.row][c] = " "
        elif final == "J":
            mode = params[0] if params else 0
            if mode in (2, 3):
                self.clear()
            elif mode == 1:
                for r in range(0, self.row):
                    self.grid[r] = [" "] * self.cols
                for c in range(0, min(self.col + 1, self.cols)):
                    self.grid[self.row][c] = " "
            else:
                for c in range(self.col, self.cols):
                    self.grid[self.row][c] = " "
                for r in range(self.row + 1, self.rows):
                    self.grid[r] = [" "] * self.cols
        elif final in ("h", "l"):
            # Alternate-screen switches (1049/1047/47) reset the visible grid.
            if any(p in (1049, 1047, 47) for p in params):
                self.clear()

    def feed(self, data):
        buf = self._pending + (data or "")
        self._pending = ""
        i = 0
        n = len(buf)
        while i < n:
            ch = buf[i]
            if ch == "\x1b":
                j = i + 1
                if j >= n:
                    self._pending = buf[i:]
                    return
                nxt = buf[j]
                if nxt == "[":
                    k = j + 1
                    while k < n and not ("@" <= buf[k] <= "~"):
                        k += 1
                    if k >= n:
                        self._pending = buf[i:]
                        return
                    self._csi(buf[j + 1:k], buf[k])
                    i = k + 1
                    continue
                if nxt in "]PX^_":
                    k = j + 1
                    while k < n:
                        if buf[k] == "\x07":
                            k += 1
                            break
                        if buf[k] == "\x1b" and k + 1 < n and buf[k + 1] == "\\":
                            k += 2
                            break
                        k += 1
                    else:
                        self._pending = buf[i:]
                        return
                    i = k
                    continue
                # Two-byte escape (charset selection, keypad mode, ...).
                i += 2
                continue
            if ch == "\n":
                self._newline()
            elif ch == "\r":
                self.col = 0
            elif ch == "\b":
                self.col = max(0, self.col - 1)
            elif ch == "\t":
                self.col = min(self.cols - 1, (self.col // 8 + 1) * 8)
            elif ch >= " " and ch != "\x7f":
                self._print(ch)
            i += 1

    def snapshot(self):
        """Visible screen as text: right-trimmed rows, no blank margins."""
        lines = ["".join(r).rstrip() for r in self.grid]
        while lines and not lines[-1]:
            lines.pop()
        while lines and not lines[0]:
            lines.pop(0)
        # Trim the common left padding TUIs use to center their layout; relative
        # indentation (ASCII art, lists, code) is preserved.
        indents = [len(line) - len(line.lstrip(" ")) for line in lines if line]
        if indents:
            cut = min(indents)
            if cut:
                lines = [line[cut:] if line else "" for line in lines]
        return "\n".join(lines)



def _make_runner():
    """Pick the runner; never hide why Docker was not used."""
    mode = os.environ.get("DENNIS_SANDBOX", "local").lower()
    if mode == "docker":
        status = sandbox.docker_status()
        if status.get("code") == "ok":
            return sandbox.DockerRunner(), None
        note = status.get("message") or "Docker is not available."
        note += " Agents are running directly on this machine."
        print("[session_manager] DENNIS_SANDBOX=docker requested but Docker is "
              "unavailable; falling back to the local runner: " + note)
        return sandbox.LocalPtyRunner(), note
    return sandbox.LocalPtyRunner(), None


class AgentSession:
    def __init__(self, session_id, agent_id, project_path, model_id=None, cli_session_id=None):
        self.id = session_id
        self.agent_id = agent_id
        self.project_path = project_path
        self.model_id = model_id
        self.cli_session_id = cli_session_id
        self.proc = None
        self.status = "stopped"
        self.error = None
        self._clients = set()
        self._lock = threading.Lock()
        self._buffer = []
        self._buffer_len = 0
        self._has_output = False
        self.started_at = None
        self.last_active = time.time()
        self.rows = 30
        self.cols = 100
        # Measured from the moment start() was called, in milliseconds.
        self.timings = {"start_ms": None, "first_output_ms": None}
        # What the backend actually launched the CLI with (never the raw UI ask).
        self.current_model = model_id
        self._watcher = None
        self._preview = filewatch.PreviewScanner()
        self._handoff_burst = 0
        self._last_handoff_at = 0.0
        # History drawer: settled screen snapshots of this session's TUI.
        self._screen = _Screen(self.rows, self.cols)
        self._history = []
        self._history_chars = 0
        self._history_timer = None
        self._history_due = 0.0
        self._last_capture_at = 0.0
        self._last_snapshot = ""

    # -- handoff signals (real backend events only) ----------------------
    def _signal_handoff(self, reason, **extra):
        now = time.monotonic()
        if now - self._last_handoff_at > _HANDOFF_GAP:
            self._handoff_burst += 1
        self._last_handoff_at = now
        event = {"type": "handoff", "reason": reason, "burst": self._handoff_burst}
        event.update(extra)
        self._emit(event)

    def _on_file_change(self, event):
        self._signal_handoff("file_change", path=event.get("path"), count=event.get("count", 1))

    def _start_watcher(self):
        self._stop_watcher()
        if not self.project_path or not os.path.isdir(self.project_path):
            return
        try:
            # Never treat the OS's own data directory as agent output when the
            # watched project happens to be the OS folder itself.
            self._watcher = filewatch.ProjectWatcher(
                self.project_path,
                on_change=self._on_file_change,
                ignore_paths=[config.DATA_DIR],
            )
            self._watcher.start()
        except Exception as exc:  # a broken watcher must not break the session
            print(f"[session_manager] watcher failed for {self.id}: {exc}")
            self._watcher = None

    def _stop_watcher(self):
        if self._watcher:
            try:
                self._watcher.stop()
            except Exception:
                pass
            self._watcher = None

    # -- streaming -------------------------------------------------------
    def add_client(self, sink):
        with self._lock:
            self._clients.add(sink)
            replay = "".join(self._buffer)
        if replay:
            try:
                sink.send_text({"type": "replay", "data": replay})
            except Exception:
                pass
        self._emit({"type": "status", "status": self.compute_status()})

    def remove_client(self, sink):
        with self._lock:
            self._clients.discard(sink)

    def history(self, limit=None):
        """Plain-text history of what this session's CLI displayed.

        Full-screen TUIs (OpenCode, Codex, Claude Code, ...) run in the
        terminal's alternate screen buffer, where xterm.js keeps no scrollback.
        The backend archives settled screens instead, so the History drawer can
        always scroll back even while such a TUI owns the terminal.
        """
        limit = _HISTORY_LIMIT if limit is None else int(limit)
        with self._lock:
            entries = list(self._history)
            last = entries[-1]["text"] if entries else ""
            current = self._screen.snapshot()
        if current and current != last:
            entries.append({"at": time.strftime("%H:%M:%S"), "text": current})
        blocks = ["\u2500\u2500 %s \u2500\u2500\n%s" % (e["at"], e["text"]) for e in entries]
        text = "\n\n".join(blocks)
        if limit > 0 and len(text) > limit:
            text = text[-limit:]
            cut = text.find("\n")
            if cut != -1:
                text = text[cut + 1:]
        return text

    def _schedule_history(self):
        with self._lock:
            self._history_due = time.monotonic() + _HISTORY_IDLE
            if self._history_timer:
                return
            self._history_timer = threading.Timer(_HISTORY_IDLE, self._history_tick)
            self._history_timer.daemon = True
            self._history_timer.start()

    def _history_tick(self):
        with self._lock:
            remaining = self._history_due - time.monotonic()
            if remaining > 0:
                self._history_timer = threading.Timer(remaining, self._history_tick)
                self._history_timer.daemon = True
                self._history_timer.start()
                return
            self._history_timer = None
        self._capture_screen()

    def _capture_screen(self):
        with self._lock:
            self._last_capture_at = time.monotonic()
            snap = self._screen.snapshot()
            if not snap or snap == self._last_snapshot:
                return
            self._last_snapshot = snap
            self._history.append({"at": time.strftime("%H:%M:%S"), "text": snap})
            self._history_chars += len(snap) + 24
            while self._history_chars > _HISTORY_LIMIT and len(self._history) > 1:
                old = self._history.pop(0)
                self._history_chars -= len(old["text"]) + 24

    def _emit(self, message):
        with self._lock:
            clients = list(self._clients)
        for sink in clients:
            try:
                sink.send_text(message)
            except Exception:
                self.remove_client(sink)

    def _on_data(self, chunk):
        with self._lock:
            self._buffer.append(chunk)
            self._buffer_len += len(chunk)
            while self._buffer_len > _BUFFER_LIMIT and len(self._buffer) > 1:
                self._buffer_len -= len(self._buffer.pop(0))
            self._has_output = True
            self._screen.resize(self.rows, self.cols)
            self._screen.feed(chunk)
        self._schedule_history()
        # A CLI that streams for minutes must still leave intermediate history.
        if time.monotonic() - self._last_capture_at >= 4.0:
            self._capture_screen()
        if self.timings.get("first_output_ms") is None and self.started_at:
            self.timings["first_output_ms"] = int((time.time() - self.started_at) * 1000)
            print(f"[session_manager] {self.id} first output after "
                  f"{self.timings['first_output_ms']} ms")
        self.last_active = time.time()
        self._emit({"type": "data", "data": chunk})
        url = self._preview.scan(chunk)
        if url:
            self._signal_handoff("preview", preview_url=url)

    def _on_exit(self, code):
        self.status = "stopped"
        self._emit({"type": "exit", "code": code})

    # -- lifecycle -------------------------------------------------------
    def start(self, agent, model_id, rows=None, cols=None):
        self.stop()
        started = time.time()
        if rows:
            self.rows = int(rows)
        if cols:
            self.cols = int(cols)
        binary = agent_registry.resolve_binary(agent)
        if not binary:
            self.error = "Agent CLI not found. Check the Agent Registry."
            self.status = "stopped"
            return False, self.error
        if not os.path.isdir(self.project_path or ""):
            self.error = f"Project folder not found: {self.project_path}"
            self.status = "stopped"
            return False, self.error

        prof = agent_registry.profile(self.agent_id) or {}
        # Some CLIs expose their interactive workspace behind a subcommand
        # (for example OpenClaw's local TUI) instead of a bare root command.
        inner = list(prof.get("base_args") or [])
        if model_id and prof.get("model_flag"):
            inner += list(prof["model_flag"]) + [model_id]
        # Resume the conversation only when the model is unchanged. Several CLIs
        # (OpenCode, verified live against its session DB) restore the session's
        # recorded model on resume and ignore the model flag, so resuming after a
        # model change silently keeps the old model. A model change therefore
        # starts a fresh CLI conversation so the new model actually takes effect;
        # the previous conversation stays in the CLI's own session store. Same-
        # model restarts still preserve history (known session id, else -c).
        model_changed = (model_id or None) != (self.current_model or None)
        resuming = self.started_at is not None and not model_changed
        if resuming and self.cli_session_id and prof.get("resume_flag"):
            inner += list(prof["resume_flag"]) + [self.cli_session_id]
        elif resuming and prof.get("continue_flag"):
            inner += list(prof["continue_flag"])

        self.model_id = model_id or self.model_id
        with self._lock:
            self._screen.resize(self.rows, self.cols)
            self._history = []
            self._history_chars = 0
            self._history_due = 0.0
            self._last_capture_at = time.monotonic()
            self._last_snapshot = ""
        self.proc = sandbox.PtyProcess(on_data=self._on_data, on_exit=self._on_exit)

        argv = self._runner.build_argv(binary, inner, self.project_path, self.id,
                                       self.rows, self.cols, agent_id=self.agent_id)
        env = self._runner.env_for(
            agent_registry.get_augmented_env(self.agent_id, model_id))
        ok, info = self.proc.start(argv, self.project_path, env,
                                   rows=self.rows, cols=self.cols)
        if not ok:
            self.error = "Could not start agent. Check the Agent Registry."
            self.status = "stopped"
            return False, self.error

        self.error = None
        self.started_at = time.time()
        self.last_active = time.time()
        self.status = "running"
        self.timings = {
            "start_ms": int((self.started_at - started) * 1000),
            "first_output_ms": None,
        }
        self.current_model = model_id or None
        startup_input = prof.get("model_input")
        if startup_input and model_id:
            # OpenClaw selects models inside its TUI rather than accepting a
            # startup flag. Give the TUI time to render before issuing /model.
            started_proc = self.proc
            timer = threading.Timer(
                1.5,
                lambda: self.write(startup_input.format(model=model_id))
                if self.proc is started_proc else False,
            )
            timer.daemon = True
            timer.start()
        self._start_watcher()
        print(f"[session_manager] {self.id} started in {self.timings['start_ms']} ms "
              f"({self._runner.__class__.__name__})")
        self._emit({"type": "status", "status": "running"})
        return True, info

    def write(self, data):
        if self.proc and self.proc.is_alive():
            self.last_active = time.time()
            return self.proc.write(data)
        return False

    def resize(self, rows, cols):
        self.rows = int(rows)
        self.cols = int(cols)
        with self._lock:
            self._screen.resize(self.rows, self.cols)
        if self.proc:
            self.proc.resize(rows, cols)

    def stop(self):
        with self._lock:
            if self._history_timer:
                self._history_timer.cancel()
                self._history_timer = None
        if self.proc:
            self.proc.stop()
        self._stop_watcher()
        self._runner.cleanup(self.id)
        self.proc = None
        self.status = "stopped"

    def is_alive(self):
        return bool(self.proc and self.proc.is_alive())

    def compute_status(self):
        if not self.is_alive():
            return "stopped"
        if self.proc.last_output_at and (time.time() - self.proc.last_output_at) < _BUSY_WINDOW:
            return "busy"
        if self._has_output:
            return "idle"
        return "running"

    def snapshot(self):
        """Public, secret-free view of the session for the API layer."""
        return {
            "status": self.compute_status(),
            "current_model": self.current_model,
            "timings": dict(self.timings),
            "rows": self.rows,
            "cols": self.cols,
        }

    # set by manager
    _runner = sandbox.LocalPtyRunner()


class SessionManager:
    def __init__(self, runner=None, idle_timeout=None, backend_note=None):
        if runner is not None:
            self.runner = runner
            self.backend_note = backend_note
        else:
            self.runner, self.backend_note = _make_runner()
        self.idle_timeout = idle_timeout if idle_timeout is not None else config.IDLE_TIMEOUT_SECONDS
        self._sessions = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        threading.Thread(target=self._reaper, daemon=True).start()

    @property
    def backend_name(self):
        return "docker" if getattr(self.runner, "uses_docker", False) else "local"

    # -- lookup ----------------------------------------------------------
    def get(self, session_id):
        with self._lock:
            return self._sessions.get(session_id)

    def get_or_create(self, session_record):
        sid = session_record["id"]
        with self._lock:
            sess = self._sessions.get(sid)
            if sess is None:
                sess = AgentSession(
                    sid,
                    session_record.get("agent_id"),
                    session_record.get("project_path"),
                    session_record.get("model_id"),
                    session_record.get("cli_session_id"),
                )
                sess._runner = self.runner
                self._sessions[sid] = sess
            else:
                # metadata may have changed on disk
                sess.project_path = session_record.get("project_path") or sess.project_path
                sess.agent_id = session_record.get("agent_id") or sess.agent_id
            return sess

    def ensure_started(self, session_record, model_id=None, rows=None, cols=None):
        """Start a session if it is not already alive. Returns (ok, message)."""
        sess = self.get_or_create(session_record)
        if sess.is_alive():
            if model_id and model_id != sess.model_id:
                return self.restart(sess, session_record, model_id, rows=rows, cols=cols)
            if rows and cols:
                sess.resize(rows, cols)
            return True, "already running"
        agent = agent_registry.get_agent(sess.agent_id)
        if not agent:
            return False, "Agent not found in registry"
        return sess.start(agent, model_id or sess.model_id, rows=rows, cols=cols)

    def restart(self, sess, session_record, model_id, rows=None, cols=None):
        """Controlled restart preserving project folder and CLI session id."""
        agent = agent_registry.get_agent(sess.agent_id)
        if not agent:
            return False, "Agent not found in registry"
        sess.cli_session_id = session_record.get("cli_session_id") or sess.cli_session_id
        ok, info = sess.start(agent, model_id, rows=rows, cols=cols)
        if ok:
            return True, "restarted"
        return False, info

    def summary(self, session_id):
        """Live, secret-free status for one session (None when never created)."""
        sess = self.get(session_id)
        if not sess:
            return None
        return sess.snapshot()

    def stop(self, session_id):
        with self._lock:
            sess = self._sessions.get(session_id)
        if sess:
            sess.stop()
        return True

    def stop_all(self):
        with self._lock:
            sessions = list(self._sessions.values())
        for sess in sessions:
            sess.stop()

    def status(self, session_id):
        sess = self.get(session_id)
        return sess.compute_status() if sess else "stopped"

    # -- idle reaper -----------------------------------------------------
    def _reaper(self):
        while not self._stop.wait(30):
            now = time.time()
            for sid, sess in list(self._sessions.items()):
                if not sess.is_alive():
                    continue
                attached = bool(sess._clients)
                if attached:
                    continue
                if now - sess.last_active > self.idle_timeout:
                    print(f"[session_manager] stopping idle session {sid}")
                    sess.stop()


MANAGER = SessionManager()
