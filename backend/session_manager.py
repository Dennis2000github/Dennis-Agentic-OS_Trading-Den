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

from . import agent_registry, config, filewatch, sandbox

_BUFFER_LIMIT = 200_000  # characters of scrollback replayed to new clients
_BUSY_WINDOW = 1.5       # seconds after the last byte before we call it idle
_HANDOFF_GAP = 10.0      # quiet time that starts a new handoff burst


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

        prof = agent_registry.profile(self.agent_id) or {}
        inner = []
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
        self.proc = sandbox.PtyProcess(on_data=self._on_data, on_exit=self._on_exit)

        argv = self._runner.build_argv(binary, inner, self.project_path, self.id,
                                       self.rows, self.cols)
        env = self._runner.env_for(agent_registry.get_augmented_env())
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
        if self.proc:
            self.proc.resize(rows, cols)

    def stop(self):
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
