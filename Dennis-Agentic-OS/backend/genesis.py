"""GENESIS production domain — the COSMOS production workflow.

GENESIS is a system-level production dashboard, not a sixth intelligence
agent. It owns the path from approved backlog records to published videos:

    intake → project → assets → render → quality review → approval → publishing

Eligibility gates (mirroring the owner-driven lifecycles that already exist):

* Ads products must be ``approved`` or ``ready_for_creation`` before they can
  enter production; intake moves them forward to ``in_production``.
* YouTube topics must be ``scripted`` with a linked script before they can
  enter production; intake moves them forward to ``in_production``.

Every project keeps full provenance back to the originating recommendation,
backlog record, execution and (where applicable) channel, and references —
never duplicates — the existing stores. Rendering and publishing state is
kept in ``genesis_store``; final media lands in the existing Studio media
library. Nothing here publishes or renders automatically without the owner's
explicit action, and no state is ever reported as complete unless it really
is.
"""

from __future__ import annotations

import os

from . import ads_backlog
from . import cosmos_common as common
from . import genesis_render
from . import genesis_store as store
from . import studio_create
from . import studio_media
from . import studio_workspaces
from . import youtube_channels
from . import youtube_scripts
from . import youtube_topics

SCENE_MAX = 40
REFERENCE_LIST_MAX = 12

ASPECTS = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
FPS_VALUES = (24, 25, 30, 50, 60)
PLATFORMS = ("youtube", "youtube_shorts", "meta_ads", "tiktok")

ADS_ELIGIBLE_STATUSES = ("approved", "ready_for_creation")
ADS_INTAKE_STATUS = "in_production"
YOUTUBE_ELIGIBLE_STATUS = "scripted"
YOUTUBE_INTAKE_STATUS = "in_production"
# Ads Create briefs are owner-reviewed creative briefs. Only a brief that
# the owner explicitly handed off to an Ads Studio project (and that project
# was then approved) can enter production.
BRIEF_ELIGIBLE_STATUSES = ("handed_off",)
BRIEF_INTAKE_STATUS = "in_production"
# Studio projects must be approved before GENESIS takes them over.
STUDIO_ELIGIBLE_STATUS = "approved"
STUDIO_INTAKE_STATUS = "in_progress"
STUDIO_INTAKE_STAGE = "assets"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clean_aspect(value, fallback="16:9"):
    text = str(value or "").strip()
    if text in ASPECTS:
        return text
    return fallback


def _aspect_from_text(value, fallback="16:9"):
    text = str(value or "").strip()
    for aspect in ("9:16", "4:5", "1:1", "16:9"):
        if aspect in text:
            return aspect
    return fallback


def _clean_platforms(value):
    return common.clean_str_list(value, "Target platforms", limit=8, item_limit=40)


def _media(media_id):
    """Return a media record or None; GENESIS never assumes a cached id exists."""
    return studio_media.find(media_id) if media_id else None


def _clean_tags(value):
    return common.clean_str_list(value, "Tags", limit=20, item_limit=60)


def _clean_media_id_list(value, label, limit=REFERENCE_LIST_MAX):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise common.CosmosError("invalid_field", "%s must be a list." % label)
    cleaned = []
    for item in value[:limit]:
        text = common.clean_text(item, label, 80)
        if text:
            record = _media(text)
            if record is None:
                raise common.CosmosError("media_not_found", "Media %s was not found in the library." % text, 404)
            cleaned.append(text)
    return cleaned


def _clean_scene(value):
    if not isinstance(value, dict):
        raise common.CosmosError("invalid_field", "Scenes must be objects.")
    scene = {
        "id": common.clean_text(value.get("id") or common.new_id("scene_"), "Scene id", 40),
        "title": common.clean_text(value.get("title"), "Scene title", 160),
        "description": common.clean_text(value.get("description"), "Scene description", 2000),
        "kind": "video" if str(value.get("kind") or "video").lower() != "image" else "image",
        "media_id": None,
        "duration_hint": None,
        "generation": None,
        "status": "ready" if value.get("media_id") else "pending_media",
    }
    media_id = common.clean_text(value.get("media_id"), "Scene media id", 80) if value.get("media_id") else None
    if media_id:
        record = _media(media_id)
        if record is None:
            raise common.CosmosError("media_not_found", "Media %s was not found in the library." % media_id, 404)
        scene["media_id"] = media_id
    if value.get("duration_hint") not in (None, ""):
        scene["duration_hint"] = common.clamp_float(value.get("duration_hint"), 0.5, 600)
    generation = value.get("generation")
    if isinstance(generation, dict) and (generation.get("provider") or generation.get("model_id")):
        scene["generation"] = {
            "provider": common.clean_text(generation.get("provider"), "Generation provider", 60),
            "model_id": common.clean_text(generation.get("model_id"), "Generation model", 120),
            "prompt": common.clean_text(generation.get("prompt"), "Generation prompt", 8000),
        }
        scene["status"] = "pending_media"
    return scene


def _clean_scenes(value):
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise common.CosmosError("invalid_field", "Scenes must be a list.")
    if len(value) > SCENE_MAX:
        raise common.CosmosError("invalid_field", "A project can have at most %d scenes." % SCENE_MAX)
    return [_clean_scene(scene) for scene in value]


def _clean_settings(value, existing=None):
    data = dict(existing or {})
    if isinstance(value, dict):
        data.update(value)
    aspect = _clean_aspect(data.get("aspect_ratio"))
    width, height = ASPECTS[aspect]
    settings = {
        "aspect_ratio": aspect,
        "width": common.clamp_int(data.get("width") or width, 240, 4096, width),
        "height": common.clamp_int(data.get("height") or height, 240, 4096, height),
        "fps": common.clamp_int(data.get("fps") or 30, 1, 120, 30),
        "export_format": "mp4" if str(data.get("export_format") or "mp4").lower() == "mp4" else "mp4",
        "target_platforms": _clean_platforms(data.get("target_platforms")),
    }
    return settings


def _clean_audio(value, existing=None):
    data = dict(existing or {})
    if isinstance(value, dict):
        data.update(value)
    audio = {}
    for key, label in (
        ("voiceover_media_id", "Voiceover"),
        ("music_media_id", "Music"),
        ("captions_media_id", "Captions file"),
    ):
        raw = data.get(key)
        if raw:
            media_id = common.clean_text(raw, label, 80)
            record = _media(media_id)
            if record is None:
                raise common.CosmosError("media_not_found", "Media %s was not found in the library." % media_id, 404)
            audio[key] = media_id
        else:
            audio[key] = None
    captions_text = data.get("captions_srt")
    if captions_text:
        text = common.clean_text(captions_text, "Captions", 60000)
        audio["captions_srt"] = text
        if len(text) > 2 * 1024 * 1024:
            raise common.CosmosError("invalid_field", "Captions are too large.")
    else:
        audio["captions_srt"] = None
    return audio


