"""Shopee ``productOfferV2`` — product/offer discovery and normalization.

Documented contract honored here:

* pagination uses ``page`` / ``limit`` / ``hasNextPage`` (NOT the offset
  scheme of the Product Feed APIs);
* ``listType`` is only valid together with ``matchId`` and never combined
  with other filter parameters;
* sort types are 1..5 (relevance is keyword-search only);
* commission values arrive as strings (``"0.25"`` = 25%);
* fields marked To Be Removed (``price``, ``appExistRate``, ``appNewRate``,
  ``webExistRate``, ``webNewRate``) are never used or normalized.

Normalized offers are upserted into ``data/integrations/shopee/products.json``
keyed by ``(itemId, shopId)`` with the raw payload preserved as provenance.
"""

from __future__ import annotations

from .. import store
from . import client

PRODUCTS_FILE = store.INTEGRATIONS_DIR + "/shopee/products.json"

LIST_TYPES = {
    "ALL": 0,
    "HIGHEST_COMMISSION": 1,  # documented as To Be Removed — accepted, flagged
    "TOP_PERFORMING": 2,
    "LANDING_CATEGORY": 3,
    "DETAIL_CATEGORY": 4,
    "DETAIL_SHOP": 5,
    "DETAIL_COLLECTION": 6,  # documented as To Be Removed — accepted, flagged
}
SORT_TYPES = (1, 2, 3, 4, 5)
DEFAULT_LIMIT = 10
MAX_LIMIT = 100

PRODUCT_OFFER_QUERY = """
query ProductOfferV2($itemId: Float, $shopId: Float, $productCatId: Int,
                     $listType: Int, $matchId: Float, $keyword: String,
                     $sortType: Int, $page: Int, $isAMSOffer: Boolean,
                     $isKeySeller: Boolean, $limit: Int) {
  productOfferV2(itemId: $itemId, shopId: $shopId, productCatId: $productCatId,
                 listType: $listType, matchId: $matchId, keyword: $keyword,
                 sortType: $sortType, page: $page, isAMSOffer: $isAMSOffer,
                 isKeySeller: $isKeySeller, limit: $limit) {
    total
    hasNextPage
    nodes {
      itemId shopId shopName shopType productName productCatIds
      commissionRate sellerCommissionRate shopeeCommissionRate commission
      sales priceMin priceMax priceDiscountRate ratingStar imageUrl
      productLink offerLink periodStartTime periodEndTime
    }
  }
}
"""


class ShopeeProductsError(store.IntegrationError):
    pass


def query_offers(params=None):
    """Run one productOfferV2 query with documented parameter validation."""
    params = dict(params or {})
    variables = {}
    if params.get("itemId") is not None:
        variables["itemId"] = _number(params["itemId"], "itemId")
    if params.get("shopId") is not None:
        variables["shopId"] = _number(params["shopId"], "shopId")
    if params.get("productCatId") is not None:
        variables["productCatId"] = _int(params["productCatId"], "productCatId")
    if params.get("keyword"):
        variables["keyword"] = store.clean_text(params["keyword"], "keyword", 200)
    if params.get("isAMSOffer") is not None:
        variables["isAMSOffer"] = bool(params["isAMSOffer"])
    if params.get("isKeySeller") is not None:
        variables["isKeySeller"] = bool(params["isKeySeller"])

    list_type = params.get("listType")
    match_id = params.get("matchId")
    if list_type is not None:
        # Documented: listType works ONLY with matchId, never other filters.
        conflicts = [k for k in ("itemId", "shopId", "productCatId", "keyword",
                                 "isAMSOffer", "isKeySeller") if k in variables]
        if match_id is None:
            raise ShopeeProductsError("params_error", "listType requires matchId.")
        if conflicts:
            raise ShopeeProductsError(
                "params_error",
                "listType cannot be combined with other filters (%s)." % ", ".join(conflicts))
        try:
            list_value = LIST_TYPES[str(list_type).upper()] if str(list_type).strip().upper() in LIST_TYPES \
                else int(list_type)
        except (TypeError, ValueError):
            raise ShopeeProductsError("params_error", "listType is not valid.")
        if list_value not in LIST_TYPES.values():
            raise ShopeeProductsError("params_error", "listType must be 0..6.")
        if list_value in (1, 6):
            raise ShopeeProductsError(
                "params_error", "listType %d is documented as To Be Removed." % list_value)
        variables["listType"] = list_value
        variables["matchId"] = _number(match_id, "matchId")
    elif match_id is not None:
        raise ShopeeProductsError("params_error", "matchId requires listType.")

    if params.get("sortType") is not None:
        sort_value = _int(params["sortType"], "sortType")
        if sort_value not in SORT_TYPES:
            raise ShopeeProductsError("params_error", "sortType must be 1..5.")
        if sort_value == 1 and not variables.get("keyword"):
            raise ShopeeProductsError("params_error", "sortType 1 (relevance) is keyword-search only.")
        variables["sortType"] = sort_value

    variables["page"] = max(1, _int(params.get("page", 1), "page"))
    variables["limit"] = min(MAX_LIMIT, max(1, _int(params.get("limit", DEFAULT_LIMIT), "limit")))

    data = client.graphql(client.clean_query(PRODUCT_OFFER_QUERY), variables)
    block = (data or {}).get("productOfferV2") or {}
    nodes = [normalize_offer(node) for node in (block.get("nodes") or [])]
    return {
        "page": variables["page"],
        "limit": variables["limit"],
        "hasNextPage": bool(block.get("hasNextPage")),
        "total": block.get("total"),
        "offers": nodes,
    }


