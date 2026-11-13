"""Process runners and execution contexts.

Two runners implement the same interface:

* :class:`LocalPtyRunner`  -- a PTY-backed process on the host (fallback).
* :class:`DockerRunner`    -- the same PTY interface, but the child is a
  ``docker run`` that executes the agent inside a locked-down container.

The manager never cares which one is in use. Selection is driven by
``DENNIS_SANDBOX=docker`` (and Docker being reachable); otherwise the local
runner is used so the OS stays usable in this environment.

Security notes enforced here:

* The Docker container runs non-root with CPU/memory/pids limits, dropped
  capabilities, ``no-new-privileges`` and only the validated project folder
  mounted at ``/workspace``. The Docker socket is never mounted.
* Upstream provider secrets are passed at start via an allowlist and are never
  returned in API responses or written to logs.
"""

import os
import pty
import fcntl
import termios
import struct
import signal
import select
import shutil
import re
import subprocess
import threading
import time

from . import config, docker_runtime
from . import freellmapi

# Minimal ANSI/escape stripper used only for readiness detection here. Keeping a
# local copy avoids a backend.sandbox -> backend.model_sources import cycle.
_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"          # CSI
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC
    r"|\x1b[()#][0-9A-Za-z]"              # charset selects
    r"|\x1b[=><78cDEHMZ]"                 # lone escapes
)

# Environment variables (provider credentials) that may be forwarded into a
# container. Everything else from the host env is dropped for Docker runs.
SECRET_ENV_ALLOWLIST = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENCODE_API_KEY",
    "GROQ_API_KEY",
    "NVIDIA_API_KEY",
    "HF_TOKEN",
)


def docker_available():
    return docker_runtime.docker_bin() is not None and docker_runtime.daemon_ok()


def docker_status():
    """Structured Docker readiness (see :mod:`backend.docker_runtime`)."""
    return docker_runtime.status()


def secret_env(agent_id=None, model_id=None):
    """Provider credentials from the host env, for container injection only."""
    env = (freellmapi.apply_agent_env(os.environ, agent_id, model_id)
           if agent_id else os.environ)
    allowed = SECRET_ENV_ALLOWLIST + (
        "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "OPENAI_BASE_URL",
    )
    return {k: env[k] for k in allowed if env.get(k)}


def build_docker_argv(container_name, image, project_path, inner_argv,
                      rows=None, cols=None, cpus=None, memory=None,
                      network=None, extra_env=None, home_volume=None,
                      docker=None):
    """Build the ``docker run`` argv for one agent session.

    The container is started with a TTY and stdin open (``-it``) because the
    host side is a real PTY: without ``-t`` the CLI would see pipes and refuse
    to render its interactive UI. ``home_volume`` is a named volume mounted at
    ``/home/agent`` so CLI logins/settings survive container restarts.
    """
    docker = docker or docker_runtime.docker_bin() or "docker"
    cpus = cpus or config.DEFAULT_CPU_LIMIT
    memory = memory or config.DEFAULT_MEMORY_LIMIT
    network = network or os.environ.get("DENNIS_SANDBOX_NETWORK", "bridge")
    argv = [
        docker, "run", "--rm", "-it",
        "--name", container_name,
        "--user", "1000:1000",
        "--cpus", str(cpus),
        "--memory", str(memory),
        "--pids-limit", "512",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--network", network,
        "-v", f"{project_path}:/workspace",
        "-w", "/workspace",
        "-e", "TERM=xterm-256color",
        "-e", "COLORTERM=truecolor",
        "-e", "LANG=C.UTF-8",
        "-e", "LC_ALL=C.UTF-8",
        "-e", "HOME=/home/agent",
    ]
    if home_volume:
        argv += ["-v", f"{home_volume}:/home/agent"]
    if rows:
        argv += ["-e", f"LINES={rows}"]
    if cols:
        argv += ["-e", f"COLUMNS={cols}"]
    for key, value in (extra_env or {}).items():
        argv += ["-e", f"{key}={value}"]
    argv.append(image)
    argv.extend(inner_argv)
    return argv