def _clean_metadata(value, existing=None):
    data = dict(existing or {})
    if isinstance(value, dict):
        data.update(value)
    thumbnail = None
    explicit_thumbnail = isinstance(value, dict) and "thumbnail_media_id" in value
    if data.get("thumbnail_media_id"):
        thumbnail = common.clean_text(data.get("thumbnail_media_id"), "Thumbnail", 80)
        record = _media(thumbnail)
        if record is None:
            if explicit_thumbnail:
                raise common.CosmosError(
                    "media_not_found", "Thumbnail media %s was not found in the library." % thumbnail, 404)
            thumbnail = None
        elif record.get("kind") != "image":
            if explicit_thumbnail:
                raise common.CosmosError("invalid_thumbnail", "The publishing thumbnail must be an image.", 400)
            thumbnail = None
    return {
        "title": common.clean_text(data.get("title"), "Publishing title", 200),
        "description": common.clean_text(data.get("description"), "Publishing description", 5000),
        "tags": _clean_tags(data.get("tags")),
        "thumbnail_media_id": thumbnail,
        "disclosure": common.clean_text(data.get("disclosure"), "Disclosure", 1000),
        "sponsored": bool(data.get("sponsored")),
        "affiliate_url": common.clean_text(data.get("affiliate_url"), "Affiliate URL", 1000),
        "privacy_status": data.get("privacy_status") if data.get("privacy_status") in ("public", "unlisted", "private") else "private",
        "category_id": common.clean_text(data.get("category_id") or "22", "Category", 4),
    }


def resolve_media(media_ids):
    """Resolve media ids for pickers/previews, reporting unknown ids honestly."""
    records = []
    missing = []
    for media_id in dict.fromkeys(media_ids or []):
        record = _media(media_id)
        if record:
            records.append(record)
        else:
            missing.append(media_id)
    return {"media": records, "missing": missing}


def project_media(project):
    """Resolve the media records referenced by a project (for previews)."""
    ids = []
    if project.get("media_id"):
        ids.append(project["media_id"])
    for scene in project.get("scenes") or []:
        if scene.get("media_id"):
            ids.append(scene["media_id"])
    refs = project.get("references") or {}
    for key in ("model_media_ids", "product_media_ids", "props_media_ids"):
        ids.extend(refs.get(key) or [])
    audio = project.get("audio") or {}
    for key in ("voiceover_media_id", "music_media_id", "captions_media_id"):
        if audio.get(key):
            ids.append(audio[key])
    thumbnail = (project.get("publishing_metadata") or {}).get("thumbnail_media_id")
    if thumbnail:
        ids.append(thumbnail)
    return resolve_media(ids)


def _history(record, event, note=None):
    record.setdefault("history", []).append({
        "at": common.now(),
        "iso": common.iso(),
        "event": event,
        "note": common.clean_text(note, "History note", 500) if note else None,
    })


def _touch(record):
    record["updated_at"] = common.now()
    record["updated_iso"] = common.iso()


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def overview_counts():
    """Real counts from the persisted production records."""
    projects = store.load_projects()
    renders = store.load_renders()
    publications = store.load_publications()
    active_renders = [r for r in renders if r.get("status") in ("queued", "preparing", "waiting_media", "assembling")]
    failed_projects = [p for p in projects if p.get("status") == "failed"]
    failed_renders = [r for r in renders if r.get("status") == "failed"]
    failed_publications = [p for p in publications if p.get("status") == "failed"]
    assets_preparing = [
        p for p in projects
        if not p.get("deleted_at") and p.get("status") == "in_production"
        and p.get("stage") in ("assets", "preparing", None)
    ]
    # The queue applies duplicate/active-production filtering, so the
    # dashboard count always matches what the owner can actually start.
    ready = len(queue()["queue"])
    return {
        "ready_for_production": ready,
        "assets_preparing": len(assets_preparing),
        "rendering": len(active_renders),
        "failed_total": len(failed_projects) + len(failed_renders) + len(failed_publications),
        "projects": {
            "total": len([p for p in projects if not p.get("deleted_at")]),
            "in_production": len([p for p in projects if p.get("status") == "in_production"]),
            "awaiting_approval": len([p for p in projects if p.get("status") == "awaiting_approval"]),
            "approved": len([p for p in projects if p.get("status") == "approved"]),
            "published": len([p for p in projects if p.get("status") == "published"]),
            "failed": len(failed_projects),
        },
        "renders": {
            "active": len(active_renders),
            "complete": len([r for r in renders if r.get("status") == "complete"]),
            "failed": len(failed_renders),
        },
        "publications": {
            "scheduled": len([p for p in publications if p.get("status") == "scheduled"]),
            "succeeded": len([p for p in publications if p.get("status") == "succeeded"]),
            "failed": len(failed_publications),
        },
    }


# ---------------------------------------------------------------------------
# Production queue — eligible backlog records
# ---------------------------------------------------------------------------
def _source_is_active(record):
    return (
        not record.get("deleted_at")
        and not record.get("archived")
        and record.get("status") not in ("published", "archived")
    )


def active_source_index(source_kind=None):
    """Map backlog source id -> active GENESIS project.

    Used by COSMOS to show that a backlog row is already in production and
    by the queue to avoid offering the same source twice.
    """
    index = {}
    for record in store.load_projects():
        if not _source_is_active(record):
            continue
        source = record.get("source") or {}
        if source_kind and source.get("kind") != source_kind:
            continue
        if source.get("id"):
            index[source["id"]] = common.public(record)
    return index


def source_projects(source_kind, source_id):
    """All production projects linked to a backlog/Studio record."""
    projects = []
    for record in store.load_projects():
        if record.get("deleted_at"):
            continue
        source = record.get("source") or {}
        if source.get("kind") == source_kind and source.get("id") == source_id:
            projects.append(common.public(record))
    projects.sort(key=lambda record: record.get("created_at") or 0, reverse=True)
    return projects


def _active_project_keys():
    keys = set()
    for record in store.load_projects():
        if not _source_is_active(record):
            continue
        source = record.get("source") or {}
        if source.get("kind") and source.get("id"):
            keys.add((source["kind"], source["id"]))
        for field in ("brief_id", "studio_project_id", "topic_id"):
            if source.get(field):
                keys.add((field, source[field]))
    return keys


def _row_is_active(row, keys):
    if (row.get("source_kind"), row.get("source_id")) in keys:
        return True
    provenance = row.get("provenance") or {}
    for field in ("brief_id", "studio_project_id", "topic_id"):
        value = provenance.get(field)
        if value and (field, value) in keys:
            return True
    return False


def _find_related_active(source):
    """Find an active project linked by brief/project/topic identity.

    This prevents a brief and its handed-off Studio project (or a topic and
    its Studio project) from entering production twice.
    """
    fields = ("brief_id", "studio_project_id", "topic_id")
    for record in store.load_projects():
        if not _source_is_active(record):
            continue
        other = record.get("source") or {}
        for field in fields:
            value = source.get(field)
            if value and other.get(field) == value:
                return common.public(record)
    return None


def _ads_queue():
    rows = []
    for status in ADS_ELIGIBLE_STATUSES:
        try:
            listed = ads_backlog.list_products(status=status, limit=100)
        except common.CosmosError:
            listed = {"products": []}
        for product in listed.get("products") or []:
            rows.append({
                "division": "ads",
                "source_kind": "ads_product",
                "source_id": product.get("id"),
                "title": product.get("name"),
                "subtitle": product.get("merchant"),
                "status": product.get("status"),
                "origin": "Ads Product Backlog",
                "destination": "Ads creative production",
                "source_label": "Product Backlog",
                "category": product.get("category"),
                "price": product.get("price"),
                "provenance": product.get("provenance") or {},
                "assets": {
                    "image_url": product.get("image_url"),
                    "affiliate_status": product.get("affiliate_status"),
                },
            })
    return rows


