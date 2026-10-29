"""Obsidian-compatible knowledge vault for the OS "Vault" view.

The vault is a plain folder of Markdown notes. It is deliberately
Obsidian-compatible:

* notes are ``.md`` files (any folder layout / nested folders),
* ``[[Wikilinks]]`` and relative Markdown links ``[text](Note.md)`` are
  understood and turned into a link graph,
* deletes are soft (moved to a hidden ``.trash/`` folder), like Obsidian.

Everything is dependency-free and confined to :data:`VAULT_DIR`; paths that
escape it are rejected. Writes are atomic.
"""

import os
import re
import tempfile
import time

from .config import DIRECTORY

# Overridable so the vault can live outside the repo if desired.
VAULT_DIR = os.path.abspath(
    os.environ.get("DENNIS_VAULT_DIR", os.path.join(DIRECTORY, "vault"))
)
TRASH_DIR_NAME = ".trash"
NOTE_EXTENSIONS = (".md", ".markdown")
MAX_NOTE_BYTES = 2 * 1024 * 1024

_WIKILINK_RE = re.compile(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")
_MDLINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+?)\)")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_TITLE_RE = re.compile(r"^title\s*:\s*(.+)$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


class VaultError(Exception):
    """Raised for invalid vault operations (bad path, missing note, ...)."""


# ---------------------------------------------------------------------------
# Paths / setup
# ---------------------------------------------------------------------------
def ensure_vault():
    os.makedirs(VAULT_DIR, exist_ok=True)
    trash = os.path.join(VAULT_DIR, TRASH_DIR_NAME)
    os.makedirs(trash, exist_ok=True)
    if not any(_is_note(n) for n in os.listdir(VAULT_DIR)) and not _has_notes(VAULT_DIR):
        _seed()
    return VAULT_DIR


