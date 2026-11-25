"""Cross-platform campaign links: Shopee products <-> YouTube videos.

The explicit relationship between a product and a video. Provenance fields
are stored verbatim (Shopee item/shop ids, product and affiliate offer
links, YouTube channel/video ids and URLs, publication timestamps,
campaign identifiers) so downstream analysis never has to guess.

**Attribution honesty:** stored performance snapshots place each platform's
retrieved metrics side by side. A YouTube view is never treated as a Shopee
conversion and vice versa — cross-platform attribution requires tracking
data the platforms actually return, and anything unavailable is reported
as unavailable, never as zero.
"""

from __future__ import annotations

from .. import studio_contracts as contracts
from . import store

LINKS_FILE = store.INTEGRATIONS_DIR + "/links.json"
MAX_LINKS = 2000

STATUSES = ("proposed", "in_production", "published", "archived")


class LinkError(store.IntegrationError):
    pass


def create_link(product=None, *, campaign=None, notes=None, status="proposed"):
    """Create a link record seeded with Shopee product provenance."""
    product = product or {}
    if not isinstance(product, dict):
        raise LinkError("invalid_field", "product must be an object.")
    status_value = store.clean_text(status, "Status", 30, required=True)
    if status_value not in STATUSES:
        raise LinkError("invalid_status", "Status must be one of: %s." % ", ".join(STATUSES))
    row = {
        "id": store.new_id("link_"),
        "status": status_value,
        "campaign": store.clean_text(campaign, "Campaign", 120) or None,
        "notes": store.clean_text(notes, "Notes", 500) or None,
        "shopee": {
            "item_id": product.get("item_id") or product.get("itemId"),
            "shop_id": product.get("shop_id") or product.get("shopId"),
            "product_name": product.get("product_name") or product.get("productName"),
            "product_link": product.get("product_link") or product.get("productLink"),
            "offer_link": product.get("offer_link") or product.get("offerLink"),
        },
        "youtube": None,
        "performance": None,
        "created_at": store.now(),
        "created_iso": store.iso(),
        "updated_at": store.now(),
        "updated_iso": store.iso(),
        "history": [{"event": "created", "at_iso": store.iso()}],
    }
    if not (row["shopee"].get("item_id") or row["shopee"].get("product_link")):
        raise LinkError("invalid_field", "A Shopee item id or product link is required.")
    with store.lock():
        items = store.load_store(LINKS_FILE, "links")
        items.insert(0, row)
        store.save_store(LINKS_FILE, "links", items[:MAX_LINKS])
    return public(row)


def attach_video(link_id, video=None, *, status="in_production"):
    """Attach YouTube provenance (video id/url/channel) to a link."""
    video = video or {}
    if not isinstance(video, dict):
        raise LinkError("invalid_field", "video must be an object.")
    status_value = store.clean_text(status, "Status", 30, required=True)
    if status_value not in STATUSES:
        raise LinkError("invalid_status", "Status must be one of: %s." % ", ".join(STATUSES))
    youtube_block = {
        "channel_id": video.get("channel_id") or video.get("channelId"),
        "video_id": video.get("video_id") or video.get("videoId"),
        "video_url": video.get("video_url") or video.get("videoUrl"),
        "title": video.get("title"),
        "published_at": video.get("published_at") or video.get("publishedAt"),
        "privacy": video.get("privacy"),
    }
    if not (youtube_block.get("video_id") or youtube_block.get("video_url")):
        raise LinkError("invalid_field", "A YouTube video id or url is required.")
    with store.lock():
        items = store.load_store(LINKS_FILE, "links")
        row = next((i for i in items if i.get("id") == link_id), None)
        if not row:
            raise LinkError("link_not_found", "Link record not found.", 404)
        row["youtube"] = youtube_block
        row["status"] = status_value
        row["updated_at"] = store.now()
        row["updated_iso"] = store.iso()
        row.setdefault("history", []).append(
            {"event": "video_attached",
             "video_id": youtube_block.get("video_id"),
             "at_iso": store.iso()})
        store.save_store(LINKS_FILE, "links", items)
        return public(row)


