"""Per-owner project session metadata.

A *project session* binds an agent to a project folder. It stores the panel
state (Code & Preview), the resume id reported by the CLI and the selected
model. PTY/Docker processes are owned by ``session_manager`` and are looked up
by these ids; this module never spawns anything.

Store shape::

    {
      "owners": {
        "local": {
          "sessions": [ {id, owner, agent_id, project, project_path,
                         title, model_id, cli_session_id, created_at,
                         last_active, panel: {...}} ]
        }
      }
    }
"""

import threading
import time
import uuid

from . import config

# The threaded HTTP server calls these helpers from many request threads; the
# lock keeps a read-modify-write of the single JSON store from losing updates.
_LOCK = threading.RLock()

DEFAULT_PANEL = {
    "state": "S1",
    "code_file": None,
    "editor": None,
    "preview_url": None,
    "pane": "split",
}


def _load():
    data = config.read_json(config.SESSION_STORE_FILE, default={})
    if not isinstance(data, dict) or "owners" not in data:
        data = {"owners": {}}
    return data


def _save(data):
    config.write_json(config.SESSION_STORE_FILE, data)


def _owner_bucket(data, owner, create=False):
    owners = data.setdefault("owners", {})
    if create:
        return owners.setdefault(owner, {"sessions": []})
    return owners.get(owner) or {"sessions": []}


def _new_id():
    return "ps-" + uuid.uuid4().hex[:12]


def list_sessions(owner, agent_id=None):
    data = _load()
    sessions = _owner_bucket(data, owner).get("sessions", [])
    if agent_id:
        sessions = [s for s in sessions if s.get("agent_id") == agent_id]
    return sorted(sessions, key=lambda s: s.get("last_active") or "", reverse=True)


def get_session(owner, session_id):
    for s in _owner_bucket(_load(), owner).get("sessions", []):
        if s.get("id") == session_id:
            return s
    return None


def create_session(owner, agent_id, project_name, project_path, model_id=None, title=None):
    with _LOCK:
        data = _load()
        bucket = _owner_bucket(data, owner, create=True)
        ts = config.now_iso()
        session = {
            "id": _new_id(),
            "owner": owner,
            "agent_id": agent_id,
            "project": project_name,
            "project_path": project_path,
            "title": title or f"{project_name}",
            "model_id": model_id,
            "cli_session_id": None,
            "created_at": ts,
            "last_active": ts,
            "panel": dict(DEFAULT_PANEL),
        }
        bucket["sessions"].insert(0, session)
        _save(data)
        return session


def _mutate(owner, session_id, fn):
    with _LOCK:
        data = _load()
        for s in _owner_bucket(data, owner, create=True).get("sessions", []):
            if s.get("id") == session_id:
                fn(s)
                _save(data)
                return s
        return None


def touch(owner, session_id):
    ts = config.now_iso()

    def _apply(s):
        s["last_active"] = ts

    return _mutate(owner, session_id, _apply)


def set_model(owner, session_id, model_id):
    def _apply(s):
        s["model_id"] = model_id
        s["last_active"] = config.now_iso()

    return _mutate(owner, session_id, _apply)


def set_cli_session(owner, session_id, cli_session_id):
    def _apply(s):
        s["cli_session_id"] = cli_session_id

    return _mutate(owner, session_id, _apply)


def set_panel(owner, session_id, panel):
    allowed = set(DEFAULT_PANEL.keys())

    def _apply(s):
        current = dict(DEFAULT_PANEL)
        current.update({k: v for k, v in (s.get("panel") or {}).items() if k in allowed})
        current.update({k: v for k, v in (panel or {}).items() if k in allowed})
        s["panel"] = current

    return _mutate(owner, session_id, _apply)


def reset_panel(owner, session_id):
    def _apply(s):
        s["panel"] = dict(DEFAULT_PANEL)

    return _mutate(owner, session_id, _apply)


def delete_session(owner, session_id):
    with _LOCK:
        data = _load()
        bucket = _owner_bucket(data, owner, create=True)
        before = len(bucket["sessions"])
        bucket["sessions"] = [s for s in bucket["sessions"] if s.get("id") != session_id]
        _save(data)
        return len(bucket["sessions"]) != before


def counts_by_agent(owner):
    counts = {}
    for s in _owner_bucket(_load(), owner).get("sessions", []):
        counts[s.get("agent_id")] = counts.get(s.get("agent_id"), 0) + 1
    return counts
