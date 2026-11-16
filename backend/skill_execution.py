"""Controlled skill execution environment (Phase 6).

Skills execute through the existing OS execution spine — never through a new
ad-hoc ``os.system``:

* path safety       — ``safe_skill_path`` confines reads to the canonical
                      Vault skill root and rejects traversal; workspace paths
                      are validated like project paths.
* secret isolation  — ``scan_for_secrets`` rejects any skill text or generated
                      artifact that looks like a credential.
* approval          — risky executions (script execution, file writes outside
                      the workspace, network) go through the Studio
                      ``ApprovalStore`` pattern: pending until the owner
                      decides; pending approvals expire on restart.
* execution         — a PTY process (``backend/sandbox.py``) with a bounded
                      env, workspace cwd, timeout and cooperative cancel.
* logging           — stdout/stderr lines are captured with a hard cap; logs
                      are never written to the Vault.

Dependency-free (stdlib only), matching the rest of ``backend/``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid

from . import config

# --- errors ---------------------------------------------------------------

class SkillExecutionError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = int(status)
        super().__init__(message)


# --- path safety ----------------------------------------------------------

# Skill-relative paths may only contain these characters, and may not escape
# the skill root. This runs before anything touches the filesystem.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Absolute filesystem roots a skill may write to during execution.
WRITABLE_ROOTS = ()


def safe_skill_path(skill_root, rel):
    """Resolve ``rel`` inside ``skill_root`` or raise. No traversal, no
    absolute paths, no symlinks that leave the root."""
    if not rel:
        raise SkillExecutionError("skill_path_required", "A skill-relative path is required.")
    rel = str(rel).replace("\\", "/").strip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if not parts:
        raise SkillExecutionError("skill_path_required", "A skill-relative path is required.")
    if any(p == ".." for p in parts):
        raise SkillExecutionError("path_traversal", "Skill path escapes the skill root.")
    if any(_SAFE_SEGMENT.match(p) is None for p in parts):
        raise SkillExecutionError("path_invalid", "Skill path has unsafe characters.")
    root = os.path.realpath(skill_root)
    target = os.path.realpath(os.path.join(root, *parts))
    if target != root and not target.startswith(root + os.sep):
        raise SkillExecutionError("path_traversal", "Skill path escapes the skill root.")
    if not os.path.exists(target):
        raise SkillExecutionError("path_missing", "Skill path not found: %s" % rel)
    return target


def safe_workspace_path(workspace_root, path):
    """Validate a caller-supplied absolute/relative path is inside the
  workspace root (mirrors projects.validate_path semantics)."""
    if not path:
        raise SkillExecutionError("workspace_path_required", "A workspace path is required.")
    path = str(path)
    if not os.path.isabs(path):
        path = os.path.join(workspace_root or "", path)
    real = os.path.realpath(path)
    root = os.path.realpath(workspace_root or "")
    if not root or (real != root and not real.startswith(root + os.sep)):
        raise SkillExecutionError("path_escape", "Path is outside the workspace: %s" % path)
    if not os.path.exists(real):
        raise SkillExecutionError("path_missing", "Workspace path not found: %s" % path)
    return real


# --- secret isolation -----------------------------------------------------

# Same spirit as skill_compiler's credential pattern check, kept local so the
# execution core has no dependency on the compiler.
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|access[_-]?token|client[_-]?secret|"
    r"secret[_-]?key|auth[_-]?token|bearer\s+[a-z0-9._-]{12,}|"
    r"sk-[a-z0-9]{16,}|AKIA[0-9A-Z]{16})")

# Variable names that may appear in *documentation* but must never carry a
# live value into an artifact.
_BANNED_ENV = ("API_KEY", "AUTH_TOKEN", "SECRET", "PASSWORD", "PRIVATE_KEY")


def scan_for_secrets(text):
    """Return a list of redacted findings (empty when clean)."""
    findings = []
    for match in _SECRET_RE.finditer(str(text or "")):
        findings.append({
            "kind": "credential-pattern",
            "excerpt": match.group(0)[:6] + "…(redacted)",
            "column": match.start(),
        })
    return findings


def redact_env(env):
    """Drop provider credential variables from an env copy (sandbox runs
  already do this for containers; local PTY runs use the same allowlist)."""
    out = dict(env or os.environ)
    for key in list(out):
        if any(banned in key.upper() for banned in _BANNED_ENV):
            out.pop(key, None)
    return out


# --- bounded environment --------------------------------------------------

# A skill process gets PATH + a minimal safe set. Provider keys are never in
# the child env unless explicitly allowed through SECRET_ENV_ALLOWLIST by the
# runner (Docker path) — local runs redact them entirely.
_BASE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "COLORTERM",
                  "TMPDIR", "SHELL", "USER")


def skill_env(extra=None):
    env = {k: v for k, v in os.environ.items() if k in _BASE_ENV_KEYS}
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", env["LANG"])
    for key, value in (extra or {}).items():
        if key.upper() in _BANNED_ENV:
            continue  # never forward credentials into a skill process
        env[key] = str(value)
    return env


# --- process control ------------------------------------------------------

class SkillProcess:
    """One bounded skill subprocess with streaming log capture and cancel."""

    MAX_LOG_LINES = 2000
    MAX_LOG_CHARS = 200_000

    def __init__(self, argv, cwd, env, timeout=300):
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.timeout = float(timeout or 300)
        self.id = "sx_" + uuid.uuid4().hex[:12]
        self.status = "queued"   # queued|running|done|cancelled|failed|timeout
        self.exit_code = None
        self.log = []            # list of dicts: {t, stream, line}
        self.started_at = None
        self.finished_at = None
        self._proc = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._threads = []

    # -- capture -----------------------------------------------------------
    def _reader(self, stream, name):
        try:
            for raw in iter(stream.readline, ""):
                line = raw.rstrip("\r\n")
                if line:
                    self._append(name, line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    def _append(self, stream, line):
        with self._lock:
            if len(self.log) >= self.MAX_LOG_LINES:
                self.log[-1] = {"t": time.time(), "stream": "system",
                                "line": "[log truncated at %d lines]" % self.MAX_LOG_LINES}
                return
            self.log.append({"t": time.time(), "stream": stream, "line": line})

    def _trim_chars(self):
        total = sum(len(entry["line"]) for entry in self.log)
        while total > self.MAX_LOG_CHARS and len(self.log) > 1:
            total -= len(self.log.pop(0)["line"])

    # -- lifecycle ---------------------------------------------------------
    def run(self):
        if self._cancel.is_set():
            self.status = "cancelled"
            return self.snapshot()
        self.status = "running"
        self.started_at = time.time()
        try:
            self._proc = subprocess.Popen(
                self.argv, cwd=self.cwd, env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, start_new_session=True,
                text=True, errors="replace")
        except FileNotFoundError:
            self.status = "failed"
            self._append("system", "executable not found: %s" % self.argv[0])
            return self.snapshot()
        except OSError as exc:
            self.status = "failed"
            self._append("system", "spawn failed: %s" % exc)
            return self.snapshot()

        threads = [
            threading.Thread(target=self._reader, args=(self._proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=self._reader, args=(self._proc.stderr, "stderr"), daemon=True),
        ]
        for t in threads:
            t.start()
        deadline = self.started_at + self.timeout
        while self._proc.poll() is None:
            if self._cancel.is_set():
                self._terminate_group()
                self.status = "cancelled"
                break
            if time.time() > deadline:
                self._terminate_group()
                self.status = "timeout"
                self._append("system", "execution timed out after %.0fs" % self.timeout)
                break
            time.sleep(0.1)
        for t in threads:
            t.join(timeout=2)
        if self.status == "running":
            self.exit_code = self._proc.returncode
            self.status = "done" if self.exit_code == 0 else "failed"
        self.finished_at = time.time()
        self._trim_chars()
        return self.snapshot()

    def _terminate_group(self):
        if self._proc is None:
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), 15)  # SIGTERM to the group
        except Exception:
            try:
                self._proc.terminate()
            except Exception:
                pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(self._proc.pid), 9)
            except Exception:
                pass

    def cancel(self):
        self._cancel.set()
        return {"id": self.id, "cancel_requested": True}

    def snapshot(self):
        with self._lock:
            log = list(self.log)
        return {
            "id": self.id,
            "status": self.status,
            "exit_code": self.exit_code,
            "argv": self.argv,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round((self.finished_at or time.time()) - (self.started_at or time.time()), 3),
            "log_lines": len(log),
            "log": log[-500:],  # last 500 lines for the API
        }


def run_skill_process(argv, cwd, timeout=300, extra_env=None):
    """Start and wait for one skill process. Returns the snapshot dict."""
    proc = SkillProcess(argv, cwd, skill_env(extra_env), timeout=timeout)
    return proc.run()


def run_python_script(script_path, args=None, cwd=None, timeout=300, extra_env=None):
    """Run a skill's Python script with the current interpreter."""
    argv = [shutil.which("python3") or "python3", script_path]
    argv.extend(args or [])
    return run_skill_process(argv, cwd or os.path.dirname(script_path),
                             timeout=timeout, extra_env=extra_env)