class PtyProcess:
    """A single PTY-backed child process with callback-based streaming."""

    def __init__(self, on_data=None, on_exit=None):
        self.master_fd = None
        self.pid = None
        self.running = False
        self.exit_code = None
        self.on_data = on_data or (lambda chunk: None)
        self.on_exit = on_exit or (lambda code: None)
        self.lock = threading.Lock()
        self.started_at = None
        self.last_output_at = None

    def start(self, argv, cwd, env, rows=30, cols=100):
        with self.lock:
            self._stop_locked()
            env = dict(env or {})
            env.setdefault("TERM", "xterm-256color")
            env.setdefault("COLORTERM", "truecolor")
            env.setdefault("LANG", "en_US.UTF-8")
            env.setdefault("LC_ALL", env["LANG"])
            # ``pty.fork()`` cannot report an error from the child: a failed
            # chdir/exec only shows up as exit code 127, which used to look
            # like the agent silently quitting. Validate first so the caller
            # gets an actionable message instead.
            cwd = cwd or config.DIRECTORY
            if not os.path.isdir(cwd):
                return False, f"Working directory does not exist: {cwd}"
            if not argv:
                return False, "No command to run."
            binary = argv[0]
            if os.path.isabs(binary):
                if not (os.path.exists(binary) and os.access(binary, os.X_OK)):
                    return False, f"Agent executable is not runnable: {binary}"
            elif not shutil.which(binary, path=env.get("PATH")):
                return False, f"Agent executable not found on PATH: {binary}"
            try:
                pid, fd = pty.fork()
            except Exception as exc:  # pragma: no cover - platform dependent
                return False, str(exc)
            if pid == 0:
                try:
                    os.chdir(cwd or config.DIRECTORY)
                    os.environ.clear()
                    os.environ.update(env)
                    os.execvpe(argv[0], argv, env)
                except Exception:
                    os._exit(127)
                os._exit(127)
            self.pid = pid
            self.master_fd = fd
            self.running = True
            self.exit_code = None
            self.started_at = time.time()
            self.last_output_at = time.time()
            self._set_winsize(fd, rows, cols)
            threading.Thread(target=self._read_loop, args=(fd,), daemon=True).start()
            threading.Thread(target=self._wait_loop, args=(pid,), daemon=True).start()
            return True, pid

    @staticmethod
    def _set_winsize(fd, rows, cols):
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass

    def _read_loop(self, fd):
        while self.running:
            try:
                ready, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if fd in ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                self.last_output_at = time.time()
                self.on_data(data.decode("utf-8", "replace"))
        self._finish()

    def _wait_loop(self, pid):
        code = 0
        try:
            _, status = os.waitpid(pid, 0)
            code = os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status
        except ChildProcessError:
            code = 0
        except Exception:
            code = -1
        self.exit_code = code

    def _finish(self):
        if not self.running and self.exit_code is not None:
            return
        self.running = False
        if self.exit_code is None:
            self.exit_code = 0
        try:
            self.on_exit(self.exit_code)
        except Exception:
            pass

    def write(self, data):
        with self.lock:
            fd = self.master_fd
        if fd is None:
            return False
        try:
            os.write(fd, data.encode("utf-8", "replace"))
            return True
        except OSError:
            return False

    def resize(self, rows, cols):
        with self.lock:
            if self.master_fd is not None:
                self._set_winsize(self.master_fd, rows, cols)

    def stop(self):
        with self.lock:
            self._stop_locked()

    def _stop_locked(self):
        pid = self.pid
        self.running = False
        self.pid = None
        if pid:
            self._terminate_process_group(pid)
        fd = self.master_fd
        self.master_fd = None
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass

    @staticmethod
    def _terminate_process_group(pid, grace=3.0):
        """SIGTERM the process group, then SIGKILL it if it outlives ``grace``.

        A CLI that ignores SIGTERM would otherwise keep running (and keep the
        project folder locked) forever.
        """
        def signal_group(sig):
            try:
                os.killpg(os.getpgid(pid), sig)
            except Exception:
                try:
                    os.kill(pid, sig)
                except Exception:
                    pass

        signal_group(signal.SIGTERM)

        def escalate():
            deadline = time.time() + grace
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                except OSError:
                    return  # already gone
                time.sleep(0.1)
            signal_group(signal.SIGKILL)

        threading.Thread(target=escalate, daemon=True).start()

    def is_alive(self):
        return self.running


class LocalPtyRunner:
    """Runs the agent CLI directly on the host inside a PTY."""

    uses_docker = False

    def build_argv(self, agent_binary, argv, project_path, session_id, rows, cols,
                   agent_id=None, model_id=None):
        return [agent_binary] + list(argv)

    def container_name(self, session_id):
        return None

    def home_volume(self, agent_id):
        return None

    def image(self, agent_id):
        return None

    def env_for(self, base_env):
        return dict(base_env)

    def cleanup(self, session_id):
        return None

    def read_text(self, path, agent_id=None, session_id=None, timeout=10):
        """Read a file from the CLI's config location (host: plain read)."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None


class DockerRunner:
    """Runs the agent CLI inside a locked-down container, streamed via PTY.

    One prebuilt image per agent CLI (the CLI is installed at build time; a
    session never installs or downloads anything). A named volume per agent
    persists the CLI's home directory so logins and settings survive restarts.
    """

    uses_docker = True

    def __init__(self, image=None, cpus=None, memory=None, network=None,
                 home_volume_prefix=None):
        self._image = image
        self.cpus = cpus
        self.memory = memory
        self.network = network
        self.home_volume_prefix = (home_volume_prefix
                                   or os.environ.get("DENNIS_HOME_VOLUME_PREFIX", "dennis-home"))

    def container_name(self, session_id):
        safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
        return f"dennis-agent-{safe}"

    def image(self, agent_id):
        if self._image:
            return self._image
        return docker_runtime.image_name(agent_id or "agent")

    def home_volume(self, agent_id):
        safe = "".join(c for c in str(agent_id or "agent") if c.isalnum() or c in "-_")
        return f"{self.home_volume_prefix}-{safe}"

    def build_argv(self, agent_binary, argv, project_path, session_id, rows, cols,
                   agent_id=None, model_id=None):
        inner = [os.path.basename(agent_binary)] + list(argv)
        if model_id is None:
            for index, value in enumerate(argv[:-1]):
                if value in ("--model", "-m"):
                    model_id = argv[index + 1]
                    break
        return build_docker_argv(
            self.container_name(session_id), self.image(agent_id), project_path, inner,
            rows=rows, cols=cols, cpus=self.cpus, memory=self.memory,
            network=self.network, extra_env=secret_env(agent_id, model_id),
            home_volume=self.home_volume(agent_id),
        )

    def env_for(self, base_env):
        # The container gets its own minimal env; only TERM/COLORTERM/LANG here.
        return {k: v for k, v in base_env.items() if k in ("TERM", "COLORTERM", "LANG", "LC_ALL")}

    def cleanup(self, session_id):
        docker = docker_runtime.docker_bin()
        if not docker:
            return
        try:
            subprocess.run([docker, "rm", "-f", self.container_name(session_id)],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    def read_text(self, path, agent_id=None, session_id=None, timeout=10):
        """Read a CLI config file from the agent's persistent volume."""
        docker = docker_runtime.docker_bin()
        if not docker or not path:
            return None
        argv = [docker, "run", "--rm", "--user", "0:0",
                "-v", f"{self.home_volume(agent_id)}:/home/agent",
                "--entrypoint", "cat", self.image(agent_id), path]
        try:
            res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            return res.stdout if res.returncode == 0 else None
        except Exception:
            return None


