"""Shared helpers for YouTube Studio domain stores.

Every YouTube store follows the same conventions as the rest of Studio:

* JSON under ``data/studio/youtube/`` with atomic ``0600`` writes;
* one lock per operation (the HTTP server is threaded);
* versioned files with lazy defaults, additive migrations;
* soft deletes - records are hidden, never silently destroyed;
* errors raise :class:`YoutubeError` with a stable ``code``/``status``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time

from . import config

YOUTUBE_DIR = os.path.join(config.DATA_DIR, "studio", "youtube")

_lock = threading.RLock()


class YoutubeError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def as_error(self):
        return {"code": self.code, "message": self.message}


def now():
    return time.time()


def iso(value=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(value if value is not None else now()))


def acquire():
    return _lock


def ensure_dir():
    os.makedirs(YOUTUBE_DIR, mode=0o700, exist_ok=True)


def read_store(path, key, schema_version=1):
    """Read a versioned store file. Missing file -> empty default."""
    if not os.path.isfile(path):
        return {"version": schema_version, key: []}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise YoutubeError("store_unreadable", "Could not read %s: %s" % (os.path.basename(path), exc), 500)
    if not isinstance(data, dict) or data.get("version") != schema_version:
        raise YoutubeError(
            "store_version", "%s uses an unsupported schema version." % os.path.basename(path), 500
        )
    items = data.get(key)
    if not isinstance(items, list):
        raise YoutubeError("store_invalid", "%s has an unsupported format." % os.path.basename(path), 500)
    return data


def write_store(path, key, items, extra=None):
    directory = os.path.dirname(path) or YOUTUBE_DIR
    ensure_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    payload = {"version": 1, key: items, "updated_at": now(), "updated_iso": iso()}
    if extra:
        payload.update(extra)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".yt-", suffix=".json")
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


def clean_text(value, field, limit, required=False):
    text = "" if value is None else str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).strip()
    if required and not text:
        raise YoutubeError("invalid_input", "%s is required." % field)
    if len(text) > limit:
        raise YoutubeError("invalid_input", "%s must be %d characters or fewer." % (field, limit))
    return text


def clean_str_list(value, field, max_items=20, item_limit=200):
    if value is None:
        return []
    if not isinstance(value, list):
        raise YoutubeError("invalid_input", "%s must be a list." % field)
    if len(value) > max_items:
        raise YoutubeError("invalid_input", "%s accepts at most %d items." % (field, max_items))
    items = []
    for entry in value:
        text = clean_text(entry, field, item_limit)
        if text and text not in items:
            items.append(text)
    return items


def clean_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def new_id(prefix):
    import uuid

    return prefix + uuid.uuid4().hex[:16]


def public(record):
    """Deep copy for JSON responses (records are plain JSON data)."""
    return json.loads(json.dumps(record))


def find(items, item_id):
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def soft_delete(record):
    stamp = now()
    record["deleted_at"] = stamp
    record["deleted_iso"] = iso(stamp)
    record["updated_at"] = stamp
    record["updated_iso"] = record["deleted_iso"]
