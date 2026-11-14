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


def next_version(name):
    record = get(name) or {}
    versions = [item.get("version") for item in record.get("versions") or []
                if isinstance(item, dict)]
    major = 1
    minor = 0
    for value in versions:
        try:
            parts = str(value).split(".")
            major = max(major, int(parts[0]))
            minor = max(minor, int(parts[1]) if len(parts) > 1 else 0)
        except (TypeError, ValueError):
            continue
    if not versions:
        return "1.0.0"
    return "%d.%d.0" % (major, minor + 1)


def register(name, path, draft_id="", sources=None, files=None, version=None,
             replaced=False):
    """Record a verified publication; never mutates the Vault itself."""
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
        skills[name] = record
        _save(data)
        return record


def _source_hash(sources):
    parts = []
    for item in sources or []:
        if isinstance(item, dict):
            parts.append(str(item.get("version") or item.get("id") or item.get("url") or ""))
    return "|".join(parts)[:500]
