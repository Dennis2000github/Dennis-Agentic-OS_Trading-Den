"""Read-only access to the pinned Studio provider catalog.

The JSON snapshot in ``backend/catalog/`` is the only source of model metadata
at runtime. Loading is cached; the file is validated once so a corrupt catalog
fails loudly at startup rather than producing half-working models.
"""

from __future__ import annotations

import os
import threading

from . import studio_contracts as contracts

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")
DEFAULT_PROVIDER = "higgsfield"
DEFAULT_CATALOG = os.path.join(CATALOG_DIR, "higgsfield-0.1.0.json")
SUPPORTED_SCHEMA_VERSION = 1

_lock = threading.Lock()
_cache = {}


class CatalogError(Exception):
    pass


def _validate(payload, path):
    if not isinstance(payload, dict):
        raise CatalogError("Catalog %s is not a JSON object." % path)
    if payload.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        raise CatalogError(
            "Catalog %s uses schema_version %r; this server supports %d."
            % (path, payload.get("schema_version"), SUPPORTED_SCHEMA_VERSION)
        )
    models = payload.get("models")
    if not isinstance(models, list) or not models:
        raise CatalogError("Catalog %s contains no models." % path)
    seen = set()
    for model in models:
        if not isinstance(model, dict):
            raise CatalogError("Catalog %s contains a non-object model." % path)
        model_id = model.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise CatalogError("Catalog %s contains a model without an id." % path)
        if model_id in seen:
            raise CatalogError("Catalog %s contains duplicate model id %s." % (path, model_id))
        seen.add(model_id)
        if not isinstance(model.get("schema"), dict) or not model["schema"].get("properties"):
            raise CatalogError("Model %s has no input schema." % model_id)
        # Validate the schema subset once so every later request gets a clear
        # error instead of a half-rendered form.
        contracts.derive_fields(model)
        contracts.derive_summary(model)
    return payload


def _load_file(path):
    import json

    if not os.path.isfile(path):
        raise CatalogError("Catalog file is missing: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise CatalogError("Catalog file cannot be read: %s" % exc)
    return _validate(payload, path)


def _version_key(filename):
    """Sort key for ``provider-1.2.3.json`` style names."""
    base = filename.rsplit(".", 1)[0]
    version = base.split("-", 1)[-1] if "-" in base else base
    parts = []
    for chunk in version.split("."):
        try:
            parts.append((0, int(chunk)))
        except ValueError:
            parts.append((1, chunk))
    return parts


def catalog_path(provider=DEFAULT_PROVIDER):
    """Resolve the pinned catalog file for a provider id."""
    provider = provider or DEFAULT_PROVIDER
    if provider == DEFAULT_PROVIDER and os.path.isfile(DEFAULT_CATALOG):
        return DEFAULT_CATALOG
    if not os.path.isdir(CATALOG_DIR):
        raise CatalogError("Catalog directory is missing: %s" % CATALOG_DIR)
    prefix = "%s-" % provider
    matches = [
        name
        for name in os.listdir(CATALOG_DIR)
        if name.startswith(prefix) and name.endswith(".json") and os.path.isfile(
            os.path.join(CATALOG_DIR, name)
        )
    ]
    if not matches:
        raise CatalogError("No catalog file found for provider %r." % provider)
    matches.sort(key=_version_key)
    return os.path.join(CATALOG_DIR, matches[-1])


def load(provider=DEFAULT_PROVIDER):
    """Load and cache a provider catalog."""
    provider = provider or DEFAULT_PROVIDER
    with _lock:
        cached = _cache.get(provider)
        if cached is not None:
            return cached
    path = catalog_path(provider)
    payload = _load_file(path)
    if payload.get("provider") != provider:
        raise CatalogError(
            "Catalog %s is for provider %r, not %r."
            % (path, payload.get("provider"), provider)
        )
    with _lock:
        _cache[provider] = payload
    return payload


def reset_cache():
    with _lock:
        _cache.clear()


def info(provider=DEFAULT_PROVIDER):
    payload = load(provider)
    source = payload.get("source") or {}
    return {
        "schema_version": payload.get("schema_version"),
        "catalog_version": payload.get("catalog_version"),
        "provider": payload.get("provider"),
        "api_base": payload.get("api_base"),
        "fetched_at": source.get("fetched_at"),
        "sha256": source.get("sha256"),
        "docs": source.get("docs"),
        "model_count": len(payload.get("models") or []),
    }


def _public(model):
    summary = contracts.derive_summary(model)
    return {
        "id": model["id"],
        "label": model.get("label") or model["id"],
        "kind": model.get("kind"),
        "mode": model.get("mode"),
        "mode_label": contracts.MODE_LABELS.get(model.get("mode"), model.get("mode")),
        "provider": model.get("provider") or DEFAULT_PROVIDER,
        "doc_path": model.get("doc_path"),
        "summary": summary,
        "capabilities": contracts.capabilities_for(model),
        "tags": _tags(model, summary),
    }


def _tags(model, summary):
    tags = []
    if summary.get("prompt"):
        tags.append("prompt")
    if summary["accepts"]["image"]:
        tags.append("image input")
    if summary["accepts"]["video"]:
        tags.append("video input")
    if summary["accepts"]["audio"]:
        tags.append("audio input")
    if summary.get("durations"):
        tags.append("%s s" % "/".join(str(x) for x in summary["durations"][:4]))
    if summary.get("resolutions"):
        tags.append("/".join(str(x) for x in summary["resolutions"][:3]))
    return tags


def list_models(provider=DEFAULT_PROVIDER, kind=None, mode=None, query=None):
    payload = load(provider)
    models = payload.get("models") or []
    if kind:
        if kind not in contracts.KINDS:
            raise CatalogError("Unknown kind %r." % kind)
        models = [m for m in models if m.get("kind") == kind]
    if mode:
        models = [m for m in models if m.get("mode") == mode]
    if query:
        terms = str(query).lower().split()
        models = [
            m
            for m in models
            if all(
                term in ("%s %s %s" % (m.get("label", ""), m.get("id", ""), m.get("mode", ""))).lower()
                for term in terms
            )
        ]
    order = {name: index for index, name in enumerate(contracts.MODES)}
    public = [_public(m) for m in models]
    public.sort(
        key=lambda m: (
            contracts.KINDS.index(m["kind"]) if m["kind"] in contracts.KINDS else 9,
            order.get(m["mode"], 99),
            m["label"].lower(),
        )
    )
    return public


def get_model(model_id, provider=DEFAULT_PROVIDER):
    if not isinstance(model_id, str) or not model_id.strip():
        raise CatalogError("A model id is required.")
    for model in load(provider).get("models") or []:
        if model.get("id") == model_id:
            return model
    raise CatalogError("Unknown model: %s" % model_id)


def model_detail(model_id, provider=DEFAULT_PROVIDER):
    model = get_model(model_id, provider)
    detail = _public(model)
    detail["schema"] = model.get("schema")
    detail["fields"] = contracts.derive_fields(model)
    detail["generation"] = bool(model.get("generation", True))
    return detail


def modes(provider=DEFAULT_PROVIDER):
    payload = load(provider)
    counts = {}
    for model in payload.get("models") or []:
        counts[model.get("mode")] = counts.get(model.get("mode"), 0) + 1
    return [
        {
            "id": mode,
            "label": contracts.MODE_LABELS.get(mode, mode),
            "count": counts.get(mode, 0),
        }
        for mode in contracts.MODES
        if counts.get(mode)
    ]
