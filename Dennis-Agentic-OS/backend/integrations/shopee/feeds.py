"""Shopee Product Feed APIs — FULL/DELTA catalog synchronization.

Documented contract honored here:

* ``listItemFeeds(feedMode)`` — FULL (entire category catalog, initial
  download) vs DELTA (adds/updates/removes since yesterday);
* a DELTA feed associates with its FULL feed via ``referenceId``;
* datafeed ids follow ``{datafeedId}_{feedMode}_{grassDate}``;
* ``getItemFeedData`` paginates by ``offset``/``limit`` with a hard maximum
  of 500 per page, returning ``rows[].columns`` (a JSON object serialized
  as a string), ``rows[].updateType`` (DELTA only: NEW/UPDATE/DELETE) and
  ``pageInfo {offset, limit, totalCount, hasMore}``;
* the next offset is computed from the RETURNED offset plus the RETURNED
  limit, never assumed;
* DELTA windows are short — a missed day requires FULL reconciliation.

Engineering rules:

* **Idempotent ingestion** — re-running a page produces the same state;
* **Checkpoints** — every feed's last offset is persisted so a partial run
  resumes instead of restarting;
* **Raw provenance** — the ``columns`` payload is stored verbatim; field
  mappings stay PROVISIONAL until the documented ``columns`` example
  arrives (see the package docstring);
* **Safe reconciliation** — a FULL rebuild only replaces the catalog after
  every page has been consumed and the row count is validated against the
  feed's declared ``totalCount``.
"""

from __future__ import annotations

import json

from .. import store
from . import client

FEEDS_FILE = store.INTEGRATIONS_DIR + "/shopee/feeds.json"
MAX_PAGE_LIMIT = 500
DEFAULT_PAGE_LIMIT = 500
MAX_PAGES_PER_RUN = 40

FEED_MODES = ("FULL", "DELTA")
UPDATE_TYPES = ("NEW", "UPDATE", "DELETE")

# PROVISIONAL identifier extraction: the documented `columns` example is
# still outstanding, so we detect common id fields without committing to a
# final mapping. Anything undetected stays raw + flagged.
_ID_FIELDS = ("itemId", "item_id", "id", "productId", "product_id")
_SHOP_FIELDS = ("shopId", "shop_id", "sellerId")
_NAME_FIELDS = ("productName", "product_name", "name", "title")


class ShopeeFeedsError(store.IntegrationError):
    pass


LIST_FEEDS_QUERY = """
query ListItemFeeds($feedMode: FeedMode!) {
  listItemFeeds(feedMode: $feedMode) {
    totalCount
    datafeeds {
      datafeedId datafeedName referenceId description totalCount date feedMode
    }
  }
}
"""

FEED_DATA_QUERY = """
query GetItemFeedData($datafeedId: String!, $offset: Int!, $limit: Int!) {
  getItemFeedData(datafeedId: $datafeedId, offset: $offset, limit: $limit) {
    rows { columns updateType }
    pageInfo { offset limit totalCount hasMore }
  }
}
"""


# ---------------------------------------------------------------------------
# Feed discovery
# ---------------------------------------------------------------------------
def list_feeds(feed_mode):
    """List feeds for one documented mode; registers them in the feed store."""
    mode = str(feed_mode or "").upper()
    if mode not in FEED_MODES:
        raise ShopeeFeedsError("params_error", "feedMode must be FULL or DELTA.")
    data = client.graphql(client.clean_query(LIST_FEEDS_QUERY), {"feedMode": mode})
    block = (data or {}).get("listItemFeeds") or {}
    feeds = _extract_feed_list(block)
    normalized = [normalize_feed(f, mode) for f in feeds]
    _register_feeds(normalized)
    return {"feed_mode": mode, "feeds": normalized, "total": block.get("total")}


def _extract_feed_list(block):
    """Accept the documented shape plus common connection variants."""
    for key in ("datafeeds", "items", "nodes", "feeds"):
        value = block.get(key)
        if isinstance(value, list):
            return [edge.get("node") if isinstance(edge, dict) and "node" in edge else edge
                    for edge in value]
    edges = block.get("edges")
    if isinstance(edges, list):
        return [edge.get("node") for edge in edges if isinstance(edge, dict)]
    return []