def record_performance(link_id, snapshot):
    """Store one side-by-side performance snapshot (honest availability)."""
    if not isinstance(snapshot, dict):
        raise LinkError("invalid_field", "snapshot must be an object.")
    safe = contracts.redact(snapshot)
    safe["recorded_iso"] = store.iso()
    with store.lock():
        items = store.load_store(LINKS_FILE, "links")
        row = next((i for i in items if i.get("id") == link_id), None)
        if not row:
            raise LinkError("link_not_found", "Link record not found.", 404)
        history = row.get("performance") if isinstance(row.get("performance"), list) else []
        history.insert(0, safe)
        row["performance"] = history[:50]
        row["updated_at"] = store.now()
        row["updated_iso"] = store.iso()
        row.setdefault("history", []).append({"event": "performance_recorded",
                                              "at_iso": store.iso()})
        store.save_store(LINKS_FILE, "links", items)
        return public(row)


def get(link_id):
    with store.lock():
        items = store.load_store(LINKS_FILE, "links")
    for row in items:
        if row.get("id") == link_id:
            return public(row)
    return None


def list_links(limit=50, status=None, campaign=None):
    with store.lock():
        items = store.load_store(LINKS_FILE, "links")
    wanted_status = store.clean_text(status, "Status", 30)
    wanted_campaign = store.clean_text(campaign, "Campaign", 120).lower()
    rows = []
    for row in items:
        if wanted_status and row.get("status") != wanted_status:
            continue
        if wanted_campaign and str(row.get("campaign") or "").lower() != wanted_campaign:
            continue
        rows.append(row)
        if len(rows) >= max(1, min(int(limit or 50), 200)):
            break
    return [public(row) for row in rows]


def set_status(link_id, status):
    status_value = store.clean_text(status, "Status", 30, required=True)
    if status_value not in STATUSES:
        raise LinkError("invalid_status", "Status must be one of: %s." % ", ".join(STATUSES))
    with store.lock():
        items = store.load_store(LINKS_FILE, "links")
        row = next((i for i in items if i.get("id") == link_id), None)
        if not row:
            raise LinkError("link_not_found", "Link record not found.", 404)
        row["status"] = status_value
        row["updated_at"] = store.now()
        row["updated_iso"] = store.iso()
        row.setdefault("history", []).append({"event": "status:" + status_value,
                                              "at_iso": store.iso()})
        store.save_store(LINKS_FILE, "links", items)
        return public(row)


def build_performance_snapshot(link_row):
    """Collect what both platforms can currently return — never fabricates.

    YouTube metrics come from the connected account through the shared
    Composio adapter; Shopee conversion data requires the configured
    Affiliate API plus the documented conversion report (still an
    outstanding documentation item). Anything unavailable says so.
    """
    snapshot = {
        "youtube": {"available": False, "note": ""},
        "shopee": {"available": False, "note": ""},
        "attribution": "none — platforms do not attribute views to conversions here",
    }
    video_id = ((link_row.get("youtube") or {}).get("video_id")) if link_row.get("youtube") else None
    if video_id:
        from . import youtube

        state = youtube.connection_state()
        if state.get("execution_available"):
            try:
                result = youtube.run("video_details", {"id": [video_id], "part": "snippet,statistics"})
                snapshot["youtube"] = {"available": True, "video_details": result.get("data")}
            except Exception as exc:  # noqa: BLE001 - honest degradation
                snapshot["youtube"] = {"available": False, "note": str(exc)[:300]}
        else:
            snapshot["youtube"] = {"available": False,
                                   "note": state.get("blocked_reason") or "execution transport unavailable"}
    else:
        snapshot["youtube"] = {"available": False, "note": "No video attached yet."}
    from .shopee import auth as shopee_auth

    if not shopee_auth.public_state()["configured"]:
        snapshot["shopee"] = {
            "available": False,
            "note": "Shopee credentials not configured; the conversion-report "
                    "documentation is also an outstanding item.",
        }
    else:
        snapshot["shopee"] = {
            "available": False,
            "note": "Get Conversion Report documentation outstanding — no invented metrics.",
        }
    return snapshot


def public(row):
    import json

    return json.loads(json.dumps(row))
