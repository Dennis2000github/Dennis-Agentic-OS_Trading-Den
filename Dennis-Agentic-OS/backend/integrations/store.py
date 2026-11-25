"""Shared storage primitives for the integrations package.

Every integration store lives under ``data/integrations/`` and follows the
OS-wide rules (see ``backend/cosmos_common.py``):

* JSON files, atomic writes, owner-only permissions (0600, dir 0700);
* additive, versioned schemas with lazy defaults (no destructive migration);
* one process-wide lock for the threaded HTTP server;
* bounded payloads and sanitized text.

Kept dependency-free (only ``backend.config``) so provider adapters can
import it without import cycles.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid

from .. import config

INTEGRATIONS_DIR = os.path.join(config.DATA_DIR, "integrations")
SCHEMA_VERSION = 1

_lock = threading.RLock()

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class IntegrationError(Exception):
    """A user-facing integration failure with a stable code and HTTP status."""

    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def as_error(self):
        return {"code": self.code, "message": self.message}


def lock():
    return _lock


def ensure_dir():
    os.makedirs(INTEGRATIONS_DIR, mode=0o700, exist_ok=True)


def now():
    return time.time()


def iso(value=None):
    return time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(value if value is not None else now())
    )


def new_id(prefix):
    return prefix + uuid.uuid4().hex[:16]


def clean_text(value, label, limit, required=False):
    """Strip control characters and bound length; raises on empty when required."""
    text = _CONTROL.sub("", "" if value is None else str(value)).strip()
    if required and not text:
        raise IntegrationError("invalid_field", "%s is required." % label)
    if len(text) > limit:
        raise IntegrationError(
            "invalid_field", "%s must be %d characters or fewer." % (label, limit)
        )
    return text


def read_json(path, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise IntegrationError(
            "store_unreadable", "Could not read %s: %s" % (os.path.basename(path), exc), 500
        )


def write_json(path, payload):
    """Atomic 0600 write so a crash cannot leave a half-written store."""
    directory = os.path.dirname(path) or INTEGRATIONS_DIR
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".intx-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_store(path, key):
    """Read a versioned list store; a missing file lazily seeds an empty list."""
    data = read_json(path, {})
    if not data:
        return []
    if data.get("version") != SCHEMA_VERSION:
        raise IntegrationError(
            "store_version", "%s uses an unsupported version." % os.path.basename(path), 500
        )
    items = data.get(key)
    if not isinstance(items, list):
        raise IntegrationError(
            "store_invalid", "%s has an unsupported format." % os.path.basename(path), 500
        )
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def save_store(path, key, items):
    with lock():
        write_json(
            path,
            {"version": SCHEMA_VERSION, key: items, "updated_at": now(), "updated_iso": iso()},
        )
