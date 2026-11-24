"""Configuration-driven domain adapters for COSMOS.

Two independent domains, one shared shape:

* **Ads** — five editable commerce templates. The effective configuration is
  the selected Ads template plus the Ads Studio workspace settings.
* **YouTube** — the selected channel configuration plus one of five editable
  video templates. Existing channels are managed by ``youtube_channels``.

Every execution stores the effective snapshot it ran with. Editing a template
changes subsequent executions only; historical records keep their snapshot.

Configuration priority (documented and enforced by the prompt builder):

1. explicit user instruction for the current task;
2. active channel / Ads configuration;
3. selected template defaults;
4. system defaults.
"""

from __future__ import annotations

import copy

from . import cosmos_common as common
from . import studio_workspaces

ADS_TEMPLATE_FIELDS = (
    "name",
    "description",
    "product_category",
    "target_market",
    "target_audience",
    "affiliate_strategy",
    "creative_style",
    "video_format",
    "language",
    "region",
)

ADS_LIST_FIELDS = (
    "product_attributes",
    "merchants",
    "marketplaces",
    "target_platforms",
    "product_restrictions",
    "product_interests",
    "creative_directions",
)

YOUTUBE_TEMPLATE_FIELDS = (
    "name",
    "description",
    "video_style",
    "format",
    "visual_approach",
    "script_direction",
)

YOUTUBE_LIST_FIELDS = (
    "structure",
    "audience_interests",
    "restrictions",
)