def search(keyword, **params):
    params["keyword"] = keyword
    params.setdefault("sortType", 1)
    return query_offers(params)


def offers_by_shop(shop_id, **params):
    params["shopId"] = shop_id
    return query_offers(params)


def offers_by_category(category_id, **params):
    params["productCatId"] = category_id
    return query_offers(params)


def top_performing(match_id, **params):
    params["listType"] = "TOP_PERFORMING"
    params["matchId"] = match_id
    return query_offers(params)


def normalize_offer(node):
    """Normalize one offer node; deprecated fields are dropped, not mapped."""
    if not isinstance(node, dict):
        return {}
    normalized = {
        "item_id": node.get("itemId"),
        "shop_id": node.get("shopId"),
        "shop_name": node.get("shopName"),
        "shop_type": _shop_type(node.get("shopType")),
        "product_name": node.get("productName"),
        "category_ids": node.get("productCatIds") or [],
        "commission_rate": _rate(node.get("commissionRate")),
        "seller_commission_rate": _rate(node.get("sellerCommissionRate")),
        "shopee_commission_rate": _rate(node.get("shopeeCommissionRate")),
        "commission": _number_or_none(node.get("commission")),
        "sales": _int_or_none(node.get("sales")),
        "price_min": _number_or_none(node.get("priceMin")),
        "price_max": _number_or_none(node.get("priceMax")),
        "discount_rate": _rate(node.get("priceDiscountRate")),
        "rating_star": _number_or_none(node.get("ratingStar")),
        "image_url": node.get("imageUrl"),
        "product_link": node.get("productLink"),
        "offer_link": node.get("offerLink"),
        "period_start_time": node.get("periodStartTime"),
        "period_end_time": node.get("periodEndTime"),
    }
    return {
        "normalized": normalized,
        "raw": node,
        "identity": _identity(normalized),
    }


def upsert_offers(offers, source="productOfferV2"):
    """Idempotent upsert keyed by (item_id, shop_id); raw provenance kept."""
    if not offers:
        return {"upserted": 0, "total": _count()}
    with store.lock():
        items = store.load_store(PRODUCTS_FILE, "products")
        upserted = 0
        for offer in offers:
            normalized = offer.get("normalized") or offer
            key = (normalized.get("item_id"), normalized.get("shop_id"))
            record = {
                "id": "shp_" + _key_hash(key),
                "item_id": normalized.get("item_id"),
                "shop_id": normalized.get("shop_id"),
                "normalized": normalized,
                "raw": offer.get("raw") if isinstance(offer, dict) else None,
                "source": source,
                "updated_at": store.now(),
                "updated_iso": store.iso(),
            }
            # Search at insert time: cached positions go stale as soon as a
            # new record is inserted at index 0 in the same batch.
            existing = next(
                (pos for pos, item in enumerate(items)
                 if (item.get("item_id"), item.get("shop_id")) == key),
                None,
            )
            if existing is None:
                record["created_at"] = record["updated_at"]
                record["created_iso"] = record["updated_iso"]
                items.insert(0, record)
            else:
                record["created_at"] = items[existing].get("created_at")
                record["created_iso"] = items[existing].get("created_iso")
                items[existing] = record
            upserted += 1
        store.save_store(PRODUCTS_FILE, "products", items[:5000])
    return {"upserted": upserted, "total": _count()}