def _youtube_queue(channel_id=None):
    if not channel_id:
        try:
            channel_id = youtube_channels.active_channel().get("id")
        except Exception:
            channel_id = None
    if not channel_id:
        return []
    try:
        listed = youtube_topics.list_topics(channel_id, status=YOUTUBE_ELIGIBLE_STATUS)
    except Exception:
        return []
    try:
        channel = youtube_channels.get_channel(channel_id)
    except Exception:
        channel = None
    channel_name = (channel or {}).get("name") or channel_id
    rows = []
    for topic in listed.get("topics") or []:
        script = None
        if topic.get("script_id"):
            try:
                script = youtube_scripts.get_script(channel_id, topic["script_id"])
            except Exception:
                script = None
        rows.append({
            "division": "youtube",
            "source_kind": "youtube_topic",
            "source_id": topic.get("id"),
            "title": topic.get("title"),
            "subtitle": topic.get("pillar") or topic.get("channel_id"),
            "status": topic.get("status"),
            "origin": "YouTube Topic Backlog",
            "destination": "YouTube · %s" % channel_name,
            "source_label": "Topic Backlog",
            "pillar": topic.get("pillar"),
            "priority": topic.get("priority"),
            "provenance": {
                "channel_id": topic.get("channel_id"),
                "topic_id": topic.get("id"),
                "notes": topic.get("notes"),
            },
            "assets": {
                "script_id": topic.get("script_id"),
                "script_title": script.get("title") if script else None,
                "script_sections": len(script.get("sections") or []) if script else 0,
            },
        })
    return rows


def _ads_briefs_queue():
    """Handed-off Ads Create briefs whose Studio project is approved."""
    try:
        listed = studio_create.list_briefs(limit=200)
    except Exception:
        return []
    rows = []
    for brief in listed.get("briefs") or []:
        if brief.get("status") not in BRIEF_ELIGIBLE_STATUSES:
            continue
        project_id = brief.get("handoff_project_id")
        if not project_id:
            continue
        try:
            project = studio_workspaces.get_project("ads", project_id)
        except Exception:
            continue
        if project.get("status") != STUDIO_ELIGIBLE_STATUS:
            continue
        product = brief.get("product") or {}
        title = product.get("name") or brief.get("instruction") or "Ad creative brief"
        rows.append({
            "division": "ads",
            "source_kind": "ads_brief",
            "source_id": brief.get("id"),
            "title": title,
            "subtitle": brief.get("instruction") or brief.get("prompt") or "",
            "status": brief.get("status"),
            "origin": "Ads Studio Create brief",
            "destination": "Ads Studio project · %s" % project.get("title"),
            "source_label": "Ads brief",
            "provenance": {
                "brief_id": brief.get("id"),
                "studio_project_id": project.get("id"),
                "template_id": brief.get("template_id"),
            },
            "assets": {
                "brief_id": brief.get("id"),
                "product": product,
                "template_name": brief.get("template_name"),
                "target_platforms": brief.get("target_platforms"),
                "duration_seconds": brief.get("duration_seconds"),
            },
        })
    return rows


def _studio_projects_queue(workspace):
    division = "youtube" if workspace == "youtube" else "ads"
    try:
        listed = studio_workspaces.list_projects(
            workspace, status=STUDIO_ELIGIBLE_STATUS, limit=200
        )
    except Exception:
        return []
    rows = []
    for project in listed.get("projects") or []:
        metadata = project.get("metadata") or {}
        # The brief row represents a handed-off Ads brief; do not offer the
        # same creative twice. If the brief is gone, keep the project row.
        if workspace == "ads" and metadata.get("brief_id"):
            try:
                studio_create.get_brief(metadata["brief_id"])
                continue
            except Exception:
                pass
        source_kind = "youtube_project" if workspace == "youtube" else "ads_project"
        channel_id = project.get("channel_id") or metadata.get("channel_id")
        platforms = metadata.get("target_platforms") or []
        if workspace == "youtube":
            destination = "YouTube · %s" % (channel_id or "active channel")
            origin = "YouTube Studio project"
            source_label = "YouTube project"
        else:
            destination = "Ads · %s" % (", ".join(platforms) if platforms else "creative production")
            origin = "Ads Studio project"
            source_label = "Ads project"
        rows.append({
            "division": division,
            "source_kind": source_kind,
            "source_id": project.get("id"),
            "title": project.get("title"),
            "subtitle": project.get("description") or project.get("content_pillar") or "",
            "status": project.get("status"),
            "origin": origin,
            "destination": destination,
            "source_label": source_label,
            "provenance": {
                "studio_workspace": workspace,
                "studio_project_id": project.get("id"),
                "brief_id": metadata.get("brief_id"),
                "topic_id": project.get("topic_id"),
                "channel_id": channel_id,
                "metadata": metadata,
            },
            "assets": {
                "channel_id": channel_id,
                "topic_id": project.get("topic_id"),
                "thumbnail_media_id": project.get("thumbnail_media_id"),
                "tags": project.get("tags") or [],
                "target_platforms": platforms,
            },
        })
    return rows


def list_projects(division=None, status=None, query=None, include_archived=False, limit=100):
    """Domain-level listing (thin passthrough over the store)."""
    return store.list_projects(
        division=division, status=status, query=query,
        include_archived=include_archived, limit=limit,
    )


def queue(division=None, query=None):
    wanted = common.normalize_division(division) if division else None
    rows = []
    if wanted in (None, "ads"):
        rows.extend(_ads_queue())
        rows.extend(_ads_briefs_queue())
        rows.extend(_studio_projects_queue("ads"))
    if wanted in (None, "youtube"):
        rows.extend(_youtube_queue())
        rows.extend(_studio_projects_queue("youtube"))
    # Never offer a source that is already in an active production project.
    keys = _active_project_keys()
    rows = [row for row in rows if not _row_is_active(row, keys)]
    if query:
        text = common.normalize_key(query)
        rows = [
            row for row in rows
            if text in common.normalize_key(
                "%s %s %s %s %s" % (
                    row.get("title"), row.get("subtitle"), row.get("origin"),
                    row.get("destination"), row.get("source_kind"),
                )
            )
        ]
    return {"queue": rows, "total": len(rows)}


# ---------------------------------------------------------------------------
# Intake — create a production project from an eligible backlog record
# ---------------------------------------------------------------------------
def _source_ads_product(product_id):
    product = ads_backlog.get_product(product_id)
    if product.get("archived") or product.get("deleted_at"):
        raise common.CosmosError("not_eligible", "Archived products cannot enter production.", 409)
    if product.get("status") not in ADS_ELIGIBLE_STATUSES:
        raise common.CosmosError(
            "not_eligible",
            "Product status is '%s'. Only approved or ready_for_creation products can enter production."
            % (product.get("status") or "unknown"),
            409,
        )
    provenance = product.get("provenance") or {}
    return {
        "record": product,
        "source": {
            "kind": "ads_product",
            "id": product.get("id"),
            "title": product.get("name"),
            "description": product.get("description"),
            "channel_id": None,
            "brief_id": None,
            "studio_project_id": None,
            "topic_id": None,
            "recommendation_id": provenance.get("recommendation_id"),
            "execution_id": provenance.get("execution_id"),
            "agent_id": provenance.get("agent_id"),
            "status_at_intake": product.get("status"),
            "config_snapshot": provenance.get("config_snapshot"),
        },
        "advance": lambda: ads_backlog.set_status(product_id, ADS_INTAKE_STATUS, note="Entered GENESIS production."),
    }