DEFAULT_ADS_TEMPLATES = [
    {
        "id": "womens-apparel",
        "name": "Women's Apparel",
        "description": "Fashion and apparel products for women, focused on wearable everyday and seasonal outfits.",
        "product_category": "Women's fashion and apparel",
        "target_market": "Philippines",
        "target_audience": "Women aged 18-35 who shop fashion on social commerce platforms and value affordable, wearable outfits.",
        "price_range": {"min": 200, "max": 2500, "currency": "PHP"},
        "product_attributes": ["breathable fabric", "true-to-size fit", "versatile styling", "easy care"],
        "merchants": ["Shopee", "Lazada", "TikTok Shop"],
        "marketplaces": ["Shopee", "Lazada", "TikTok Shop", "Facebook Marketplace"],
        "affiliate_strategy": "Only use affiliate links from programs the owner has explicitly joined and validated. A product listing is not an affiliate relationship.",
        "creative_style": "Warm, natural daylight; confident mirror try-on and outfit demonstration; no misleading body claims.",
        "video_format": "9:16 short-form try-on / outfit showcase",
        "target_platforms": ["TikTok", "Instagram Reels", "Facebook Reels"],
        "language": "en-PH",
        "region": "PH",
        "product_restrictions": ["No counterfeit goods", "No medical or guaranteed weight-loss claims", "No unverified celebrity endorsements"],
        "product_interests": ["Dresses", "Casual outfits", "Office wear", "Fashion accessories", "Seasonal apparel"],
        "creative_directions": ["Outfit demonstrations", "Mirror try-on", "Outfit comparisons", "Product showcases", "Fashion recommendations"],
    },
    {
        "id": "beauty-skincare",
        "name": "Beauty & Skincare",
        "description": "Skincare, makeup and personal-care products with a strong evidence and disclosure requirement.",
        "product_category": "Beauty and skincare",
        "target_market": "Philippines",
        "target_audience": "Adults aged 18-40 interested in accessible skincare routines and everyday makeup.",
        "price_range": {"min": 150, "max": 3000, "currency": "PHP"},
        "product_attributes": ["ingredient transparency", "skin-type suitability", "dermatologist-tested where documented", "local availability"],
        "merchants": ["Watsons", "Shopee", "Lazada", "TikTok Shop"],
        "marketplaces": ["Shopee", "Lazada", "TikTok Shop", "Watsons"],
        "affiliate_strategy": "Affiliate links require a validated program and an approved product. Never imply medical outcomes.",
        "creative_style": "Clean, well-lit vanity or bathroom setting; honest texture shots; no before/after manipulation.",
        "video_format": "9:16 short-form routine, texture and first-impression demo",
        "target_platforms": ["TikTok", "Instagram Reels", "YouTube Shorts"],
        "language": "en-PH",
        "region": "PH",
        "product_restrictions": ["No medical claims", "No guaranteed results", "No counterfeit or unregistered products", "No unverified before/after imagery"],
        "product_interests": ["Cleansers", "Sunscreen", "Moisturisers", "Makeup essentials", "Hair care"],
        "creative_directions": ["Routine demonstration", "Texture close-up", "Ingredient explainer", "Product comparison", "First-impression review"],
    },
    {
        "id": "electronics-gadgets",
        "name": "Electronics & Gadgets",
        "description": "Consumer electronics and practical gadgets with specification-led demonstration.",
        "product_category": "Consumer electronics and gadgets",
        "target_market": "Philippines",
        "target_audience": "Adults aged 20-45 who buy practical tech for work, travel or home use.",
        "price_range": {"min": 500, "max": 15000, "currency": "PHP"},
        "product_attributes": ["documented specifications", "warranty where available", "local compatibility", "value for money"],
        "merchants": ["Shopee", "Lazada", "Official brand stores"],
        "marketplaces": ["Shopee", "Lazada", "TikTok Shop"],
        "affiliate_strategy": "Only link to programs the owner has joined; use official product pages where possible.",
        "creative_style": "Clear desk or lifestyle demonstration; show the actual product; avoid impossible performance claims.",
        "video_format": "9:16 short-form unboxing, setup and real-use demonstration",
        "target_platforms": ["TikTok", "YouTube Shorts", "Facebook Reels"],
        "language": "en-PH",
        "region": "PH",
        "product_restrictions": ["No unverified performance claims", "No counterfeit products", "No warranty misrepresentation"],
        "product_interests": ["Phone accessories", "Audio", "Chargers and power banks", "Smart home", "Productivity gadgets"],
        "creative_directions": ["Unboxing", "Setup walkthrough", "Real-use test", "Comparison", "Value breakdown"],
    },
    {
        "id": "home-living",
        "name": "Home & Living",
        "description": "Home organisation, decor and practical living products for small spaces.",
        "product_category": "Home and living",
        "target_market": "Philippines",
        "target_audience": "Adults aged 22-45 setting up apartments, condos or family homes.",
        "price_range": {"min": 100, "max": 8000, "currency": "PHP"},
        "product_attributes": ["space efficiency", "durability", "easy assembly", "renter-friendly"],
        "merchants": ["Shopee", "Lazada", "IKEA", "TikTok Shop"],
        "marketplaces": ["Shopee", "Lazada", "TikTok Shop"],
        "affiliate_strategy": "Affiliate links require a validated program; do not imply installation services the merchant does not provide.",
        "creative_style": "Bright, lived-in home setting; before/after organisation; honest size and scale.",
        "video_format": "9:16 short-form makeover, organisation and product-in-use",
        "target_platforms": ["TikTok", "Instagram Reels", "Pinterest", "Facebook Reels"],
        "language": "en-PH",
        "region": "PH",
        "product_restrictions": ["No safety or structural claims without evidence", "No counterfeit brands"],
        "product_interests": ["Storage and organisation", "Kitchen tools", "Small-space furniture", "Decor", "Cleaning tools"],
        "creative_directions": ["Room makeover", "Organisation before/after", "Product-in-use", "Small-space solution", "Comparison"],
    },
    {
        "id": "health-lifestyle",
        "name": "Health & Lifestyle",
        "description": "Everyday wellness, fitness and lifestyle products with strict claim and disclosure controls.",
        "product_category": "Health and lifestyle",
        "target_market": "Philippines",
        "target_audience": "Adults aged 20-45 building sustainable everyday health and fitness routines.",
        "price_range": {"min": 200, "max": 6000, "currency": "PHP"},
        "product_attributes": ["ingredient or material transparency", "beginner-friendly", "local availability", "documented usage"],
        "merchants": ["Shopee", "Lazada", "Watsons", "Official brand stores"],
        "marketplaces": ["Shopee", "Lazada", "TikTok Shop", "Watsons"],
        "affiliate_strategy": "Affiliate links require a validated program. Health products may require regulatory review before promotion.",
        "creative_style": "Calm, realistic routine demonstration; no medical authority impression; no guaranteed outcomes.",
        "video_format": "9:16 short-form routine, usage demonstration and honest review",
        "target_platforms": ["TikTok", "Instagram Reels", "YouTube Shorts"],
        "language": "en-PH",
        "region": "PH",
        "product_restrictions": ["No medical advice", "No guaranteed health outcomes", "No unregistered supplements", "No fear-based claims"],
        "product_interests": ["Fitness accessories", "Hydration", "Sleep and recovery", "Meal preparation", "Everyday wellness"],
        "creative_directions": ["Routine demonstration", "Beginner walkthrough", "Honest review", "Comparison", "Habit-building story"],
    },
]