def list_products(limit=50, keyword=None):
    with store.lock():
        items = store.load_store(PRODUCTS_FILE, "products")
    wanted = store.clean_text(keyword, "keyword", 200).lower()
    rows = []
    for item in items:
        if wanted:
            name = str((item.get("normalized") or {}).get("product_name") or "").lower()
            if wanted not in name:
                continue
        rows.append(item)
        if len(rows) >= max(1, min(int(limit or 50), 200)):
            break
    return rows


# ---------------------------------------------------------------------------
# Feed ingestion store helpers (used by feeds.py)
# ---------------------------------------------------------------------------
def upsert_feed_record(record):
    """Idempotent upsert of one feed-derived product record."""
    identity = record.get("identity")
    if not identity:
        return False
    with store.lock():
        items = store.load_store(PRODUCTS_FILE, "products")
        existing = next((i for i in items if i.get("identity") == identity), None)
        now = store.now()
        if existing is None:
            entry = dict(record)
            entry["id"] = "shp_" + _key_hash(identity)
            entry["created_at"] = now
            entry["created_iso"] = store.iso(now)
            entry["updated_at"] = now
            entry["updated_iso"] = store.iso(now)
            items.insert(0, entry)
        else:
            entry = dict(existing)
            for key in ("item_id", "shop_id", "normalized", "raw_columns", "source"):
                if record.get(key) is not None:
                    entry[key] = record[key]
            entry["updated_at"] = now
            entry["updated_iso"] = store.iso(now)
            items[items.index(existing)] = entry
        store.save_store(PRODUCTS_FILE, "products", items[:5000])
    return True


def delete_by_identity(identity):
    """Apply a DELTA DELETE; returns False when the row is unknown."""
    with store.lock():
        items = store.load_store(PRODUCTS_FILE, "products")
        remaining = [i for i in items if i.get("identity") != identity]
        if len(remaining) == len(items):
            return False
        store.save_store(PRODUCTS_FILE, "products", remaining)
    return True


def replace_from_feed(columns_raw_list, source="reconcile"):
    """Atomically replace the catalog from a validated FULL feed.

    Only called by ``feeds.reconcile`` after completeness validation, so a
    partial download can never destroy a good catalog.
    """
    now = store.now()
    fresh = []
    seen = set()
    from . import feeds as feeds_module

    for raw in columns_raw_list or []:
        columns = feeds_module.parse_columns(raw)
        identity, item_id, shop_id = feeds_module._detect_identity(columns)
        if identity in seen:
            continue
        seen.add(identity)
        name = None
        for field in feeds_module._NAME_FIELDS:
            if columns.get(field) is not None:
                name = str(columns[field])[:300]
                break
        fresh.append({
            "id": "shp_" + _key_hash(identity),
            "identity": identity,
            "item_id": item_id,
            "shop_id": shop_id,
            "normalized": {
                "item_id": item_id,
                "shop_id": shop_id,
                "product_name": name,
                "mapping_version": "provisional",
                "columns_schema": columns.get("_schema"),
            },
            "raw_columns": raw,
            "source": source,
            "created_at": now,
            "created_iso": store.iso(now),
            "updated_at": now,
            "updated_iso": store.iso(now),
        })
    with store.lock():
        store.save_store(PRODUCTS_FILE, "products", fresh)
    return len(fresh)


def _count():
    with store.lock():
        return len(store.load_store(PRODUCTS_FILE, "products"))


def _identity(normalized):
    raw_key = "%s|%s" % (normalized.get("item_id"), normalized.get("shop_id"))
    return _key_hash(raw_key)


def _key_hash(key):
    import hashlib

    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:16]


def _shop_type(value):
    mapping = {1: "OFFICIAL_SHOP", 2: "PREFERRED_SHOP", 4: "PREFERRED_PLUS_SHOP"}
    try:
        return mapping.get(int(value), str(value))
    except (TypeError, ValueError):
        return value


def _rate(value):
    """Commission/discount strings like \"0.25\" (25%) -> float 0.25."""
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _number_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _number(value, label):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ShopeeProductsError("params_error", "%s must be numeric." % label)


def _int(value, label):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise ShopeeProductsError("params_error", "%s must be an integer." % label)