def _source_youtube_topic(topic_id, channel_id=None):
    if not channel_id:
        try:
            channel_id = youtube_channels.active_channel().get("id")
        except Exception:
            channel_id = None
    if not channel_id:
        raise common.CosmosError("channel_required", "No active YouTube channel is configured.", 400)
    topic = youtube_topics.get_topic(channel_id, topic_id)
    if topic.get("archived") or topic.get("deleted_at"):
        raise common.CosmosError("not_eligible", "Archived topics cannot enter production.", 409)
    if topic.get("status") != YOUTUBE_ELIGIBLE_STATUS or not topic.get("script_id"):
        raise common.CosmosError(
            "not_eligible",
            "Topic status is '%s'. A topic must be scripted (with an approved script) before production."
            % (topic.get("status") or "unknown"),
            409,
        )
    script = youtube_scripts.get_script(channel_id, topic["script_id"])
    channel = None
    try:
        channel = youtube_channels.get_channel(channel_id)
    except Exception:
        channel = None
    snapshot = None
    if channel:
        snapshot = {
            "channel_id": channel.get("id"),
            "channel_name": channel.get("name") or channel.get("title"),
            "handle": channel.get("handle"),
            "pillars": channel.get("pillars"),
            "publishing": channel.get("publishing"),
        }
    return {
        "record": topic,
        "script": script,
        "source": {
            "kind": "youtube_topic",
            "id": topic.get("id"),
            "title": topic.get("title"),
            "description": topic.get("angle"),
            "channel_id": channel_id,
            "channel_title": (channel or {}).get("name") or (channel or {}).get("title"),
            "brief_id": None,
            "studio_project_id": topic.get("project_id"),
            "topic_id": topic.get("id"),
            "recommendation_id": None,
            "execution_id": None,
            "agent_id": None,
            "status_at_intake": topic.get("status"),
            "script_id": topic.get("script_id"),
            "config_snapshot": snapshot,
        },
        "advance": lambda: youtube_topics.set_status(channel_id, topic_id, YOUTUBE_INTAKE_STATUS),
    }


def _source_ads_brief(brief_id):
    try:
        brief = studio_create.get_brief(brief_id).get("brief") or {}
    except Exception:
        raise common.CosmosError("not_eligible", "Creative brief not found.", 404)
    if brief.get("status") not in BRIEF_ELIGIBLE_STATUSES:
        raise common.CosmosError(
            "not_eligible",
            "Brief status is '%s'. Hand the brief off to an Ads Studio project first."
            % (brief.get("status") or "unknown"),
            409,
        )
    project_id = brief.get("handoff_project_id")
    if not project_id:
        raise common.CosmosError(
            "not_eligible",
            "This brief has no Ads Studio project yet. Hand it off before production.",
            409,
        )
    try:
        project = studio_workspaces.get_project("ads", project_id)
    except Exception:
        raise common.CosmosError(
            "not_eligible", "The brief's Ads Studio project could not be found.", 409
        )
    if project.get("archived") or project.get("deleted_at"):
        raise common.CosmosError("not_eligible", "The linked Ads Studio project is archived.", 409)
    if project.get("status") != STUDIO_ELIGIBLE_STATUS:
        raise common.CosmosError(
            "not_eligible",
            "The linked Ads Studio project is '%s'. Approve it before production."
            % (project.get("status") or "unknown"),
            409,
        )
    product = brief.get("product") or {}
    title = product.get("name") or brief.get("instruction") or "Ad creative brief"

    def advance():
        studio_workspaces.update_project("ads", project_id, {
            "status": STUDIO_INTAKE_STATUS,
            "stage": STUDIO_INTAKE_STAGE,
        })
        try:
            studio_create.set_status(brief_id, BRIEF_INTAKE_STATUS)
        except Exception:
            # Keep the two stores consistent if the brief cannot advance.
            try:
                studio_workspaces.update_project("ads", project_id, {
                    "status": STUDIO_ELIGIBLE_STATUS,
                    "stage": project.get("stage") or "script",
                })
            except Exception:
                pass
            raise

    return {
        "record": brief,
        "source": {
            "kind": "ads_brief",
            "id": brief.get("id"),
            "title": title,
            "description": brief.get("prompt") or brief.get("instruction"),
            "creative_direction": brief.get("creative_style"),
            "channel_id": None,
            "brief_id": brief.get("id"),
            "studio_project_id": project.get("id"),
            "topic_id": None,
            "recommendation_id": None,
            "execution_id": None,
            "agent_id": None,
            "status_at_intake": brief.get("status"),
            "studio_status_at_intake": project.get("status"),
            "config_snapshot": {
                "template_id": brief.get("template_id"),
                "template_name": brief.get("template_name"),
                "target_platforms": brief.get("target_platforms"),
                "video_format": brief.get("video_format"),
                "duration_seconds": brief.get("duration_seconds"),
                "disclosures": brief.get("disclosures"),
            },
        },
        "product": product,
        "advance": advance,
    }


def _source_studio_project(workspace, project_id):
    workspace = str(workspace or "").strip().lower()
    if workspace not in ("ads", "youtube"):
        raise common.CosmosError("invalid_field", "Studio workspace must be ads or youtube.")
    try:
        project = studio_workspaces.get_project(workspace, project_id)
    except Exception:
        raise common.CosmosError("not_eligible", "Studio project not found.", 409)
    if project.get("archived") or project.get("deleted_at"):
        raise common.CosmosError("not_eligible", "Archived Studio projects cannot enter production.", 409)
    if project.get("status") != STUDIO_ELIGIBLE_STATUS:
        raise common.CosmosError(
            "not_eligible",
            "Studio project status is '%s'. Only approved Studio projects can enter production."
            % (project.get("status") or "unknown"),
            409,
        )
    metadata = project.get("metadata") or {}
    source_kind = "youtube_project" if workspace == "youtube" else "ads_project"
    channel_id = project.get("channel_id") or metadata.get("channel_id")

    def advance():
        studio_workspaces.update_project(workspace, project_id, {
            "status": STUDIO_INTAKE_STATUS,
            "stage": STUDIO_INTAKE_STAGE,
        })

    return {
        "record": project,
        "source": {
            "kind": source_kind,
            "id": project.get("id"),
            "title": project.get("title"),
            "description": project.get("description"),
            "channel_id": channel_id,
            "brief_id": metadata.get("brief_id"),
            "studio_project_id": project.get("id"),
            "studio_workspace": workspace,
            "topic_id": project.get("topic_id"),
            "recommendation_id": None,
            "execution_id": None,
            "agent_id": None,
            "status_at_intake": project.get("status"),
            "config_snapshot": {
                "workspace": workspace,
                "stage": project.get("stage"),
                "content_pillar": project.get("content_pillar"),
                "tags": project.get("tags") or [],
                "metadata": metadata,
            },
        },
        "product": metadata.get("product") if isinstance(metadata.get("product"), dict) else None,
        "advance": advance,
    }


