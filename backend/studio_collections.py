"""Persistent Studio playlists and generation templates.

Both are owner-local collections stored next to the job and media indexes:

    data/studio/playlists.json
    data/studio/templates.json

They contain references, never provider credentials. Media records are
hydrated on read so a deleted library item disappears from playlists instead of
breaking the page. Writes are atomic and owner-only (0600).
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import time
import uuid

from . import config
from . import studio_media

STUDIO_DIR = os.path.join(config.DATA_DIR, "studio")
PLAYLISTS_FILE = os.path.join(STUDIO_DIR, "playlists.json")
TEMPLATES_FILE = os.path.join(STUDIO_DIR, "templates.json")

MAX_PLAYLISTS = 50
MAX_TEMPLATES = 80
MAX_PLAYLIST_ITEMS = 200
NAME_LIMIT = 80

_lock = threading.RLock()


class CollectionError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _ensure_dir():
    os.makedirs(STUDIO_DIR, mode=0o700, exist_ok=True)


def _read(path, key):
    if not os.path.isfile(path):
        return {key: []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CollectionError("collection_unreadable", "Could not read %s: %s" % (os.path.basename(path), exc), 500)
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise CollectionError("collection_invalid", "%s has an unsupported format." % os.path.basename(path), 500)
    return data


def _write(path, payload):
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or STUDIO_DIR, prefix=".studio-", suffix=".json")
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


def _clean_name(value, fallback="Untitled"):
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    name = re.sub(r"\s+", " ", name)
    if not name:
        name = fallback
    if len(name) > NAME_LIMIT:
        raise CollectionError("invalid_name", "Names are limited to %d characters." % NAME_LIMIT)
    return name


def _media_summary(record):
    if not isinstance(record, dict):
        return None
    return {
        key: record.get(key)
        for key in (
            "id", "kind", "role", "title", "filename", "content_type", "size",
            "created_at", "created_iso", "provider", "model_id", "job_id",
            "width", "height", "duration",
        )
        if record.get(key) is not None
    }


def _playlist_public(record):
    items = []
    for item in record.get("items") or []:
        try:
            media = studio_media.get(item.get("media_id"))
        except studio_media.MediaError:
            continue
        summary = _media_summary(media)
        if summary:
            items.append({
                "media_id": summary["id"],
                "added_at": item.get("added_at"),
                "added_iso": item.get("added_iso"),
                "media": summary,
            })
    return {
        "id": record.get("id"),
        "name": record.get("name") or "Playlist",
        "created_at": record.get("created_at"),
        "created_iso": record.get("created_iso"),
        "updated_at": record.get("updated_at"),
        "updated_iso": record.get("updated_iso"),
        "items": items,
        "item_count": len(items),
    }


def _find(records, item_id, kind):
    for record in records:
        if record.get("id") == item_id:
            return record
    raise CollectionError("%s_not_found" % kind, "%s not found." % kind.replace("_", " ").title(), 404)


def list_playlists():
    with _lock:
        data = _read(PLAYLISTS_FILE, "playlists")
        records = list(data.get("playlists") or [])
    records.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
    return [_playlist_public(record) for record in records]


def create_playlist(name):
    name = _clean_name(name, "New playlist")
    now = time.time()
    record = {
        "id": "pl_" + uuid.uuid4().hex[:16],
        "name": name,
        "created_at": now,
        "created_iso": config.now_iso(),
        "updated_at": now,
        "updated_iso": config.now_iso(),
        "items": [],
    }
    with _lock:
        data = _read(PLAYLISTS_FILE, "playlists")
        records = data.get("playlists") or []
        if len(records) >= MAX_PLAYLISTS:
            raise CollectionError("playlist_limit", "At most %d playlists can be stored." % MAX_PLAYLISTS)
        records.insert(0, record)
        data["playlists"] = records
        _write(PLAYLISTS_FILE, data)
    return _playlist_public(record)


def rename_playlist(playlist_id, name):
    name = _clean_name(name, "Playlist")
    with _lock:
        data = _read(PLAYLISTS_FILE, "playlists")
        record = _find(data.get("playlists") or [], playlist_id, "playlist")
        record["name"] = name
        record["updated_at"] = time.time()
        record["updated_iso"] = config.now_iso()
        _write(PLAYLISTS_FILE, data)
    return _playlist_public(record)


def delete_playlist(playlist_id):
    with _lock:
        data = _read(PLAYLISTS_FILE, "playlists")
        records = data.get("playlists") or []
        record = _find(records, playlist_id, "playlist")
        data["playlists"] = [x for x in records if x.get("id") != playlist_id]
        _write(PLAYLISTS_FILE, data)
    return {"deleted": record.get("id")}


def add_to_playlist(playlist_id, media_id):
    try:
        media = studio_media.get(media_id)
    except studio_media.MediaError:
        raise CollectionError("media_not_found", "Media not found.", 404)
    summary = _media_summary(media)
    if not summary:
        raise CollectionError("media_not_found", "Media not found.", 404)
    with _lock:
        data = _read(PLAYLISTS_FILE, "playlists")
        record = _find(data.get("playlists") or [], playlist_id, "playlist")
        items = record.get("items") or []
        if any(item.get("media_id") == media_id for item in items):
            return _playlist_public(record)
        if len(items) >= MAX_PLAYLIST_ITEMS:
            raise CollectionError("playlist_full", "A playlist holds at most %d items." % MAX_PLAYLIST_ITEMS)
        items.insert(0, {
            "media_id": media_id,
            "added_at": time.time(),
            "added_iso": config.now_iso(),
        })
        record["items"] = items
        record["updated_at"] = time.time()
        record["updated_iso"] = config.now_iso()
        _write(PLAYLISTS_FILE, data)
    return _playlist_public(record)


def remove_from_playlist(playlist_id, media_id):
    with _lock:
        data = _read(PLAYLISTS_FILE, "playlists")
        record = _find(data.get("playlists") or [], playlist_id, "playlist")
        record["items"] = [item for item in (record.get("items") or []) if item.get("media_id") != media_id]
        record["updated_at"] = time.time()
        record["updated_iso"] = config.now_iso()
        _write(PLAYLISTS_FILE, data)
    return _playlist_public(record)


def forget_media(media_id):
    """Remove a deleted media id from every playlist (idempotent)."""
    with _lock:
        if not os.path.isfile(PLAYLISTS_FILE):
            return
        data = _read(PLAYLISTS_FILE, "playlists")
        changed = False
        for record in data.get("playlists") or []:
            before = record.get("items") or []
            after = [item for item in before if item.get("media_id") != media_id]
            if len(after) != len(before):
                record["items"] = after
                record["updated_at"] = time.time()
                record["updated_iso"] = config.now_iso()
                changed = True
        if changed:
            _write(PLAYLISTS_FILE, data)


def list_templates():
    with _lock:
        data = _read(TEMPLATES_FILE, "templates")
        records = list(data.get("templates") or [])
    records.sort(key=lambda x: x.get("updated_at") or 0, reverse=True)
    return [copy.deepcopy(record) for record in records]


def save_template(name, provider, model_id, model_label, params, summary=None):
    name = _clean_name(name, "Template")
    if not isinstance(provider, str) or not provider.strip():
        raise CollectionError("invalid_template", "A provider is required.")
    if not isinstance(model_id, str) or not model_id.strip():
        raise CollectionError("invalid_template", "A model is required.")
    if not isinstance(params, dict):
        raise CollectionError("invalid_template", "Template parameters must be an object.")
    now = time.time()
    record = {
        "id": "tpl_" + uuid.uuid4().hex[:16],
        "name": name,
        "provider": provider.strip(),
        "model_id": model_id.strip(),
        "model_label": model_label or model_id,
        "params": copy.deepcopy(params),
        "summary": copy.deepcopy(summary) if isinstance(summary, dict) else {},
        "created_at": now,
        "created_iso": config.now_iso(),
        "updated_at": now,
        "updated_iso": config.now_iso(),
    }
    with _lock:
        data = _read(TEMPLATES_FILE, "templates")
        records = data.get("templates") or []
        if len(records) >= MAX_TEMPLATES:
            raise CollectionError("template_limit", "At most %d templates can be stored." % MAX_TEMPLATES)
        records.insert(0, record)
        data["templates"] = records
        _write(TEMPLATES_FILE, data)
    return copy.deepcopy(record)


def delete_template(template_id):
    with _lock:
        data = _read(TEMPLATES_FILE, "templates")
        records = data.get("templates") or []
        record = _find(records, template_id, "template")
        data["templates"] = [x for x in records if x.get("id") != template_id]
        _write(TEMPLATES_FILE, data)
    return {"deleted": record.get("id")}