DEFAULT_YOUTUBE_TEMPLATES = [
    {
        "id": "educational-video",
        "name": "Educational Video",
        "description": "A structured explainer that answers one audience question with practical, sourced teaching.",
        "video_style": "Animated educational explainer",
        "format": "longform",
        "default_duration_min": 12,
        "structure": ["Hook", "The question", "Step-by-step explanation", "Worked example", "Common mistakes", "Recap", "Next step"],
        "visual_approach": "Clean animated diagrams, labelled examples and chapter cards; no stock-photo filler.",
        "script_direction": "Plain language, one idea per section, concrete examples, explicit sources and no fabricated credentials.",
        "audience_interests": ["Practical tutorials", "Beginner explainers", "Tool walkthroughs", "Common mistakes"],
        "restrictions": ["No fabricated statistics", "No guaranteed outcomes", "No individualized professional advice"],
    },
    {
        "id": "documentary-explainer",
        "name": "Documentary / Explainer",
        "description": "A narrative documentary explainer that investigates a question across evidence and perspectives.",
        "video_style": "Documentary-style explainer with archival and animated sequences",
        "format": "longform",
        "default_duration_min": 18,
        "structure": ["Cold open", "Central question", "Context", "Evidence chapters", "Counterpoint", "Synthesis", "Closing implication"],
        "visual_approach": "Cinematic b-roll direction, archival source cards, maps and timeline graphics.",
        "script_direction": "Narrative but factual; attribute every claim; separate verified facts from interpretation.",
        "audience_interests": ["Investigations", "History and context", "Business stories", "Evidence-led analysis"],
        "restrictions": ["No fabricated interviews", "No unlicensed archival footage", "No sensational unsupported claims"],
    },
    {
        "id": "faceless-youtube",
        "name": "Faceless YouTube",
        "description": "Voice-led, screen-and-motion content that needs no on-camera presenter.",
        "video_style": "Faceless voiceover with screen recordings, motion graphics and b-roll",
        "format": "longform",
        "default_duration_min": 10,
        "structure": ["Hook", "Promise", "Demonstration", "Evidence", "Practical takeaway", "CTA"],
        "visual_approach": "Screen capture, UI zooms, kinetic typography and simple diagrams; consistent brand palette.",
        "script_direction": "Conversational voiceover written for the ear; short sentences; clear transitions; no filler.",
        "audience_interests": ["Tool tutorials", "Productivity systems", "Software comparisons", "Automation"],
        "restrictions": ["No misleading screen recordings", "No unlicensed footage", "No fake presenter credentials"],
    },
    {
        "id": "entertainment-storytelling",
        "name": "Entertainment / Storytelling",
        "description": "Character-led or narrative entertainment with a clear story arc.",
        "video_style": "Character storytelling with animated scenes",
        "format": "longform",
        "default_duration_min": 14,
        "structure": ["Setup", "Inciting incident", "Rising tension", "Turn", "Resolution", "Lesson"],
        "visual_approach": "Expressive character animation, consistent world design, emotional lighting.",
        "script_direction": "Strong voice, scene-based writing, show-don't-tell; fiction must be labelled as fiction.",
        "audience_interests": ["Stories", "Characters", "Dramatic lessons", "Relatable situations"],
        "restrictions": ["Label fiction clearly", "No real-person defamation", "No misleading factual framing"],
    },
    {
        "id": "product-review",
        "name": "Product Review",
        "description": "An evidence-led product review with hands-on demonstration and clear disclosure.",
        "video_style": "Hands-on review with close-ups and structured verdict",
        "format": "longform",
        "default_duration_min": 9,
        "structure": ["Hook", "What it is", "Hands-on demonstration", "Pros and cons", "Who it is for", "Verdict", "Disclosure"],
        "visual_approach": "Clean product close-ups, real usage, comparison shots and honest on-screen ratings.",
        "script_direction": "State what was tested and for how long; separate measured results from impressions; disclose sponsorship or affiliate links.",
        "audience_interests": ["Buying advice", "Comparisons", "Real-world tests", "Value analysis"],
        "restrictions": ["Disclose sponsorship and affiliate links", "No fabricated test results", "No unverified safety claims"],
    },
]