def _studio_project_identity(workspace, project_id):
    """Identity fields for duplicate detection before lifecycle validation.

    A project that is already part of an active production is no longer
    ``approved``; without this pre-check the API would report ``not_eligible``
    instead of the truthful ``duplicate_project``.
    """
    try:
        project = studio_workspaces.get_project(workspace, project_id)
    except Exception:
        return None
    metadata = project.get("metadata") or {}
    return {
        "brief_id": metadata.get("brief_id"),
        "studio_project_id": project.get("id"),
        "topic_id": project.get("topic_id"),
    }


def create_project(payload):
    if not isinstance(payload, dict):
        raise common.CosmosError("invalid_field", "A project payload is required.")
    source_kind = common.clean_text(payload.get("source_kind") or payload.get("kind"), "Source kind", 40)
    source_id = common.clean_text(payload.get("source_id"), "Source id", 80, required=True)
    if source_kind not in (
        "ads_product", "ads_brief", "ads_project", "youtube_topic", "youtube_project",
    ):
        raise common.CosmosError(
            "invalid_field",
            "Source kind must be ads_product, ads_brief, ads_project, youtube_topic or youtube_project.",
        )
    existing = store.find_active_by_source(source_kind, source_id)
    if existing:
        raise common.CosmosError(
            "duplicate_project",
            "This %s already has an active production project (%s)." % (source_kind, existing["id"]),
            409,
        )
    if source_kind in ("ads_project", "youtube_project"):
        identity = _studio_project_identity(
            "youtube" if source_kind == "youtube_project" else "ads", source_id
        )
        related = _find_related_active(identity) if identity else None
        if related:
            raise common.CosmosError(
                "duplicate_project",
                "This %s is already part of an active production project (%s)."
                % (source_kind, related["id"]),
                409,
            )
    if source_kind == "ads_product":
        resolved = _source_ads_product(source_id)
    elif source_kind == "youtube_topic":
        resolved = _source_youtube_topic(source_id, payload.get("channel_id"))
    elif source_kind == "ads_brief":
        resolved = _source_ads_brief(source_id)
    else:
        resolved = _source_studio_project(
            "youtube" if source_kind == "youtube_project" else "ads", source_id
        )

    source = resolved["source"]
    record_record = resolved["record"]
    division = "youtube" if source_kind in ("youtube_topic", "youtube_project") else "ads"
    existing = _find_related_active(source)
    if existing:
        raise common.CosmosError(
            "duplicate_project",
            "This %s already has an active production project (%s)." % (source_kind, existing["id"]),
            409,
        )
    title = common.clean_text(
        payload.get("title") or source.get("title") or record_record.get("title") or record_record.get("name"),
        "Title", 160, required=True,
    )
    description = common.clean_text(
        payload.get("description") if payload.get("description") is not None
        else (source.get("description") or record_record.get("description") or record_record.get("angle") or ""),
        "Description", 4000,
    )
    creative_direction = common.clean_text(
        payload.get("creative_direction") or source.get("creative_direction"),
        "Creative direction", 4000,
    )

    scenes = _clean_scenes(payload.get("scenes"))
    if not scenes and division == "youtube":
        # Seed the timeline from the approved script's real structure.
        script = resolved.get("script") or {}
        for section in (script.get("sections") or [])[:SCENE_MAX]:
            narration = common.clean_text(section.get("narration"), "Narration", 2000)
            scenes.append({
                "id": common.clean_text(section.get("id") or common.new_id("scene_"), "Scene id", 40),
                "title": common.clean_text(section.get("heading"), "Scene title", 160),
                "description": narration or common.clean_text(section.get("purpose"), "Purpose", 2000),
                "kind": "video",
                "media_id": None,
                "duration_hint": None,
                "generation": None,
                "status": "pending_media",
            })

    snapshot = source.get("config_snapshot") or {}
    platforms = snapshot.get("target_platforms")
    if not isinstance(platforms, list) or not platforms:
        platforms = ["youtube"] if division == "youtube" else ["meta_ads"]
    aspect = _aspect_from_text(
        snapshot.get("video_format") or (snapshot.get("metadata") or {}).get("video_format"),
        "16:9",
    )
    settings = _clean_settings(payload.get("settings"), {
        "aspect_ratio": aspect,
        "width": ASPECTS[aspect][0],
        "height": ASPECTS[aspect][1],
        "fps": 30,
        "target_platforms": platforms,
    })
    audio = _clean_audio(payload.get("audio"))
    product = resolved.get("product") or {}
    affiliate = record_record.get("affiliate_destination")
    affiliate_url = ""
    if division == "ads":
        affiliate_url = (
            (affiliate or {}).get("url") if isinstance(affiliate, dict) else None
        ) or product.get("product_url") or record_record.get("affiliate_url") or record_record.get("product_url") or ""
    metadata = _clean_metadata(payload.get("publishing_metadata"), {
        "title": title,
        "description": description,
        "sponsored": bool(payload.get("sponsored") or (division == "ads")),
        "affiliate_url": affiliate_url,
    })

    now = common.now()
    project = {
        "id": common.new_id("gp_"),
        "division": division,
        "title": title,
        "description": description,
        "creative_direction": creative_direction,
        "status": "in_production",
        "stage": "assets",
        "source": source,
        "provenance": {
            "division": division,
            "agent_id": source.get("agent_id"),
            "recommendation_id": source.get("recommendation_id"),
            "execution_id": source.get("execution_id"),
            "backlog_item_id": source.get("id"),
            "backlog_kind": source_kind,
            "product_id": source.get("id") if source_kind == "ads_product" else product.get("id"),
            "brief_id": source.get("brief_id"),
            "studio_project_id": source.get("studio_project_id") or (
                common.clean_text(payload.get("studio_project_id"), "Studio project", 80)
                if payload.get("studio_project_id") else None
            ),
            "studio_workspace": source.get("studio_workspace"),
            "topic_id": source.get("topic_id"),
            "script_id": source.get("script_id"),
            "channel_id": source.get("channel_id"),
            "created_via": "genesis_intake",
        },
        "scenes": scenes,
        "references": {
            "model_media_ids": _clean_media_id_list(payload.get("model_media_ids"), "Model references"),
            "product_media_ids": _clean_media_id_list(payload.get("product_media_ids"), "Product references"),
            "props_media_ids": _clean_media_id_list(payload.get("props_media_ids"), "Props references"),
        },
        "audio": audio,
        "settings": settings,
        "publishing_metadata": metadata,
        "quality_review": None,
        "approval": None,
        "media_id": None,
        "render_ids": [],
        "publication_ids": [],
        "archived": False,
        "deleted_at": None,
        "deleted_iso": None,
        "created_at": now,
        "created_iso": common.iso(now),
        "updated_at": now,
        "updated_iso": common.iso(now),
        "history": [],
    }
    _history(project, "created", "Intake from %s %s (status %s)." % (
        source_kind, source_id, source.get("status_at_intake")))
    _import_ads_reference_image(project, resolved.get("product") or record_record)

    # Insert first and let the store re-check the source under its lock, so
    # two simultaneous intake requests cannot both create a project. If the
    # source lifecycle transition then fails, roll the project back instead of
    # leaving a half-created production record.
    project = store.insert_project_unique(project)
    try:
        resolved["advance"]()
    except Exception:
        try:
            store.remove_project(project["id"])
        except common.CosmosError:
            pass
        raise
    note = "Source advanced from '%s'; GENESIS production started." % (
        source.get("status_at_intake") or "unknown"
    )
    _history(project, "source_status_advanced", note)
    store.update_project(project["id"], {
        "history": project.get("history") or [],
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    })

    return store.get_project(project["id"])