def _has_notes(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if any(_is_note(f) for f in filenames):
            return True
    return False


def _seed():
    welcome = os.path.join(VAULT_DIR, "Welcome.md")
    body = (
        "# Welcome to the Vault\n\n"
        "This is your **Obsidian-compatible** knowledge base, served by DENNIS "
        "Agentic OS. Notes are plain Markdown files stored in `vault/`.\n\n"
        "## Try this\n\n"
        "- Create a note with the **New note** button.\n"
        "- Link notes with `[[Wikilinks]]` — open [[Ideas]] to see one.\n"
        "- Use the **Graph** tab to visualise how notes connect.\n"
        "- Everything you write stays on disk as `.md` and opens in Obsidian too.\n\n"
        "See also: [[Ideas]] and [[Projects]].\n"
    )
    _atomic_write(welcome, body)
    _atomic_write(
        os.path.join(VAULT_DIR, "Ideas.md"),
        "# Ideas\n\nA scratchpad. Back to [[Welcome]].\n",
    )
    _atomic_write(
        os.path.join(VAULT_DIR, "Projects.md"),
        "# Projects\n\n- Trading Den\n- Agentic OS\n\nSee [[Welcome]].\n",
    )


def is_note(name):
    return _is_note(name)


def _is_note(name):
    return name.lower().endswith(NOTE_EXTENSIONS)


def _root():
    """Canonical (symlink-resolved) vault root used for walking/relativising."""
    return os.path.realpath(VAULT_DIR)


def _norm_rel(rel):
    """Normalise a client-supplied relative path to a POSIX string."""
    rel = (rel or "").replace("\\", "/").strip()
    rel = rel.lstrip("/")
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise VaultError("Path escapes the vault")
    return "/".join(parts)


def resolve(rel, *, must_exist=False):
    """Resolve a vault-relative path to an absolute path, confined to VAULT_DIR."""
    rel = _norm_rel(rel)
    target = os.path.realpath(os.path.join(VAULT_DIR, rel))
    root = _root()
    if target != root and not target.startswith(root + os.sep):
        raise VaultError("Path escapes the vault")
    if must_exist and not os.path.exists(target):
        raise VaultError("Note not found")
    return target


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------
def _atomic_write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def note_title(path, text=None):
    """Best-effort note title: frontmatter title, first H1, else the file stem."""
    if text is None:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(4096)
        except OSError:
            text = ""
    m = _FRONTMATTER_RE.match(text)
    if m:
        t = _TITLE_RE.search(m.group(1))
        if t:
            return t.group(1).strip().strip('"').strip("'")
    h = _HEADING_RE.search(text)
    if h:
        return h.group(1).strip()
    return os.path.splitext(os.path.basename(path))[0]


def list_tree():
    """Nested tree of folders + notes (folders first, case-insensitive sort)."""
    ensure_vault()

    def walk(abs_dir, rel_dir):
        entries = []
        try:
            names = os.listdir(abs_dir)
        except OSError:
            return entries
        for name in names:
            if name.startswith("."):
                continue
            abs_child = os.path.join(abs_dir, name)
            rel_child = f"{rel_dir}/{name}" if rel_dir else name
            if os.path.isdir(abs_child):
                children = walk(abs_child, rel_child)
                entries.append({"type": "dir", "name": name, "path": rel_child,
                                "children": children})
            elif _is_note(name):
                entries.append({"type": "note", "name": name,
                                "title": note_title(abs_child), "path": rel_child})
        entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
        return entries

    return walk(_root(), "")


def read_note(rel):
    target = resolve(rel, must_exist=True)
    if not os.path.isfile(target) or not _is_note(target):
        raise VaultError("Not a note")
    size = os.path.getsize(target)
    if size > MAX_NOTE_BYTES:
        raise VaultError("Note is larger than 2 MB")
    with open(target, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()
    return {
        "path": _norm_rel(rel),
        "title": note_title(target, content),
        "content": content,
        "size": size,
        "mtime": os.path.getmtime(target),
        "links": extract_links(content),
    }


def write_note(rel, content):
    if content is None:
        content = ""
    if len(content.encode("utf-8")) > MAX_NOTE_BYTES:
        raise VaultError("Note is larger than 2 MB")
    rel = _norm_rel(rel)
    if not rel:
        raise VaultError("A note path is required")
    if not _is_note(rel):
        rel += ".md"
    target = resolve(rel)
    existed = os.path.isfile(target)
    _atomic_write(target, content)
    return {
        "path": rel,
        "title": note_title(target, content),
        "created": not existed,
        "mtime": os.path.getmtime(target),
    }


def create_note(rel=None, content=None):
    ensure_vault()
    rel = _norm_rel(rel)
    if not rel:
        rel = _unique_name("Untitled")
    if not _is_note(rel):
        rel += ".md"
    target = resolve(rel)
    if os.path.exists(target):
        raise VaultError("A note with that name already exists")
    if content is None:
        title = os.path.splitext(os.path.basename(rel))[0]
        content = f"# {title}\n\n"
    _atomic_write(target, content)
    return {"path": rel, "title": note_title(target, content), "created": True}


def delete_note(rel):
    target = resolve(rel, must_exist=True)
    if not os.path.isfile(target):
        raise VaultError("Not a note")
    trash = os.path.join(VAULT_DIR, TRASH_DIR_NAME)
    os.makedirs(trash, exist_ok=True)
    base = os.path.basename(target)
    dest = os.path.join(trash, base)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(base)
        dest = os.path.join(trash, f"{stem}-{int(time.time())}{ext}")
    os.replace(target, dest)
    return {"path": _norm_rel(rel), "trashed": os.path.relpath(dest, VAULT_DIR)}


def rename_note(old_rel, new_rel):
    src = resolve(old_rel, must_exist=True)
    if not os.path.isfile(src):
        raise VaultError("Not a note")
    new_rel = _norm_rel(new_rel)
    if not new_rel:
        raise VaultError("A new path is required")
    if not _is_note(new_rel):
        new_rel += ".md"
    dest = resolve(new_rel)
    if os.path.exists(dest):
        raise VaultError("A note with that name already exists")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.replace(src, dest)
    return {"from": _norm_rel(old_rel), "path": new_rel, "title": note_title(dest)}


def _unique_name(base):
    rel = f"{base}.md"
    i = 1
    while os.path.exists(resolve(rel)):
        i += 1
        rel = f"{base} {i}.md"
    return rel


# ---------------------------------------------------------------------------
# Links, search, graph
# ---------------------------------------------------------------------------
def extract_links(text):
    """Outgoing link targets (wikilinks + markdown links), de-duplicated."""
    targets = []
    seen = set()
    for m in _WIKILINK_RE.finditer(text or ""):
        target = m.group(1).strip()
        key = target.lower()
        if key and key not in seen:
            seen.add(key)
            targets.append(target)
    for m in _MDLINK_RE.finditer(text or ""):
        href = m.group(1).strip()
        if href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        target = href.split("#", 1)[0]
        if target.lower().endswith(NOTE_EXTENSIONS):
            target = os.path.splitext(target)[0]
            key = target.lower()
            if key and key not in seen:
                seen.add(key)
                targets.append(target)
    return targets


def _index_notes():
    """Return (notes, index) where index maps lowercased title/stem -> rel path."""
    notes = []
    index = {}
    for dirpath, dirnames, filenames in os.walk(_root()):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not _is_note(name):
                continue
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, _root()).replace(os.sep, "/")
            stem = os.path.splitext(name)[0]
            title = note_title(abs_path)
            notes.append({"rel": rel, "abs": abs_path, "stem": stem, "title": title})
            index.setdefault(stem.lower(), rel)
            index.setdefault(title.lower(), rel)
            index.setdefault(rel.lower(), rel)
    return notes, index


def graph():
    ensure_vault()
    notes, index = _index_notes()
    nodes = []
    links = []
    known = {n["rel"] for n in notes}
    for n in notes:
        try:
            with open(n["abs"], "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = ""
        out = []
        for target in extract_links(text):
            dest = index.get(target.lower())
            if not dest:
                dest = index.get((target.lower() + ".md"))
            if dest and dest in known and dest != n["rel"]:
                out.append(dest)
        nodes.append({
            "id": n["rel"],
            "title": n["title"],
            "stem": n["stem"],
            "links": len(out),
        })
        for dest in set(out):
            links.append({"source": n["rel"], "target": dest})
    return {"nodes": nodes, "links": links, "vault": VAULT_DIR}


def backlinks(rel):
    rel = _norm_rel(rel)
    stem = os.path.splitext(os.path.basename(rel))[0].lower()
    notes, _ = _index_notes()
    out = []
    for n in notes:
        if n["rel"] == rel:
            continue
        try:
            with open(n["abs"], "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for target in extract_links(text):
            if target.lower() in (stem, n["rel"].lower()):
                out.append({"path": n["rel"], "title": n["title"]})
                break
    return out


def search(query, limit=50):
    ensure_vault()
    q = (query or "").strip()
    if not q:
        return []
    needle = q.lower()
    results = []
    for dirpath, dirnames, filenames in os.walk(_root()):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not _is_note(name):
                continue
            abs_path = os.path.join(dirpath, name)
            rel = os.path.relpath(abs_path, _root()).replace(os.sep, "/")
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            low = text.lower()
            if needle not in low and needle not in name.lower():
                continue
            count = low.count(needle)
            snippet = ""
            pos = low.find(needle)
            if pos >= 0:
                start = max(0, pos - 40)
                snippet = text[start:pos + len(needle) + 60].replace("\n", " ").strip()
            results.append({
                "path": rel,
                "title": note_title(abs_path, text),
                "score": count + (5 if needle in name.lower() else 0),
                "snippet": snippet,
            })
    results.sort(key=lambda r: (-r["score"], r["title"].lower()))
    return results[:limit]