_TEMPLATE_FIELDS = {
    "ads": (ADS_TEMPLATE_FIELDS, ADS_LIST_FIELDS),
    "youtube": (YOUTUBE_TEMPLATE_FIELDS, YOUTUBE_LIST_FIELDS),
}


def _defaults(domain):
    if domain == "ads":
        return copy.deepcopy(DEFAULT_ADS_TEMPLATES)
    if domain == "youtube":
        return copy.deepcopy(DEFAULT_YOUTUBE_TEMPLATES)
    raise common.CosmosError("unknown_domain", "Unknown intelligence domain.", 404)


def _validate_domain(domain):
    key = str(domain or "").strip().lower()
    if key not in common.DIVISIONS:
        raise common.CosmosError("unknown_domain", "Unknown intelligence domain.", 404)
    return key


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
def _blank_config():
    return {
        "version": common.SCHEMA_VERSION,
        "ads_templates": _defaults("ads"),
        "youtube_templates": _defaults("youtube"),
        "active_ads_template_id": DEFAULT_ADS_TEMPLATES[0]["id"],
        "active_youtube_template_id": DEFAULT_YOUTUBE_TEMPLATES[0]["id"],
        "active_youtube_channel_id": None,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    }


def _load_config():
    data = common.read_json(common.CONFIG_FILE, {})
    if not data:
        data = _blank_config()
        common.write_json(common.CONFIG_FILE, data)
        return data
    if data.get("version") != common.SCHEMA_VERSION:
        raise common.CosmosError("config_version", "COSMOS configuration uses an unsupported version.", 500)
    changed = False
    for domain in ("ads", "youtube"):
        key = "%s_templates" % domain
        items = data.get(key)
        if not isinstance(items, list):
            data[key] = _defaults(domain)
            changed = True
            continue
        known = {item.get("id") for item in items if isinstance(item, dict)}
        for default in _defaults(domain):
            if default["id"] not in known:
                items.append(default)
                changed = True
    if not _template_ids(data, "ads"):
        data["ads_templates"] = _defaults("ads")
        changed = True
    if not _template_ids(data, "youtube"):
        data["youtube_templates"] = _defaults("youtube")
        changed = True
    if data.get("active_ads_template_id") not in _template_ids(data, "ads"):
        data["active_ads_template_id"] = _template_ids(data, "ads")[0]
        changed = True
    if data.get("active_youtube_template_id") not in _template_ids(data, "youtube"):
        data["active_youtube_template_id"] = _template_ids(data, "youtube")[0]
        changed = True
    data.setdefault("active_youtube_channel_id", None)
    if changed:
        data["updated_at"] = common.now()
        data["updated_iso"] = common.iso()
        common.write_json(common.CONFIG_FILE, data)
    return data


def _template_ids(data, domain):
    items = data.get("%s_templates" % domain) or []
    return [item.get("id") for item in items if isinstance(item, dict) and item.get("id")]


def _find_template(data, domain, template_id):
    for item in data.get("%s_templates" % domain) or []:
        if item.get("id") == template_id:
            return item
    return None


def ensure_seed():
    with common.acquire():
        _load_config()


def config_public():
    with common.acquire():
        data = _load_config()
    return {
        "ads_templates": copy.deepcopy(data.get("ads_templates") or []),
        "youtube_templates": copy.deepcopy(data.get("youtube_templates") or []),
        "active_ads_template_id": data.get("active_ads_template_id"),
        "active_youtube_template_id": data.get("active_youtube_template_id"),
        "active_youtube_channel_id": data.get("active_youtube_channel_id"),
        "ads_platforms": copy.deepcopy(_load_platforms(data)),
        "updated_at": data.get("updated_at"),
        "updated_iso": data.get("updated_iso"),
    }


def list_templates(domain):
    domain = _validate_domain(domain)
    with common.acquire():
        data = _load_config()
    return copy.deepcopy(data.get("%s_templates" % domain) or [])


def get_template(domain, template_id):
    domain = _validate_domain(domain)
    with common.acquire():
        data = _load_config()
    found = _find_template(data, domain, str(template_id or ""))
    if not found:
        raise common.CosmosError("template_not_found", "Template not found.", 404)
    return copy.deepcopy(found)


