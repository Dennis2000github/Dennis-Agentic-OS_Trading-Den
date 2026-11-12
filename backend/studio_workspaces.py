"""Workspace registry and creative project store for Studio.

Studio now hosts more than one production workspace. This module is the
workspace boundary: it owns the registry (Ads Studio, YouTube Studio), each
workspace's production settings, and its creative projects.

Design constraints match the rest of Studio:

* JSON files under ``data/studio/workspaces/`` with atomic ``0600`` writes;
* one lock per operation so the threaded HTTP server is safe;
* additive, versioned files with lazy defaults (no destructive migration);
* projects are soft-deleted and archived, never hard-deleted;
* reads are scoped by workspace so Ads data can never appear in YouTube lists
  and vice versa.

The existing Ads Studio generation data (`jobs.json`, `media.json`,
`playlists.json`, `templates.json`) is untouched by this module. Ads projects
start empty and are opt-in; the existing Generate/Library/History screens keep
working on their own stores.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid

from . import config

STUDIO_DIR = os.path.join(config.DATA_DIR, "studio")
WORKSPACES_FILE = os.path.join(STUDIO_DIR, "workspaces.json")
WORKSPACES_DIR = os.path.join(STUDIO_DIR, "workspaces")

SCHEMA_VERSION = 1
MAX_PROJECTS = 500
MAX_ACTIVITY = 200

PROJECT_STATUSES = (
    "draft",
    "in_progress",
    "review",
    "approved",
    "scheduled",
    "published",
    "archived",
)

PROJECT_STAGES = (
    "research",
    "script",
    "storyboard",
    "assets",
    "voice",
    "animation",
    "assembly",
    "review",
    "publishing",
    "analytics",
)

YOUTUBE_SECTIONS = (
    {"id": "overview", "label": "Overview", "stage": 2, "available": True},
    {"id": "projects", "label": "Projects", "stage": 2, "available": True},
    {"id": "channel-manager", "label": "Channel Manager", "stage": 3, "available": True},
    {"id": "research", "label": "Niche Research", "stage": 3, "available": True},
    {"id": "topics", "label": "Topic Studio", "stage": 3, "available": True},
    {"id": "scripts", "label": "Script Studio", "stage": 3, "available": True},
    {"id": "storyboard", "label": "Storyboard Studio", "stage": 4, "available": False},
    {"id": "assets", "label": "Asset Studio", "stage": 4, "available": False},
    {"id": "voice", "label": "Voice Studio", "stage": 4, "available": False},
    {"id": "animation", "label": "Animation Studio", "stage": 4, "available": False},
    {"id": "thumbnails", "label": "Thumbnail Studio", "stage": 4, "available": False},
    {"id": "publishing", "label": "Publishing Manager", "stage": 5, "available": False},
    {"id": "analytics", "label": "Analytics", "stage": 5, "available": False},
    {"id": "calendar", "label": "Publishing Calendar", "stage": 5, "available": False},
)

WORKSPACES = {
    "ads": {
        "id": "ads",
        "label": "Ads Studio",
        "subtitle": "Advertising and marketing creative production",
        "sections": [],
        "legacy_tabs": ["fashion", "assets", "library", "playlists", "preset", "history", "setup"],
    },
    "youtube": {
        "id": "youtube",
        "label": "YouTube Studio",
        "subtitle": "Research. Script. Visualize. Animate. Publish.",
        "sections": list(YOUTUBE_SECTIONS),
        "legacy_tabs": [],
    },
}

DEFAULT_SETTINGS = {
    "ads": {
        "default_provider": "higgsfield",
    },
    "youtube": {
        "publishing_schedule": ["mon", "wed", "fri"],
        "target_videos_per_week": 3,
        "render": {
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "aspect_ratio": "16:9",
        },
        # Empty means "not chosen yet"; the UI must show a configuration gap
        # rather than silently picking a provider.
        "providers": {
            "llm": None,
            "image": None,
            "video": None,
            "voice": None,
            "music": None,
            "sfx": None,
            "transcription": None,
            "render": None,
        },
        "active_channel_id": None,
        "default_voice": None,
        "default_visual_style": None,
    },
}

_lock = threading.RLock()


class WorkspaceError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def as_error(self):
        return {"code": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# Small IO helpers (atomic, 0600)
# ---------------------------------------------------------------------------
def _ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)


def _read_json(path, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise WorkspaceError("workspace_unreadable", "Could not read %s: %s" % (os.path.basename(path), exc), 500)
    if not isinstance(data, dict):
        raise WorkspaceError("workspace_invalid", "%s has an unsupported format." % os.path.basename(path), 500)
    return data


def _write_json(path, payload):
    _ensure_dir(os.path.dirname(path) or STUDIO_DIR)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or STUDIO_DIR, prefix=".workspace-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _now():
    return time.time()


def _iso(value=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(value if value is not None else _now()))


def _workspace(workspace_id):
    workspace = WORKSPACES.get(str(workspace_id or "").strip().lower())
    if not workspace:
        raise WorkspaceError("unknown_workspace", "Unknown Studio workspace.", 404)
    return workspace


def _workspace_dir(workspace_id):
    return os.path.join(WORKSPACES_DIR, _workspace(workspace_id)["id"])


def _path(workspace_id, name):
    return os.path.join(_workspace_dir(workspace_id), name)


def _default_settings(workspace_id):
    return json.loads(json.dumps(DEFAULT_SETTINGS[_workspace(workspace_id)["id"]]))


# ---------------------------------------------------------------------------
# Registry / active workspace
# ---------------------------------------------------------------------------
def _read_registry():
    data = _read_json(WORKSPACES_FILE, {})
    if not data:
        data = {"version": SCHEMA_VERSION, "active": "ads", "updated_at": _now()}
    if data.get("version") != SCHEMA_VERSION:
        raise WorkspaceError(
            "workspace_registry_version",
            "Workspace registry uses an unsupported version.",
            500,
        )
    data.setdefault("active", "ads")
    if data["active"] not in WORKSPACES:
        data["active"] = "ads"
    return data


def get_active():
    with _lock:
        return _read_registry().get("active") or "ads"


def set_active(workspace_id):
    workspace = _workspace(workspace_id)
    with _lock:
        data = _read_registry()
        data["active"] = workspace["id"]
        data["updated_at"] = _now()
        data["updated_iso"] = _iso()
        _write_json(WORKSPACES_FILE, data)
    return workspace["id"]


def _project_counts(workspace_id):
    items = _load_projects(workspace_id)
    live = [p for p in items if not p.get("deleted_at")]
    counts = {status: 0 for status in PROJECT_STATUSES}
    by_stage = {stage: 0 for stage in PROJECT_STAGES}
    for project in live:
        counts[project.get("status") or "draft"] = counts.get(project.get("status") or "draft", 0) + 1
        by_stage[project.get("stage") or "research"] = by_stage.get(project.get("stage") or "research", 0) + 1
    production_stages = ("assets", "voice", "animation", "assembly")
    return {
        "total": len(live),
        "by_status": counts,
        "by_stage": by_stage,
        "scripts_in_progress": sum(1 for p in live if p.get("stage") == "script"),
        "videos_in_production": sum(1 for p in live if p.get("stage") in production_stages),
        "awaiting_approval": sum(
            1 for p in live if p.get("status") == "review" or p.get("stage") == "review"
        ),
        "published": counts.get("published", 0),
        "scheduled": counts.get("scheduled", 0),
    }


def descriptor(workspace_id):
    workspace = _workspace(workspace_id)
    return {
        "id": workspace["id"],
        "label": workspace["label"],
        "subtitle": workspace["subtitle"],
        "sections": [dict(section) for section in workspace["sections"]],
        "legacy_tabs": list(workspace["legacy_tabs"]),
        "counts": _project_counts(workspace["id"]),
    }


def overview():
    with _lock:
        registry = _read_registry()
    return {
        "workspaces": [descriptor(key) for key in ("ads", "youtube")],
        "active": registry.get("active") or "ads",
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def _load_settings(workspace_id):
    workspace_id = _workspace(workspace_id)["id"]
    path = _path(workspace_id, "settings.json")
    data = _read_json(path, {})
    if not data:
        data = {"version": SCHEMA_VERSION, "settings": _default_settings(workspace_id)}
        _write_json(path, data)
    if data.get("version") != SCHEMA_VERSION:
        raise WorkspaceError("workspace_settings_version", "Workspace settings use an unsupported version.", 500)
    settings = _default_settings(workspace_id)
    _deep_merge(settings, data.get("settings") or {})
    return settings


def _deep_merge(base, patch):
    if not isinstance(patch, dict):
        return base
    for key, value in patch.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def get_settings(workspace_id):
    with _lock:
        return _load_settings(workspace_id)


def update_settings(workspace_id, patch):
    workspace_id = _workspace(workspace_id)["id"]
    if not isinstance(patch, dict):
        raise WorkspaceError("invalid_settings", "Settings must be a JSON object.")
    # Keep the payload bounded and reject obviously unsafe shapes.
    try:
        encoded = json.dumps(patch)
    except (TypeError, ValueError):
        raise WorkspaceError("invalid_settings", "Settings must be JSON serializable.")
    if len(encoded) > 64 * 1024:
        raise WorkspaceError("invalid_settings", "Settings payload is too large.")
    with _lock:
        settings = _load_settings(workspace_id)
        _deep_merge(settings, patch)
        _write_json(
            _path(workspace_id, "settings.json"),
            {"version": SCHEMA_VERSION, "settings": settings, "updated_at": _now(), "updated_iso": _iso()},
        )
        _log_activity_locked(workspace_id, "settings.updated", "Production settings updated.", None)
    return settings


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def _load_projects(workspace_id):
    workspace_id = _workspace(workspace_id)["id"]
    data = _read_json(_path(workspace_id, "projects.json"), {})
    if not data:
        return []
    if data.get("version") != SCHEMA_VERSION:
        raise WorkspaceError("workspace_projects_version", "Project store uses an unsupported version.", 500)
    items = data.get("projects")
    if not isinstance(items, list):
        raise WorkspaceError("workspace_projects_invalid", "Project store has an unsupported format.", 500)
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _save_projects(workspace_id, projects):
    workspace_id = _workspace(workspace_id)["id"]
    _write_json(
        _path(workspace_id, "projects.json"),
        {
            "version": SCHEMA_VERSION,
            "workspace": workspace_id,
            "projects": projects,
            "updated_at": _now(),
            "updated_iso": _iso(),
        },
    )


def _clean_text(value, field, limit, required=False):
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()
    if required and not text:
        raise WorkspaceError("invalid_project", "%s is required." % field)
    if len(text) > limit:
        raise WorkspaceError("invalid_project", "%s must be %d characters or fewer." % (field, limit))
    return text


def _clean_tags(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise WorkspaceError("invalid_project", "tags must be a list.")
    if len(value) > 20:
        raise WorkspaceError("invalid_project", "A project can have at most 20 tags.")
    tags = []
    for tag in value:
        text = _clean_text(tag, "tag", 40)
        if text and text not in tags:
            tags.append(text)
    return tags


def _clean_metadata(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WorkspaceError("invalid_project", "metadata must be a JSON object.")
    try:
        encoded = json.dumps(value)
    except (TypeError, ValueError):
        raise WorkspaceError("invalid_project", "metadata must be JSON serializable.")
    if len(encoded) > 16 * 1024:
        raise WorkspaceError("invalid_project", "metadata is too large.")
    return value


def _public_project(project):
    return json.loads(json.dumps(project))


def _new_id(workspace_id):
    prefix = "ytp_" if workspace_id == "youtube" else "sp_"
    return prefix + uuid.uuid4().hex[:16]


def _find(projects, project_id):
    for project in projects:
        if project.get("id") == project_id:
            return project
    return None


def list_projects(workspace_id, status=None, query=None, include_archived=False, include_deleted=False, limit=200):
    workspace_id = _workspace(workspace_id)["id"]
    with _lock:
        items = _load_projects(workspace_id)
    if not include_deleted:
        items = [p for p in items if not p.get("deleted_at")]
    if not include_archived and str(status or "").strip() != "archived":
        items = [p for p in items if p.get("status") != "archived"]
    if status:
        wanted = str(status).strip()
        if wanted not in PROJECT_STATUSES:
            raise WorkspaceError("invalid_status", "Unknown project status.")
        items = [p for p in items if p.get("status") == wanted]
    needle = str(query or "").strip().lower()
    if needle:
        items = [
            p for p in items
            if needle in ("%s %s %s" % (p.get("title", ""), p.get("description", ""), p.get("content_pillar", ""))).lower()
        ]
    items.sort(key=lambda p: p.get("updated_at") or p.get("created_at") or 0, reverse=True)
    items = items[: max(1, min(int(limit or 200), MAX_PROJECTS))]
    return {
        "projects": [_public_project(p) for p in items],
        "total": len(items),
        "counts": _project_counts(workspace_id),
    }


def get_project(workspace_id, project_id, include_deleted=False):
    workspace_id = _workspace(workspace_id)["id"]
    with _lock:
        projects = _load_projects(workspace_id)
    project = _find(projects, str(project_id or ""))
    if not project or (project.get("deleted_at") and not include_deleted):
        raise WorkspaceError("project_not_found", "Project not found.", 404)
    return _public_project(project)


def create_project(workspace_id, payload):
    workspace_id = _workspace(workspace_id)["id"]
    if not isinstance(payload, dict):
        raise WorkspaceError("invalid_project", "Project payload must be a JSON object.")
    title = _clean_text(payload.get("title"), "Title", 160, required=True)
    description = _clean_text(payload.get("description"), "Description", 4000)
    content_pillar = _clean_text(payload.get("content_pillar"), "Content pillar", 80)
    status = str(payload.get("status") or "draft").strip().lower()
    stage = str(payload.get("stage") or "research").strip().lower()
    if status not in PROJECT_STATUSES:
        raise WorkspaceError("invalid_status", "Unknown project status.")
    if stage not in PROJECT_STAGES:
        raise WorkspaceError("invalid_stage", "Unknown production stage.")
    now = _now()
    project = {
        "id": _new_id(workspace_id),
        "workspace": workspace_id,
        "title": title,
        "description": description,
        "content_pillar": content_pillar,
        "status": status,
        "stage": stage,
        "channel_id": _clean_text(payload.get("channel_id"), "Channel", 64) or None,
        "topic_id": _clean_text(payload.get("topic_id"), "Topic", 64) or None,
        "thumbnail_media_id": _clean_text(payload.get("thumbnail_media_id"), "Thumbnail", 64) or None,
        "tags": _clean_tags(payload.get("tags")),
        "metadata": _clean_metadata(payload.get("metadata")),
        "archived": False,
        "deleted_at": None,
        "created_at": now,
        "created_iso": _iso(now),
        "updated_at": now,
        "updated_iso": _iso(now),
    }
    with _lock:
        projects = _load_projects(workspace_id)
        if len([p for p in projects if not p.get("deleted_at")]) >= MAX_PROJECTS:
            raise WorkspaceError("project_limit", "This workspace reached its project limit.", 409)
        projects.append(project)
        _save_projects(workspace_id, projects)
        _log_activity_locked(workspace_id, "project.created", title, project["id"])
    return _public_project(project)


def update_project(workspace_id, project_id, patch):
    workspace_id = _workspace(workspace_id)["id"]
    if not isinstance(patch, dict):
        raise WorkspaceError("invalid_project", "Project payload must be a JSON object.")
    with _lock:
        projects = _load_projects(workspace_id)
        project = _find(projects, str(project_id or ""))
        if not project or project.get("deleted_at"):
            raise WorkspaceError("project_not_found", "Project not found.", 404)
        if "title" in patch:
            project["title"] = _clean_text(patch.get("title"), "Title", 160, required=True)
        if "description" in patch:
            project["description"] = _clean_text(patch.get("description"), "Description", 4000)
        if "content_pillar" in patch:
            project["content_pillar"] = _clean_text(patch.get("content_pillar"), "Content pillar", 80)
        if "status" in patch:
            status = str(patch.get("status") or "").strip().lower()
            if status not in PROJECT_STATUSES:
                raise WorkspaceError("invalid_status", "Unknown project status.")
            project["status"] = status
            project["archived"] = status == "archived"
        if "stage" in patch:
            stage = str(patch.get("stage") or "").strip().lower()
            if stage not in PROJECT_STAGES:
                raise WorkspaceError("invalid_stage", "Unknown production stage.")
            project["stage"] = stage
        if "channel_id" in patch:
            project["channel_id"] = _clean_text(patch.get("channel_id"), "Channel", 64) or None
        if "topic_id" in patch:
            project["topic_id"] = _clean_text(patch.get("topic_id"), "Topic", 64) or None
        if "thumbnail_media_id" in patch:
            project["thumbnail_media_id"] = _clean_text(patch.get("thumbnail_media_id"), "Thumbnail", 64) or None
        if "tags" in patch:
            project["tags"] = _clean_tags(patch.get("tags"))
        if "metadata" in patch:
            project["metadata"] = _clean_metadata(patch.get("metadata"))
        project["updated_at"] = _now()
        project["updated_iso"] = _iso(project["updated_at"])
        _save_projects(workspace_id, projects)
        _log_activity_locked(workspace_id, "project.updated", project.get("title") or "", project["id"])
        return _public_project(project)


def delete_project(workspace_id, project_id):
    """Soft-delete a project. Assets and media are never touched here."""
    workspace_id = _workspace(workspace_id)["id"]
    with _lock:
        projects = _load_projects(workspace_id)
        project = _find(projects, str(project_id or ""))
        if not project or project.get("deleted_at"):
            raise WorkspaceError("project_not_found", "Project not found.", 404)
        project["deleted_at"] = _now()
        project["deleted_iso"] = _iso(project["deleted_at"])
        project["updated_at"] = project["deleted_at"]
        project["updated_iso"] = project["deleted_iso"]
        _save_projects(workspace_id, projects)
        _log_activity_locked(workspace_id, "project.deleted", project.get("title") or "", project["id"])
        return {"deleted": project["id"], "project": _public_project(project)}


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------
def _load_activity(workspace_id):
    data = _read_json(_path(workspace_id, "activity.json"), {})
    items = data.get("activity")
    return items if isinstance(items, list) else []


def _log_activity_locked(workspace_id, event, detail, project_id):
    items = _load_activity(workspace_id)
    items.append(
        {
            "id": "act_" + uuid.uuid4().hex[:12],
            "event": event,
            "detail": _clean_text(detail, "Detail", 300),
            "project_id": project_id,
            "created_at": _now(),
            "created_iso": _iso(),
        }
    )
    items = items[-MAX_ACTIVITY:]
    _write_json(_path(workspace_id, "activity.json"), {"version": SCHEMA_VERSION, "activity": items})


def log_activity(workspace_id, event, detail, project_id=None):
    workspace_id = _workspace(workspace_id)["id"]
    with _lock:
        _log_activity_locked(workspace_id, _clean_text(event, "Event", 80, required=True), detail, project_id)
    return {"ok": True}


def activity(workspace_id, limit=50):
    workspace_id = _workspace(workspace_id)["id"]
    with _lock:
        items = _load_activity(workspace_id)
    items = sorted(items, key=lambda item: item.get("created_at") or 0, reverse=True)
    return {"activity": items[: max(1, min(int(limit or 50), MAX_ACTIVITY))]}
