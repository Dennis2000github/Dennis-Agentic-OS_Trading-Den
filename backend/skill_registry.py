"""DENNIS Skill Registry.

The Vault stays a plain Obsidian folder.  Version history, provenance and
publication records live in ``data/skill_registry.json`` so no non-Markdown
metadata leaks into the Vault.
"""

import os
import threading
import time

from . import config

REGISTRY_FILE = os.path.join(config.DATA_DIR, "skill_registry.json")
_LOCK = threading.RLock()


def _load():
    data = config.read_json(REGISTRY_FILE, {}) or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    skills = data.get("skills")
    if not isinstance(skills, dict):
        data["skills"] = {}
    return data


def _save(data):
    config.write_json(REGISTRY_FILE, data)


def list_skills():
    with _LOCK:
        data = _load()
        items = []
        for name, record in data.get("skills", {}).items():
            if not isinstance(record, dict):
                continue
            items.append({
                "name": name,
                "path": record.get("path"),
                "current_version": record.get("current_version"),
                "published_at": record.get("published_at"),
                "versions": len(record.get("versions") or []),
                "sources": record.get("sources") or [],
            })
        return {"count": len(items),
                "skills": sorted(items, key=lambda item: item.get("name") or "")}


def get(name):
    with _LOCK:
        return (_load().get("skills") or {}).get(name)


def record(name):
    """Full registry record incl. the additive ``meta`` block (Phase 2).

    v1 records carry only name/path/version/sources; callers get sensible
    defaults so no migration is needed.
    """
    raw = get(name)
    if not isinstance(raw, dict):
        return None
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    return {
        "name": name,
        "path": raw.get("path"),
        "current_version": raw.get("current_version"),
        "published_at": raw.get("published_at"),
        "versions": len(raw.get("versions") or []),
        "sources": raw.get("sources") or [],
        "meta": meta,
    }


def set_meta(name, meta):
    """Attach/replace the additive metadata block for a registered skill."""
    if not name:
        raise ValueError("name is required")
    if not isinstance(meta, dict):
        raise ValueError("meta must be a dict")
    with _LOCK:
        data = _load()
        skills = data.setdefault("skills", {})
        record = skills.get(name)
        if not isinstance(record, dict):
            record = skills[name] = {"name": name}
        record["meta"] = meta
        _save(data)
        return record


def set_enabled(name, enabled):
    """Enable/disable a skill. Disabling blocks new invocations only;
    the canonical Vault source and the registration are kept."""
    if not name:
        raise ValueError("name is required")
    with _LOCK:
        data = _load()
        record = (data.get("skills") or {}).get(name)
        if not isinstance(record, dict):
            raise KeyError("skill '%s' is not registered" % name)
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        meta["enabled"] = bool(enabled)
        meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        record["meta"] = meta
        _save(data)
        return meta


def remove(name):
    """Unregister a skill. NEVER deletes the canonical Vault source."""
    if not name:
        raise ValueError("name is required")
    with _LOCK:
        data = _load()
        skills = data.get("skills") or {}
        record = skills.pop(name, None)
        _save(data)
        return {"removed": bool(record), "name": name,
                "vault_source_kept": bool(record and record.get("path"))}


def touch(name):
    """Refresh a skill's validation stamp without changing its version."""
    if not name:
        raise ValueError("name is required")
    with _LOCK:
        data = _load()
        record = (data.get("skills") or {}).get(name)
        if not isinstance(record, dict):
            raise KeyError("skill '%s' is not registered" % name)
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        meta["last_validated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        meta["validation_status"] = "validated"
        record["meta"] = meta
        _save(data)
        return meta


def _version_parts(value):
    """Best-effort ``(major, minor)`` from any stored version string."""
    try:
        parts = str(value).split(".")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError, IndexError):
        return None


def next_version(name):
    record = get(name) or {}
    versions = [item.get("version") for item in record.get("versions") or []
                if isinstance(item, dict)]
    major = 1
    minor = 0
    for value in versions:
        parts = _version_parts(value)
        if parts:
            major = max(major, parts[0])
            minor = max(minor, parts[1])
    if not versions:
        return "1.0.0"
    return "%d.%d.0" % (major, minor + 1)


def register(name, path, draft_id="", sources=None, files=None, version=None,
             replaced=False, meta=None):
    """Record a verified publication; never mutates the Vault itself.

    ``meta`` (optional) is the additive Phase-2 metadata block: capability
    tags, I/O spec, runtime deps, compatible agents, execution requirements,
    validation status, enable state. v1 records simply have no meta block.
    """
    if not name:
        raise ValueError("name is required")
    with _LOCK:
        data = _load()
        skills = data.setdefault("skills", {})
        record = skills.get(name) if isinstance(skills.get(name), dict) else {}
        versions = record.get("versions") if isinstance(record.get("versions"), list) else []
        version = version or next_version(name)
        versions.append({
            "version": version,
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "draft_id": draft_id or "",
            "files": int(files or 1),
            "replaced": bool(replaced),
            "source_hash": _source_hash(sources),
        })
        record.update({
            "name": name,
            "path": path,
            "current_version": version,
            "published_at": versions[-1]["published_at"],
            "sources": sources or record.get("sources") or [],
            "versions": versions[-50:],
        })
        if isinstance(meta, dict):
            merged = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            merged.update(meta)
            merged.setdefault("enabled", True)
            merged.setdefault("validation_status", "unvalidated")
            record["meta"] = merged
        skills[name] = record
        _save(data)
        return record


def _source_hash(sources):
    parts = []
    for item in sources or []:
        if isinstance(item, dict):
            parts.append(str(item.get("version") or item.get("id") or item.get("url") or ""))
    return "|".join(parts)[:500]
