"""Docker discovery, preflight and per-agent image helpers.

The backend process may run with a PATH that differs from the user's shell
(GUI-launched servers, launchd, IDE run configs), so Docker is resolved from a
fixed list of install locations or an explicit ``DOCKER_BIN``. ``DOCKER_HOST``
is honoured automatically because every probe is executed through the resolved
CLI, which reads that variable itself (OrbStack, Colima, remote daemons).

Missing CLI and a stopped daemon are *different* errors with different,
actionable messages. Nothing here raises raw Docker output at the user.
"""

import os
import shutil
import subprocess

# Fixed install locations, checked in order. ``~/.rd/bin`` covers Rancher
# Desktop; OrbStack and Colima expose docker through the normal locations.
_FIXED_PATHS = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
    "~/.docker/bin/docker",
    "~/.rd/bin/docker",
)
_FIXED_PATHS = tuple(os.path.expanduser(p) for p in _FIXED_PATHS)

CLI_MISSING = "docker_cli_missing"
DAEMON_DOWN = "docker_daemon_down"
READY = "ok"


def docker_bin():
    """Absolute path to the docker CLI, or ``None`` when it is not installed."""
    override = os.environ.get("DOCKER_BIN")
    if override:
        return override if os.path.exists(override) and os.access(override, os.X_OK) else None
    for path in _FIXED_PATHS:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return shutil.which("docker")


def daemon_ok(docker=None, timeout=6):
    """True when ``docker info`` succeeds (daemon reachable)."""
    binary = docker or docker_bin()
    if not binary:
        return False
    try:
        res = subprocess.run([binary, "info"], capture_output=True, timeout=timeout)
        return res.returncode == 0
    except Exception:
        return False


def status(timeout=6):
    """Structured Docker readiness for the UI and the preflight script.

    ``code`` is one of ``docker_cli_missing`` / ``docker_daemon_down`` / ``ok``.
    ``message`` is short and actionable; raw CLI output is never included.
    """
    binary = docker_bin()
    host = os.environ.get("DOCKER_HOST") or ""
    if not binary:
        return {
            "code": CLI_MISSING,
            "cli_found": False,
            "cli_path": None,
            "daemon_ok": False,
            "host": host,
            "message": "Docker CLI not found. Install Docker or set DOCKER_BIN to its path.",
        }
    ok = daemon_ok(binary, timeout=timeout)
    if not ok:
        where = f" ({host})" if host else ""
        return {
            "code": DAEMON_DOWN,
            "cli_found": True,
            "cli_path": binary,
            "daemon_ok": False,
            "host": host,
            "message": f"Docker is installed but its daemon is not running{where}. "
                       "Start Docker and try again.",
        }
    return {
        "code": READY,
        "cli_found": True,
        "cli_path": binary,
        "daemon_ok": True,
        "host": host,
        "message": "Docker is ready.",
    }


def image_name(agent_id, prefix=None):
    """Per-agent image tag, e.g. ``dennis-agent-opencode:latest``."""
    prefix = prefix or os.environ.get("DENNIS_SANDBOX_IMAGE_PREFIX", "dennis-agent")
    safe = "".join(c for c in str(agent_id) if c.isalnum() or c in "-_") or "agent"
    return f"{prefix}-{safe}:latest"


def image_present(image, timeout=12):
    binary = docker_bin()
    if not binary or not image:
        return False
    try:
        res = subprocess.run([binary, "image", "inspect", image],
                             capture_output=True, timeout=timeout)
        return res.returncode == 0
    except Exception:
        return False


def image_status(agent_ids, timeout=12):
    have_cli = docker_bin() is not None
    result = []
    for agent_id in agent_ids:
        name = image_name(agent_id)
        result.append({
            "agent_id": agent_id,
            "image": name,
            "present": image_present(name, timeout=timeout) if have_cli else False,
            "buildable": have_cli,
        })
    return result