def _import_ads_reference_image(project, product):
    """Best-effort import of the product image into the shared media library."""
    image_url = (product or {}).get("image_url")
    if not image_url:
        return
    try:
        record = studio_media.add_from_url(image_url, kind="image", meta={
            "title": "%s — product reference" % (product.get("name") or project["title"]),
            "role": "input",
            "provider": "genesis_intake",
            "model_id": None,
        })
    except Exception as exc:
        _history(project, "reference_import_failed", str(exc)[:300])
        return
    if record.get("id") and record["id"] not in project["references"]["product_media_ids"]:
        project["references"]["product_media_ids"].append(record["id"])
        _history(project, "reference_imported", "Media %s" % record["id"])


# ---------------------------------------------------------------------------
# Project updates
# ---------------------------------------------------------------------------
# The owner can iterate on a project until it is approved. Pre-approval
# stages stay editable (e.g. adding captions while awaiting approval); the
# approval itself is withdrawn before an approved project can change again.
EDITABLE_STATUSES = ("in_production", "failed", "awaiting_approval", "quality_review")
ACTIVE_RENDER_STATUSES = ("queued", "preparing", "waiting_media", "assembling")


def _active_render(project_id):
    for render in store.list_renders(project_id=project_id, limit=100):
        if render.get("status") in ACTIVE_RENDER_STATUSES:
            return render
    return None


def update_project(project_id, patch):
    project = store.get_project(project_id)
    if project.get("archived"):
        raise common.CosmosError("project_archived", "Archived projects cannot be edited.", 409)
    if project.get("status") not in EDITABLE_STATUSES:
        raise common.CosmosError(
            "project_locked",
            "Project is %s — finish or cancel the active stage first." % project.get("status"),
            409,
        )
    active = _active_render(project_id)
    if active:
        raise common.CosmosError(
            "render_in_progress",
            "Render %s is %s. Wait for it to finish before editing the project."
            % (active["id"], active.get("status")),
            409,
        )

    updates = {}
    if "title" in patch:
        updates["title"] = common.clean_text(patch.get("title"), "Title", 160, required=True)
    if "description" in patch:
        updates["description"] = common.clean_text(patch.get("description"), "Description", 4000)
    if "creative_direction" in patch:
        updates["creative_direction"] = common.clean_text(patch.get("creative_direction"), "Creative direction", 4000)
    if "scenes" in patch:
        updates["scenes"] = _clean_scenes(patch.get("scenes"))
    if "settings" in patch:
        updates["settings"] = _clean_settings(patch.get("settings"), project.get("settings"))
    if "audio" in patch:
        updates["audio"] = _clean_audio(patch.get("audio"), project.get("audio"))
    if "publishing_metadata" in patch:
        updates["publishing_metadata"] = _clean_metadata(patch.get("publishing_metadata"), project.get("publishing_metadata"))
    if "references" in patch:
        refs = patch.get("references") or {}
        existing = project.get("references") or {}
        updates["references"] = {
            "model_media_ids": _clean_media_id_list(
                refs.get("model_media_ids", existing.get("model_media_ids")), "Model references"),
            "product_media_ids": _clean_media_id_list(
                refs.get("product_media_ids", existing.get("product_media_ids")), "Product references"),
            "props_media_ids": _clean_media_id_list(
                refs.get("props_media_ids", existing.get("props_media_ids")), "Props references"),
        }

    if not updates:
        raise common.CosmosError("invalid_field", "Nothing to update.")

    updates["updated_at"] = common.now()
    updates["updated_iso"] = common.iso()
    updates["history"] = project.get("history") or []
    _history(updates, "updated", ", ".join(sorted(updates.keys() - {"updated_at", "updated_iso", "history"})))
    return store.update_project(project_id, updates)


def _active_publication(project_id):
    for publication in store.list_publications(project_id=project_id, limit=100):
        if publication.get("status") in ("scheduled", "publishing"):
            return publication
    return None


def reopen_project(project_id):
    """Send a reviewed/approved project back to editable production."""
    project = store.get_project(project_id)
    if project.get("archived"):
        raise common.CosmosError("project_archived", "Archived projects cannot be reopened.", 409)
    if project.get("status") not in ("quality_review", "awaiting_approval", "approved", "failed"):
        raise common.CosmosError(
            "reopen_not_allowed",
            "Project is %s — it cannot be sent back to production from here." % project.get("status"),
            409,
        )
    active = _active_render(project_id)
    if active:
        raise common.CosmosError(
            "render_in_progress",
            "Render %s is %s. Wait for it to finish before reopening the project."
            % (active["id"], active.get("status")),
            409,
        )
    publication = _active_publication(project_id)
    if publication:
        raise common.CosmosError(
            "publication_in_progress",
            "Publication %s is %s. Cancel or finish it before reopening the project."
            % (publication["id"], publication.get("status")),
            409,
        )
    store.update_project(project_id, {
        "status": "in_production",
        "stage": "assets",
        "quality_review": None,
        "approval": None,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
        "history": _append(project, "returned_to_production", "Owner reopened the project for edits."),
    })
    return store.get_project(project_id)


def archive_project(project_id):
    project = store.get_project(project_id)
    active = _active_render(project_id)
    if active:
        raise common.CosmosError(
            "render_in_progress",
            "Render %s is %s. Cancel or wait for it before archiving." % (active["id"], active.get("status")),
            409,
        )
    publication = _active_publication(project_id)
    if publication:
        raise common.CosmosError(
            "publication_in_progress",
            "Publication %s is %s. Cancel or finish it before archiving."
            % (publication["id"], publication.get("status")),
            409,
        )
    updates = {
        "archived": True,
        "status": "published" if project.get("status") == "published" else "archived",
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    }
    _history(updates, "archived")
    return store.update_project(project_id, updates)


def delete_project(project_id):
    store.get_project(project_id)
    active = _active_render(project_id)
    if active:
        raise common.CosmosError(
            "render_in_progress",
            "Render %s is %s. Cancel or wait for it before deleting." % (active["id"], active.get("status")),
            409,
        )
    publication = _active_publication(project_id)
    if publication:
        raise common.CosmosError(
            "publication_in_progress",
            "Publication %s is %s. Cancel or finish it before deleting."
            % (publication["id"], publication.get("status")),
            409,
        )
    updates = {
        "deleted_at": common.now(),
        "deleted_iso": common.iso(),
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    }
    return store.update_project(project_id, updates)


