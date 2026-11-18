"""Unified model registry spanning connections and agent-native sources.

Every connection-sourced model gets a namespaced internal id::

    conn:<connection_id>:<native_model_id>

so gateway model ids can never collide with a CLI's native model ids, and
the original runtime identifier is always preserved verbatim in
``native_id``. Capability and pricing metadata is taken from upstream
responses when provided and stays ``unknown`` otherwise - nothing is
invented. Records persist in ``data/model_catalog.json`` so a failed
refresh marks entries *stale* instead of deleting known models.
"""

from __future__ import annotations

import json
import os
import threading

from . import config
from . import connections

CATALOG_FILE = os.path.join(config.DATA_DIR, "model_catalog.json")
_STORE_VERSION = 1
_HISTORY_LIMIT = 100

_LOCK = threading.RLock()

# Capability values: unknown < unsupported < supported. ``not_tested`` is a
# UI-level distinction for values never verified against a live run.
CAP_UNKNOWN = "unknown"
CAP_UNSUPPORTED = "unsupported"
CAP_SUPPORTED = "supported"

_KNOWN_CAPABILITY_FIELDS = ("image", "audio", "video", "tools", "streaming")


class CatalogError(Exception):
    """Raised when discovery fails; callers must never fabricate a list."""


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _read_store():
    data = config.read_json(CATALOG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", _STORE_VERSION)
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    if not isinstance(data.get("aliases"), dict):
        data["aliases"] = {}
    if not isinstance(data.get("history"), list):
        data["history"] = []
    return data


def _write_store(data):
    config.write_json(CATALOG_FILE, data)


# ---------------------------------------------------------------------------
# Internal ids
# ---------------------------------------------------------------------------
def internal_id(connection_id, native_id):
    return "conn:%s:%s" % (connection_id, native_id)


def parse_internal_id(model_id):
    """Return ``(connection_id, native_id)`` for connection models, else None."""
    if not isinstance(model_id, str) or not model_id.startswith("conn:"):
        return None
    body = model_id[len("conn:"):]
    known = set()
    with _LOCK:
        try:
            known.update(c.get("id") for c in _read_store()["connections"])
        except Exception:
            pass
    known.discard(None)
    for connection_id in sorted(known, key=len, reverse=True):
        prefix = connection_id + ":"
        if body.startswith(prefix) and len(body) > len(prefix):
            return connection_id, body[len(prefix):]
    # Unknown connection: split at the first colon (legacy simple ids).
    parts = body.split(":", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None


def is_connection_model(model_id):
    return parse_internal_id(model_id) is not None


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _capabilities_from_metadata(meta):
    """Extract capability facts from upstream metadata. Never invent them."""
    caps = {field: CAP_UNKNOWN for field in _KNOWN_CAPABILITY_FIELDS}
    if not isinstance(meta, dict):
        return caps
    arch = meta.get("architecture") if isinstance(meta.get("architecture"), dict) else {}
    modality = (arch.get("modality") or meta.get("modality") or "").lower()
    if modality:
        caps["image"] = CAP_SUPPORTED if ("image" in modality or "->image" in modality or "image+text" in modality) else CAP_UNSUPPORTED
        if "audio" in modality:
            caps["audio"] = CAP_SUPPORTED
        if "video" in modality:
            caps["video"] = CAP_SUPPORTED
    input_modalities = meta.get("input_modalities") or meta.get("input_types")
    if isinstance(input_modalities, list):
        lowered = [str(m).lower() for m in input_modalities]
        caps["image"] = CAP_SUPPORTED if "image" in lowered else caps["image"]
        caps["audio"] = CAP_SUPPORTED if "audio" in lowered else caps["audio"]
        caps["video"] = CAP_SUPPORTED if "video" in lowered else caps["video"]
    for key, field in (("tool_call", "tools"), ("tools", "tools"),
                       ("function_calling", "tools"), ("supported_parameters", "tools")):
        value = meta.get(key)
        if isinstance(value, bool):
            caps["tools"] = CAP_SUPPORTED if value else CAP_UNSUPPORTED
            break
        if isinstance(value, list) and key == "supported_parameters":
            if any("tool" in str(v).lower() for v in value):
                caps["tools"] = CAP_SUPPORTED
    return caps


def _pricing_from_metadata(meta, connection_pricing):
    """Prefer reliable upstream pricing; fall back to connection metadata."""
    if isinstance(meta, dict):
        pricing = meta.get("pricing")
        if isinstance(pricing, dict):
            values = []
            for key in ("prompt", "completion", "request", "image"):
                raw = pricing.get(key)
                if raw is None:
                    continue
                try:
                    values.append(float(raw))
                except (TypeError, ValueError):
                    return connection_pricing
            if values and all(v == 0 for v in values):
                return connections.PRICING_FREE
            if values:
                return connections.PRICING_PAID
        if isinstance(meta.get("max_model_len"), int) and meta.get("max_model_len"):
            pass  # context only; pricing stays as-is
    return connection_pricing


def normalize_entry(conn, native_id, name=None, metadata=None, source="discovery"):
    """Build a unified model record. ``native_id`` is preserved verbatim."""
    meta = metadata if isinstance(metadata, dict) else {}
    context_window = None
    for key in ("context_length", "max_model_len", "context_window", "max_tokens", "top_provider"):
        raw = meta.get(key)
        if isinstance(raw, int) and raw > 0:
            context_window = raw
            break
        if isinstance(raw, dict) and isinstance(raw.get("context_length"), int):
            context_window = raw["context_length"]
            break
    record = {
        "id": internal_id(conn["id"], native_id),
        "native_id": native_id,
        "name": (name or "").strip() or native_id,
        "connection_id": conn["id"],
        "connection_type": conn["type"],
        "connection_name": conn["name"],
        "provider_id": conn.get("provider_id") or "",
        "gateway_model_id": native_id if conn.get("type") == connections.GATEWAY else None,
        "upstream_provider_id": conn.get("provider_id") or None,
        "protocol": conn.get("protocol"),
        "context_window": context_window,
        "capabilities": _capabilities_from_metadata(meta),
        "pricing": _pricing_from_metadata(meta, conn.get("pricing") or connections.PRICING_UNKNOWN),
        "available": True,
        "availability": "available",
        "discovered_at": config.now_iso(),
        "source": source,
        "raw_metadata": {k: v for k, v in meta.items() if k in (
            "context_length", "max_model_len", "modality", "architecture",
            "pricing", "input_modalities", "output_modalities", "description")},
    }
    return record


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------
def _fetch_models(conn, timeout=15.0):
    """Call the protocol's model-listing endpoint. Returns (items, payload)."""
    key, _key_source = connections.resolve_key(conn)
    status_code, body = connections._models_request(conn, key, timeout)
    if status_code in (401, 403):
        raise CatalogError("Authentication rejected (HTTP %d)." % status_code)
    if status_code == 429:
        raise CatalogError("Rate limited (HTTP 429).")
    if status_code != 200:
        raise CatalogError("HTTP %d from model listing." % status_code)
    try:
        payload = json.loads(body.decode("utf-8", "replace"))
    except Exception:
        raise CatalogError("Model listing returned unparsable JSON.")
    items = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise CatalogError("Model listing response has no model list.")
    return items, payload


def discover(connection_id, timeout=15.0):
    """Discover models for one connection and persist the result."""
    conn = connections.require_connection(connection_id)
    items, payload = _fetch_models(conn, timeout=timeout)
    records = []
    for item in items:
        if isinstance(item, str):
            records.append(normalize_entry(conn, item, source="discovery"))
        elif isinstance(item, dict):
            native = (item.get("id") or item.get("name") or item.get("model") or "").strip()
            if not native:
                continue
            records.append(normalize_entry(conn, native,
                                           name=item.get("display_name") or item.get("name"),
                                           metadata=item, source="discovery"))
    if not records:
        raise CatalogError("Model listing returned no usable models.")
    with _LOCK:
        data = _read_store()
        data["entries"][connection_id] = {
            "discovered_at": config.now_iso(),
            "models": records,
            "error": None,
        }
        _push_history(data, connection_id, ok=True, count=len(records))
        _write_store(data)
    return records


def _push_history(data, connection_id, ok, count=0, error=None):
    data["history"].insert(0, {
        "connection_id": connection_id,
        "at": config.now_iso(),
        "ok": bool(ok),
        "model_count": count,
        "error": (str(error)[:200] if error else None),
    })
    del data["history"][_HISTORY_LIMIT:]


def discovery_failed(connection_id, error):
    """Record a failed refresh; previously known entries stay but go stale."""
    with _LOCK:
        data = _read_store()
        entry = data["entries"].get(connection_id)
        if isinstance(entry, dict):
            entry["error"] = str(error)[:200]
            for record in entry.get("models", []):
                record["availability"] = "stale"
                record["available"] = True  # stale, not confirmed unavailable
        _push_history(data, connection_id, ok=False, error=error)
        _write_store(data)


def catalog_records(connection_id, refresh=False):
    """Records for one connection: cached discovery + manual registrations.

    With ``refresh=True`` a failed discovery never raises away known models:
    the failure is recorded, cached entries are marked ``stale`` and returned
    alongside the error. Returns ``(records, error)``.
    """
    conn = connections.require_connection(connection_id)
    error = None
    if refresh:
        try:
            return discover(connection_id), None
        except CatalogError as exc:
            discovery_failed(connection_id, exc)
            error = str(exc)
    with _LOCK:
        data = _read_store()
        entry = data["entries"].get(connection_id)
    manual = [normalize_entry(conn, mid, source="manual")
              for mid in conn.get("model_ids") or []]
    cached = (entry or {}).get("models") or []
    by_native = {r["native_id"]: r for r in cached}
    merged = list(cached)
    for record in manual:
        if record["native_id"] not in by_native:
            merged.append(record)
    return merged, error


# ---------------------------------------------------------------------------
# Manual registration, aliases, lookup
# ---------------------------------------------------------------------------
def register_model(connection_id, native_id, name=None, metadata=None):
    conn = connections.require_connection(connection_id)
    native_id = (native_id or "").strip()
    if not native_id:
        raise CatalogError("native model id is required")
    with _LOCK:
        data = _read_store()
        entry = data["entries"].setdefault(connection_id, {
            "discovered_at": None, "models": [], "error": None})
        models = entry.setdefault("models", [])
        existing = next((r for r in models if r.get("native_id") == native_id), None)
        record = normalize_entry(conn, native_id, name=name, metadata=metadata,
                                 source=existing.get("source") if existing else "manual")
        if existing:
            record["source"] = existing.get("source") or "manual"
            models[models.index(existing)] = record
        else:
            models.append(record)
        # Keep the connection's manual list in sync so the registration
        # survives re-discovery.
        if native_id not in (conn.get("model_ids") or []):
            connections.update_connection(connection_id, {
                "model_ids": list(conn.get("model_ids") or []) + [native_id]})
        entry["discovered_at"] = entry.get("discovered_at") or config.now_iso()
        _write_store(data)
        return record


def remove_model(connection_id, native_id):
    with _LOCK:
        data = _read_store()
        entry = data["entries"].get(connection_id)
        removed = False
        if isinstance(entry, dict):
            before = len(entry.get("models", []))
            entry["models"] = [r for r in entry.get("models", [])
                               if r.get("native_id") != native_id]
            removed = len(entry.get("models", [])) < before
            _write_store(data)
        conn = connections.get_connection(connection_id)
        if conn and native_id in (conn.get("model_ids") or []) and not conn.get("derived"):
            connections.update_connection(connection_id, {
                "model_ids": [m for m in conn.get("model_ids") or [] if m != native_id]})
        if not removed and not conn:
            raise CatalogError("Unknown model on connection %s" % connection_id)
        return {"removed": removed}


def set_alias(connection_id, gateway_model_id, upstream_model_id):
    """Record a known gateway → upstream mapping (user-provided knowledge)."""
    if not gateway_model_id or not upstream_model_id:
        raise CatalogError("Both gateway and upstream model ids are required")
    conn = connections.require_connection(connection_id)
    aliases = dict(conn.get("aliases") or {})
    aliases[gateway_model_id] = upstream_model_id
    connections.update_connection(connection_id, {"aliases": aliases})
    return {"connection_id": connection_id, "aliases": aliases}


def get_record(model_id):
    """Look up one unified record by internal id. Returns (record, conn)."""
    parsed = parse_internal_id(model_id)
    if not parsed:
        return None, None
    connection_id, native_id = parsed
    conn = connections.get_connection(connection_id)
    if not conn:
        return None, None
    with _LOCK:
        data = _read_store()
        entry = data["entries"].get(connection_id) or {}
    for record in entry.get("models", []):
        if record.get("native_id") == native_id:
            return record, conn
    if native_id in (conn.get("model_ids") or []):
        return normalize_entry(conn, native_id, source="manual"), conn
    return None, conn


def alias_for(connection_id, native_id):
    """Known upstream id for a gateway model id, else None."""
    conn = connections.get_connection(connection_id)
    if not conn:
        return None
    return (conn.get("aliases") or {}).get(native_id)
