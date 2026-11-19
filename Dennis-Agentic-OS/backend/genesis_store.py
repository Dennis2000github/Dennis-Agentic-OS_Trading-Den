"""Persistence for the GENESIS production dashboard.

GENESIS is the COSMOS production workflow: production projects, render jobs
and publishing records live here. The store follows the exact conventions of
the other COSMOS stores (see ``cosmos_common``):

* JSON files under ``data/cosmos/genesis/`` — atomic writes, owner-only
  permissions (0700 directory / 0600 files);
* one process-wide lock for read-modify-write (the HTTP server is threaded);
* versioned, additive schemas with lazy defaults;
* bounded lists and sanitized text;
* soft deletion only — records are preserved for provenance.

Credentials (OAuth tokens) also live in this directory with the same
permissions. They are never returned through the public API.
"""

from __future__ import annotations

import os
import threading

from . import cosmos_common as common

GENESIS_DIR = os.path.join(common.COSMOS_DIR, "genesis")
PROJECTS_FILE = os.path.join(GENESIS_DIR, "projects.json")
RENDERS_FILE = os.path.join(GENESIS_DIR, "renders.json")
PUBLICATIONS_FILE = os.path.join(GENESIS_DIR, "publications.json")
TOKENS_FILE = os.path.join(GENESIS_DIR, "oauth-tokens.json")
TMP_DIR = os.path.join(GENESIS_DIR, "tmp")

SCHEMA_VERSION = common.SCHEMA_VERSION

MAX_PROJECTS = 500
MAX_RENDERS = 400
MAX_PUBLICATIONS = 400

PROJECT_STATUSES = (
    "in_production", "rendering", "quality_review", "awaiting_approval",
    "approved", "published", "failed", "archived",
)
RENDER_STATUSES = (
    "queued", "preparing", "waiting_media", "assembling", "complete",
    "failed", "cancelled",
)
PUBLICATION_STATUSES = (
    "awaiting_approval", "scheduled", "publishing", "succeeded", "failed",
    "cancelled",
)

_lock = threading.RLock()


def acquire():
    return _lock


def ensure_dirs():
    common.ensure_dir(GENESIS_DIR)
    common.ensure_dir(TMP_DIR)


# ---------------------------------------------------------------------------
# Generic list-store helpers
# ---------------------------------------------------------------------------
def _load(path, key):
    items = common.load_store(path, key)
    ensure_dirs()
    return items


def _save(path, key, items):
    ensure_dirs()
    common.save_store(path, key, items[-_limit_for(key):])


def _limit_for(key):
    return {
        "projects": MAX_PROJECTS,
        "renders": MAX_RENDERS,
        "publications": MAX_PUBLICATIONS,
    }.get(key, 500)


def _find(items, record_id):
    for item in items:
        if item.get("id") == record_id:
            return item
    return None


def _update(path, key, record_id, patch, expected_status=None, status_key="status"):
    """Apply ``patch`` to a record under the store lock.

    ``expected_status`` optionally guards against racing transitions (used by
    the publishing scheduler so a manual retry and a due schedule cannot both
    upload the same video).
    """
    with _lock:
        items = _load(path, key)
        record = _find(items, record_id)
        if record is None:
            raise common.CosmosError("not_found", "%s %s was not found." % (key[:-1], record_id), 404)
        if expected_status is not None and record.get(status_key) != expected_status:
            raise common.CosmosError(
                "conflict",
                "%s is %s, expected %s." % (key[:-1].title(), record.get(status_key), expected_status),
                409,
            )
        record.update(patch)
        _save(path, key, items)
        return common.public(record)


def _append_history(record, event, note=None):
    record.setdefault("history", []).append({
        "at": common.now(),
        "iso": common.iso(),
        "event": event,
        "note": common.clean_text(note, "History note", 500) if note else None,
    })


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
def load_projects():
    return _load(PROJECTS_FILE, "projects")


def save_projects(items):
    with _lock:
        _save(PROJECTS_FILE, "projects", items)


def insert_project(record):
    with _lock:
        items = _load(PROJECTS_FILE, "projects")
        items.append(record)
        _save(PROJECTS_FILE, "projects", items)
    return common.public(record)


def insert_project_unique(record):
    """Insert a project only when its backlog source has no active project.

    The check and the write share one lock so two simultaneous intake requests
    cannot both create a project for the same product/topic.
    """
    source = record.get("source") or {}
    with _lock:
        items = _load(PROJECTS_FILE, "projects")
        for existing in items:
            if existing.get("deleted_at") or existing.get("archived"):
                continue
            other = existing.get("source") or {}
            if other.get("kind") == source.get("kind") and other.get("id") == source.get("id"):
                if existing.get("status") not in ("published", "archived"):
                    raise common.CosmosError(
                        "duplicate_project",
                        "This %s already has an active production project (%s)."
                        % (source.get("kind"), existing.get("id")),
                        409,
                    )
        items.append(record)
        _save(PROJECTS_FILE, "projects", items)
    return common.public(record)


