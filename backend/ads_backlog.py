"""Ads Product Backlog — the commerce counterpart to the YouTube Topic Backlog.

Lifecycle (forward-only, per the COSMOS specification):

    discovered -> drafted -> reviewing -> approved -> ready_for_creation
    -> in_production -> published -> archived

Discovery creates recommendation cards, never backlog rows. A backlog row is
created only when the owner explicitly drafts a recommendation (or adds a
product manually). Rejection never touches this store.

The store is isolated from the existing Ads generation data: ``jobs.json``,
``media.json``, ``playlists.json`` and ``templates.json`` are untouched.
"""

from __future__ import annotations

import copy
import os
import time

from . import cosmos_common as common

STATUSES = (
    "discovered",
    "drafted",
    "reviewing",
    "approved",
    "ready_for_creation",
    "in_production",
    "published",
    "archived",
)

FORWARD_ORDER = {status: index for index, status in enumerate(STATUSES)}
AFFILIATE_STATUSES = ("unvalidated", "potential", "pending", "validated", "none")
MAX_PRODUCTS = 2000


class BacklogError(common.CosmosError):
    pass


def _store_path():
    return common.ADS_BACKLOG_FILE


def _load():
    data = common.read_json(_store_path(), {})
    if not data:
        return []
    if data.get("version") != common.SCHEMA_VERSION:
        raise BacklogError("backlog_version", "Ads Product Backlog uses an unsupported version.", 500)
    items = data.get("products")
    if not isinstance(items, list):
        raise BacklogError("backlog_invalid", "Ads Product Backlog has an unsupported format.", 500)
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _save(items):
    common.write_json(_store_path(), {
        "version": common.SCHEMA_VERSION,
        "products": items,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    })


def _now():
    return common.now()


def _iso(value=None):
    return common.iso(value)


def counts():
    with common.acquire():
        items = _load()
    live = [item for item in items if not item.get("deleted_at")]
    result = {status: 0 for status in STATUSES}
    for item in live:
        status = item.get("status") or "drafted"
        result[status] = result.get(status, 0) + 1
    result["total"] = len([item for item in live if item.get("status") != "archived"])
    return result


def _clean_price(value, fallback_currency="PHP"):
    if value is None:
        return {"amount": None, "currency": fallback_currency or "PHP", "verified": False}
    if isinstance(value, (int, float)):
        return {"amount": round(float(value), 2), "currency": fallback_currency or "PHP", "verified": False}
    if not isinstance(value, dict):
        raise BacklogError("invalid_product", "price must be a number or an object.")
    amount = value.get("amount")
    if amount in (None, ""):
        amount = None
    else:
        amount = round(common.clamp_float(amount, 0, 100_000_000, 0), 2)
    currency = common.clean_text(value.get("currency"), "Currency", 8) or fallback_currency or "PHP"
    return {
        "amount": amount,
        "currency": currency,
        "verified": bool(value.get("verified")),
    }


