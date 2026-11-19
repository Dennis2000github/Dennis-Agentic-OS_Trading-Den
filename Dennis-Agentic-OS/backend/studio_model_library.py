"""Persistent Studio Fashion model library.

Stores the owner's own preset-model entries next to the other Studio
collections:

    data/studio/model-library.json

Two kinds of state live in the file:

* ``models``         — model photos the owner uploaded or generated with an
                       image model ("Create Model"). Each entry keeps the
                       public asset URL plus where it came from.
* ``hidden_presets`` — catalog preset ids the owner deleted from the picker.
                       The catalog file itself is read-only synced data, so a
                       delete is stored as a hide.

References only, never provider credentials. Writes are atomic and owner-only
(0600), matching the playlists/templates stores.
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
LIBRARY_FILE = os.path.join(STUDIO_DIR, "model-library.json")

MAX_MODELS = 80
MAX_HIDDEN = 400
NAME_LIMIT = 120
PROMPT_LIMIT = 2000
_SOURCES = ("upload", "url", "generated")
#: Library slots: "model" feeds Step 1, "product" Step 2, "dressed" Step 3.
_KINDS = ("model", "product", "dressed")

_lock = threading.RLock()


class LibraryError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def _ensure_dir():
    os.makedirs(STUDIO_DIR, mode=0o700, exist_ok=True)


def _read():
    if not os.path.isfile(LIBRARY_FILE):
        return {"models": [], "hidden_presets": []}
    try:
        with open(LIBRARY_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {"models": [], "hidden_presets": []}
    if not isinstance(data, dict):
        return {"models": [], "hidden_presets": []}
    models = data.get("models")
    hidden = data.get("hidden_presets")
    return {
        "models": models if isinstance(models, list) else [],
        "hidden_presets": hidden if isinstance(hidden, list) else [],
    }


def _write(payload):
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=STUDIO_DIR, prefix=".model-library-", suffix=".json")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, LIBRARY_FILE)
        os.chmod(LIBRARY_FILE, 0o600)
    except Exception as exc:  # pragma: no cover - disk failure path
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise LibraryError("library_unwritable", "Could not save the model library: %s" % exc, 500)


def _clean_url(value):
    url = str(value or "").strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise LibraryError("invalid_url", "Model images need a public http(s) URL.")
    if len(url) > 2048:
        raise LibraryError("invalid_url", "The image URL is too long.")
    return url


def _clean_name(value, url):
    name = str(value or "").strip()[:NAME_LIMIT]
    if not name:
        tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        name = tail or "Model"
    return name


def public_model(entry):
    return {
        "id": entry.get("id"),
        "url": entry.get("url"),
        "name": entry.get("name"),
        "source": entry.get("source") or "upload",
        "prompt": entry.get("prompt") or "",
        "kind": entry.get("kind") or "model",
        "created_at": entry.get("created_at"),
    }


def list_library():
    """Return the owner library plus the hidden catalog preset ids."""
    with _lock:
        data = _read()
    return {
        "custom": [public_model(m) for m in data["models"]],
        "hidden_presets": [str(p) for p in data["hidden_presets"]],
    }


def add_model(url, name=None, source="upload", prompt="", kind="model"):
    if source not in _SOURCES:
        source = "upload"
    if kind not in _KINDS:
        kind = "model"
    with _lock:
        data = _read()
        clean_url = _clean_url(url)
        for entry in data["models"]:
            if entry.get("url") == clean_url:
                return public_model(entry)  # already saved
        if len(data["models"]) >= MAX_MODELS:
            raise LibraryError(
                "library_full",
                "The model library holds %d images; delete one first." % MAX_MODELS,
            )
        entry = {
            "id": "um_" + uuid.uuid4().hex[:12],
            "url": clean_url,
            "name": _clean_name(name, clean_url),
            "source": source,
            "prompt": str(prompt or "").strip()[:PROMPT_LIMIT],
            "kind": kind,
            "created_at": time.time(),
        }
        data["models"].insert(0, entry)
        _write(data)
    return public_model(entry)


def delete_model(model_id):
    with _lock:
        data = _read()
        before = len(data["models"])
        data["models"] = [m for m in data["models"] if m.get("id") != model_id]
        if len(data["models"]) == before:
            raise LibraryError("model_not_found", "That model is not in the library.", 404)
        _write(data)
    return {"deleted": model_id}


def set_preset_hidden(preset_id, hidden=True):
    preset_id = str(preset_id or "").strip()
    if not preset_id:
        raise LibraryError("missing_preset", "A preset id is required.")
    with _lock:
        data = _read()
        hidden_list = [str(p) for p in data["hidden_presets"]]
        if hidden:
            if preset_id not in hidden_list:
                if len(hidden_list) >= MAX_HIDDEN:
                    raise LibraryError("hidden_full", "Too many hidden presets.")
                hidden_list.append(preset_id)
        else:
            hidden_list = [p for p in hidden_list if p != preset_id]
        data["hidden_presets"] = hidden_list
        _write(data)
    return {"preset_id": preset_id, "hidden": bool(hidden)}
