"""Ads Studio Create — intent-driven creative briefs.

The Create dashboard turns a natural-language request plus structured fields
into a reviewable creative brief. It never renders, publishes or charges
anything. ``handoff`` creates an Ads workspace project and returns the prompt
the owner can carry into the existing five-step Generate workflow.

This module is additive: the Generate workflow, its job store and its provider
pipeline are untouched.
"""

from __future__ import annotations

import copy
import os

from . import ads_backlog
from . import cosmos_common as common
from . import cosmos_config
from . import studio_workspaces

PREFIX = "/api/studio/ads/create"
CREATE_DIR = os.path.join(common.COSMOS_DIR, "..", "studio", "ads")
BRIEFS_FILE = os.path.join(os.path.normpath(CREATE_DIR), "create-briefs.json")
MAX_BRIEFS = 400

STATUSES = ("draft", "handed_off", "archived")


class CreateError(common.CosmosError):
    pass


def _load():
    data = common.read_json(BRIEFS_FILE, {})
    if not data:
        return []
    if data.get("version") != common.SCHEMA_VERSION:
        raise CreateError("briefs_version", "Create briefs use an unsupported version.", 500)
    items = data.get("briefs")
    if not isinstance(items, list):
        raise CreateError("briefs_invalid", "Create briefs have an unsupported format.", 500)
    return [item for item in items if isinstance(item, dict) and item.get("id")]


def _save(items):
    common.write_json(BRIEFS_FILE, {
        "version": common.SCHEMA_VERSION,
        "briefs": items[-MAX_BRIEFS:],
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    })


def _active_template():
    config = cosmos_config.config_public()
    template_id = config.get("active_ads_template_id")
    try:
        return cosmos_config.get_template("ads", template_id)
    except Exception:
        return {}


def _clean_url(value, label):
    url = common.clean_text(value, label, 1000)
    if url and not url.lower().startswith(("http://", "https://")):
        raise CreateError("invalid_url", "%s must be an http(s) URL." % label)
    return url or None


def _product_snapshot(payload, template):
    manual = payload.get("product") if isinstance(payload.get("product"), dict) else {}
    product_id = common.clean_text(payload.get("product_id"), "Product", 80) or common.clean_text(
        manual.get("product_id"), "Product", 80
    )
    if product_id:
        product = ads_backlog.get_product(product_id)
        if product.get("status") == "archived":
            raise CreateError("product_archived", "Archived products cannot be used for a new brief.", 409)
        return {
            "source": "backlog",
            "id": product["id"],
            "name": product.get("name"),
            "category": product.get("category"),
            "merchant": product.get("merchant"),
            "description": product.get("description"),
            "price": copy.deepcopy(product.get("price")),
            "product_url": product.get("product_url"),
            "image_url": product.get("image_url"),
            "target_audience": product.get("target_audience"),
        }
    name = common.clean_text(manual.get("name"), "Product name", 200)
    if not name:
        raise CreateError("product_required", "Select a product from the backlog or enter a product name.", 400)
    price = None
    raw_price = manual.get("price")
    # The backlog stores price as {"amount", "currency"}; accept that shape
    # here too so a form can post the same object to both endpoints.
    if isinstance(raw_price, dict):
        raw_amount = raw_price.get("amount")
        raw_currency = raw_price.get("currency")
        raw_verified = bool(raw_price.get("verified")) or bool(manual.get("price_verified"))
    else:
        raw_amount = raw_price
        raw_currency = manual.get("currency")
        raw_verified = bool(manual.get("price_verified"))
    if raw_amount not in (None, ""):
        price = {
            "amount": round(common.clamp_float(raw_amount, 0, 100_000_000, 0), 2),
            "currency": common.clean_text(raw_currency, "Currency", 8)
            or (template.get("price_range") or {}).get("currency")
            or "PHP",
            "verified": raw_verified,
        }
    return {
        "source": "manual",
        "id": None,
        "name": name,
        "category": common.clean_text(manual.get("category"), "Category", 160)
        or template.get("product_category"),
        "merchant": common.clean_text(manual.get("merchant"), "Merchant", 160) or None,
        "description": common.clean_text(manual.get("description"), "Description", 3000),
        "price": price,
        "product_url": _clean_url(manual.get("product_url"), "Product URL"),
        "image_url": _clean_url(manual.get("image_url"), "Image URL"),
        "target_audience": common.clean_text(manual.get("target_audience"), "Target audience", 600)
        or template.get("target_audience"),
    }