def _clean_sources(value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise BacklogError("invalid_product", "sources must be a list.")
    if len(value) > 12:
        raise BacklogError("invalid_product", "At most 12 sources are allowed.")
    sources = []
    for entry in value:
        if isinstance(entry, str):
            entry = {"label": entry, "url": None, "type": "provided"}
        if not isinstance(entry, dict):
            continue
        label = common.clean_text(entry.get("label"), "Source label", 300)
        url = common.clean_text(entry.get("url"), "Source URL", 1000) or None
        if url and not url.lower().startswith(("http://", "https://")):
            url = None
        kind = str(entry.get("type") or "").strip().lower()
        if kind not in ("provided", "observed", "third_party", "none"):
            kind = "provided" if label or url else "none"
        if label or url:
            sources.append({"label": label, "url": url, "type": kind})
    return sources


def _clean_provenance(value):
    if not isinstance(value, dict):
        return {}
    allowed = ("agent_id", "execution_id", "recommendation_id", "division", "template_id", "config_snapshot")
    result = {}
    for key in allowed:
        if key in value:
            if key == "config_snapshot":
                snapshot = value.get(key)
                if isinstance(snapshot, dict):
                    result[key] = copy.deepcopy(snapshot)
            else:
                result[key] = common.clean_text(value.get(key), key.replace("_", " ").title(), 120)
    return result


def _dedupe_key(name, merchant, product_url):
    parts = [common.normalize_key(name)]
    if product_url:
        parts.append(common.normalize_key(product_url))
    elif merchant:
        parts.append(common.normalize_key(merchant))
    return "|".join(part for part in parts if part)


def _find_active_duplicate(items, key):
    for item in items:
        if item.get("deleted_at") or item.get("status") == "archived":
            continue
        if item.get("dedupe_key") == key:
            return item
    return None


def _public(item):
    return copy.deepcopy(item)


def _new_product(payload, origin=None, status="drafted"):
    if not isinstance(payload, dict):
        raise BacklogError("invalid_product", "Product payload must be a JSON object.")
    name = common.clean_text(payload.get("name"), "Product name", 200, required=True)
    merchant = common.clean_text(payload.get("merchant"), "Merchant", 160) or None
    product_url = common.clean_text(payload.get("product_url"), "Product URL", 1000) or None
    if product_url and not product_url.lower().startswith(("http://", "https://")):
        product_url = None
    image_url = common.clean_text(payload.get("image_url"), "Image URL", 1000) or None
    if image_url and not image_url.lower().startswith(("http://", "https://")):
        image_url = None
    affiliate_status = str(payload.get("affiliate_status") or "unvalidated").strip().lower()
    if affiliate_status not in AFFILIATE_STATUSES:
        affiliate_status = "unvalidated"
    status = str(status or "drafted").strip().lower()
    if status not in STATUSES:
        raise BacklogError("invalid_status", "Unknown Ads backlog status.")
    stamp = _now()
    item = {
        "id": common.new_id("adp_"),
        "name": name,
        "description": common.clean_text(payload.get("description"), "Description", 4000),
        "category": common.clean_text(payload.get("category"), "Category", 160),
        "merchant": merchant,
        "product_url": product_url,
        "image_url": image_url,
        "price": _clean_price(payload.get("price"), (payload.get("currency") or "PHP")),
        "price_verified": bool(payload.get("price_verified")) or bool((payload.get("price") or {}).get("verified") if isinstance(payload.get("price"), dict) else False),
        "target_audience": common.clean_text(payload.get("target_audience"), "Target audience", 600),
        "template_id": common.clean_text(payload.get("template_id"), "Template", 80) or None,
        "reason": common.clean_text(payload.get("reason"), "Reason", 1200),
        "sources": _clean_sources(payload.get("sources")),
        "relevance_score": common.clamp_int(payload.get("relevance_score"), 0, 100, 0),
        "affiliate_status": affiliate_status,
        "creative_format": common.clean_text(payload.get("creative_format"), "Creative format", 300),
        "evidence_status": str(payload.get("evidence_status") or "hypothesis").strip().lower(),
        "provenance": _clean_provenance(origin),
        "status": status,
        "history": [{"status": status, "at": stamp, "iso": _iso(stamp), "note": "created", "by": "owner"}],
        "dedupe_key": _dedupe_key(name, merchant, product_url),
        "archived": status == "archived",
        "deleted_at": None,
        "created_at": stamp,
        "created_iso": _iso(stamp),
        "updated_at": stamp,
        "updated_iso": _iso(stamp),
    }
    if item["price"]["verified"]:
        item["price_verified"] = True
    return item


def create_product(payload, origin=None):
    item = _new_product(payload, origin=origin, status=(payload or {}).get("status") or "drafted")
    with common.acquire():
        items = _load()
        duplicate = _find_active_duplicate(items, item["dedupe_key"])
        if duplicate:
            raise BacklogError(
                "duplicate_product",
                "An active backlog item already exists: %s." % duplicate.get("name"),
                409,
            )
        if len([existing for existing in items if not existing.get("deleted_at")]) >= MAX_PRODUCTS:
            raise BacklogError("backlog_limit", "The Ads Product Backlog reached its limit.", 409)
        items.append(item)
        _save(items)
    return _public(item)


def create_from_recommendation(recommendation):
    payload = copy.deepcopy(recommendation.get("payload") or {})
    payload.setdefault("template_id", recommendation.get("template_id"))
    payload.setdefault("relevance_score", recommendation.get("relevance_score"))
    payload.setdefault("sources", recommendation.get("sources"))
    payload.setdefault("evidence_status", recommendation.get("evidence_status"))
    origin = {
        "agent_id": recommendation.get("agent_id"),
        "execution_id": recommendation.get("execution_id"),
        "recommendation_id": recommendation.get("id"),
        "division": recommendation.get("division"),
        "template_id": recommendation.get("template_id"),
        "config_snapshot": recommendation.get("config_snapshot"),
    }
    return create_product(payload, origin=origin)


def list_products(status=None, query=None, limit=200, include_archived=False):
    with common.acquire():
        items = _load()
    items = [item for item in items if not item.get("deleted_at")]
    if not include_archived and str(status or "").strip() != "archived":
        items = [item for item in items if item.get("status") != "archived"]
    wanted = str(status or "").strip().lower()
    if wanted:
        if wanted not in STATUSES:
            raise BacklogError("invalid_status", "Unknown Ads backlog status.")
        items = [item for item in items if item.get("status") == wanted]
    needle = common.normalize_key(query)
    if needle:
        items = [
            item for item in items
            if needle in common.normalize_key(
                " ".join(str(item.get(field) or "") for field in ("name", "description", "category", "merchant", "target_audience"))
            )
        ]
    items.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or 0, reverse=True)
    try:
        requested = int(limit or 200)
    except (TypeError, ValueError):
        requested = 200
    requested = max(1, min(requested, MAX_PRODUCTS))
    return {
        "products": [_public(item) for item in items[:requested]],
        "total": len(items),
        "counts": counts(),
    }