# --- approval -------------------------------------------------------------

class ApprovalRequired(SkillExecutionError):
    """Raised when a skill execution needs explicit owner approval."""

    def __init__(self, approval_id, preview):
        super().__init__("approval_required",
                         "This skill execution requires owner approval.", status=202)
        self.approval_id = approval_id
        self.preview = preview


class _NullApprovals:
    """Fallback when the Studio approval store is unavailable."""

    def create(self, tool, arguments, preview):
        return {
            "id": "apv_local_" + uuid.uuid4().hex[:12],
            "tool": tool, "arguments": arguments, "preview": preview,
            "status": "pending", "created_at": time.time(),
        }

    def get(self, approval_id):
        return None

    def update(self, approval_id, **fields):
        return None


_APPROVALS = None


def set_approval_store(store):
    """Wire the Studio ApprovalStore (called once from app.py)."""
    global _APPROVALS
    _APPROVALS = store


def _approval_store():
    return _APPROVALS or _NullApprovals()


def request_approval(kind, skill_id, argv, workspace, reason):
    """Create a pending approval for a risky skill execution."""
    preview = {
        "kind": kind,
        "skill_id": skill_id,
        "argv": argv,
        "workspace": workspace,
        "reason": reason,
        "risk": "skill-execution",
    }
    record = _approval_store().create("skill_execute", preview, preview)
    return record


def approval_status(approval_id):
    if not approval_id:
        return None
    record = _approval_store().get(approval_id)
    return record.get("status") if isinstance(record, dict) else None


def approval_decided(approval_id):
    return approval_status(approval_id) == "approved"


# --- dependency gate ------------------------------------------------------

def check_binary(name):
    """Resolve a required runtime binary; returns path or None."""
    return shutil.which(name, path=skill_env().get("PATH", os.environ.get("PATH", "")))


def missing_binaries(names):
    return [n for n in names or [] if not check_binary(n)]


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


__all__ = [
    "SkillExecutionError", "ApprovalRequired", "SkillProcess",
    "safe_skill_path", "safe_workspace_path", "scan_for_secrets", "redact_env",
    "skill_env", "run_skill_process", "run_python_script",
    "request_approval", "approval_status", "approval_decided",
    "check_binary", "missing_binaries", "now_iso", "WRITABLE_ROOTS",
    "set_approval_store",
]