def remove_project(project_id):
    """Remove a just-inserted project when intake cannot advance its source."""
    with _lock:
        items = _load(PROJECTS_FILE, "projects")
        remaining = [item for item in items if item.get("id") != project_id]
        if len(remaining) == len(items):
            raise common.CosmosError("project_not_found", "Production project %s was not found." % project_id, 404)
        _save(PROJECTS_FILE, "projects", remaining)
    return {"deleted": project_id}


def get_project(project_id, include_deleted=False):
    with _lock:
        items = _load(PROJECTS_FILE, "projects")
        record = _find(items, project_id)
    if record is None:
        raise common.CosmosError("project_not_found", "Production project %s was not found." % project_id, 404)
    if record.get("deleted_at") and not include_deleted:
        raise common.CosmosError("project_deleted", "Production project %s was deleted." % project_id, 410)
    return common.public(record)


def update_project(project_id, patch, expected_status=None):
    return _update(PROJECTS_FILE, "projects", project_id, patch, expected_status=expected_status)


def find_active_by_source(source_kind, source_id):
    """Return an existing non-terminal project for a backlog record, if any."""
    with _lock:
        items = _load(PROJECTS_FILE, "projects")
        for record in items:
            if record.get("deleted_at") or record.get("archived"):
                continue
            source = record.get("source") or {}
            if source.get("kind") == source_kind and source.get("id") == source_id:
                if record.get("status") not in ("published", "archived"):
                    return common.public(record)
    return None


def list_projects(division=None, status=None, query=None, include_archived=False, limit=100):
    with _lock:
        items = _load(PROJECTS_FILE, "projects")
    wanted_division = common.normalize_division(division) if division else None
    wanted_status = str(status or "").strip().lower() or None
    text = common.normalize_key(query) if query else None
    result = []
    for record in reversed(items):
        if record.get("deleted_at"):
            continue
        if not include_archived and record.get("archived"):
            continue
        if wanted_division and common.normalize_division(record.get("division"), "ads") != wanted_division:
            continue
        if wanted_status and record.get("status") != wanted_status:
            continue
        if text:
            haystack = common.normalize_key("%s %s" % (record.get("title"), record.get("description")))
            if text not in haystack:
                continue
        result.append(common.public(record))
    return result[: max(1, min(int(limit or 100), 500))]


# ---------------------------------------------------------------------------
# Render jobs
# ---------------------------------------------------------------------------
def load_renders():
    return _load(RENDERS_FILE, "renders")


def insert_render(record):
    with _lock:
        items = _load(RENDERS_FILE, "renders")
        items.append(record)
        _save(RENDERS_FILE, "renders", items)
    return common.public(record)


def get_render(render_id):
    with _lock:
        items = _load(RENDERS_FILE, "renders")
        record = _find(items, render_id)
    if record is None:
        raise common.CosmosError("render_not_found", "Render job %s was not found." % render_id, 404)
    return common.public(record)


def update_render(render_id, patch, expected_status=None):
    return _update(RENDERS_FILE, "renders", render_id, patch, expected_status=expected_status)


def list_renders(project_id=None, status=None, limit=100):
    with _lock:
        items = _load(RENDERS_FILE, "renders")
    wanted_status = str(status or "").strip().lower() or None
    result = []
    for record in reversed(items):
        if project_id and record.get("project_id") != project_id:
            continue
        if wanted_status and record.get("status") != wanted_status:
            continue
        result.append(common.public(record))
    return result[: max(1, min(int(limit or 100), 500))]


# ---------------------------------------------------------------------------
# Publications
# ---------------------------------------------------------------------------
def load_publications():
    return _load(PUBLICATIONS_FILE, "publications")


def insert_publication(record):
    with _lock:
        items = _load(PUBLICATIONS_FILE, "publications")
        items.append(record)
        _save(PUBLICATIONS_FILE, "publications", items)
    return common.public(record)


def get_publication(publication_id):
    with _lock:
        items = _load(PUBLICATIONS_FILE, "publications")
        record = _find(items, publication_id)
    if record is None:
        raise common.CosmosError("publication_not_found", "Publication %s was not found." % publication_id, 404)
    return common.public(record)


def update_publication(publication_id, patch, expected_status=None):
    return _update(PUBLICATIONS_FILE, "publications", publication_id, patch, expected_status=expected_status)


def find_publication(project_id, destination, statuses=None):
    with _lock:
        items = _load(PUBLICATIONS_FILE, "publications")
        for record in reversed(items):
            if record.get("project_id") != project_id:
                continue
            if record.get("destination") != destination:
                continue
            if statuses and record.get("status") not in statuses:
                continue
            return common.public(record)
    return None


def list_publications(project_id=None, status=None, limit=100):
    with _lock:
        items = _load(PUBLICATIONS_FILE, "publications")
    wanted_status = str(status or "").strip().lower() or None
    result = []
    for record in reversed(items):
        if project_id and record.get("project_id") != project_id:
            continue
        if wanted_status and record.get("status") != wanted_status:
            continue
        result.append(common.public(record))
    return result[: max(1, min(int(limit or 100), 500))]
