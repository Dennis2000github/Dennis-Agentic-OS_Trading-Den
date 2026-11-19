"""Allowed project roots and validated project folders.

Every working directory handed to an agent (and every path the Code editor or
Preview may touch) has to resolve inside an allowed root. Paths are compared
using ``os.path.realpath`` so ``..`` tricks and symlinks cannot escape.
"""

import os

from . import config

DEFAULT_PROJECTS = {
    "roots": [config.DIRECTORY],
    "projects": [
        {"name": "DENNIS Agentic OS", "path": config.DIRECTORY},
    ],
}


def _load():
    data = config.read_json(config.PROJECTS_FILE)
    if not isinstance(data, dict):
        config.write_json(config.PROJECTS_FILE, DEFAULT_PROJECTS)
        return dict(DEFAULT_PROJECTS)
    data.setdefault("roots", list(DEFAULT_PROJECTS["roots"]))
    data.setdefault("projects", list(DEFAULT_PROJECTS["projects"]))
    return data


def _save(data):
    config.write_json(config.PROJECTS_FILE, data)


def roots():
    """Allowed roots.

    ``DENNIS_ALLOWED_ROOTS`` (colon-separated) overrides the stored list so a
    deployment can restrict or widen access without editing JSON.
    """
    override = os.environ.get("DENNIS_ALLOWED_ROOTS")
    if override:
        raw = [r for r in override.split(os.pathsep) if r.strip()]
    else:
        raw = _load().get("roots", [])
    return [os.path.realpath(r) for r in raw]


def is_within_allowed_root(path):
    if not path:
        return False
    real = os.path.realpath(path)
    for root in roots():
        if real == root or real.startswith(root + os.sep):
            return True
    return False


def validate_path(path):
    """Return the canonical path when it is inside an allowed root and exists."""
    if not path:
        return None
    real = os.path.realpath(path)
    if not os.path.isdir(real):
        return None
    if not is_within_allowed_root(real):
        return None
    return real


# A folder only counts as a project when it looks like one, so caches and
# tooling directories do not pollute the project selector.
_PROJECT_MARKERS = (
    ".git", "package.json", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "go.mod", "README.md", "dennis.project.json",
)
_IGNORED_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "dist", "build"}


def _looks_like_project(path, entry):
    if entry in _IGNORED_DIRS:
        return False
    return any(os.path.exists(os.path.join(path, marker)) for marker in _PROJECT_MARKERS)


def _discover():
    """Top-level sub-folders of each allowed root that look like real projects."""
    found = {}
    for root in roots():
        try:
            for entry in os.listdir(root):
                full = os.path.join(root, entry)
                if entry.startswith(".") or not os.path.isdir(full):
                    continue
                if not _looks_like_project(full, entry):
                    continue
                found[os.path.realpath(full)] = {"name": entry, "path": os.path.realpath(full)}
        except OSError:
            continue
    return found


def list_projects():
    data = _load()
    result = {}
    for proj in data.get("projects", []):
        path = proj.get("path")
        if not path:
            continue
        real = os.path.realpath(path)
        if not os.path.isdir(real) or not is_within_allowed_root(real):
            continue
        result[real] = {"name": proj.get("name") or os.path.basename(real), "path": real}
    for real, proj in _discover().items():
        result.setdefault(real, proj)
    return sorted(result.values(), key=lambda p: p["name"].lower())


def resolve_project(identifier):
    """Resolve a project by name or path and return its validated real path."""
    if not identifier:
        return None
    for proj in list_projects():
        if identifier in (proj["name"], proj["path"]):
            return proj["path"]
    return validate_path(identifier)


def add_project(name, path):
    validated = validate_path(path)
    if not validated:
        raise ValueError("Project path is outside the allowed roots or does not exist")
    data = _load()
    projects = [p for p in data.get("projects", []) if os.path.realpath(p.get("path", "")) != validated]
    projects.append({"name": name or os.path.basename(validated), "path": validated})
    data["projects"] = projects
    _save(data)
    return {"name": name or os.path.basename(validated), "path": validated}
