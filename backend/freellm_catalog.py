"""Structured catalog for the awesome-free-llm-apis repository.

The upstream project publishes its dataset as machine-generated Markdown
tables inside ``README.md`` (each table is wrapped in HTML comment markers).
This module turns those tables into the JSON shape the FreeLLMAPI dashboard
renders. It only reads ``README.md``; repository code is never executed.

The OS ships a snapshot under ``public-plugins/free-llm-apis``. A local
checkout of the upstream repository is used when the snapshot is absent, and
``DENNIS_FREELLM_REPO`` can point at any checkout explicitly.
"""

import html
import json
import os
import re
import threading

from . import config

SOURCE = "https://github.com/open-free-llm-api/awesome-freellm-apis"
SITE = "https://freellm.net"
SNAPSHOT_DIR = os.path.join(config.DIRECTORY, "public-plugins", "free-llm-apis")

_MARKERS = {
    "permanent": ("BEGIN_PERMANENT_FREE", "END_PERMANENT_FREE"),
    "renewable": ("BEGIN_RENEWABLE", "END_RENEWABLE"),
    "quick": ("BEGIN_QUICK_REF", "END_QUICK_REF"),
    "best": ("BEGIN_BEST_MODELS", "END_BEST_MODELS"),
    "top": ("BEGIN_TOP_MODELS", "END_TOP_MODELS"),
}

_CACHE = {"path": None, "mtime": None, "data": None}
_LOCK = threading.RLock()


class FreeLLMCatalogError(ValueError):
    pass


def candidate_paths():
    """Directories that may contain the upstream README, in priority order."""
    paths = []
    override = os.environ.get("DENNIS_FREELLM_REPO", "").strip()
    if override:
        paths.append(override)
    paths.append(SNAPSHOT_DIR)
    parent = os.path.dirname(config.DIRECTORY)
    paths.append(os.path.join(parent, "awesome-freellm-apis"))
    paths.append(os.path.join(parent, "awesome-free-llm-apis"))
    return paths


def find_repo():
    for path in candidate_paths():
        if path and os.path.isfile(os.path.join(path, "README.md")):
            return os.path.abspath(path)
    return None