def _affiliate(value):
    if not isinstance(value, dict):
        return {"status": "none", "url": None, "program": None, "note": None}
    url = _clean_url(value.get("url"), "Affiliate URL")
    program = common.clean_text(value.get("program"), "Affiliate program", 200) or None
    validated = value.get("validated") is True
    if not url:
        return {"status": "none", "url": None, "program": program, "note": None}
    if validated and program:
        return {
            "status": "validated",
            "url": url,
            "program": program,
            "note": "Owner confirmed this is a validated affiliate link for the selected product.",
        }
    return {
        "status": "unvalidated",
        "url": url,
        "program": program,
        "note": "Not validated. The link is excluded from the creative prompt until the owner validates it.",
    }


def _disclosures(values, affiliate, sponsored):
    items = common.clean_str_list(values, "Disclosures", 12, 300)
    if affiliate.get("status") == "validated":
        text = "Contains affiliate links. The owner may earn a commission."
        if text not in items:
            items.append(text)
    if sponsored:
        text = "Sponsored content. The owner received compensation."
        if text not in items:
            items.append(text)
    return items


def _compose_prompt(instruction, product, template, fields):
    platforms = ", ".join(fields["target_platforms"]) or "the target platforms"
    price = ""
    if product.get("price") and product["price"].get("amount") is not None:
        price = " Price: %s %s%s." % (
            product["price"].get("currency") or "",
            product["price"].get("amount"),
            " (verified)" if product["price"].get("verified") else " (unverified)",
        )
    lines = [
        instruction or "Create a short product video for the selected product.",
        "",
        "PRODUCT",
        "Name: %s" % product.get("name"),
        "Category: %s" % (product.get("category") or "not specified"),
        "Merchant: %s" % (product.get("merchant") or "not specified"),
        "Description: %s" % (product.get("description") or "not specified"),
    ]
    if price:
        lines.append(price.strip())
    lines += [
        "",
        "CREATIVE DIRECTION",
        "Style: %s" % fields["creative_style"],
        "Audience: %s" % fields["target_audience"],
        "Platforms: %s" % platforms,
        "Format: %s" % fields["video_format"],
        "Duration: %s seconds" % fields["duration_seconds"],
        "Template: %s (%s)" % (template.get("name") or fields["template_id"], fields["template_id"]),
    ]
    if fields["reference_assets"]:
        lines.append("Reference assets: %s" % ", ".join(fields["reference_assets"]))
    if fields["affiliate"]["status"] == "validated":
        lines.append("Affiliate program: %s — include the owner-supplied tracking link in the caption." % fields["affiliate"]["program"])
    else:
        lines.append("Affiliate link: none validated; do not invent a link or a commission claim.")
    if fields["disclosures"]:
        lines.append("Required disclosures: %s" % "; ".join(fields["disclosures"]))
    lines += [
        "",
        "CONSTRAINTS",
        "- Do not invent product claims, prices, reviews or certifications.",
        "- Follow the active Ads template's restrictions.",
        "- This is a creative brief for owner review; do not publish automatically.",
    ]
    return "\n".join(lines)