def normalize_feed(feed, mode_hint=None):
    if not isinstance(feed, dict):
        return {}
    return {
        "datafeed_id": feed.get("datafeedId"),
        "datafeed_name": feed.get("datafeedName"),
        "reference_id": feed.get("referenceId"),
        "description": feed.get("description"),
        "total_count": feed.get("totalCount"),
        "date": feed.get("date"),
        "feed_mode": feed.get("feedMode") or mode_hint,
    }


def _register_feeds(feeds):
    with store.lock():
        data = store.read_json(FEEDS_FILE, {})
        if not isinstance(data, dict):
            data = {}
        registry = data.get("feeds") if isinstance(data.get("feeds"), list) else []
        known = {f.get("datafeed_id"): f for f in registry if isinstance(f, dict)}
        for feed in feeds:
            if not feed.get("datafeed_id"):
                continue
            known[feed["datafeed_id"]] = dict(known.get(feed["datafeed_id"]) or {}, **feed)
        store.write_json(FEEDS_FILE, {
            "version": store.SCHEMA_VERSION,
            "feeds": list(known.values())[:500],
            "syncs": data.get("syncs") if isinstance(data.get("syncs"), list) else [],
            "checkpoints": data.get("checkpoints") if isinstance(data.get("checkpoints"), dict) else {},
            "updated_at": store.now(),
            "updated_iso": store.iso(),
        })


def _load_feed_state():
    data = store.read_json(FEEDS_FILE, {})
    if not isinstance(data, dict):
        return [], {}, {}
    return (data.get("feeds") if isinstance(data.get("feeds"), list) else [],
            data.get("checkpoints") if isinstance(data.get("checkpoints"), dict) else {},
            data.get("syncs") if isinstance(data.get("syncs"), list) else [])


def _save_feed_state(feeds, checkpoints, syncs):
    store.write_json(FEEDS_FILE, {
        "version": store.SCHEMA_VERSION,
        "feeds": feeds[:500],
        "checkpoints": checkpoints,
        "syncs": syncs[:200],
        "updated_at": store.now(),
        "updated_iso": store.iso(),
    })


# ---------------------------------------------------------------------------
# Feed data ingestion
# ---------------------------------------------------------------------------
def get_feed_rows(datafeed_id, offset=0, limit=DEFAULT_PAGE_LIMIT):
    """One page of feed rows with documented offset/limit semantics."""
    datafeed_id = store.clean_text(datafeed_id, "datafeedId", 200, required=True)
    offset = max(0, int(offset or 0))
    limit = min(MAX_PAGE_LIMIT, max(1, int(limit or DEFAULT_PAGE_LIMIT)))
    data = client.graphql(client.clean_query(FEED_DATA_QUERY),
                          {"datafeedId": datafeed_id, "offset": offset, "limit": limit})
    block = (data or {}).get("getItemFeedData") or {}
    page_info = block.get("pageInfo") or {}
    rows = []
    for row in block.get("rows") or []:
        rows.append({
            "columns_raw": row.get("columns"),
            "columns": parse_columns(row.get("columns")),
            "update_type": str(row.get("updateType") or "").upper() or None,
        })
    return {
        "datafeed_id": datafeed_id,
        "requested_offset": offset,
        "requested_limit": limit,
        "rows": rows,
        "page_info": {
            "offset": page_info.get("offset", offset),
            "limit": page_info.get("limit", limit),
            "total_count": page_info.get("totalCount"),
            "has_more": bool(page_info.get("hasMore")),
        },
    }