def _block(text, name):
    begin, end = _MARKERS[name]
    pattern = re.compile(
        r"<!--\s*" + begin + r"\s*-->(.*?)<!--\s*" + end + r"\s*-->",
        re.S | re.I,
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


def _clean(cell):
    value = re.sub(r"<[^>]+>", "", cell or "")
    value = html.unescape(value).replace("`", "")
    return re.sub(r"\s+", " ", value).strip()


def _link(cell):
    match = re.search(r'<a\s+[^>]*href="([^"]*)"[^>]*>(.*?)</a>', cell or "", re.I | re.S)
    if match:
        href = html.unescape(match.group(1)).strip()
        label = _clean(match.group(2))
        return label, href
    return _clean(cell), ""


def _rows(block):
    rows = []
    for line in (block or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{2,}:?", cell or "") for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return []
    return rows[1:]


def _int(value):
    match = re.search(r"\d[\d,]*", value or "")
    return int(match.group(0).replace(",", "")) if match else 0


def _context_tokens(value):
    raw = (value or "").strip().upper()
    if not raw or raw in ("—", "-", "0"):
        return 0
    match = re.fullmatch(r"([\d.]+)\s*([KMB]?)", raw)
    if not match:
        return 0
    number = float(match.group(1))
    unit = match.group(2)
    factor = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[unit]
    return int(number * factor)


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _modalities(value):
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _provider(providers, name):
    name = (name or "").strip()
    if not name:
        return None
    key = _slug(name)
    if key not in providers:
        providers[key] = {
            "id": key,
            "name": name,
            "tier": "permanent",
            "free_models": 0,
            "credit_card": "",
            "max_context": "",
            "context_tokens": 0,
            "modalities": [],
            "key_url": "",
            "base_url": "",
            "models": [],
        }
    return providers[key]


def _read_readme(path):
    with open(os.path.join(path, "README.md"), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _snapshot(path):
    try:
        with open(os.path.join(path, "SNAPSHOT.json"), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def parse(text, path=""):
    providers = {}
    models = []
    top_models = []

    for tier in ("permanent", "renewable"):
        for row in _rows(_block(text, tier)):
            if len(row) < 6:
                continue
            item = _provider(providers, _clean(row[0]))
            if not item:
                continue
            item["tier"] = tier
            item["free_models"] = _int(row[1])
            item["credit_card"] = _clean(row[2])
            item["max_context"] = _clean(row[3])
            item["context_tokens"] = _context_tokens(row[3])
            item["modalities"] = _modalities(_clean(row[4]))
            _label, href = _link(row[5])
            if href:
                item["key_url"] = href

    for row in _rows(_block(text, "quick")):
        if len(row) < 4:
            continue
        item = _provider(providers, _clean(row[0]))
        if not item:
            continue
        item["base_url"] = _clean(row[1])
        _label, href = _link(row[2])
        if href:
            item["key_url"] = href
        if not item["credit_card"]:
            item["credit_card"] = _clean(row[3])

    current = None
    for row in _rows(_block(text, "best")):
        if len(row) < 5:
            continue
        name = _clean(row[0])
        if name:
            current = _provider(providers, name)
        if not current:
            continue
        model_name, href = _link(row[1])
        model_id = _clean(row[2])
        if not model_id:
            continue
        model = {
            "name": model_name or model_id,
            "id": model_id,
            "context": _clean(row[3]),
            "context_tokens": _context_tokens(row[3]),
            "rate_limit": _clean(row[4]),
            "url": href,
            "provider_id": current["id"],
            "provider": current["name"],
        }
        current["models"].append(model)
        models.append(model)

    for row in _rows(_block(text, "top")):
        if len(row) < 4:
            continue
        name, href = _link(row[0])
        if not name:
            continue
        top_models.append({
            "name": name,
            "url": href,
            "provider": _clean(row[1]),
            "context": _clean(row[2]),
            "usage": _clean(row[3]),
        })

    for item in providers.values():
        if not item["max_context"]:
            contexts = [model["context_tokens"] for model in item["models"] if model["context_tokens"]]
            if contexts:
                item["max_context"] = item["models"][0]["context"]
                item["context_tokens"] = max(contexts)

    ordered = sorted(
        providers.values(),
        key=lambda item: (-item["free_models"], item["name"].lower()),
    )

    updated = ""
    match = re.search(r"Last updated:\s*(\d{4}-\d{2}-\d{2})", text)
    if match:
        updated = match.group(1)

    no_card = sum(1 for item in ordered if item["credit_card"].lower() in ("no", ""))
    modalities = sorted({value for item in ordered for value in item["modalities"]})
    model_total = sum(item["free_models"] for item in ordered)

    return {
        "ok": bool(ordered),
        "source": SOURCE,
        "site": SITE,
        "repo_path": path,
        "repo_ready": bool(ordered),
        "repo_name": os.path.basename(path) if path else "",
        "repo_kind": "snapshot" if path and os.path.abspath(path) == os.path.abspath(SNAPSHOT_DIR) else "checkout",
        "updated": updated,
        "counts": {
            "providers": len(ordered),
            "models": model_total,
            "listed_models": len(models),
            "no_card_providers": no_card,
            "top_models": len(top_models),
            "modalities": len(modalities),
        },
        "modalities": modalities,
        "providers": ordered,
        "models": models,
        "top_models": top_models,
        "snapshot": _snapshot(path) if path else {},
    }


def catalog(force=False):
    """Return the parsed catalog, re-reading README.md when it changes."""
    path = find_repo()
    if not path:
        return {
            "ok": False,
            "source": SOURCE,
            "site": SITE,
            "repo_ready": False,
            "error": "The awesome-free-llm-apis README was not found.",
            "counts": {},
            "providers": [],
            "models": [],
            "top_models": [],
        }
    readme = os.path.join(path, "README.md")
    try:
        mtime = os.stat(readme).st_mtime_ns
    except OSError as exc:
        raise FreeLLMCatalogError("The FreeLLMAPI catalog could not be read.") from exc
    with _LOCK:
        if (not force and _CACHE["data"] is not None
                and _CACHE["path"] == path and _CACHE["mtime"] == mtime):
            return _CACHE["data"]
        try:
            text = _read_readme(path)
        except OSError as exc:
            raise FreeLLMCatalogError("The FreeLLMAPI catalog could not be read.") from exc
        data = parse(text, path)
        _CACHE.update({"path": path, "mtime": mtime, "data": data})
        return data
