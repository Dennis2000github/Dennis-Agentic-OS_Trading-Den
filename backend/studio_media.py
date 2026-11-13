"""Studio media library.

Generated outputs are copied out of the provider's CDN because provider URLs
are retained for a limited time. Inputs uploaded for a request are also stored
so the UI can show what was sent.

All remote fetches go through the same public-host SSRF guard used by the Skill
Builder; redirects are re-validated. Downloads are capped and written
atomically. Files are served back only by opaque media id, never by path.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import threading
import time
import urllib.error
import uuid

from . import config
from . import skill_sources
from . import studio_contracts as contracts

STUDIO_DIR = os.path.join(config.DATA_DIR, "studio")
MEDIA_DIR = os.path.join(STUDIO_DIR, "media")
INDEX_FILE = os.path.join(STUDIO_DIR, "media.json")

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_ITEMS = 400
CHUNK = 64 * 1024

ALLOWED_CONTENT_TYPES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/quicktime", "video/webm",
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp4", "audio/ogg",
    "application/zip",
}

_lock = threading.RLock()


class MediaError(Exception):
    pass


def _ensure_dirs():
    os.makedirs(MEDIA_DIR, mode=0o700, exist_ok=True)
    os.makedirs(STUDIO_DIR, mode=0o700, exist_ok=True)


def _read_index():
    if not os.path.isfile(INDEX_FILE):
        return {"version": 1, "items": []}
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise MediaError("Media index cannot be read: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise MediaError("Media index has an unsupported format.")
    return data


def _write_index(data):
    _ensure_dirs()
    fd, tmp = tempfile.mkstemp(dir=STUDIO_DIR, prefix=".media-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, INDEX_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _public(record):
    return dict(record)


def _kind_from_content_type(content_type):
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("audio/"):
        return "audio"
    return "file"


def _new_id():
    return "med_" + uuid.uuid4().hex[:16]


def _path_for(record):
    name = record.get("filename") or (record["id"] + ".bin")
    path = os.path.join(MEDIA_DIR, name)
    if os.path.dirname(os.path.abspath(path)) != os.path.abspath(MEDIA_DIR):
        raise MediaError("Invalid media path.")
    return path


def _prune(items):
    if len(items) <= MAX_ITEMS:
        return items
    outputs = [x for x in items if x.get("role", "output") == "output"]
    inputs = [x for x in items if x.get("role") == "input"]
    keep = sorted(outputs, key=lambda x: x.get("created_at", 0), reverse=True)[:MAX_ITEMS]
    removed = [x for x in items if x not in keep]
    for record in removed:
        if record.get("role") == "input":
            continue
        try:
            os.remove(_path_for(record))
        except OSError:
            pass
    # Keep recent inputs in the index but never let them evict outputs.
    return keep + sorted(inputs, key=lambda x: x.get("created_at", 0), reverse=True)[:50]


def _insert(record):
    with _lock:
        data = _read_index()
        items = data.get("items") or []
        items.insert(0, record)
        data["items"] = _prune(items)
        _write_index(data)
    return _public(record)


def _commit_bytes(data, content_type, kind, filename, meta):
    """Write ``data`` and register a media record. Caller supplies metadata."""
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise MediaError("Unsupported media type: %s" % (content_type or "unknown"))
    if not data:
        raise MediaError("The media file is empty.")
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise MediaError("The media file is larger than the 200 MB limit.")

    media_id = _new_id()
    safe = contracts.sanitize_filename(filename, fallback=media_id)
    ext = contracts.extension_for(content_type, kind)
    if not os.path.splitext(safe)[1]:
        safe += ext
    if not safe.startswith(media_id):
        safe = media_id + "-" + safe
    path = os.path.join(MEDIA_DIR, safe)

    _ensure_dirs()
    fd, tmp = tempfile.mkstemp(dir=MEDIA_DIR, prefix=".dl-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    now = time.time()
    record = {
        "id": media_id,
        "kind": kind,
        "role": meta.get("role", "output"),
        "title": meta.get("title") or safe,
        "filename": safe,
        "content_type": content_type,
        "size": len(data),
        "created_at": now,
        "created_iso": config.now_iso(),
        "provider": meta.get("provider"),
        "model_id": meta.get("model_id"),
        "job_id": meta.get("job_id"),
        "source_url": meta.get("source_url"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "duration": meta.get("duration"),
    }
    return _insert(record)


def add_bytes(data, content_type, kind=None, filename="upload", meta=None):
    kind = kind or _kind_from_content_type(content_type)
    return _commit_bytes(data, content_type, kind, filename, meta or {})


def add_from_url(url, kind=None, meta=None):
    """Download a provider output into the library. Returns the record.

    Re-downloading the same output URL for the same job is idempotent.
    """
    try:
        safe_url = skill_sources.validate_public_url(url)
    except skill_sources.SourceError as exc:
        raise MediaError(str(exc))

    meta = dict(meta or {})
    with _lock:
        for record in _read_index().get("items") or []:
            if (
                record.get("source_url") == safe_url
                and record.get("job_id")
                and record.get("job_id") == meta.get("job_id")
            ):
                return _public(record)

    try:
        response = skill_sources.open_url(safe_url, timeout=60, headers={"Accept": "*/*"})
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise MediaError("Could not download the output: %s" % exc)

    try:
        status = getattr(response, "status", 200)
        if status < 200 or status >= 300:
            raise MediaError("The output URL returned HTTP %s." % status)
        header_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        data = bytearray()
        while True:
            chunk = response.read(CHUNK)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise MediaError("The media file is larger than the 200 MB limit.")
    finally:
        try:
            response.close()
        except Exception:
            pass

    content_type = header_type if header_type in ALLOWED_CONTENT_TYPES else ""
    if not content_type:
        guessed, _ = mimetypes.guess_type(safe_url.split("?", 1)[0])
        fallback = "video/mp4" if kind == "video" else "image/png"
        content_type = guessed or fallback
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise MediaError("The output has an unsupported media type: %s" % content_type)

    name = os.path.basename(re.sub(r"[/\\]", "/", safe_url.split("?", 1)[0])) or "output"
    return _commit_bytes(
        bytes(data),
        content_type,
        kind or _kind_from_content_type(content_type),
        name,
        dict(meta, source_url=safe_url),
    )


def list_items(kind=None, role="output", query=None, limit=60):
    with _lock:
        items = list(_read_index().get("items") or [])
    if kind:
        items = [x for x in items if x.get("kind") == kind]
    if role:
        items = [x for x in items if x.get("role", "output") == role]
    if query:
        needle = str(query).lower()
        items = [
            x for x in items
            if needle in ("%s %s" % (x.get("title", ""), x.get("model_id", ""))).lower()
        ]
    items.sort(key=lambda x: x.get("created_at", 0), reverse=True)
    return [_public(x) for x in items[: max(1, min(int(limit or 60), 200))]]


def get(media_id):
    with _lock:
        for record in _read_index().get("items") or []:
            if record.get("id") == media_id:
                return _public(record)
    raise MediaError("Media not found.")


def open_path(media_id):
    record = get(media_id)
    path = _path_for(record)
    if not os.path.isfile(path):
        raise MediaError("The media file is missing from the library.")
    return path, record


def delete(media_id):
    with _lock:
        data = _read_index()
        items = data.get("items") or []
        record = next((x for x in items if x.get("id") == media_id), None)
        if not record:
            raise MediaError("Media not found.")
        items = [x for x in items if x.get("id") != media_id]
        data["items"] = items
        _write_index(data)
    try:
        os.remove(_path_for(record))
    except OSError:
        pass
    return {"deleted": media_id}


def stats():
    with _lock:
        items = list(_read_index().get("items") or [])
    outputs = [x for x in items if x.get("role", "output") == "output"]
    return {
        "count": len(outputs),
        "bytes": sum(int(x.get("size") or 0) for x in outputs),
        "directory": MEDIA_DIR,
    }