def _clean_price_range(value):
    if not isinstance(value, dict):
        raise common.CosmosError("invalid_template", "price_range must be an object.")
    minimum = common.clamp_float(value.get("min"), 0, 10_000_000, 0)
    maximum = common.clamp_float(value.get("max"), 0, 10_000_000, minimum)
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    currency = common.clean_text(value.get("currency"), "Currency", 8) or "PHP"
    return {"min": minimum, "max": maximum, "currency": currency}


def update_template(domain, template_id, patch):
    domain = _validate_domain(domain)
    if not isinstance(patch, dict):
        raise common.CosmosError("invalid_template", "Template payload must be a JSON object.")
    text_fields, list_fields = _TEMPLATE_FIELDS[domain]
    with common.acquire():
        data = _load_config()
        template = _find_template(data, domain, str(template_id or ""))
        if not template:
            raise common.CosmosError("template_not_found", "Template not found.", 404)
        for field in text_fields:
            if field in patch:
                limit = 120 if field == "name" else 2000
                value = common.clean_text(patch.get(field), field.replace("_", " ").title(), limit, required=field == "name")
                template[field] = value
        for field in list_fields:
            if field in patch:
                template[field] = common.clean_str_list(patch.get(field), field.replace("_", " ").title(), 30, 120)
        if "price_range" in patch:
            template["price_range"] = _clean_price_range(patch.get("price_range"))
        if "default_duration_min" in patch:
            template["default_duration_min"] = common.clamp_int(patch.get("default_duration_min"), 1, 120, 12)
        if "format" in patch:
            fmt = str(patch.get("format") or "").strip().lower()
            if fmt not in ("longform", "shorts"):
                raise common.CosmosError("invalid_template", "format must be longform or shorts.")
            template["format"] = fmt
        data["updated_at"] = common.now()
        data["updated_iso"] = common.iso()
        common.write_json(common.CONFIG_FILE, data)
    return copy.deepcopy(template)


def set_active_template(domain, template_id):
    domain = _validate_domain(domain)
    with common.acquire():
        data = _load_config()
        found = _find_template(data, domain, str(template_id or ""))
        if not found:
            raise common.CosmosError("template_not_found", "Template not found.", 404)
        data["active_%s_template_id" % domain] = found["id"]
        data["updated_at"] = common.now()
        data["updated_iso"] = common.iso()
        common.write_json(common.CONFIG_FILE, data)
    return {"active_%s_template_id" % domain: found["id"]}


def set_active_channel(channel_id):
    from . import youtube_channels

    channel = youtube_channels.get_channel(channel_id)
    with common.acquire():
        data = _load_config()
        data["active_youtube_channel_id"] = channel["id"]
        data["updated_at"] = common.now()
        data["updated_iso"] = common.iso()
        common.write_json(common.CONFIG_FILE, data)
    return {"active_youtube_channel_id": channel["id"]}


# ---------------------------------------------------------------------------
# Ads platform registration
# ---------------------------------------------------------------------------
# The Ads division sources product candidates from registered commerce
# platforms. Registration records the owner's affiliate-program context so
# discovery and card verification use real, owner-confirmed handles and
# program links. No marketplace API credentials are stored here.
ADS_PLATFORM_IDS = ("shopee", "lazada", "tiktok_shop")
ADS_PLATFORM_FIELDS = ("handle", "program_url", "region", "notes")


def _load_platforms(data):
    platforms = data.get("ads_platforms")
    return platforms if isinstance(platforms, dict) else {}


def get_ads_platforms():
    with common.acquire():
        data = _load_config()
    return copy.deepcopy(_load_platforms(data))


def set_ads_platform(platform_id, payload):
    """Register (or update) one commerce platform for the Ads division."""
    platform_id = str(platform_id or "").strip().lower()
    if platform_id not in ADS_PLATFORM_IDS:
        raise common.CosmosError(
            "unknown_platform", "platform must be one of: %s." % ", ".join(ADS_PLATFORM_IDS), 400)
    payload = payload if isinstance(payload, dict) else {}
    record = {
        "platform": platform_id,
        "connected": bool(payload.get("connected", True)),
        "handle": common.clean_text(payload.get("handle"), "Affiliate handle", 200) or None,
        "program_url": common.clean_text(payload.get("program_url"), "Program URL", 500) or None,
        "region": common.clean_text(payload.get("region"), "Region", 40) or None,
        "notes": common.clean_text(payload.get("notes"), "Notes", 2000) or None,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    }
    with common.acquire():
        data = _load_config()
        platforms = _load_platforms(data)
        previous = platforms.get(platform_id)
        if isinstance(previous, dict) and previous.get("registered_iso"):
            record["registered_iso"] = previous["registered_iso"]
        else:
            record["registered_iso"] = common.iso()
        platforms[platform_id] = record
        data["ads_platforms"] = platforms
        data["updated_at"] = common.now()
        data["updated_iso"] = common.iso()
        common.write_json(common.CONFIG_FILE, data)
    return copy.deepcopy(record)