def get_product(product_id):
    with common.acquire():
        items = _load()
    for item in items:
        if item.get("id") == str(product_id or ""):
            if item.get("deleted_at"):
                break
            return _public(item)
    raise BacklogError("product_not_found", "Product not found.", 404)


def update_product(product_id, patch):
    if not isinstance(patch, dict):
        raise BacklogError("invalid_product", "Product payload must be a JSON object.")
    with common.acquire():
        items = _load()
        item = next((entry for entry in items if entry.get("id") == str(product_id or "") and not entry.get("deleted_at")), None)
        if not item:
            raise BacklogError("product_not_found", "Product not found.", 404)
        if "name" in patch:
            item["name"] = common.clean_text(patch.get("name"), "Product name", 200, required=True)
        if "description" in patch:
            item["description"] = common.clean_text(patch.get("description"), "Description", 4000)
        if "category" in patch:
            item["category"] = common.clean_text(patch.get("category"), "Category", 160)
        if "merchant" in patch:
            item["merchant"] = common.clean_text(patch.get("merchant"), "Merchant", 160) or None
        if "product_url" in patch:
            url = common.clean_text(patch.get("product_url"), "Product URL", 1000) or None
            item["product_url"] = url if url and url.lower().startswith(("http://", "https://")) else None
        if "image_url" in patch:
            url = common.clean_text(patch.get("image_url"), "Image URL", 1000) or None
            item["image_url"] = url if url and url.lower().startswith(("http://", "https://")) else None
        if "price" in patch:
            currency = item.get("price", {}).get("currency") or "PHP"
            item["price"] = _clean_price(patch.get("price"), currency)
            item["price_verified"] = bool(item["price"].get("verified"))
        if "target_audience" in patch:
            item["target_audience"] = common.clean_text(patch.get("target_audience"), "Target audience", 600)
        if "reason" in patch:
            item["reason"] = common.clean_text(patch.get("reason"), "Reason", 1200)
        if "creative_format" in patch:
            item["creative_format"] = common.clean_text(patch.get("creative_format"), "Creative format", 300)
        if "affiliate_status" in patch:
            status = str(patch.get("affiliate_status") or "").strip().lower()
            if status not in AFFILIATE_STATUSES:
                raise BacklogError("invalid_affiliate_status", "Unknown affiliate status.")
            item["affiliate_status"] = status
        if "sources" in patch:
            item["sources"] = _clean_sources(patch.get("sources"))
        item["dedupe_key"] = _dedupe_key(item.get("name"), item.get("merchant"), item.get("product_url"))
        item["updated_at"] = _now()
        item["updated_iso"] = _iso(item["updated_at"])
        _save(items)
    return _public(item)


def set_status(product_id, status, note=None, by="owner"):
    wanted = str(status or "").strip().lower()
    if wanted not in STATUSES:
        raise BacklogError("invalid_status", "Unknown Ads backlog status.")
    with common.acquire():
        items = _load()
        item = next((entry for entry in items if entry.get("id") == str(product_id or "") and not entry.get("deleted_at")), None)
        if not item:
            raise BacklogError("product_not_found", "Product not found.", 404)
        current = item.get("status") or "drafted"
        if wanted != current:
            if current == "archived":
                raise BacklogError("invalid_transition", "Archived products cannot change status.", 409)
            if wanted != "archived" and FORWARD_ORDER.get(wanted, -1) < FORWARD_ORDER.get(current, -1):
                raise BacklogError(
                    "invalid_transition",
                    "Ads backlog status is forward-only: %s -> %s." % (current, wanted),
                    409,
                )
            item["status"] = wanted
            item["archived"] = wanted == "archived"
            item["history"].append({
                "status": wanted,
                "from": current,
                "at": _now(),
                "iso": _iso(),
                "note": common.clean_text(note, "Note", 500),
                "by": common.clean_text(by, "By", 80) or "owner",
            })
            item["updated_at"] = _now()
            item["updated_iso"] = _iso()
            _save(items)
    return _public(item)


def archive(product_id):
    return set_status(product_id, "archived", note="archived")


def delete_product(product_id):
    """Soft delete. Used by explicit owner cleanup, never by discovery."""
    with common.acquire():
        items = _load()
        item = next((entry for entry in items if entry.get("id") == str(product_id or "") and not entry.get("deleted_at")), None)
        if not item:
            raise BacklogError("product_not_found", "Product not found.", 404)
        item["deleted_at"] = _now()
        item["deleted_iso"] = _iso()
        item["updated_at"] = item["deleted_at"]
        item["updated_iso"] = item["deleted_iso"]
        _save(items)
    return {"deleted": item["id"]}
