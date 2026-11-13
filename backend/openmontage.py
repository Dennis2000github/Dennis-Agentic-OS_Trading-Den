"""Supervisor for the isolated OpenMontage Backlot service.

OpenMontage is intentionally kept outside the DENNIS source tree.  This
module only owns its local Backlot process and exposes a small, redacted
status contract for the Studio shell.  Provider credentials stay in
OpenMontage's server-side environment/configuration; they are never passed
through the browser or included in status responses.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen


class OpenMontageError(RuntimeError):
    """A safe, user-facing lifecycle error."""


_lock = threading.RLock()
_process: subprocess.Popen[str] | None = None
_log_lines: deque[str] = deque(maxlen=80)
_log_thread: threading.Thread | None = None


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def root_dir() -> Path:
    """Return the approved isolated OpenMontage checkout."""

    return _env_path(
        "DENNIS_OPENMONTAGE_ROOT",
        str(Path.home() / "PycharmProjects" / "OpenMontage-Workspace" / "OpenMontage"),
    )


def port() -> int:
    try:
        value = int(os.environ.get("DENNIS_OPENMONTAGE_PORT", "4750"))
    except ValueError:
        value = 4750
    return value if 1024 <= value <= 65535 else 4750


def python_bin(root: Path | None = None) -> Path:
    root = root or root_dir()
    return root / ".venv" / "bin" / "python"


def projects_dir(root: Path | None = None) -> Path:
    root = root or root_dir()
    return _env_path(
        "OPENMONTAGE_PROJECTS_DIR",
        str(root / "projects"),
    )


def _redact(value: str) -> str:
    """Redact common credential-shaped values before storing child logs."""

    text = str(value or "")
    for name, secret in os.environ.items():
        if len(secret) >= 8 and any(token in name.upper() for token in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            text = text.replace(secret, "[REDACTED]")
    return text


def _health(service_port: int | None = None) -> dict[str, Any] | None:
    service_port = service_port or port()
    request = Request(
        f"http://127.0.0.1:{service_port}/api/health",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=1.5) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else {"ok": True}
    except (OSError, URLError, ValueError, TimeoutError):
        return None


def _capture(process: subprocess.Popen[str]) -> None:
    if process.stdout is None:
        return
    try:
        for line in process.stdout:
            with _lock:
                _log_lines.append(_redact(line.rstrip()))
    except (OSError, ValueError):
        return


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _port_in_use(service_port: int | None = None) -> bool:
    return _health(service_port) is not None


def status() -> dict[str, Any]:
    """Return safe service state suitable for the Studio browser client."""

    root = root_dir()
    service_port = port()
    health = _health(service_port)
    with _lock:
        process = _process
        owned = bool(process and process.poll() is None)
        logs = list(_log_lines)[-12:]
    return {
        "running": health is not None,
        "owned_process": owned,
        "healthy": health is not None,
        "url": f"http://127.0.0.1:{service_port}/" if health is not None else "",
        "port": service_port,
        "root": str(root),
        "configured": root.is_dir() and python_bin(root).is_file(),
        "root_exists": root.is_dir(),
        "python_exists": python_bin(root).is_file(),
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "health": health or {},
        "logs": logs,
    }


def start() -> dict[str, Any]:
    """Start Backlot using OpenMontage's documented native launch command."""

    global _process, _log_thread
    service_port = port()
    if _health(service_port) is not None:
        return status()

    root = root_dir()
    interpreter = python_bin(root)
    if not root.is_dir() or not interpreter.is_file():
        raise OpenMontageError(
            "OpenMontage is not configured. Set DENNIS_OPENMONTAGE_ROOT to its "
            "isolated checkout and run its supported setup first."
        )
    if _port_in_use(service_port):
        raise OpenMontageError(f"OpenMontage port {service_port} is occupied by an unavailable service.")

    try:
        projects_dir(root).mkdir(parents=True, exist_ok=True)
        os.chmod(projects_dir(root), 0o700)
    except OSError as exc:
        raise OpenMontageError("OpenMontage project storage is not writable.") from exc

    environment = os.environ.copy()
    environment["OPENMONTAGE_PROJECTS_DIR"] = str(projects_dir(root))
    environment["PYTHONUNBUFFERED"] = "1"
    command = [str(interpreter), "-m", "backlot", "serve", "--port", str(service_port)]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(root),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name == "posix"),
        )
    except OSError as exc:
        raise OpenMontageError("Could not start the OpenMontage service.") from exc

    with _lock:
        _process = process
        _log_thread = threading.Thread(target=_capture, args=(process,), daemon=True)
        _log_thread.start()

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _health(service_port) is not None:
            return status()
        if process.poll() is not None:
            with _lock:
                detail = _log_lines[-1] if _log_lines else "no service log was produced"
                _process = None
            raise OpenMontageError(f"OpenMontage stopped during startup: {detail}")
        time.sleep(0.25)

    _terminate(process)
    with _lock:
        _process = None
    raise OpenMontageError("OpenMontage did not become healthy within 15 seconds.")


def stop() -> dict[str, Any]:
    """Stop only a process started by this DENNIS supervisor."""

    global _process
    with _lock:
        process = _process
        _process = None
    if process is not None:
        _terminate(process)
    return status()


def restart() -> dict[str, Any]:
    stop()
    return start()