# ---------------------------------------------------------------------------
# Effective configuration snapshots
# ---------------------------------------------------------------------------
def _ads_backlog_summary():
    try:
        from . import ads_backlog

        counts = ads_backlog.counts()
        recent = ads_backlog.list_products(limit=12).get("products", [])
        return {
            "counts": counts,
            "active_titles": [item.get("name") for item in recent if item.get("name")],
        }
    except Exception:
        return {"counts": {}, "active_titles": []}


def effective_ads(template_id=None):
    with common.acquire():
        data = _load_config()
        active_id = template_id or data.get("active_ads_template_id")
        template = _find_template(data, "ads", active_id)
        if not template:
            raise common.CosmosError("template_not_found", "Active Ads template was not found.", 404)
        template = copy.deepcopy(template)
    try:
        settings = studio_workspaces.get_settings("ads")
    except Exception:
        settings = {}
    return {
        "domain": "ads",
        "template_id": template["id"],
        "template": template,
        "workspace_settings": settings,
        "backlog": _ads_backlog_summary(),
        "source_priority": [
            "explicit user instruction",
            "active Ads configuration",
            "selected template defaults",
            "system defaults",
        ],
        "captured_at": common.now(),
        "captured_iso": common.iso(),
    }


def _resolve_channel(channel_id=None):
    from . import youtube_channels

    if channel_id:
        return youtube_channels.get_channel(channel_id)
    with common.acquire():
        data = _load_config()
        stored = data.get("active_youtube_channel_id")
    if stored:
        try:
            return youtube_channels.get_channel(stored)
        except Exception:
            pass
    return youtube_channels.active_channel()


def _youtube_backlog_summary(channel_id):
    try:
        from . import youtube_topics

        data = youtube_topics.list_topics(channel_id)
        topics = data.get("topics") or []
        return {
            "counts": data.get("counts") or {},
            "existing_titles": [item.get("title") for item in topics if item.get("title")][:40],
        }
    except Exception:
        return {"counts": {}, "existing_titles": []}


def effective_youtube(template_id=None, channel_id=None):
    channel = _resolve_channel(channel_id)
    with common.acquire():
        data = _load_config()
        active_id = template_id or data.get("active_youtube_template_id")
        template = _find_template(data, "youtube", active_id)
        if not template:
            raise common.CosmosError("template_not_found", "Active YouTube template was not found.", 404)
        template = copy.deepcopy(template)
    channel_snapshot = {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "tagline": channel.get("tagline"),
        "description": channel.get("description"),
        "content_niche": channel.get("content_niche"),
        "target_audience": channel.get("target_audience"),
        "audience_age_focus": channel.get("audience_age_focus"),
        "language": channel.get("language"),
        "country": channel.get("country"),
        "content_pillars": copy.deepcopy(channel.get("content_pillars") or []),
        "editorial_guidelines": channel.get("editorial_guidelines"),
        "publishing": copy.deepcopy(channel.get("publishing") or {}),
        "default_voice": channel.get("default_voice"),
        "default_visual_style": channel.get("default_visual_style"),
    }
    return {
        "domain": "youtube",
        "template_id": template["id"],
        "template": template,
        "channel": channel_snapshot,
        "channel_id": channel["id"],
        "backlog": _youtube_backlog_summary(channel["id"]),
        "source_priority": [
            "explicit user instruction",
            "active channel configuration",
            "selected template defaults",
            "system defaults",
        ],
        "captured_at": common.now(),
        "captured_iso": common.iso(),
    }


def effective(domain, template_id=None, channel_id=None):
    if _validate_domain(domain) == "ads":
        return effective_ads(template_id=template_id)
    return effective_youtube(template_id=template_id, channel_id=channel_id)
