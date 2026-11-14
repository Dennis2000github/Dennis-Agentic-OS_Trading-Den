"""Shared primitives for the COSMOS multi-agent intelligence layer.

Every COSMOS store lives under ``data/cosmos/`` and follows the same rules as
the rest of the OS backend:

* JSON files, atomic writes, owner-only permissions (0600);
* additive, versioned schemas with lazy defaults (no destructive migration);
* one process-wide lock per store operation for the threaded HTTP server;
* bounded payloads and sanitized text;
* no fabricated discovery or analytics data.

This module is dependency-free so every other COSMOS module can import it
without creating cycles.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid

from . import config

COSMOS_DIR = os.path.join(config.DATA_DIR, "cosmos")
CONFIG_FILE = os.path.join(COSMOS_DIR, "config.json")
EXECUTIONS_FILE = os.path.join(COSMOS_DIR, "executions.json")
RECOMMENDATIONS_FILE = os.path.join(COSMOS_DIR, "recommendations.json")
ADS_BACKLOG_FILE = os.path.join(COSMOS_DIR, "ads", "backlog.json")

SCHEMA_VERSION = 1
MAX_EXECUTIONS = 600
MAX_RECOMMENDATIONS = 1500
MAX_RESULT_BYTES = 120 * 1024

DIVISIONS = ("ads", "youtube")

_lock = threading.RLock()


class CosmosError(Exception):
    """A user-facing COSMOS failure with a stable code and HTTP status."""

    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def as_error(self):
        return {"code": self.code, "message": self.message}


# ---------------------------------------------------------------------------
# Time / ids
# ---------------------------------------------------------------------------
def now():
    return time.time()


def iso(value=None):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(value if value is not None else now()))


def new_id(prefix):
    return prefix + uuid.uuid4().hex[:16]


def acquire():
    return _lock


# ---------------------------------------------------------------------------
# Text / JSON sanitizing
# ---------------------------------------------------------------------------
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_text(value, label, limit, required=False):
    text = "" if value is None else str(value)
    text = _CONTROL.sub("", text).strip()
    if required and not text:
        raise CosmosError("invalid_field", "%s is required." % label)
    if len(text) > limit:
        raise CosmosError("invalid_field", "%s must be %d characters or fewer." % (label, limit))
    return text


def clean_str_list(value, label, limit=20, item_limit=300):
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        raise CosmosError("invalid_field", "%s must be a list." % label)
    if len(value) > limit:
        raise CosmosError("invalid_field", "%s can have at most %d entries." % (label, limit))
    result = []
    for item in value:
        text = clean_text(item, label, item_limit)
        if text and text not in result:
            result.append(text)
    return result


def clamp_int(value, minimum, maximum, fallback=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        if fallback is not None:
            return fallback
        raise CosmosError("invalid_field", "Expected an integer.")
    return max(minimum, min(maximum, number))


def clamp_float(value, minimum, maximum, fallback=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        if fallback is not None:
            return fallback
        raise CosmosError("invalid_field", "Expected a number.")
    return max(minimum, min(maximum, number))


def public(value):
    """Deep copy through JSON so callers cannot mutate stored records."""
    return json.loads(json.dumps(value))


def hash_text(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Store IO
# ---------------------------------------------------------------------------
def ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)


def read_json(path, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CosmosError("store_unreadable", "Could not read %s: %s" % (os.path.basename(path), exc), 500)
    return data


def write_json(path, payload):
    ensure_dir(os.path.dirname(path) or COSMOS_DIR)
    directory = os.path.dirname(path) or COSMOS_DIR
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".cosmos-", suffix=".json")
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
    data = read_json(path, {})
    if not data:
        return []
    if data.get("version") != SCHEMA_VERSION:
        raise CosmosError("store_version", "%s uses an unsupported version." % os.path.basename(path), 500)
    items = data.get(key)
    if not isinstance(items, list):
        raise CosmosError("store_invalid", "%s has an unsupported format." % os.path.basename(path), 500)
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def save_store(path, key, items):
    write_json(path, {"version": SCHEMA_VERSION, key: items, "updated_at": now(), "updated_iso": iso()})


# ---------------------------------------------------------------------------
# Model output parsing
# ---------------------------------------------------------------------------
def extract_json(text):
    """Parse the first JSON object/array in a model response.

    Models occasionally wrap valid JSON in markdown fences or add a short
    preamble. We only accept real JSON — never eval, never guess.
    """
    raw = str(text or "").strip()
    if not raw:
        raise CosmosError("invalid_model_output", "The model returned no content.", 502)
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except (TypeError, ValueError):
                continue
    raise CosmosError(
        "invalid_model_output",
        "The model did not return valid JSON. Re-run the task or switch the model.",
        502,
    )


def normalize_key(text):
    text = str(text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(token for token in text.split() if token)
