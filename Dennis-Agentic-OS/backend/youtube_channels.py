"""YouTube channel profiles, master prompt versions and agent task extensions.

The channel master prompt is *data*, not code: it is seeded once from the
pinned catalog file and afterwards lives in ``channel-prompts.json`` as
immutable versions. Editing creates a new version; restoring an old version
also creates a new version, so history is never rewritten. The frontend never
hardcodes prompt text.

Agent task extensions (Research Agent, Scriptwriter Agent, ...) are stored per
channel and always composed *after* the channel master prompt, so a task
extension can never overwrite it.
"""

from __future__ import annotations

import json
import os

from . import studio_workspaces
from . import youtube_common as common

CATALOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog")
SEED_CATALOG = os.path.join(CATALOG_DIR, "business-money-blueprint-0.1.0.json")
SEED_PROMPT_FILE = os.path.join(CATALOG_DIR, "business-money-blueprint-master-prompt.txt")


def _channels_file():
    return os.path.join(common.YOUTUBE_DIR, "channels.json")


def _prompts_file():
    return os.path.join(common.YOUTUBE_DIR, "channel-prompts.json")


def _agent_prompts_file():
    return os.path.join(common.YOUTUBE_DIR, "agent-prompts.json")

AGENT_ROLES = (
    "research",
    "topic",
    "scriptwriter",
    "fact_check",
    "storyboard",
    "visual_asset",
    "voice",
    "animation",
    "publishing",
)

CHANNEL_FIELDS = (
    "name",
    "tagline",
    "handle",
    "description",
    "target_audience",
    "audience_age_focus",
    "language",
    "country",
    "content_niche",
    "editorial_guidelines",
    "default_voice",
    "default_visual_style",
    "narration_personality",
    "monetization_objective",
    "logo_media_id",
    "banner_media_id",
)

_seed_cache = None


class ChannelError(common.YoutubeError):
    pass


# ---------------------------------------------------------------------------
# Seed catalog
# ---------------------------------------------------------------------------
def seed():
    """Load the pinned channel seed catalog (cached)."""
    global _seed_cache
    if _seed_cache is None:
        try:
            with open(SEED_CATALOG, "r", encoding="utf-8") as fh:
                _seed_cache = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ChannelError("seed_unreadable", "Channel seed catalog cannot be read: %s" % exc, 500)
        if not isinstance(_seed_cache, dict) or not isinstance(_seed_cache.get("channel"), dict):
            raise ChannelError("seed_invalid", "Channel seed catalog has an unsupported format.", 500)
    return _seed_cache