def create_brief(payload):
    if not isinstance(payload, dict):
        raise CreateError("invalid_brief", "Brief payload must be a JSON object.")
    instruction = common.clean_text(payload.get("instruction"), "Instruction", 4000)
    template = _active_template()
    template_id = common.clean_text(payload.get("template_id"), "Template", 80) or template.get("id")
    if template_id and template.get("id") != template_id:
        try:
            template = cosmos_config.get_template("ads", template_id)
        except Exception:
            raise CreateError("template_not_found", "Ads template not found.", 404)
    product = _product_snapshot(payload, template)
    if not instruction and not product.get("name"):
        raise CreateError("instruction_required", "Describe what you want to create.", 400)
    affiliate = _affiliate(payload.get("affiliate_destination"))
    target_platforms = common.clean_str_list(
        payload.get("target_platforms"), "Target platforms", 8, 60
    ) or list(template.get("target_platforms") or [])
    fields = {
        "template_id": template.get("id"),
        "creative_style": common.clean_text(payload.get("creative_style"), "Creative style", 500)
        or template.get("creative_style") or "",
        "target_audience": common.clean_text(payload.get("target_audience"), "Target audience", 600)
        or product.get("target_audience") or template.get("target_audience") or "",
        "target_platforms": target_platforms,
        "video_format": common.clean_text(payload.get("video_format"), "Video format", 300)
        or template.get("video_format") or "9:16 short-form product video",
        "duration_seconds": common.clamp_int(payload.get("duration_seconds"), 5, 180, 15),
        "reference_assets": [
            url for url in (
                _clean_url(item, "Reference asset") for item in (payload.get("reference_assets") or [])[:8]
            ) if url
        ],
        "affiliate": affiliate,
        "disclosures": _disclosures(payload.get("disclosures"), affiliate, bool(payload.get("sponsored"))),
    }
    prompt = _compose_prompt(instruction, product, template, fields)
    stamp = common.now()
    brief = {
        "id": common.new_id("crb_"),
        "instruction": instruction,
        "product": product,
        "template_id": template_id,
        "template_name": template.get("name"),
        "creative_style": fields["creative_style"],
        "target_audience": fields["target_audience"],
        "target_platforms": fields["target_platforms"],
        "video_format": fields["video_format"],
        "duration_seconds": fields["duration_seconds"],
        "reference_assets": fields["reference_assets"],
        "affiliate_destination": fields["affiliate"],
        "disclosures": fields["disclosures"],
        "sponsored": bool(payload.get("sponsored")),
        "prompt": prompt,
        "status": "draft",
        "handoff_project_id": None,
        "handoff_iso": None,
        "created_at": stamp,
        "created_iso": common.iso(stamp),
        "updated_at": stamp,
        "updated_iso": common.iso(stamp),
        "deleted_at": None,
    }
    with common.acquire():
        items = _load()
        items.append(brief)
        _save(items)
    return {"brief": copy.deepcopy(brief)}


def list_briefs(limit=100):
    with common.acquire():
        items = _load()
    items = [item for item in items if not item.get("deleted_at")]
    items.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    try:
        requested = int(limit or 100)
    except (TypeError, ValueError):
        requested = 100
    return {"briefs": copy.deepcopy(items[:max(1, min(requested, MAX_BRIEFS))]), "total": len(items)}


def get_brief(brief_id):
    with common.acquire():
        items = _load()
    for item in items:
        if item.get("id") == str(brief_id or "") and not item.get("deleted_at"):
            return {"brief": copy.deepcopy(item)}
    raise CreateError("brief_not_found", "Creative brief not found.", 404)


def delete_brief(brief_id):
    with common.acquire():
        items = _load()
        brief = next((item for item in items if item.get("id") == str(brief_id or "") and not item.get("deleted_at")), None)
        if not brief:
            raise CreateError("brief_not_found", "Creative brief not found.", 404)
        brief["deleted_at"] = common.now()
        brief["deleted_iso"] = common.iso()
        brief["updated_at"] = brief["deleted_at"]
        brief["updated_iso"] = brief["deleted_iso"]
        _save(items)
    return {"deleted": brief["id"]}