class ExecContext:
    """Non-interactive command execution for model listing / diagnostics.

    Wraps a runner so read-only commands run in the same sandbox as the agent.
    """

    def __init__(self, runner=None, base_env=None, project_path=None, agent_id=None):
        self.runner = runner or LocalPtyRunner()
        self.base_env = base_env or {}
        self.project_path = project_path
        self.agent_id = agent_id

    def read_text(self, path, timeout=10):
        """Read a CLI config file from wherever the sandbox keeps it.

        Local runs read the host file; Docker runs read the file from the
        agent's persistent home volume inside the same image. Returns ``None``
        when the file is absent or unreadable.
        """
        return self.runner.read_text(path, agent_id=self.agent_id, timeout=timeout)

    def _resolve(self, argv, cwd):
        # A unique probe id so two concurrent listings never collide on the
        # container name (docker refuses duplicate names).
        probe_id = f"probe-{os.getpid()}-{int(time.time() * 1000) % 10_000_000}"
        self._probe_id = probe_id
        return self.runner.build_argv(argv[0], argv[1:], cwd or self.project_path,
                                      probe_id, None, None, agent_id=self.agent_id)

    def exec(self, argv, cwd=None, timeout=30):
        full = self._resolve(argv, cwd)
        env = self.runner.env_for(self.base_env)
        if self.runner.uses_docker:
            env = dict(self.base_env)  # docker cli needs the host PATH to be found
        try:
            res = subprocess.run(full, capture_output=True, text=True,
                                 timeout=timeout, cwd=None if self.runner.uses_docker else (cwd or self.project_path),
                                 env=env)
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "command timed out"
        except Exception as exc:
            return 127, "", str(exc)

    def capture_interactive(self, argv, cwd=None, send=None, settle=2.0, timeout=20,
                            ready_for=None, ready_timeout=15.0):
        """Run a short-lived interactive PTY, send input, capture output, close.

        ``ready_for`` is an optional list of needles; we wait until the CLI has
        rendered one of them (compared with all non-alphanumerics removed, since
        TUIs often drop spaces via cursor movement) before sending ``send``.
        This matters for CLIs like Claude Code whose banner takes many seconds
        to appear -- sending too early means the keystrokes are consumed by the
        command palette instead of executing the command.
        """
        full = self._resolve(argv, cwd)
        env = self.runner.env_for(self.base_env)
        if self.runner.uses_docker:
            env = dict(self.base_env)
        proc = PtyProcess()
        chunks = []
        proc.on_data = chunks.append
        started = time.time()
        ok, info = proc.start(full, cwd if not self.runner.uses_docker else config.DIRECTORY,
                              env, rows=40, cols=120)
        if not ok:
            raise RuntimeError(info)
        deadline = started + min(timeout, 60)
        ready_needles = [re.sub(r"[^a-z0-9]", "", n.lower()) for n in (ready_for or [])]
        if ready_needles:
            wait_until = min(time.time() + ready_timeout, deadline)
            while time.time() < wait_until:
                time.sleep(0.2)
                visible = _ESCAPE_RE.sub("", "".join(chunks))
                blob = re.sub(r"[^a-z0-9]", "", visible.lower())
                if any(n and n in blob for n in ready_needles):
                    break
        else:
            # Give the CLI a moment to render its prompt before sending.
            time.sleep(min(settle, 1.5))
        if send:
            proc.write(send)
        end = time.time() + settle
        while time.time() < end and time.time() < deadline:
            time.sleep(0.1)
        proc.stop()
        if self.runner.uses_docker:
            self.runner.cleanup(getattr(self, "_probe_id", "probe"))
        return "".join(chunks)