# ---------------------------------------------------------------------------
# Quality review — real checks against the rendered output
# ---------------------------------------------------------------------------
def _check(check_id, label, status, detail):
    return {"id": check_id, "label": label, "status": status, "detail": detail}


def run_quality_review(project_id):
    project = store.get_project(project_id)
    if project.get("status") == "published":
        raise common.CosmosError("project_published", "Published projects are already live.", 409)
    active = _active_render(project_id)
    if active:
        raise common.CosmosError(
            "render_in_progress",
            "Render %s is %s. Quality review can only inspect a finished render."
            % (active["id"], active.get("status")),
            409,
        )

    render = None
    for render_id in reversed(project.get("render_ids") or []):
        try:
            candidate = store.get_render(render_id)
        except common.CosmosError:
            continue
        if candidate.get("status") == "complete":
            render = candidate
            break

    checks = []
    settings = project.get("settings") or {}
    metadata = project.get("publishing_metadata") or {}
    media_record = None
    probe = None
    output_duration = None

    if render is None:
        checks.append(_check("render_exists", "Completed render exists", "fail",
                             "No completed render job is linked to this project."))
    else:
        checks.append(_check("render_exists", "Completed render exists", "pass",
                             "Render %s completed." % render["id"]))
        media_record = _media(render.get("media_id"))
        if media_record is None:
            checks.append(_check("output_in_library", "Output saved in the media library", "fail",
                                 "The completed render has no media library record."))
        else:
            checks.append(_check("output_in_library", "Output saved in the media library", "pass",
                                 "Media %s (%s)." % (media_record["id"], media_record.get("content_type"))))
            try:
                path, _ = studio_media.open_path(media_record["id"])
            except studio_media.MediaError:
                path = None
            if not path or not os.path.isfile(path):
                checks.append(_check("file_exists", "Output file exists", "fail", "The media file is missing on disk."))
            else:
                checks.append(_check("file_exists", "Output file exists", "pass", "File present."))
                try:
                    probe = genesis_render.probe(path)
                except Exception as exc:
                    checks.append(_check("playable", "File is playable", "fail", str(exc)[:300]))
                if probe:
                    streams = probe.get("streams") or []
                    video = next((s for s in streams if s.get("codec_type") == "video"), None)
                    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
                    if video:
                        checks.append(_check("video_stream", "Video stream is valid", "pass",
                                             "codec %s" % video.get("codec_name")))
                        width = int(video.get("width") or 0)
                        height = int(video.get("height") or 0)
                        if settings.get("width") and settings.get("height") and (width, height) != (settings["width"], settings["height"]):
                            checks.append(_check("resolution", "Resolution matches export settings", "warn",
                                                 "Rendered %dx%d, expected %dx%d." % (width, height, settings["width"], settings["height"])))
                        else:
                            checks.append(_check("resolution", "Resolution matches export settings", "pass",
                                                 "%dx%d" % (width, height)))
                        aspect = _check_aspect(width, height, settings.get("aspect_ratio"))
                        checks.append(_check("aspect_ratio", "Aspect ratio", aspect[0], aspect[1]))
                    else:
                        checks.append(_check("video_stream", "Video stream is valid", "fail", "No video stream found."))
                    if audio:
                        checks.append(_check("audio_stream", "Audio stream is valid", "pass", "codec %s" % audio.get("codec_name")))
                        try:
                            v_dur = float((video or {}).get("duration") or probe.get("format", {}).get("duration") or 0)
                            a_dur = float(audio.get("duration") or 0)
                        except (TypeError, ValueError):
                            v_dur = a_dur = 0
                        if v_dur and a_dur and abs(v_dur - a_dur) > 1.5:
                            checks.append(_check("audio_integrity", "Audio integrity", "warn",
                                                 "Audio %.1fs vs video %.1fs." % (a_dur, v_dur)))
                        else:
                            checks.append(_check("audio_integrity", "Audio integrity", "pass", "Audio track present."))
                    else:
                        checks.append(_check("audio_stream", "Audio stream is valid", "fail", "No audio track."))
                    fps = genesis_render.parse_fps((video or {}).get("avg_frame_rate") or (video or {}).get("r_frame_rate"))
                    if fps and settings.get("fps") and abs(fps - settings["fps"]) > 1.5:
                        checks.append(_check("frame_rate", "Frame rate matches export settings", "warn",
                                             "Rendered %.2f fps, expected %s." % (fps, settings["fps"])))
                    else:
                        checks.append(_check("frame_rate", "Frame rate matches export settings", "pass",
                                             "%.2f fps" % (fps or 0)))
                    duration = None
                    try:
                        duration = float(probe.get("format", {}).get("duration") or 0) or None
                    except (TypeError, ValueError):
                        pass
                    output_duration = duration
                    checks.append(_check("duration", "Duration readable", "pass" if duration else "warn",
                                         "%.2f s" % duration if duration else "Duration not reported by the container."))

    missing = [scene.get("title") or scene.get("id") for scene in (project.get("scenes") or [])
               if not scene.get("media_id")]
    if missing:
        checks.append(_check("missing_assets", "No missing scene assets", "fail",
                             "Scenes without media: %s." % ", ".join(missing[:6])))
    else:
        checks.append(_check("missing_assets", "No missing scene assets", "pass",
                             "%d scene(s) with media." % len(project.get("scenes") or [])))

    captions = project.get("audio") or {}
    burned = bool((render or {}).get("captions_burned"))
    if captions.get("captions_srt") or captions.get("captions_media_id"):
        if burned:
            checks.append(_check("captions", "Captions", "pass", "Captions burned into the rendered video."))
            caption_body = None
            caption_error = None
            try:
                caption_body = genesis_render.caption_text(captions)
            except genesis_render.RenderError as exc:
                caption_error = exc.message
            if caption_error:
                checks.append(_check("caption_sync", "Caption timing", "fail", caption_error))
            elif caption_body:
                sync_status, sync_detail = genesis_render.caption_sync(caption_body, output_duration)
                checks.append(_check("caption_sync", "Caption timing", sync_status, sync_detail))
        else:
            checks.append(_check("captions", "Captions", "fail",
                                 "Captions were supplied but are not embedded in the rendered output."))
    else:
        checks.append(_check("captions", "Captions", "warn", "No captions supplied for this project."))

    for platform in (settings.get("target_platforms") or []):
        passed, detail = _platform_check(platform, probe)
        checks.append(_check("platform_%s" % platform, "Platform requirements: %s" % platform, passed, detail))

    if metadata.get("sponsored") and not metadata.get("disclosure"):
        checks.append(_check("disclosure", "Sponsored/affiliate disclosure", "fail",
                             "The project is marked sponsored but no disclosure text is set."))
    elif metadata.get("affiliate_url") and not metadata.get("disclosure"):
        checks.append(_check("disclosure", "Sponsored/affiliate disclosure", "fail",
                             "An affiliate destination is set but no disclosure text is set."))
    else:
        checks.append(_check("disclosure", "Sponsored/affiliate disclosure", "pass",
                             metadata.get("disclosure") or "No sponsorship or affiliate destination recorded."))

    if not metadata.get("title"):
        checks.append(_check("metadata_title", "Destination metadata: title", "fail", "A publishing title is required."))
    else:
        checks.append(_check("metadata_title", "Destination metadata: title", "pass", metadata["title"]))
    if not metadata.get("description"):
        checks.append(_check("metadata_description", "Destination metadata: description", "warn", "No description set."))
    else:
        checks.append(_check("metadata_description", "Destination metadata: description", "pass",
                             "%d characters." % len(metadata["description"])))

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    verdict = "fail" if failed else ("warn" if warned else "pass")

    review = {
        "ran_at": common.now(),
        "ran_iso": common.iso(),
        "verdict": verdict,
        "render_id": render.get("id") if render else None,
        "media_id": media_record.get("id") if media_record else None,
        "checks": checks,
    }
    stage = "awaiting_approval" if verdict != "fail" else "quality_review"
    status = "awaiting_approval" if verdict != "fail" else "failed" if project.get("status") != "in_production" else project.get("status")
    store.update_project(project_id, {
        "quality_review": review,
        "stage": stage,
        "status": status if status in store.PROJECT_STATUSES else project.get("status"),
        "updated_at": common.now(),
        "updated_iso": common.iso(),
        "history": _append(project, "quality_review", "Verdict %s (%d fail, %d warn)." % (verdict, len(failed), len(warned))),
    })
    return {"project": store.get_project(project_id), "review": review}


