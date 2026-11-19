"""API Mega List repository workspace.

The Plugins tab is not a second API catalog. It is a browser for the real
``cporter202/API-mega-list`` repository: the README that its pipeline generates,
the category READMEs, the pipeline scripts, and any other text file in the
snapshot. Nothing here re-indexes or republishes the list.

``public-plugins/api-mega-list/repo`` is a snapshot of the upstream repository.
``SNAPSHOT.json`` records the commit and sync time. The only write operation is
an explicit sync, which downloads the upstream tarball, validates every member,
and atomically replaces the snapshot. The repository's own generated files are
the data source for the workspace.
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import PurePosixPath

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK_DIR = os.path.join(REPO_ROOT, "public-plugins", "api-mega-list")
REPO_DIR = os.path.join(PACK_DIR, "repo")
SNAPSHOT_FILE = os.path.join(PACK_DIR, "SNAPSHOT.json")

SOURCE = "https://github.com/cporter202/API-mega-list"
SOURCE_GIT = "https://github.com/cporter202/API-mega-list.git"
BRANCH = "main"
TARBALL_URL = "https://codeload.github.com/cporter202/API-mega-list/tar.gz/refs/heads/main"

MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 96 * 1024 * 1024

TEXT_EXTS = {
    ".md", ".markdown", ".txt", ".js", ".cjs", ".mjs", ".json", ".yml", ".yaml",
    ".css", ".html", ".htm", ".svg", ".xml", ".csv", ".log",
}
ASSET_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf"}
CATEGORY_RE = re.compile(r"^(?P<slug>.+)-apis-(?P<count>\d+)$")

PIPELINE = [
    {
        "id": "fetch",
        "name": "Fetch Apify actors",
        "path": "settings/fetch_apify_actors.js",
        "description": "Pulls the public Apify store into apify_actors.json (the raw pipeline input).",
    },
    {
        "id": "generate",
        "name": "Generate READMEs",
        "path": "settings/generate_readme_clean.js",
        "description": "Rebuilds README.md and every category README from apify_actors.json.",
    },
    {
        "id": "validate",
        "name": "Validate partner collections",
        "path": "settings/validate_partner_collections.js",
        "description": "Checks the sponsored / partner collection entries before publishing.",
    },
]

_lock = threading.RLock()
_stats_cache = {"mtime": None, "data": None}


class MegalistError(Exception):
    def __init__(self, message, code="megalist_error", status=400):
        super().__init__(message)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Snapshot metadata
# ---------------------------------------------------------------------------
def _read_snapshot():
    try:
        with open(SNAPSHOT_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _write_snapshot(data):
    os.makedirs(os.path.dirname(SNAPSHOT_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SNAPSHOT_FILE), prefix=".snapshot-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, SNAPSHOT_FILE)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _repo_stats():
    files = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(REPO_DIR):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        for name in filenames:
            files += 1
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                pass
    return files, total


def _require_repo():
    if not os.path.isdir(REPO_DIR) or not os.path.isfile(os.path.join(REPO_DIR, "README.md")):
        raise MegalistError(
            "The API Mega List repository snapshot is missing. Sync it from GitHub.",
            "repo_missing", 503,
        )


# ---------------------------------------------------------------------------
# Path handling
# ---------------------------------------------------------------------------
def _parts(path):
    raw = str(path or "").strip().replace("\\", "/")
    if raw in ("", "."):
        return []
    if raw.startswith("/"):
        raise MegalistError("Absolute paths are not allowed.", "path_invalid", 400)
    parts = []
    for part in PurePosixPath(raw).parts:
        if part in ("", ".", ".."):
            if part == "..":
                raise MegalistError("Path escapes the repository.", "path_invalid", 400)
            continue
        parts.append(part)
    return parts


def _resolve(path, allow_dir=False):
    _require_repo()
    parts = _parts(path)
    target = os.path.realpath(os.path.join(REPO_DIR, *parts))
    root = os.path.realpath(REPO_DIR)
    if target != root and not target.startswith(root + os.sep):
        raise MegalistError("Path escapes the repository.", "path_invalid", 400)
    if not os.path.exists(target):
        raise MegalistError("File not found in the repository.", "not_found", 404)
    if os.path.isdir(target):
        if not allow_dir:
            raise MegalistError("That path is a directory.", "not_a_file", 400)
        readme = os.path.join(target, "README.md")
        if not os.path.isfile(readme):
            raise MegalistError("That directory has no README.md.", "readme_missing", 404)
        target = readme
        parts = parts + ["README.md"]
    return target, "/".join(parts)


def _kind(path):
    ext = os.path.splitext(path)[1].lower()
    return "markdown" if ext in (".md", ".markdown") else "text"


def _heading(path):
    if _kind(path) != "markdown":
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for _ in range(80):
                line = fh.readline()
                if not line:
                    break
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return ""


def _category_label(slug):
    known = {
        "ai": "AI",
        "mcp-servers": "MCP Servers",
        "seo-tools": "SEO Tools",
        "developer-tools": "Developer Tools",
        "lead-generation": "Lead Generation",
        "real-estate": "Real Estate",
        "social-media": "Social Media",
        "open-source": "Open Source",
        "for-creators": "For Creators",
        "ecommerce": "Ecommerce",
    }
    if slug in known:
        return known[slug]
    return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-") if word)


def _categories():
    _require_repo()
    out = []
    try:
        names = os.listdir(REPO_DIR)
    except OSError as exc:
        raise MegalistError("Cannot read the repository snapshot: %s" % exc, "repo_unreadable", 503)
    for name in names:
        match = CATEGORY_RE.match(name)
        if not match or name == "00-featured-apis":
            continue
        full = os.path.join(REPO_DIR, name)
        if not os.path.isdir(full):
            continue
        readme = os.path.join(full, "README.md")
        out.append({
            "id": name,
            "slug": match.group("slug"),
            "name": _category_label(match.group("slug")),
            "count": int(match.group("count")),
            "path": name,
            "readme": name + "/README.md" if os.path.isfile(readme) else "",
        })
    out.sort(key=lambda item: item["name"].casefold())
    return out


def _file_node(rel, label=None, kind=None):
    full, normalized = _resolve(rel)
    size = os.path.getsize(full)
    return {
        "type": "file",
        "path": normalized,
        "name": os.path.basename(normalized),
        "label": label or os.path.basename(normalized),
        "kind": kind or _kind(full),
        "size": size,
        "title": _heading(full),
    }


def _dir_node(rel, label=None, children=None):
    parts = _parts(rel)
    if not parts:
        raise MegalistError("A directory path is required.", "path_invalid", 400)
    normalized = "/".join(parts)
    full_dir = os.path.realpath(os.path.join(REPO_DIR, *parts))
    root = os.path.realpath(REPO_DIR)
    if full_dir != root and not full_dir.startswith(root + os.sep):
        raise MegalistError("Path escapes the repository.", "path_invalid", 400)
    readme = os.path.join(full_dir, "README.md")
    if not os.path.isfile(readme):
        raise MegalistError("That directory has no README.md.", "readme_missing", 404)
    name = parts[-1]
    count = 0
    match = CATEGORY_RE.match(name)
    if match:
        count = int(match.group("count"))
    node = {
        "type": "dir",
        "path": normalized,
        "name": name,
        "label": label or _category_label(name.rsplit("-apis-", 1)[0]),
        "kind": "markdown",
        "count": count,
        "readme": normalized + "/README.md",
        "title": _heading(readme),
    }
    if children:
        node["children"] = children
    return node


def _pipeline_nodes():
    nodes = []
    for item in PIPELINE:
        rel = item["path"]
        if os.path.isfile(os.path.join(REPO_DIR, rel)):
            node = _file_node(rel)
            node["label"] = item["name"]
            node["pipeline"] = item["id"]
            node["description"] = item["description"]
            nodes.append(node)
    docs = "settings/APIFY_ACTORS.md"
    if os.path.isfile(os.path.join(REPO_DIR, docs)):
        node = _file_node(docs, label="Pipeline notes")
        node["pipeline"] = "docs"
        nodes.append(node)
    return nodes


def _featured_count():
    root = os.path.join(REPO_DIR, "00-featured-apis")
    if not os.path.isdir(root):
        return 0
    count = 0
    try:
        for name in os.listdir(root):
            if os.path.isdir(os.path.join(root, name)) and os.path.isfile(os.path.join(root, name, "README.md")):
                count += 1
    except OSError:
        return 0
    return count


def _featured_node():
    rel = "00-featured-apis"
    if not os.path.isdir(os.path.join(REPO_DIR, rel)):
        return None
    node = _dir_node(rel, label="Featured APIs")
    node["count"] = _featured_count()
    return node


# ---------------------------------------------------------------------------
# Public read API
# ---------------------------------------------------------------------------
def status():
    _require_repo()
    snapshot = _read_snapshot()
    stats = _readme_stats()
    categories = _categories()
    files = snapshot.get("files")
    size = snapshot.get("bytes")
    if not isinstance(files, int) or not isinstance(size, int):
        files, size = _repo_stats()
    return {
        "source": SOURCE,
        "source_git": SOURCE_GIT,
        "branch": BRANCH,
        "commit": snapshot.get("commit") or "",
        "synced_at": snapshot.get("synced_at") or "",
        "api_count": stats["api_count"],
        "sponsored_count": stats["sponsored_count"],
        "category_count": stats["category_count"],
        "core_category_count": len(categories),
        "featured_count": stats["featured_count"],
        "last_updated": stats["last_updated"],
        "description": stats["description"],
        "files": files,
        "bytes": size,
        "categories": categories,
        "pipeline": PIPELINE,
        "repo_ready": True,
    }


def categories():
    cats = _categories()
    return {"categories": cats, "count": len(cats)}


def tree():
    _require_repo()
    repository = []
    for rel, label in (("README.md", "Overview"), ("FOLLOW_CREATOR.md", "Follow the creator"),
                       ("SPONSORED_PARTNERS.md", "Sponsored partners")):
        if os.path.isfile(os.path.join(REPO_DIR, rel)):
            repository.append(_file_node(rel, label=label))
    sections = [
        {"id": "repository", "label": "Repository", "items": repository},
    ]
    featured = _featured_node()
    if featured:
        sections.append({"id": "featured", "label": "Featured", "items": [featured]})
    sections.append({
        "id": "categories",
        "label": "Core categories",
        "items": [_dir_node(cat["path"], label=cat["name"]) for cat in _categories() if cat.get("readme")],
    })
    pipeline = _pipeline_nodes()
    if pipeline:
        sections.append({"id": "pipeline", "label": "Repository pipeline", "items": pipeline})
    return {
        "sections": sections,
        "pipeline": PIPELINE,
        "source": SOURCE,
        "commit": _read_snapshot().get("commit") or "",
    }


def read(path="README.md"):
    _require_repo()
    full, normalized = _resolve(path or "README.md", allow_dir=True)
    if os.path.splitext(full)[1].lower() not in TEXT_EXTS:
        raise MegalistError("That file is not a text file.", "not_text", 415)
    size = os.path.getsize(full)
    if size > MAX_TEXT_BYTES:
        raise MegalistError("That file is too large to display.", "file_too_large", 413)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        raise MegalistError("Cannot read that file: %s" % exc, "read_failed", 500)
    if "\x00" in text[:4096]:
        raise MegalistError("That file is binary.", "not_text", 415)
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    return {
        "path": normalized,
        "name": os.path.basename(normalized),
        "title": _heading(full) or os.path.basename(normalized),
        "kind": _kind(full),
        "size": size,
        "lines": lines,
        "text": text,
        "modified": int(os.path.getmtime(full)),
        "github_url": "%s/blob/%s/%s" % (SOURCE, BRANCH, normalized),
    }


def asset(path):
    _require_repo()
    full, normalized = _resolve(path)
    ext = os.path.splitext(full)[1].lower()
    if ext not in ASSET_EXTS:
        raise MegalistError("Unsupported asset type.", "asset_invalid", 415)
    size = os.path.getsize(full)
    if size > MAX_ASSET_BYTES:
        raise MegalistError("That asset is too large.", "asset_too_large", 413)
    try:
        with open(full, "rb") as fh:
            body = fh.read()
    except OSError as exc:
        raise MegalistError("Cannot read that asset: %s" % exc, "read_failed", 500)
    return body, mimetypes.guess_type(full)[0] or "application/octet-stream", normalized


def _readme_stats():
    path = os.path.join(REPO_DIR, "README.md")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _stats_defaults()
    with _lock:
        if _stats_cache["mtime"] == mtime and _stats_cache["data"]:
            return dict(_stats_cache["data"])
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return _stats_defaults()

    def number(pattern, default=0):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return default
        try:
            return int(match.group(1).replace(",", ""))
        except (TypeError, ValueError):
            return default

    description = "The most comprehensive collection of APIs on GitHub."
    match = re.search(r"\*\*(.+?)\*\*", text)
    if match:
        candidate = re.sub(r"\s+", " ", match.group(1)).strip()
        if 20 <= len(candidate) <= 240:
            description = candidate

    featured = _featured_count()

    data = {
        "api_count": number(r"Core directory APIs\**\s*\|\s*\**([\d,]+)"),
        "sponsored_count": number(r"Sponsored partner listings\**\s*\|\s*\**([\d,]+)"),
        "category_count": number(r"Categories\**\s*\|\s*\**([\d,]+)"),
        "last_updated": "",
        "featured_count": featured,
        "description": description,
    }
    updated = re.search(r"Last Updated\**\s*\|\s*([0-9][0-9-]*)", text)
    if updated:
        data["last_updated"] = updated.group(1)
    if not data["api_count"]:
        data["api_count"] = 0
    if not data["category_count"]:
        data["category_count"] = len(_categories()) + 1
    with _lock:
        _stats_cache["mtime"] = mtime
        _stats_cache["data"] = data
    return dict(data)


def _stats_defaults():
    return {
        "api_count": 0,
        "sponsored_count": 0,
        "category_count": 0,
        "last_updated": "",
        "featured_count": 0,
        "description": "The most comprehensive collection of APIs on GitHub.",
    }


# ---------------------------------------------------------------------------
# Sync the real upstream repository
# ---------------------------------------------------------------------------
def _remote_commit():
    git = shutil.which("git")
    if not git:
        return ""
    try:
        proc = subprocess.run(
            [git, "ls-remote", SOURCE_GIT, "refs/heads/%s" % BRANCH],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        return ""
    commit = line[0].split()[0]
    return commit if re.fullmatch(r"[0-9a-f]{40}", commit) else ""


def _download():
    request = urllib.request.Request(
        TARBALL_URL,
        headers={"User-Agent": "DENNIS-OS-API-Mega-List/1.0", "Accept": "application/x-gzip"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read(MAX_DOWNLOAD_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise MegalistError("GitHub returned HTTP %s while syncing." % exc.code, "sync_http_error", 502)
    except urllib.error.URLError as exc:
        raise MegalistError("Cannot reach GitHub: %s" % exc.reason, "sync_unreachable", 502)
    except TimeoutError:
        raise MegalistError("GitHub did not respond in time.", "sync_timeout", 504)
    if len(body) > MAX_DOWNLOAD_BYTES:
        raise MegalistError("The upstream archive is larger than the sync limit.", "sync_too_large", 413)
    return body


def _extract(body, dest):
    try:
        archive = tarfile.open(fileobj=io.BytesIO(body), mode="r:gz")
    except tarfile.TarError as exc:
        raise MegalistError("GitHub returned an invalid archive: %s" % exc, "sync_bad_archive", 502)
    with archive:
        members = archive.getmembers()
        if not members:
            raise MegalistError("The upstream archive is empty.", "sync_bad_archive", 502)
        for member in members:
            parts = _parts(member.name)
            if not parts:
                continue
            if not (member.isfile() or member.isdir()):
                raise MegalistError("The upstream archive contains a link or special file.", "sync_unsafe", 502)
            target = os.path.join(dest, *parts)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise MegalistError("The upstream archive contains an unreadable file.", "sync_bad_archive", 502)
            with source, open(target, "wb") as fh:
                shutil.copyfileobj(source, fh)


def _replace_repo(new_repo):
    backup = REPO_DIR + ".previous"
    if os.path.exists(backup):
        shutil.rmtree(backup)
    if os.path.exists(REPO_DIR):
        os.rename(REPO_DIR, backup)
    try:
        os.rename(new_repo, REPO_DIR)
    except Exception:
        if os.path.exists(backup):
            os.rename(backup, REPO_DIR)
        raise
    if os.path.exists(backup):
        shutil.rmtree(backup)


def sync():
    """Download the upstream repository, then stage a validated catalog index.

    The new index is parsed and validated *before* the healthy snapshot is
    replaced, so a broken or incomplete upstream cannot wipe the workspace.
    The old snapshot and old index stay in place if parsing or validation
    fails.
    """
    from . import megalist_catalog

    with _lock:
        started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        body = _download()
        tmp = tempfile.mkdtemp(prefix="dennis-megalist-sync-")
        try:
            _extract(body, tmp)
            roots = [item for item in os.listdir(tmp) if os.path.isdir(os.path.join(tmp, item))]
            if len(roots) != 1 or not roots[0].startswith("API-mega-list-"):
                raise MegalistError("The upstream archive has an unexpected layout.", "sync_bad_archive", 502)
            new_repo = os.path.join(tmp, roots[0])
            if not os.path.isfile(os.path.join(new_repo, "README.md")):
                raise MegalistError("The upstream archive has no README.md.", "sync_bad_archive", 502)
            categories_found = [
                name for name in os.listdir(new_repo)
                if CATEGORY_RE.match(name) and os.path.isdir(os.path.join(new_repo, name))
            ]
            if not categories_found:
                raise MegalistError("The upstream archive has no category folders.", "sync_bad_archive", 502)

            commit = _remote_commit() or _read_snapshot().get("commit") or ""
            synced_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            previous = None
            try:
                previous = megalist_catalog.read_index()
            except megalist_catalog.CatalogError:
                previous = None
            index = megalist_catalog.parse_repository(
                new_repo, commit=commit, synced_at=synced_at)
            megalist_catalog._validate_index(index, previous)
            index["import"] = megalist_catalog._diff_counts(previous, index)
            index["import"].update({
                "trigger": "sync",
                "started_at": started,
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            })

            _replace_repo(new_repo)
            files, size = _repo_stats()
            snapshot = {
                "source": SOURCE,
                "source_git": SOURCE_GIT,
                "branch": BRANCH,
                "commit": commit,
                "synced_at": synced_at,
                "files": files,
                "bytes": size,
            }
            _write_snapshot(snapshot)
            megalist_catalog.write_index(index)
        except MegalistError as exc:
            megalist_catalog.record_history(
                "failed", None, "sync", "%s (%s)" % (exc, getattr(exc, "code", "")))
            raise
        except megalist_catalog.CatalogError as exc:
            megalist_catalog.record_history(
                "failed", None, "sync", "%s (%s)" % (exc, exc.code))
            raise MegalistError(str(exc), exc.code, exc.status)
        except Exception as exc:
            megalist_catalog.record_history("failed", None, "sync", str(exc))
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        with _lock:
            _stats_cache.update({"mtime": None, "data": None})
        megalist_catalog.record_history("success", index, "sync")
        return {
            "synced": True,
            "snapshot": snapshot,
            "import": index.get("import") or {},
            "counts": index.get("counts") or {},
            "status": status(),
        }