def parse_columns(columns_text):
    """Defensively parse the serialized JSON in ``columns``.

    Malformed payloads never crash ingestion: they are flagged and preserved
    verbatim for later reconciliation once the documented schema is known.
    """
    if columns_text is None:
        return {"_schema": "empty"}
    if isinstance(columns_text, dict):
        return dict(columns_text, _schema="provisional")
    try:
        parsed = json.loads(columns_text)
    except (TypeError, ValueError):
        return {"_schema": "malformed", "_raw": str(columns_text)[:2000]}
    if isinstance(parsed, dict):
        parsed["_schema"] = "provisional"
        return parsed
    return {"_schema": "unexpected_type", "_raw": str(parsed)[:2000]}


def _detect_identity(columns):
    """Provisional identity extraction (itemId if present, else content hash)."""
    for field in _ID_FIELDS:
        if columns.get(field) is not None:
            shop = None
            for shop_field in _SHOP_FIELDS:
                if columns.get(shop_field) is not None:
                    shop = columns[shop_field]
                    break
            return "id:%s|%s" % (columns[field], shop), columns[field], shop
    import hashlib

    digest = hashlib.sha256(json.dumps(columns, sort_keys=True, default=str)
                            .encode("utf-8")).hexdigest()[:20]
    return "hash:%s" % digest, None, None


def apply_row(row, feed_mode):
    """Apply one feed row to the product store. Returns an action label."""
    from . import products as products_store

    columns = row.get("columns") or {}
    identity, item_id, shop_id = _detect_identity(columns)
    update_type = row.get("update_type")
    if feed_mode == "DELTA" and update_type == "DELETE":
        removed = products_store.delete_by_identity(identity)
        return "delete_applied" if removed else "delete_ignored"
    name = None
    for field in _NAME_FIELDS:
        if columns.get(field) is not None:
            name = str(columns[field])[:300]
            break
    record = {
        "item_id": item_id,
        "shop_id": shop_id,
        "identity": identity,
        "normalized": {
            "item_id": item_id,
            "shop_id": shop_id,
            "product_name": name,
            "mapping_version": "provisional",
            "columns_schema": columns.get("_schema"),
        },
        "raw_columns": row.get("columns_raw"),
        "source": "feed:%s" % feed_mode,
    }
    products_store.upsert_feed_record(record)
    if feed_mode == "DELTA" and update_type in UPDATE_TYPES:
        return {"NEW": "created", "UPDATE": "updated"}.get(update_type, "upserted")
    return "upserted"


def sync_feed(datafeed_id, *, feed_mode=None, max_pages=MAX_PAGES_PER_RUN):
    """Checkpointed, idempotent ingestion of one feed.

    Resumes from the stored checkpoint; stops early (keeping the checkpoint)
    when ``max_pages`` is reached so long feeds can sync across runs.
    """
    datafeed_id = store.clean_text(datafeed_id, "datafeedId", 200, required=True)
    feeds, checkpoints, syncs = _load_feed_state()
    feed_meta = next((f for f in feeds if f.get("datafeed_id") == datafeed_id), {})
    mode = (feed_mode or feed_meta.get("feed_mode") or
            ("DELTA" if "_DELTA_" in datafeed_id else "FULL")).upper()
    checkpoint = checkpoints.get(datafeed_id) or {}
    offset = int(checkpoint.get("offset") or 0)
    processed = int(checkpoint.get("rows_processed") or 0)
    actions = {}
    pages = 0
    complete = False
    started = store.now()
    try:
        while pages < max_pages:
            page = get_feed_rows(datafeed_id, offset=offset)
            for row in page["rows"]:
                try:
                    action = apply_row(row, mode)
                except Exception as exc:  # noqa: BLE001 - one bad row never kills a sync
                    action = "error: %s" % store.clean_text(str(exc), "row error", 120)
                actions[action] = actions.get(action, 0) + 1
                processed += 1
            pages += 1
            info = page["page_info"]
            offset = int(info.get("offset") or 0) + int(info.get("limit") or 0)
            checkpoints[datafeed_id] = {
                "offset": offset,
                "rows_processed": processed,
                "updated_at": store.now(),
                "updated_iso": store.iso(),
            }
            if not info.get("has_more"):
                complete = True
                break
    finally:
        sync_record = {
            "id": store.new_id("shfs_"),
            "datafeed_id": datafeed_id,
            "feed_mode": mode,
            "pages": pages,
            "rows_processed": processed,
            "actions": actions,
            "complete": complete,
            "started_iso": store.iso(started),
            "finished_iso": store.iso(),
            "duration_ms": int((store.now() - started) * 1000),
        }
        syncs.insert(0, sync_record)
        _save_feed_state(feeds, checkpoints, syncs)
    if not complete and pages >= max_pages:
        return {"status": "partial", "datafeed_id": datafeed_id, "pages": pages,
                "rows_processed": processed, "actions": actions,
                "note": "Checkpoint saved; re-run to continue."}
    return {"status": "complete", "datafeed_id": datafeed_id, "pages": pages,
            "rows_processed": processed, "actions": actions}


