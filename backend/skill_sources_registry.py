"""Canonical skill-source management (Phase 7 support).

One canonical checkout per shared skill, under
``data/skill-sources/<name>/``. Agent consumption is by symlink or by
instruction injection — never by copying the skill into each agent directory
(directive: "Do not install independent copies for each agent").

Fetching a source is a **network + untrusted-code** action: this module only
records what was fetched and where; the fetch itself is a guarded shell-out
the owner must allow (git clone / curl tarball). The examination step is
report-first: metadata is read as *data*, nothing from the source is executed
until the owner approves an execution.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time

from . import config

SOURCES_DIR = os.path.join(config.DATA_DIR, "skill-sources")
_STATE_FILE = os.path.join(SOURCES_DIR, "sources.json")
_LOCK = threading.RLock()

SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class SourceError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = int(status)
        super().__init__(message)


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load():
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    return data


def _save(data):
    os.makedirs(SOURCES_DIR, exist_ok=True)
    fd, tmp = _atomic_tmp(_STATE_FILE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, _STATE_FILE)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _atomic_tmp(path):
    return tempfile.mkstemp(dir=os.path.dirname(path) or SOURCES_DIR,
                            prefix=".tmp-", suffix=".json")


def ensure_source_dir():
    os.makedirs(SOURCES_DIR, exist_ok=True)
    return SOURCES_DIR


def canonical_path(name):
    """Where the canonical checkout of ``name`` lives."""
    return os.path.join(SOURCES_DIR, str(name).strip().lower())


def registered_sources():
    with _LOCK:
        data = _load()
    return dict(data.get("sources") or {})


def source_record(name):
    return registered_sources().get(str(name).strip().lower())


def register_source(name, source_url="", kind="git", note="", fetched=False,
                    meta=None):
    """Record a canonical source. Does NOT fetch anything by itself."""
    name = _clean(name)
    if not source_url:
        raise SourceError("source_url_required", "A source URL is required.")
    with _LOCK:
        data = _load()
        data["sources"][name] = {
            "name": name,
            "source_url": source_url,
            "kind": kind,
            "note": note,
            "fetched": bool(fetched),
            "canonical_path": canonical_path(name),
            "registered_at": _now(),
            "meta": meta or {},
        }
        _save(data)
    return data["sources"][name]


def mark_fetched(name, commit="", integrity=None):
    """Stamp a source as fetched (called after the owner-approved fetch)."""
    name = _clean(name)
    with _LOCK:
        data = _load()
        rec = data["sources"].get(name)
        if not rec:
            raise SourceError("not_registered", "Source '%s' is not registered." % name, 404)
        rec["fetched"] = True
        rec["fetched_at"] = _now()
        if commit:
            rec["commit"] = str(commit)[:64]
        if integrity:
            rec["integrity"] = integrity
        data["sources"][name] = rec
        _save(data)
    return rec


def integrity_of(path):
    """SHA-256 of the canonical tree (sorted file list + per-file hashes)."""
    root = os.path.realpath(path)
    if not os.path.isdir(root):
        raise SourceError("missing", "Canonical source missing: %s" % path, 404)
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, root)
            if rel.startswith(".git"):
                continue
            digest = hashlib.sha256()
            try:
                with open(full, "rb") as fh:
                    for chunk in iter(lambda: fh.read(65536), b""):
                        digest.update(chunk)
            except OSError:
                continue
            entries.append("%s:%s" % (rel, digest.hexdigest()))
    tree_hash = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()
    return {"files": len(entries), "sha256": tree_hash}


def remove_source(name, delete_checkout=False):
    """Unregister a source. The checkout is kept unless explicitly deleted."""
    name = _clean(name)
    with _LOCK:
        data = _load()
        rec = data["sources"].pop(name, None)
        _save(data)
    removed_checkout = False
    if rec and delete_checkout:
        path = rec.get("canonical_path") or canonical_path(name)
        try:
            shutil.rmtree(path)
            removed_checkout = True
        except OSError:
            removed_checkout = False
    return {"removed": bool(rec), "checkout_deleted": bool(removed_checkout),
            "canonical_path_kept": bool(rec) and not removed_checkout}


def _clean(name):
    name = str(name or "").strip().lower()
    if not SAFE_NAME.match(name):
        raise SourceError("bad_name", "Skill source name must be 1-64 chars of [a-z0-9._-].")
    return name


def install_for_agent(name, agent_id):
    """Symlink the canonical source into an agent's skill dir.

    This is the no-duplication contract: one canonical tree, N agent views.
    """
    from . import plugins as dennis_plugins
    rec = source_record(name)
    if not rec:
        raise SourceError("not_registered", "Source '%s' is not registered." % name, 404)
    canonical = rec.get("canonical_path") or canonical_path(name)
    if not os.path.isdir(canonical):
        raise SourceError("not_fetched",
                          "Canonical source '%s' is not fetched yet: %s" % (name, canonical), 409)
    agent_root = dennis_plugins.AGENT_SKILL_DIRS.get(agent_id)
    if not agent_root:
        raise SourceError("unknown_agent",
                          "Agent '%s' has no skill directory." % agent_id, 404)
    os.makedirs(agent_root, exist_ok=True)
    target = os.path.join(agent_root, name)
    if os.path.islink(target) or os.path.exists(target):
        try:
            if os.path.islink(target) and os.path.realpath(target) == os.path.realpath(canonical):
                return {"name": name, "agent_id": agent_id, "target": target,
                        "action": "already-linked", "canonical": canonical}
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.unlink(target)
        except OSError as exc:
            raise SourceError("link_failed", "Could not replace target: %s" % exc)
    try:
        os.symlink(canonical, target)
    except OSError as exc:
        raise SourceError("link_failed", "Could not link skill: %s" % exc)
    return {"name": name, "agent_id": agent_id, "target": target,
            "action": "linked", "canonical": canonical}


__all__ = [
    "SourceError", "SOURCES_DIR", "register_source", "mark_fetched",
    "source_record", "registered_sources", "canonical_path", "integrity_of",
    "remove_source", "install_for_agent", "ensure_source_dir",
]