def _check_aspect(width, height, expected):
    if not width or not height:
        return "warn", "Aspect ratio not reported."
    def gcd(a, b):
        while b:
            a, b = b, a % b
        return a or 1
    divisor = gcd(width, height)
    actual = "%d:%d" % (width // divisor, height // divisor)
    if actual == expected:
        return "pass", actual
    ratio = width / max(1, height)
    wanted = ASPECTS.get(expected)
    if wanted:
        target = wanted[0] / wanted[1]
        if abs(ratio - target) < 0.02:
            return "pass", "%s (%s)" % (actual, expected)
    return "warn", "Rendered %s, export setting is %s." % (actual, expected)


def _platform_check(platform, probe):
    duration = None
    try:
        duration = float((probe or {}).get("format", {}).get("duration") or 0) or None
    except (TypeError, ValueError):
        pass
    streams = (probe or {}).get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    if platform == "youtube_shorts":
        if height and width and height <= width:
            return "warn", "Shorts are vertical; this render is %dx%d." % (width, height)
        if duration and duration > 60:
            return "warn", "Shorts should stay under 60 seconds (rendered %.0fs)." % duration
        return "pass", "Vertical format within Shorts limits."
    if platform == "meta_ads":
        if duration and duration > 60:
            return "warn", "Meta ads recommend 15–30 seconds (rendered %.0fs)." % duration
        return "pass", "Suitable for Meta feed placements."
    if platform in ("youtube", "tiktok"):
        return "pass", "No blocking platform rule configured for %s." % platform
    return "warn", "Unknown destination platform '%s' — no rules applied." % platform


def _append(project, event, note=None):
    history = list(project.get("history") or [])
    history.append({
        "at": common.now(),
        "iso": common.iso(),
        "event": event,
        "note": common.clean_text(note, "History note", 500) if note else None,
    })
    return history


# ---------------------------------------------------------------------------
# Owner approval
# ---------------------------------------------------------------------------
def approve_project(project_id, payload):
    project = store.get_project(project_id)
    approved = bool((payload or {}).get("approved"))
    note = common.clean_text((payload or {}).get("note"), "Approval note", 500)
    review = project.get("quality_review")
    if approved:
        if not review or review.get("verdict") == "fail":
            raise common.CosmosError(
                "quality_review_required",
                "A passing quality review is required before approval. Run the quality review first.",
                409,
            )
        if not project.get("media_id") or review.get("media_id") != project.get("media_id"):
            raise common.CosmosError(
                "quality_review_stale",
                "The quality review does not match the current render. Re-run the quality review before approving.",
                409,
            )
        approval = {
            "approved": True,
            "by": "owner",
            "at": common.now(),
            "at_iso": common.iso(),
            "note": note,
            "review_verdict": review.get("verdict"),
            "review_ran_iso": review.get("ran_iso"),
            "render_id": review.get("render_id"),
            "media_id": review.get("media_id"),
        }
        updates = {
            "approval": approval,
            "status": "approved",
            "stage": "publishing",
            "history": _append(project, "approved", note or "Owner approved for publishing."),
        }
    else:
        approval = {
            "approved": False,
            "by": "owner",
            "at": common.now(),
            "at_iso": common.iso(),
            "note": note,
            "review_verdict": review.get("verdict") if review else None,
            "render_id": (review or {}).get("render_id"),
            "media_id": (review or {}).get("media_id"),
        }
        updates = {
            "approval": approval,
            "status": "in_production",
            "stage": "quality_review" if review else "assets",
            "history": _append(project, "approval_withdrawn", note),
        }
        # Withdrawing approval must also stop a schedule that was waiting on it;
        # the scheduler re-checks approval, but cancelling keeps the UI honest.
        for publication in store.list_publications(project_id=project_id, status="scheduled", limit=100):
            try:
                store.update_publication(publication["id"], {
                    "status": "cancelled",
                    "updated_iso": common.iso(),
                    "history": (publication.get("history") or []) + [{
                        "iso": common.iso(), "event": "cancelled",
                        "note": "Project approval was withdrawn."}],
                })
            except common.CosmosError:
                continue
    updates["updated_at"] = common.now()
    updates["updated_iso"] = common.iso()
    store.update_project(project_id, updates)
    return {"project": store.get_project(project_id)}


def mark_published(project_id, publication):
    """Called by the publishing adapter after the destination confirmed success."""
    project = store.get_project(project_id)
    publication_ids = list(project.get("publication_ids") or [])
    if publication.get("id") and publication["id"] not in publication_ids:
        publication_ids.append(publication["id"])
    patch = {
        "publication_ids": publication_ids,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    }
    if project.get("status") != "published":
        patch.update({
            "status": "published",
            "stage": "done",
            "history": _append(project, "published", "%s · %s" % (
                publication.get("destination"), publication.get("remote_url") or publication.get("remote_id"))),
        })
    store.update_project(project_id, patch)
    _mark_source_published(store.get_project(project_id), publication)
    return store.get_project(project_id)


def _mark_source_published(project, publication):
    """Advance the originating backlog record through its own lifecycle.

    Only a destination-confirmed publication moves the source record — the
    forward-only transitions in the existing backlogs stay authoritative.
    """
    source = project.get("source") or {}
    try:
        if source.get("kind") == "ads_product":
            product = ads_backlog.get_product(source.get("id"))
            if product.get("status") == "in_production":
                ads_backlog.set_status(
                    source["id"], "published",
                    note="Published via GENESIS (%s · %s)" % (
                        publication.get("destination"),
                        publication.get("remote_url") or publication.get("remote_id") or "confirmed"),
                )
        elif source.get("kind") == "youtube_topic":
            channel_id = source.get("channel_id") or (project.get("provenance") or {}).get("channel_id")
            topic = youtube_topics.get_topic(channel_id, source.get("id"))
            if topic.get("status") == "in_production":
                youtube_topics.set_status(
                    channel_id, source["id"], "published",
                )
    except Exception as exc:  # source update must never break the publication record
        store.update_project(project["id"], {
            "history": _append(project, "source_status_update_failed", str(exc)[:300]),
        })
