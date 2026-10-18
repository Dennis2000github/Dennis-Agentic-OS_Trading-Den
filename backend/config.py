"""Shared configuration, paths and small IO helpers.

Kept dependency-free so every other backend module can import it without
creating cycles.
"""

import json
import os
import tempfile
import time

DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(DIRECTORY, "data")

AGENTS_FILE = os.path.join(DATA_DIR, "agents.json")
PROJECTS_FILE = os.path.join(DATA_DIR, "projects.json")
SESSION_STORE_FILE = os.path.join(DATA_DIR, "project_sessions.json")
AUTH_FILE = os.path.join(DATA_DIR, "auth.json")

DEFAULT_OWNER = "local"

# Container sandbox defaults (overridable by the caller / env).
DEFAULT_IMAGE = os.environ.get("DENNIS_SANDBOX_IMAGE", "dennis-agent:latest")
DEFAULT_CPU_LIMIT = os.environ.get("DENNIS_SANDBOX_CPUS", "2")
DEFAULT_MEMORY_LIMIT = os.environ.get("DENNIS_SANDBOX_MEMORY", "2g")
IDLE_TIMEOUT_SECONDS = int(os.environ.get("DENNIS_IDLE_TIMEOUT", "1800"))
MODEL_CACHE_TTL_SECONDS = int(os.environ.get("DENNIS_MODEL_CACHE_TTL", "300"))


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def now_epoch():
    return time.time()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[config] failed to read {path}: {exc}")
        return default


def write_json(path, data):
    """Write JSON atomically so a crash cannot leave a half-written file."""
    ensure_data_dir()
    dirname = os.path.dirname(path)
    os.makedirs(dirname, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirname, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
