"""Owner-scoped saved APIs, notes, collections, readiness plans and settings.

The catalog itself is shared, read-only source data. Everything a user changes
lives here, under ``data/megalist/state.json`` (mode 0600), keyed by the owner
id issued by the existing auth layer. Saved entries store a fingerprint so the
UI can say whether the source row changed after it was saved.

Verification results are shared too (the same link is the same link for every
owner) and are kept separately in ``data/megalist/verification.json`` so a link
check never rewrites the catalog index.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import time

from . import config
from . import megalist_catalog

MEGALIST_DIR = os.path.join(config.DATA_DIR, "megalist")
STATE_FILE = os.path.join(MEGALIST_DIR, "state.json")
VERIFY_FILE = os.path.join(MEGALIST_DIR, "verification.json")

DEFAULT_SETTINGS = {
    "page_size": 24,
    "verify_timeout": 8,
    "verify_concurrency": 4,
    "verify_hosts": ["apify.com", "www.apify.com", "apimesa.com", "www.apimesa.com"],
    "show_sponsored": True,
}
MAX_NOTE = 4000
MAX_COLLECTION = 60
MAX_READINESS = 500

_lock = threading.RLock()


class StoreError(Exception):
    def __init__(self, message, code="state_error", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def _ensure_dir():
    os.makedirs(MEGALIST_DIR, mode=0o700, exist_ok=True)


def _read(path, fallback):
    if not os.path.isfile(path):
        return copy.deepcopy(fallback)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise StoreError("Cannot read %s: %s" % (os.path.basename(path), exc),
                         "state_unreadable", 500)
    if not isinstance(data, dict):
        raise StoreError("%s has an unsupported format." % os.path.basename(path),
                         "state_invalid", 500)
    return data


def _write(path, payload):
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or MEGALIST_DIR,
                               prefix=".megalist-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
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
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _owner_node(state, owner):
    owners = state.setdefault("owners", {})
    node = owners.setdefault(owner, {})
    if not isinstance(node, dict):
        node = {}
        owners[owner] = node
    node.setdefault("saved", {})
    node.setdefault("readiness", {})
    node.setdefault("settings", {})
    return node


def _clean_notes(value):
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > MAX_NOTE:
        raise StoreError("Notes are limited to %d characters." % MAX_NOTE, "note_too_long", 413)
    return text


def _clean_collection(value):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > MAX_COLLECTION:
        raise StoreError("Collection names are limited to %d characters." % MAX_COLLECTION,
                         "collection_invalid", 400)
    return text


def _saved(owner):
    state = _read(STATE_FILE, {"version": 1, "owners": {}})
    node = _owner_node(state, owner)
    saved = node.get("saved")
    return saved if isinstance(saved, dict) else {}


def saved_map(owner):
    """Return ``{entry_id: record}`` for the owner."""
    return copy.deepcopy(_saved(owner))


def is_saved(owner, entry_id):
    return entry_id in _saved(owner)


def set_saved(owner, entry, saved, collection=None):
    if not isinstance(entry, dict) or not entry.get("id"):
        raise StoreError("A catalog entry is required.", "entry_required", 400)
    with _lock:
        state = _read(STATE_FILE, {"version": 1, "owners": {}})
        node = _owner_node(state, owner)
        entries = node.setdefault("saved", {})
        entry_id = entry["id"]
        if saved:
            existing = entries.get(entry_id) or {}
            record = {
                "id": entry_id,
                "saved_at": existing.get("saved_at") or _now(),
                "updated_at": _now(),
                "fingerprint": entry.get("fingerprint") or "",
                "source_commit": entry.get("source_commit") or "",
                "collection": _clean_collection(
                    collection if collection is not None else existing.get("collection", "")
                ),
                "notes": existing.get("notes") or "",
                "name": entry.get("name") or "",
            }
            entries[entry_id] = record
        else:
            entries.pop(entry_id, None)
        _write(STATE_FILE, state)
        return {"saved": bool(saved), "id": entry_id, "entry": entries.get(entry_id)}


def set_notes(owner, entry, notes):
    if not isinstance(entry, dict) or not entry.get("id"):
        raise StoreError("A catalog entry is required.", "entry_required", 400)
    text = _clean_notes(notes)
    with _lock:
        state = _read(STATE_FILE, {"version": 1, "owners": {}})
        node = _owner_node(state, owner)
        entries = node.setdefault("saved", {})
        entry_id = entry["id"]
        existing = entries.get(entry_id)
        if not existing:
            # Adding a note is an explicit save, matching the "Add Notes" action.
            existing = {
                "id": entry_id,
                "saved_at": _now(),
                "fingerprint": entry.get("fingerprint") or "",
                "source_commit": entry.get("source_commit") or "",
                "collection": "",
                "name": entry.get("name") or "",
            }
        existing["notes"] = text
        existing["updated_at"] = _now()
        entries[entry_id] = existing
        _write(STATE_FILE, state)
        return copy.deepcopy(existing)


def set_collection(owner, entry, collection):
    if not isinstance(entry, dict) or not entry.get("id"):
        raise StoreError("A catalog entry is required.", "entry_required", 400)
    collection = _clean_collection(collection)
    with _lock:
        state = _read(STATE_FILE, {"version": 1, "owners": {}})
        node = _owner_node(state, owner)
        entries = node.setdefault("saved", {})
        record = entries.get(entry["id"])
        if not record:
            raise StoreError("Save the API before adding it to a collection.", "not_saved", 409)
        record["collection"] = collection
        record["updated_at"] = _now()
        _write(STATE_FILE, state)
        return copy.deepcopy(record)


def collections(owner):
    counts = {}
    for record in _saved(owner).values():
        name = str(record.get("collection") or "").strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: item[0].casefold())
    ]


def settings(owner):
    state = _read(STATE_FILE, {"version": 1, "owners": {}})
    node = _owner_node(state, owner)
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    stored = node.get("settings")
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in merged:
                merged[key] = value
    return merged


def update_settings(owner, patch):
    if not isinstance(patch, dict):
        raise StoreError("Settings must be an object.", "settings_invalid", 400)
    with _lock:
        state = _read(STATE_FILE, {"version": 1, "owners": {}})
        node = _owner_node(state, owner)
        current = node.setdefault("settings", {})
        for key in ("page_size", "verify_timeout", "verify_concurrency"):
            if key in patch:
                try:
                    number = int(patch[key])
                except (TypeError, ValueError):
                    raise StoreError("%s must be a number." % key, "settings_invalid", 400)
                limits = {"page_size": (10, 100), "verify_timeout": (3, 20),
                          "verify_concurrency": (1, 6)}[key]
                if not limits[0] <= number <= limits[1]:
                    raise StoreError(
                        "%s must be between %d and %d." % (key, limits[0], limits[1]),
                        "settings_invalid", 400,
                    )
                current[key] = number
        if "show_sponsored" in patch:
            current["show_sponsored"] = bool(patch["show_sponsored"])
        _write(STATE_FILE, state)
    return settings(owner)


# ---------------------------------------------------------------------------
# Integration readiness
# ---------------------------------------------------------------------------
def readiness_map(owner):
    state = _read(STATE_FILE, {"version": 1, "owners": {}})
    node = _owner_node(state, owner)
    records = node.get("readiness")
    return copy.deepcopy(records if isinstance(records, dict) else {})


def get_readiness(owner, entry_id):
    return readiness_map(owner).get(entry_id)


def prepare_readiness(owner, entry):
    if not isinstance(entry, dict) or not entry.get("id"):
        raise StoreError("A catalog entry is required.", "entry_required", 400)
    plan = megalist_catalog.readiness_plan(entry)
    with _lock:
        state = _read(STATE_FILE, {"version": 1, "owners": {}})
        node = _owner_node(state, owner)
        records = node.setdefault("readiness", {})
        if len(records) >= MAX_READINESS and entry["id"] not in records:
            raise StoreError(
                "The readiness workspace is limited to %d entries." % MAX_READINESS,
                "readiness_limit", 413,
            )
        existing = records.get(entry["id"]) or {}
        plan["id"] = entry["id"]
        plan["name"] = entry.get("name") or ""
        plan["created_at"] = existing.get("created_at") or _now()
        plan["updated_at"] = _now()
        plan["fingerprint"] = entry.get("fingerprint") or ""
        plan["source_commit"] = entry.get("source_commit") or ""
        records[entry["id"]] = plan
        _write(STATE_FILE, state)
        return copy.deepcopy(plan)


def update_readiness(owner, entry_id, patch):
    if not isinstance(patch, dict):
        raise StoreError("Readiness updates must be an object.", "readiness_invalid", 400)
    with _lock:
        state = _read(STATE_FILE, {"version": 1, "owners": {}})
        node = _owner_node(state, owner)
        records = node.setdefault("readiness", {})
        record = records.get(entry_id)
        if not record:
            raise StoreError("No readiness plan exists for that entry.", "not_found", 404)
        allowed = {"status": {"proposed", "approved", "blocked", "deferred"}}
        for key, values in allowed.items():
            if key in patch:
                value = str(patch[key] or "").strip().lower()
                if value not in values:
                    raise StoreError("Unknown readiness status.", "readiness_invalid", 400)
                record[key] = value
        if "note" in patch:
            record["note"] = _clean_notes(patch["note"])
        record["updated_at"] = _now()
        # Approval never creates a connection or marks anything tested.
        record.setdefault("connection", {
            "configured": False, "tested": False, "enabled": False, "endpoint": None,
        })
        _write(STATE_FILE, state)
        return copy.deepcopy(record)


def delete_readiness(owner, entry_id):
    with _lock:
        state = _read(STATE_FILE, {"version": 1, "owners": {}})
        node = _owner_node(state, owner)
        records = node.setdefault("readiness", {})
        existed = records.pop(entry_id, None)
        if existed is not None:
            _write(STATE_FILE, state)
        return {"deleted": existed is not None, "id": entry_id}


# ---------------------------------------------------------------------------
# Verification results (shared catalog data)
# ---------------------------------------------------------------------------
def verification_map():
    data = _read(VERIFY_FILE, {"version": 1, "records": {}})
    records = data.get("records")
    return copy.deepcopy(records if isinstance(records, dict) else {})


def verification_for(entry_id):
    return verification_map().get(entry_id)


def record_verification(entry, result):
    if not isinstance(entry, dict) or not entry.get("id"):
        raise StoreError("A catalog entry is required.", "entry_required", 400)
    with _lock:
        data = _read(VERIFY_FILE, {"version": 1, "records": {}})
        records = data.setdefault("records", {})
        previous = records.get(entry["id"]) or {}
        record = {
            "id": entry["id"],
            "status": result.get("status") or "needs_review",
            "http_status": result.get("http_status"),
            "checked_at": _now(),
            "method": result.get("method") or "",
            "final_url": result.get("final_url") or "",
            "error": str(result.get("error") or "")[:400],
            "commit": entry.get("source_commit") or "",
            "reported_broken": bool(result.get("reported_broken", previous.get("reported_broken", False))),
        }
        records[entry["id"]] = record
        _write(VERIFY_FILE, data)
        return record


def report_broken(owner, entry, reason=""):
    if not isinstance(entry, dict) or not entry.get("id"):
        raise StoreError("A catalog entry is required.", "entry_required", 400)
    reason = _clean_notes(reason)[:1000]
    with _lock:
        data = _read(VERIFY_FILE, {"version": 1, "records": {}})
        records = data.setdefault("records", {})
        previous = records.get(entry["id"]) or {}
        record = {
            "id": entry["id"],
            "status": "needs_review",
            "http_status": previous.get("http_status"),
            "checked_at": _now(),
            "method": previous.get("method") or "user_report",
            "final_url": previous.get("final_url") or "",
            "error": reason or "Reported broken by the owner.",
            "commit": entry.get("source_commit") or "",
            "reported_broken": True,
            "reported_by": owner,
        }
        records[entry["id"]] = record
        _write(VERIFY_FILE, data)
        return record


def stats(owner):
    state = _read(STATE_FILE, {"version": 1, "owners": {}})
    node = _owner_node(state, owner)
    return {
        "saved": len(node.get("saved") or {}),
        "readiness": len(node.get("readiness") or {}),
        "collections": len(collections(owner)),
    }