def handoff(brief_id):
    data = get_brief(brief_id)
    brief = data["brief"]
    if brief.get("handoff_project_id"):
        try:
            project = studio_workspaces.get_project("ads", brief["handoff_project_id"])
        except Exception:
            project = None
        if project:
            return {
                "brief": brief,
                "project": project,
                "prefill": {
                    "prompt": brief.get("prompt"),
                    "brief_id": brief["id"],
                    "product": brief.get("product"),
                },
                "already_handed_off": True,
            }
    product = brief.get("product") or {}
    title = "Ad creative — %s" % (product.get("name") or "Untitled product")
    project = studio_workspaces.create_project("ads", {
        "title": title[:160],
        "description": brief.get("prompt") or brief.get("instruction") or "",
        "content_pillar": brief.get("template_name") or brief.get("template_id") or "",
        "status": "draft",
        "stage": "script",
        "tags": ["ads-create"],
        "metadata": {
            "brief_id": brief["id"],
            "template_id": brief.get("template_id"),
            "product": product,
            "target_platforms": brief.get("target_platforms"),
            "video_format": brief.get("video_format"),
            "duration_seconds": brief.get("duration_seconds"),
            "disclosures": brief.get("disclosures"),
            "affiliate_status": (brief.get("affiliate_destination") or {}).get("status"),
            "prompt": brief.get("prompt"),
            "source": "cosmos_ads_create",
        },
    })
    with common.acquire():
        items = _load()
        stored = next((item for item in items if item.get("id") == brief["id"]), None)
        if stored:
            stored["handoff_project_id"] = project["id"]
            stored["handoff_iso"] = common.iso()
            stored["status"] = "handed_off"
            stored["updated_at"] = common.now()
            stored["updated_iso"] = common.iso()
            _save(items)
            brief = copy.deepcopy(stored)
    return {
        "brief": brief,
        "project": project,
        "prefill": {
            "prompt": brief.get("prompt"),
            "brief_id": brief["id"],
            "product": brief.get("product"),
        },
        "already_handed_off": False,
    }


def context():
    template = _active_template()
    try:
        products = ads_backlog.list_products(limit=200).get("products", [])
    except Exception:
        products = []
    return {
        "template": template,
        "products": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "category": item.get("category"),
                "merchant": item.get("merchant"),
                "price": item.get("price"),
                "status": item.get("status"),
                "image_url": item.get("image_url"),
            }
            for item in products
        ],
        "platforms": template.get("target_platforms") or [],
        "duration_default": 15,
    }


def handle(method, path, query=None, body=None):
    subpath = path[len(PREFIX):].strip("/")
    parts = [part for part in subpath.split("/") if part]
    try:
        if method == "GET":
            if not parts or parts[0] == "context":
                return 200, context()
            if parts[0] == "briefs" and len(parts) == 1:
                limit = None
                try:
                    limit = (query or {}).get("limit", [100])[0]
                except AttributeError:
                    limit = 100
                return 200, list_briefs(limit)
            if parts[0] == "briefs" and len(parts) == 2:
                return 200, get_brief(parts[1])
        if method == "POST" and parts and parts[0] == "briefs":
            if len(parts) == 1:
                return 200, create_brief(body or {})
            if len(parts) == 3 and parts[2] == "handoff":
                return 200, handoff(parts[1])
        if method == "DELETE" and parts and parts[0] == "briefs" and len(parts) == 2:
            return 200, delete_brief(parts[1])
        return 404, {"error": "Create endpoint not found.", "code": "not_found"}
    except Exception as exc:
        if isinstance(exc, common.CosmosError):
            return exc.status if 400 <= exc.status <= 599 else 400, {
                "error": exc.message,
                "code": exc.code,
            }
        return 500, {"error": str(exc) or "create_error", "code": "create_error"}