def reconcile(full_datafeed_id, *, expected_total=None):
    """FULL reconciliation: rebuild only on validated completeness.

    The staged catalog replaces the live one only when every page has been
    consumed (``hasMore`` false) and, when the feed declares a ``totalCount``,
    the row count matches it. Otherwise the existing catalog is untouched.
    """
    full_datafeed_id = store.clean_text(full_datafeed_id, "datafeedId", 200, required=True)
    from . import products as products_store

    offset = 0
    staged = []
    pages = 0
    while True:
        if pages >= MAX_PAGES_PER_RUN * 4:
            raise ShopeeFeedsError(
                "reconcile_incomplete",
                "Reconciliation aborted after %d pages; catalog untouched." % pages)
        page = get_feed_rows(full_datafeed_id, offset=offset)
        staged.extend(row["columns_raw"] for row in page["rows"])
        pages += 1
        info = page["page_info"]
        offset = int(info.get("offset") or 0) + int(info.get("limit") or 0)
        total = info.get("total_count")
        if total is not None:
            expected_total = int(total)
        if not info.get("has_more"):
            break
    if expected_total is not None and len(staged) != int(expected_total):
        raise ShopeeFeedsError(
            "reconcile_incomplete",
            "Fetched %d rows but the feed declares %s; catalog untouched."
            % (len(staged), expected_total))
    replaced = products_store.replace_from_feed(staged, source="reconcile")
    syncs = None
    feeds, checkpoints, syncs = _load_feed_state()
    syncs.insert(0, {
        "id": store.new_id("shfr_"),
        "datafeed_id": full_datafeed_id,
        "feed_mode": "FULL",
        "kind": "reconcile",
        "rows_processed": replaced,
        "complete": True,
        "finished_iso": store.iso(),
    })
    _save_feed_state(feeds, checkpoints, syncs)
    return {"status": "reconciled", "datafeed_id": full_datafeed_id,
            "rows": replaced, "pages": pages}


def check_delta_continuity():
    """Detect missed DELTA windows against the newest FULL feed.

    A DELTA feed's ``date`` that is missing from the sync history between the
    last FULL sync and now means incremental sync became unreliable — the
    documented remedy is FULL reconciliation.
    """
    feeds, checkpoints, syncs = _load_feed_state()
    full_feeds = sorted(
        (f for f in feeds if (f.get("feed_mode") or "").upper() == "FULL" and f.get("date")),
        key=lambda f: str(f.get("date")), reverse=True)
    newest_full = full_feeds[0] if full_feeds else None
    synced = {s.get("datafeed_id") for s in syncs}
    missing = [
        {"datafeed_id": f.get("datafeed_id"), "date": f.get("date"),
         "reference_id": f.get("reference_id")}
        for f in feeds
        if (f.get("feed_mode") or "").upper() == "DELTA"
        and f.get("date")
        and newest_full and str(f.get("date")) > str(newest_full.get("date"))
        and f.get("datafeed_id") not in synced
    ]
    return {
        "newest_full": newest_full,
        "missing_deltas": missing,
        "reconciliation_recommended": bool(missing),
    }


def sync_status():
    feeds, checkpoints, syncs = _load_feed_state()
    return {
        "feeds": feeds,
        "checkpoints": checkpoints,
        "recent_syncs": syncs[:20],
        "continuity": check_delta_continuity(),
    }