def seed_master_prompt_body():
    try:
        with open(SEED_PROMPT_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError as exc:
        raise ChannelError("seed_unreadable", "Master prompt seed file cannot be read: %s" % exc, 500)


def _channel_dir():
    return common.YOUTUBE_DIR


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------
def _load_channels():
    data = common.read_store(_channels_file(), "channels")
    return [item for item in data.get("channels", []) if isinstance(item, dict) and item.get("id")]


def _save_channels(items):
    common.write_store(_channels_file(), "channels", items)


def _save_channel(channel):
    with common.acquire():
        items = _load_channels()
        for index, item in enumerate(items):
            if item["id"] == channel["id"]:
                items[index] = channel
                break
        else:
            items.append(channel)
        _save_channels(items)


def ensure_seed():
    """Create the initial channel, its master prompt and agent extensions.

    Idempotent: only runs when no channel exists yet.
    """
    with common.acquire():
        channels = _load_channels()
        if channels:
            return channels[0]
        data = seed()["channel"]
        stamp = common.now()
        channel = {
            "id": common.new_id("ytch_"),
            "name": common.clean_text(data.get("name"), "Channel name", 120, required=True),
            "tagline": common.clean_text(data.get("tagline"), "Tagline", 200),
            "handle": common.clean_text(data.get("handle"), "Handle", 80),
            "description": common.clean_text(data.get("description"), "Description", 2000),
            "brand_colors": data.get("brand_colors") or {},
            "target_audience": common.clean_text(data.get("target_audience"), "Target audience", 500),
            "audience_age_focus": common.clean_text(data.get("audience_age_focus"), "Age focus", 40),
            "language": common.clean_text(data.get("language"), "Language", 20),
            "country": common.clean_text(data.get("country"), "Country", 20),
            "content_niche": common.clean_text(data.get("content_niche"), "Niche", 300),
            "content_pillars": [
                {
                    "id": pillar.get("id"),
                    "label": pillar.get("label"),
                    "allocation": pillar.get("allocation"),
                }
                for pillar in seed().get("pillars", [])
            ],
            "editorial_guidelines": common.clean_text(data.get("editorial_guidelines"), "Guidelines", 4000),
            "publishing": data.get("publishing") or {},
            "default_voice": common.clean_text(data.get("default_voice"), "Voice", 200) or None,
            "default_visual_style": common.clean_text(data.get("default_visual_style"), "Visual style", 500) or None,
            "narration_personality": common.clean_text(data.get("narration_personality"), "Narration", 200) or None,
            "monetization_objective": common.clean_text(data.get("monetization_objective"), "Objective", 500) or None,
            "logo_media_id": None,
            "banner_media_id": None,
            "youtube_account": None,
            "master_prompt_version_id": None,
            "archived": False,
            "deleted_at": None,
            "created_at": stamp,
            "created_iso": common.iso(stamp),
            "updated_at": stamp,
            "updated_iso": common.iso(stamp),
        }
        channels.append(channel)
        _save_channels(channels)

        version = _create_version_locked(
            channel["id"],
            seed_master_prompt_body(),
            note="Seeded from MASTER PROMPT 01 (The Business Money Blueprint).",
            is_default=True,
        )
        channel["master_prompt_version_id"] = version["id"]
        _save_channel(channel)

        for spec in seed().get("agent_prompts", []):
            role = spec.get("role")
            if role in AGENT_ROLES:
                _set_agent_prompt_locked(channel["id"], role, spec.get("body") or "")

        try:
            studio_workspaces.update_settings("youtube", {"active_channel_id": channel["id"]})
        except Exception:  # settings are a convenience; never block the seed
            pass
        return channel


def _public(channel):
    data = common.public(channel)
    version = _find_version(channel["id"], channel.get("master_prompt_version_id"))
    if version:
        data["master_prompt"] = {
            "version_id": version["id"],
            "version": version["version"],
            "updated_iso": version["created_iso"],
            "chars": len(version.get("body") or ""),
        }
    return data


def list_channels(include_archived=False):
    with common.acquire():
        ensure_seed()
        items = _load_channels()
    items = [c for c in items if not c.get("deleted_at")]
    if not include_archived:
        items = [c for c in items if not c.get("archived")]
    items.sort(key=lambda c: c.get("created_at") or 0)
    return {"channels": [common.public(c) for c in items]}


def get_channel(channel_id, include_deleted=False):
    with common.acquire():
        items = _load_channels()
    channel = common.find(items, str(channel_id or ""))
    if not channel or (channel.get("deleted_at") and not include_deleted):
        raise ChannelError("channel_not_found", "Channel not found.", 404)
    return _public(channel)


def active_channel():
    try:
        settings = studio_workspaces.get_settings("youtube")
        active_id = settings.get("active_channel_id")
    except Exception:
        active_id = None
    with common.acquire():
        items = [c for c in _load_channels() if not c.get("deleted_at") and not c.get("archived")]
    if active_id:
        for channel in items:
            if channel["id"] == active_id:
                return _public(channel)
    if items:
        return _public(items[0])
    return _public(ensure_seed())


def activate_channel(channel_id):
    channel = get_channel(channel_id)
    studio_workspaces.update_settings("youtube", {"active_channel_id": channel["id"]})
    return {"active_channel_id": channel["id"]}


def create_channel(payload):
    if not isinstance(payload, dict):
        raise ChannelError("invalid_channel", "Channel payload must be a JSON object.")
    name = common.clean_text(payload.get("name"), "Channel name", 120, required=True)
    with common.acquire():
        items = _load_channels()
        for item in items:
            if not item.get("deleted_at") and item.get("name", "").lower() == name.lower():
                raise ChannelError("duplicate_channel", "A channel with this name already exists.", 409)
        stamp = common.now()
        channel = {
            "id": common.new_id("ytch_"),
            "name": name,
            "tagline": common.clean_text(payload.get("tagline"), "Tagline", 200),
            "handle": common.clean_text(payload.get("handle"), "Handle", 80),
            "description": common.clean_text(payload.get("description"), "Description", 2000),
            "brand_colors": payload.get("brand_colors") if isinstance(payload.get("brand_colors"), dict) else {},
            "target_audience": common.clean_text(payload.get("target_audience"), "Target audience", 500),
            "audience_age_focus": common.clean_text(payload.get("audience_age_focus"), "Age focus", 40),
            "language": common.clean_text(payload.get("language"), "Language", 20) or "en-US",
            "country": common.clean_text(payload.get("country"), "Country", 20) or "US",
            "content_niche": common.clean_text(payload.get("content_niche"), "Niche", 300),
            "content_pillars": _clean_pillars(payload.get("content_pillars")),
            "editorial_guidelines": common.clean_text(payload.get("editorial_guidelines"), "Guidelines", 4000),
            "publishing": _clean_publishing(payload.get("publishing")),
            "default_voice": common.clean_text(payload.get("default_voice"), "Voice", 200) or None,
            "default_visual_style": common.clean_text(payload.get("default_visual_style"), "Visual style", 500) or None,
            "narration_personality": common.clean_text(payload.get("narration_personality"), "Narration", 200) or None,
            "monetization_objective": common.clean_text(payload.get("monetization_objective"), "Objective", 500) or None,
            "logo_media_id": common.clean_text(payload.get("logo_media_id"), "Logo", 64) or None,
            "banner_media_id": common.clean_text(payload.get("banner_media_id"), "Banner", 64) or None,
            "youtube_account": None,
            "master_prompt_version_id": None,
            "archived": False,
            "deleted_at": None,
            "created_at": stamp,
            "created_iso": common.iso(stamp),
            "updated_at": stamp,
            "updated_iso": common.iso(stamp),
        }
        items.append(channel)
        _save_channels(items)
    # A brand new channel starts with an editable copy of the master prompt.
    version = _create_version_locked(
        channel["id"], seed_master_prompt_body(), note="Initial master prompt (copied from the seed channel).", is_default=True
    )
    channel["master_prompt_version_id"] = version["id"]
    _save_channel(channel)
    return _public(channel)


def _clean_pillars(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 12:
        raise ChannelError("invalid_channel", "content_pillars must be a list of at most 12 pillars.")
    pillars = []
    for pillar in value:
        if not isinstance(pillar, dict):
            raise ChannelError("invalid_channel", "Each pillar must be an object.")
        label = common.clean_text(pillar.get("label"), "Pillar label", 80, required=True)
        allocation = pillar.get("allocation")
        try:
            allocation = int(allocation)
        except (TypeError, ValueError):
            allocation = 0
        pillars.append(
            {
                "id": common.clean_text(pillar.get("id"), "Pillar id", 60) or label.lower().replace(" ", "-")[:60],
                "label": label,
                "allocation": max(0, min(100, allocation)),
            }
        )
    return pillars


def _clean_publishing(value):
    base = {"schedule": ["mon", "wed", "fri"], "videos_per_week": 3, "default_duration_min": 12}
    if value is None:
        return base
    if not isinstance(value, dict):
        raise ChannelError("invalid_channel", "publishing must be an object.")
    schedule = value.get("schedule")
    if isinstance(schedule, list):
        allowed = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
        days = [str(day).strip().lower()[:3] for day in schedule]
        cleaned = [day for day in days if day in allowed]
        if cleaned:
            base["schedule"] = sorted(set(cleaned), key=["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index)
    for key in ("videos_per_week", "default_duration_min", "duration_range_min", "duration_range_max"):
        if value.get(key) is not None:
            try:
                base[key] = max(1, min(60, int(value.get(key))))
            except (TypeError, ValueError):
                raise ChannelError("invalid_channel", "%s must be a number." % key)
    return base


def update_channel(channel_id, patch):
    if not isinstance(patch, dict):
        raise ChannelError("invalid_channel", "Channel payload must be a JSON object.")
    with common.acquire():
        items = _load_channels()
        channel = common.find(items, str(channel_id or ""))
        if not channel or channel.get("deleted_at"):
            raise ChannelError("channel_not_found", "Channel not found.", 404)
        if "name" in patch:
            channel["name"] = common.clean_text(patch.get("name"), "Channel name", 120, required=True)
        for field in CHANNEL_FIELDS:
            if field in patch and field != "name":
                channel[field] = common.clean_text(patch.get(field), field.replace("_", " ").title(), 4000) or None
        if "tagline" in patch:
            channel["tagline"] = common.clean_text(patch.get("tagline"), "Tagline", 200)
        if "brand_colors" in patch:
            if patch.get("brand_colors") is not None and not isinstance(patch.get("brand_colors"), dict):
                raise ChannelError("invalid_channel", "brand_colors must be an object.")
            channel["brand_colors"] = patch.get("brand_colors") or {}
        if "content_pillars" in patch:
            channel["content_pillars"] = _clean_pillars(patch.get("content_pillars"))
        if "publishing" in patch:
            channel["publishing"] = _clean_publishing(patch.get("publishing"))
        if "archived" in patch:
            channel["archived"] = common.clean_bool(patch.get("archived"))
        stamp = common.now()
        channel["updated_at"] = stamp
        channel["updated_iso"] = common.iso(stamp)
        _save_channels(items)
    return _public(channel)


def delete_channel(channel_id):
    with common.acquire():
        items = _load_channels()
        channel = common.find(items, str(channel_id or ""))
        if not channel or channel.get("deleted_at"):
            raise ChannelError("channel_not_found", "Channel not found.", 404)
        live = [c for c in items if not c.get("deleted_at")]
        if len(live) <= 1:
            raise ChannelError("last_channel", "At least one channel profile must remain.", 409)
        common.soft_delete(channel)
        _save_channels(items)
    return {"deleted": channel["id"]}


# ---------------------------------------------------------------------------
# Master prompt versions
# ---------------------------------------------------------------------------
def _load_versions():
    data = common.read_store(_prompts_file(), "prompts")
    return [item for item in data.get("prompts", []) if isinstance(item, dict) and item.get("id")]


def _save_versions(items):
    common.write_store(_prompts_file(), "prompts", items)


def _next_version_number(items, channel_id):
    numbers = [item.get("version") or 0 for item in items if item.get("channel_id") == channel_id]
    return (max(numbers) if numbers else 0) + 1


def _create_version_locked(channel_id, body, note="", is_default=False, title=None):
    body = common.clean_text(body, "Prompt body", 200_000, required=True)
    note = common.clean_text(note, "Note", 300)
    items = _load_versions()
    stamp = common.now()
    version = {
        "id": common.new_id("ytcv_"),
        "channel_id": channel_id,
        "version": _next_version_number(items, channel_id),
        "title": common.clean_text(title, "Title", 160) or ("Master Prompt v%d" % _next_version_number(items, channel_id)),
        "body": body,
        "note": note,
        "chars": len(body),
        "created_at": stamp,
        "created_iso": common.iso(stamp),
    }
    items.append(version)
    _save_versions(items)
    if is_default:
        _set_default_version_locked(channel_id, version["id"])
    return version


def _set_default_version_locked(channel_id, version_id):
    items = _load_channels()
    channel = common.find(items, channel_id)
    if not channel:
        raise ChannelError("channel_not_found", "Channel not found.", 404)
    channel["master_prompt_version_id"] = version_id
    stamp = common.now()
    channel["updated_at"] = stamp
    channel["updated_iso"] = common.iso(stamp)
    _save_channels(items)


def _find_version(channel_id, version_id):
    for item in _load_versions():
        if item["channel_id"] == channel_id and item["id"] == version_id:
            return item
    return None


def list_prompt_versions(channel_id):
    channel = get_channel(channel_id)
    with common.acquire():
        items = [
            v for v in _load_versions()
            if v["channel_id"] == channel["id"]
        ]
    items.sort(key=lambda v: v.get("version") or 0, reverse=True)
    versions = []
    for item in items:
        data = common.public(item)
        data["is_default"] = item["id"] == channel.get("master_prompt_version_id")
        versions.append(data)
    return {"channel_id": channel["id"], "versions": versions}


def current_prompt(channel_id):
    channel = get_channel(channel_id)
    version = _find_version(channel["id"], channel.get("master_prompt_version_id"))
    if not version:
        with common.acquire():
            items = [v for v in _load_versions() if v["channel_id"] == channel["id"]]
        if not items:
            raise ChannelError("prompt_missing", "This channel has no master prompt version yet.", 404)
        version = max(items, key=lambda v: v.get("version") or 0)
    return {"channel_id": channel["id"], "prompt": _version_public(version, channel)}


def _version_public(version, channel=None):
    data = common.public(version)
    if channel is not None:
        data["is_default"] = version["id"] == channel.get("master_prompt_version_id")
    return data


def add_prompt_version(channel_id, payload):
    if not isinstance(payload, dict):
        raise ChannelError("invalid_prompt", "Prompt payload must be a JSON object.")
    channel = get_channel(channel_id)
    with common.acquire():
        version = _create_version_locked(
            channel["id"],
            payload.get("body"),
            note=payload.get("note") or "",
            is_default=common.clean_bool(payload.get("set_default"), True),
            title=payload.get("title"),
        )
    return {"channel_id": channel["id"], "version": _version_public(version, get_channel(channel_id))}


def restore_prompt_version(channel_id, version_id, note=None):
    """Restore = create a NEW version with the old body. History is immutable."""
    channel = get_channel(channel_id)
    with common.acquire():
        old = _find_version(channel["id"], str(version_id or ""))
        if not old:
            raise ChannelError("version_not_found", "Prompt version not found.", 404)
        version = _create_version_locked(
            channel["id"],
            old["body"],
            note=note or ("Restored from v%d." % old["version"]),
            is_default=True,
            title="Master Prompt v%d (restored v%d)" % (_next_version_number(_load_versions(), channel["id"]), old["version"]),
        )
    return {"channel_id": channel["id"], "version": _version_public(version, get_channel(channel_id))}


def duplicate_prompt_version(channel_id, version_id, to_channel_id, note=None):
    source_channel = get_channel(channel_id)
    target = get_channel(to_channel_id)
    with common.acquire():
        old = _find_version(source_channel["id"], str(version_id or ""))
        if not old:
            raise ChannelError("version_not_found", "Prompt version not found.", 404)
        version = _create_version_locked(
            target["id"],
            old["body"],
            note=note or ("Duplicated from %s v%d." % (source_channel["name"], old["version"])),
            is_default=True,
            title="Master Prompt v%d (from %s v%d)" % (
                _next_version_number(_load_versions(), target["id"]), source_channel["name"], old["version"]
            ),
        )
    return {"channel_id": target["id"], "version": _version_public(version, get_channel(target["id"]))}


def set_default_prompt_version(channel_id, version_id):
    channel = get_channel(channel_id)
    with common.acquire():
        version = _find_version(channel["id"], str(version_id or ""))
        if not version:
            raise ChannelError("version_not_found", "Prompt version not found.", 404)
        _set_default_version_locked(channel["id"], version["id"])
    return {"channel_id": channel["id"], "version": _version_public(version, get_channel(channel_id))}


# ---------------------------------------------------------------------------
# Agent task extensions
# ---------------------------------------------------------------------------
def _load_agent_prompts():
    data = common.read_store(_agent_prompts_file(), "prompts")
    return [item for item in data.get("prompts", []) if isinstance(item, dict)]


def _save_agent_prompts(items):
    common.write_store(_agent_prompts_file(), "prompts", items)


def _default_agent_prompt(role):
    for spec in seed().get("agent_prompts", []):
        if spec.get("role") == role:
            return spec
    return {"role": role, "label": role.replace("_", " ").title(), "body": ""}


def _set_agent_prompt_locked(channel_id, role, body):
    if role not in AGENT_ROLES:
        raise ChannelError("unknown_role", "Unknown agent role: %s" % role, 404)
    items = _load_agent_prompts()
    for item in items:
        if item.get("channel_id") == channel_id and item.get("role") == role:
            item["body"] = common.clean_text(body, "Prompt body", 20_000)
            stamp = common.now()
            item["updated_at"] = stamp
            item["updated_iso"] = common.iso(stamp)
            _save_agent_prompts(items)
            return item
    default = _default_agent_prompt(role)
    stamp = common.now()
    item = {
        "id": common.new_id("ytap_"),
        "channel_id": channel_id,
        "role": role,
        "label": default.get("label") or role.title(),
        "body": common.clean_text(body, "Prompt body", 20_000),
        "updated_at": stamp,
        "updated_iso": common.iso(stamp),
    }
    items.append(item)
    _save_agent_prompts(items)
    return item


def list_agent_prompts(channel_id):
    channel = get_channel(channel_id)
    with common.acquire():
        items = _load_agent_prompts()
    by_role = {item["role"]: item for item in items if item.get("channel_id") == channel["id"]}
    prompts = []
    for role in AGENT_ROLES:
        default = _default_agent_prompt(role)
        stored = by_role.get(role)
        prompts.append(
            {
                "role": role,
                "label": (stored or default).get("label") or role.replace("_", " ").title(),
                "body": (stored or default).get("body") or "",
                "customized": bool(stored and stored.get("body") and stored.get("body") != default.get("body")),
                "updated_iso": (stored or {}).get("updated_iso"),
            }
        )
    return {"channel_id": channel["id"], "prompts": prompts}


def update_agent_prompt(channel_id, role, body):
    channel = get_channel(channel_id)
    with common.acquire():
        item = _set_agent_prompt_locked(channel["id"], str(role or ""), body or "")
    return {"channel_id": channel["id"], "prompt": common.public(item)}


def compose_agent_prompt(channel_id, role, task_context=None):
    """Compose the full instruction for a production agent.

    Order matters and is fixed: channel master prompt, then the task-specific
    extension, then the task context. A task extension can never overwrite the
    channel master prompt.
    """
    channel = get_channel(channel_id)
    master = current_prompt(channel["id"])["prompt"]["body"]
    prompts = {item["role"]: item for item in list_agent_prompts(channel["id"])["prompts"]}
    extension = (prompts.get(str(role or "")) or {}).get("body") or ""
    parts = [master.strip()]
    if extension.strip():
        parts.append(extension.strip())
    context = common.clean_text(task_context, "Task context", 20_000)
    if context:
        parts.append("TASK CONTEXT\n%s" % context)
    return {
        "channel_id": channel["id"],
        "role": role,
        "master_prompt_version": current_prompt(channel["id"])["prompt"].get("version"),
        "prompt": "\n\n".join(parts),
        "chars": sum(len(part) for part in parts),
    }
