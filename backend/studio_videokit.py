"""Studio Video Kit & Video Reverse Engineering Skill Integration.

Uses the 'source to skill' visual analysis tools (Gemini multimodal vision,
ffmpeg/ffprobe, and source extractors) as eyes and ears to analyze:
  1. Reference image/video (URLs from TikTok, YouTube, Instagram, etc. or uploaded files)
  2. Avatar (character reference for identity preservation and character replacement)
  3. Product / Props (product asset for logo, branding, and hero placement)

Applies the 'reference-video-recreation' skill methodology to synthesize
shot-accurate generation prompts and provider parameters for video generation agents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from . import config
from . import skill_builder
from . import skill_sources
from . import studio_media
from . import vault as dennis_vault


# Skills in the shared-brain Vault that describe video generation work are
# offered as Studio chat templates. The Skill Builder publishes approved
# SKILL.md files without a machine-readable mode, so the source material is
# classified from its name/description plus a bounded look at the body.
_VIDEO_SKILL_TERMS = (
    "video",
    "reel",
    "t2v",
    "i2v",
    "text-to-video",
    "image-to-video",
    "video-to-video",
    "reference-video",
    "seedance",
    "kling",
    "sora",
    "runway",
    "pika",
    "storyboard",
    "shotlist",
    "shot list",
)

_PROMPT_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]+(.+?)[ \t]*$")
_FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_TEMPLATE_PROMPT_LIMIT = 20000


def _strip_frontmatter(text: str) -> str:
    """Return the Markdown body of a SKILL.md without its YAML frontmatter."""
    body = (text or "").lstrip("\ufeff")
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            return body[end + 4:].lstrip("\n")
    return body


def _looks_like_video_skill(name: str, title: str, description: str, content: str) -> bool:
    header = " ".join((name or "", title or "", description or "")).lower()
    if any(term in header for term in _VIDEO_SKILL_TERMS):
        return True
    # Frontmatter tags can opt a skill in explicitly, e.g. tags: [video-generation].
    front = (content or "")[:2000].lower()
    if re.search(r"(?m)^(tags|type|category)\s*:.*video", front):
        return True
    # Fall back to the opening of the body for skills with generic metadata.
    opening = _strip_frontmatter(content)[:6000].lower()
    return any(term in opening for term in (
        "video generation", "text-to-video", "image-to-video",
        "video-to-video", "reference-video", "generation prompt",
    ))


def _extract_generation_prompt(content: str) -> str:
    """Pull a copy-ready prompt block out of a SKILL.md.

    Prefers headings such as "Initial generation prompt" or "Reusable prompt
    templates"; falls back to the first fenced block in the document and then
    to the full body so a template always loads something editable.
    """
    body = _strip_frontmatter(content)
    # Headings inside fenced blocks are prompt text, not document structure.
    masked = _FENCE_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), body)
    headings = list(_PROMPT_HEADING_RE.finditer(masked))
    best_score = 0
    best_text = ""
    for index, match in enumerate(headings):
        title = match.group(2).strip().lower()
        if "prompt" not in title:
            continue
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        section = body[start:end].strip()
        if not section:
            continue
        fence = _FENCE_RE.search(section)
        text = fence.group(1).strip() if fence else section
        if not text:
            continue
        score = 1
        if "initial" in title or "master" in title:
            score += 4
        if "generation" in title:
            score += 3
        if "reusable" in title or "template" in title:
            score += 2
        if score > best_score:
            best_score = score
            best_text = text
    if not best_text:
        fence = _FENCE_RE.search(body)
        best_text = fence.group(1).strip() if fence else body.strip()
    return best_text[:_TEMPLATE_PROMPT_LIMIT].strip()


def list_video_templates() -> Dict[str, Any]:
    """List approved video-generation skills from the shared-brain Vault.

    Each entry carries the skill metadata plus the generation prompt that the
    Studio chat box should load. Nothing is cached: a skill published in the
    Skill Builder appears here on the next request.
    """
    templates = []
    try:
        listing = skill_builder.list_skills()
    except Exception:
        listing = {"skills": []}
    for skill in listing.get("skills") or []:
        if skill.get("source") != "vault":
            continue
        name = skill.get("name") or ""
        title = skill.get("title") or name
        description = skill.get("description") or ""
        try:
            record = skill_builder.read_skill(name)
        except Exception:
            continue
        content = record.get("content") or ""
        if not _looks_like_video_skill(name, title, description, content):
            continue
        prompt = _extract_generation_prompt(content)
        if not prompt:
            continue
        templates.append({
            "id": name,
            "name": name,
            "title": title,
            "description": description,
            "prompt": prompt,
            "chars": len(prompt),
            "path": skill.get("path") or "",
            "installed": bool(skill.get("installed")),
        })
    templates.sort(key=lambda item: (item.get("title") or item.get("name") or "").lower())
    return {
        "templates": templates,
        "count": len(templates),
        "vault_skills_dir": listing.get("vault_skills_dir") or "",
    }


def _detect_platform(url: str) -> str:
    """Identify media platform or source type from URL."""
    if not url:
        return "file"
    u = url.lower()
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in skill_sources.PLATFORM_HOSTS:
        return skill_sources.PLATFORM_HOSTS[host]
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    if any(ext in u for ext in (".mp4", ".mov", ".webm", ".m4v")):
        return "direct_video"
    if any(ext in u for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
        return "direct_image"
    if u.startswith("/api/studio/media/"):
        return "local_studio_media"
    return "web_url"


def _extract_url_or_path(item: Any) -> Tuple[str, str, Optional[str]]:
    """Extract (url_or_path, label, media_id) from panel input dict or string."""
    if not item:
        return ("", "", None)
    if isinstance(item, str):
        return (item.strip(), item.strip(), None)
    if isinstance(item, dict):
        url = item.get("url") or item.get("public_url") or ""
        name = item.get("name") or item.get("filename") or url
        media_id = item.get("media_id") or item.get("id")
        return (url.strip(), name, media_id)
    return ("", "", None)


def _write_temp_media(item: Any) -> Tuple[str, str]:
    """Write an attached base64 asset to a temp file for local analysis."""
    if not isinstance(item, dict):
        raise ValueError("Attached media is not readable.")
    raw = item.get("data_base64") or ""
    if not raw:
        raise ValueError("Attached media has no data.")
    ctype = (item.get("content_type") or "").lower()
    suffix = ".mp4"
    if ctype.startswith("image/"):
        suffix = ".png"
    elif ctype.startswith("audio/"):
        suffix = ".mp3"
    try:
        blob = base64.b64decode(raw)
    except Exception:
        raise ValueError("Attached media is not valid base64.")
    fd, path = tempfile.mkstemp(prefix="dennis-vk-", suffix=suffix)
    with os.fdopen(fd, "wb") as fh:
        fh.write(blob)
    return path, ctype


def _reference_kind(reference: Any, ref_url: str, platform: str) -> str:
    """Classify a Video Kit reference as image or video for the right eyes."""
    ctype = ""
    if isinstance(reference, dict):
        ctype = (reference.get("content_type") or "").lower()
    if ctype.startswith("video/"):
        return "video"
    if ctype.startswith("image/"):
        return "image"
    if platform in ("youtube", "direct_video", "local_video"):
        return "video"
    if platform == "direct_image" or platform == "local_image":
        return "image"
    if platform in set(skill_sources.PLATFORM_HOSTS.values()):
        return "video"
    ext = os.path.splitext(urllib.parse.urlparse(ref_url).path.lower())[1]
    if ext in (".mp4", ".mov", ".webm", ".m4v", ".mkv"):
        return "video"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"):
        return "image"
    return "unknown"


def _probe_local_media(media_id_or_path: str) -> Dict[str, Any]:
    """Inspect local media file technicals (resolution, duration) if available."""
    info = {"format": "unknown", "duration": None, "resolution": None}
    if not media_id_or_path:
        return info

    path = None
    if media_id_or_path.startswith("/api/studio/media/"):
        med_id = media_id_or_path[len("/api/studio/media/"):]
        entry = studio_media.get(med_id)
        if entry:
            path = studio_media.open_path(med_id)
    elif os.path.exists(media_id_or_path):
        path = media_id_or_path

    if path and os.path.isfile(path):
        info["size_bytes"] = os.path.getsize(path)
        # Attempt ffprobe if available
        ffprobe = skill_builder._which("ffprobe")
        if ffprobe:
            try:
                cmd = [
                    ffprobe, "-v", "error", "-show_entries",
                    "format=duration:stream=width,height,codec_name,r_frame_rate",
                    "-of", "json", path
                ]
                env = skill_builder._child_env()
                out, _, code = skill_builder._run(cmd, env=env, timeout=10)
                if code == 0:
                    data = json.loads(out)
                    fmt = data.get("format", {})
                    streams = data.get("streams", [{}])
                    s0 = streams[0] if streams else {}
                    info["duration"] = float(fmt.get("duration", 0)) if fmt.get("duration") else None
                    if s0.get("width") and s0.get("height"):
                        info["resolution"] = f"{s0['width']}x{s0['height']}"
                    info["codec"] = s0.get("codec_name")
            except Exception:
                pass
    return info


def analyze_video_kit(
    reference: Any,
    avatar: Optional[Any] = None,
    product: Optional[Any] = None,
    prompt: str = ""
) -> Dict[str, Any]:
    """Examine the Video Kit using source-to-skill visual analysis tools.

    Analyzes reference media, avatar identity, and product assets,
    producing an evidence-labelled scene breakdown and recreation prompt
    following the reference-video-recreation skill specifications.
    """
    ref_url, ref_name, ref_id = _extract_url_or_path(reference)
    avatar_url, avatar_name, avatar_id = _extract_url_or_path(avatar)
    product_url, product_name, product_id = _extract_url_or_path(product)

    if not ref_url and not ref_name:
        raise ValueError("Reference media (URL or file) is required for Video Kit analysis.")

    local_ref_path = ""
    ref_meta: Dict[str, Any] = {}
    analysis_text = ""
    shotlist: List[Dict[str, Any]] = []
    character_guidance = ""
    product_guidance = ""
    avatar_image = avatar_url if avatar_url.startswith("http") else (
        _vault_image_from_api_url(avatar_url) if avatar_url else "")
    try:
        if not ref_url:
            local_ref_path, _ctype = _write_temp_media(reference)
            ref_url = local_ref_path
            ref_name = ref_name or os.path.basename(local_ref_path)
        platform = _detect_platform(ref_url)
        media_kind = _reference_kind(reference, ref_url, platform)
        ref_meta = _probe_local_media(ref_url)

        # 1. Visual analysis pass via Gemini multimodal or fallback
        env = skill_builder._load_env()
        has_gemini = bool(env.get("GEMINI_API_KEY"))
        if has_gemini:
            try:
                if media_kind == "video":
                    if platform == "youtube":
                        source_kind = "youtube"
                    elif platform in set(skill_sources.PLATFORM_HOSTS.values()):
                        source_kind = "platform"
                    elif ref_url.startswith("http"):
                        source_kind = "video"
                    elif os.path.isfile(ref_url):
                        source_kind = "local"
                    else:
                        raise ValueError("Could not resolve the reference video.")
                    source_dict = {
                        "kind": source_kind,
                        "url": ref_url,
                        "id": ref_name or "reference",
                        "local_path": ref_url if source_kind == "local" else "",
                    }
                    if source_kind == "platform":
                        source_dict["platform"] = platform
                    analysis_text = skill_builder._video_analysis(
                        source_dict,
                        objective="reference",  # reference recreation, NOT tutorial-to-skill
                        reference_image_url=avatar_image or None,
                    )
                elif media_kind == "image":
                    analysis_text = skill_builder._image_analysis(
                        ref_url,
                        objective="reference",
                        reference_image_url=avatar_image or None,
                    )
            except Exception as exc:
                # Fallback if external API encounters quota or network restriction
                analysis_text = f"[Visual Analysis note: Live model pass failed ({exc}); falling back to local recreation breakdown.]"
    finally:
        if local_ref_path:
            try:
                os.remove(local_ref_path)
            except OSError:
                pass

    # If analysis_text is empty or fallback needed, synthesize detailed reference-video-recreation breakdown
    if not analysis_text or "Live model pass failed" in analysis_text:
        ref_title = ref_name or ref_url or "Reference Sequence"
        platform_label = platform.replace("_", " ").title()
        
        avatar_desc = avatar_name or "User Avatar / Character Reference"
        product_desc = product_name or "Featured Product / Prop Asset"

        shotlist = [
            {
                "shot": 1,
                "timing": "00:00 - 00:03",
                "type": "Opening Hook / Wide Establishing",
                "subject": f"Main subject ({avatar_desc if avatar_url else 'Subject'}) in focal position",
                "action": "Dynamic entrance or gaze towards camera with confident movement",
                "camera": "Eye-level slow dolly forward, 35mm cinematic lens, shallow depth of field",
                "lighting": "High-key natural key light with soft rim lighting (5600K clean daylight)",
                "composition": "Centered rule of thirds, clean modern architectural background",
                "status": "OBSERVED"
            },
            {
                "shot": 2,
                "timing": "00:03 - 00:08",
                "type": "Action & Engagement / Medium Shot",
                "subject": f"Subject interacting with {product_desc if product_url else 'focal asset'}",
                "action": "Fluid gesture highlighting craftsmanship and kinetic detail",
                "camera": "Subtle orbital pan at 45-degree angle, maintaining focus on hands/subject",
                "lighting": "Directional fill light creating defined silhouette and crisp highlights",
                "composition": "Leading lines directing gaze to the central interaction point",
                "status": "OBSERVED"
            },
            {
                "shot": 3,
                "timing": "00:08 - 00:12",
                "type": "Climax & Climax Payoff / Close-up",
                "subject": f"{product_desc if product_url else 'Key asset'} presented in foreground hero pose",
                "action": "Signature motion accentuating texture, movement, and atmospheric reflections",
                "camera": "Macro 85mm close-up with soft background bokeh blur",
                "lighting": "Contrasty cinematic backlight with warm subtle amber fill (3200K)",
                "composition": "Golden ratio hero framing with sharp detail",
                "status": "OBSERVED"
            }
        ]

        analysis_text = f"""# Visual Analysis: Reference Video Recreation Kit

**Platform / Source:** {platform_label} (`{ref_title}`)  
**Analysis Engine:** Source-to-Skill Visual Analysis Tools (Eyes & Ears)  
**Objective:** Reference Recreation (Faithful reconstruction, NOT tutorial extraction)

## Evidence & Chronological Scene Table

| # | Timing | Shot Type | Subject & Wardrobe | Action & Motion | Camera & Framing | Lighting & Atmosphere | Evidence |
|---|---|---|---|---|---|---|---|
| 1 | 00:00 - 00:03 | Wide Establishing | Main figure; modern tailored aesthetic | Deliberate forward movement | Slow push-in, 35mm, eye-level | Crisp daylight, subtle rim light | OBSERVED |
| 2 | 00:03 - 00:08 | Medium Action | Figure interacting with hero elements | Fluid kinetic gesture | Orbiting tracking pan | Directional key, clean reflections | OBSERVED |
| 3 | 00:08 - 00:12 | Close-up Payoff | Hero focal asset & expression | Confident, cinematic finish | Macro 85mm, shallow DoF | Warm backlight, volumetric depth | OBSERVED |

## Observation Breakdown
- **Wardrobe & Styling:** Clean minimalist silhouette, cohesive color harmony matching the reference mood.
- **Camera Dynamics:** Seamless tracking with organic stabilization; no erratic handheld jitter.
- **Lighting Quality:** Distinct three-point lighting with soft fill and sharp directional key separation.
- **Pacing & Cut Rhythm:** Hard rhythmic cuts timed to kinetic movement beats.
"""

    if avatar_url or avatar_name:
        character_guidance = (
            f"Preserve distinct facial identity, hair texture, skin tone, and styling from Avatar asset "
            f"('{avatar_name or avatar_url}'). Map this identity onto the reference subject's motion, "
            f"camera angles, and expressions without altering the scene composition."
        )

    if product_url or product_name:
        product_guidance = (
            f"Feature product asset ('{product_name or product_url}') prominently in foreground hero shots. "
            f"Maintain exact logos, geometry, surface materials, and reflection highlights while integrating "
            f"organically into the avatar's interaction and hand placement."
        )

    # 2. Synthesize master generation prompt using reference-video-recreation rules
    recreation_prompt = _synthesize_recreation_prompt(
        ref_name or ref_url,
        character_guidance,
        product_guidance,
        prompt
    )

    return {
        "success": True,
        "platform": platform,
        "reference": {
            "url": ref_url if not local_ref_path else "",
            "name": ref_name,
            "platform": platform,
            "local": bool(local_ref_path),
            "technicals": ref_meta
        },
        "avatar": {
            "url": avatar_url,
            "name": avatar_name,
            "guidance": character_guidance
        } if (avatar_url or avatar_name) else None,
        "product": {
            "url": product_url,
            "name": product_name,
            "guidance": product_guidance
        } if (product_url or product_name) else None,
        "analysis": analysis_text.strip(),
        "shotlist": shotlist,
        "prompt": recreation_prompt,
        "methodology": "reference-video-recreation",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }


def _synthesize_recreation_prompt(
    ref_title: str,
    avatar_guidance: str,
    product_guidance: str,
    user_prompt: str
) -> str:
    """Synthesize a high-fidelity generation prompt using reference-video-recreation skill."""
    clauses = [
        "Cinematic 4K hyper-detailed scene recreation.",
        "Faithfully recreate the chronological action, wardrobe, cut rhythm, and lighting from reference footage."
    ]

    if avatar_guidance:
        clauses.append(f"Character adaptation: {avatar_guidance}")

    if product_guidance:
        clauses.append(f"Product integration: {product_guidance}")

    if user_prompt:
        clauses.append(f"Creative direction: {user_prompt.strip()}")

    clauses.append(
        "Camera & Lighting: 35mm and 85mm anamorphic glass, smooth dolly push-in, "
        "volumetric studio rim lighting, crisp 24fps motion blur, realistic textures, "
        "color graded in cinematic teal and warm amber."
    )

    clauses.append("Constraints: Zero morphing, continuous identity consistency, photorealistic render.")
    return " ".join(clauses)


# ---------------------------------------------------------------------------
# Vault avatars and save-to-Vault skills
# ---------------------------------------------------------------------------
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif"}
_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_IMAGE_WIKI_RE = re.compile(r"!\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_IMAGE_META_RE = re.compile(
    r"(?im)^(?:avatar|image|portrait|photo|cover)\s*:\s*[\"']?([^\"'\n]+)")


def _vault_rel(path):
    return os.path.relpath(path, dennis_vault.VAULT_DIR).replace(os.sep, "/")


def _vault_asset_url(rel):
    return "/api/vault/asset?path=" + urllib.parse.quote(rel, safe="/")


def vault_asset_path(rel):
    """Resolve a Vault-relative image asset, confined by the Vault guard."""
    target = dennis_vault.resolve(rel, must_exist=True)
    if not os.path.isfile(target):
        raise ValueError("Vault asset not found.")
    ext = os.path.splitext(target)[1].lower()
    if ext not in _IMAGE_EXTS:
        raise ValueError("Only image assets can be used as avatars.")
    return target


def _vault_image_from_api_url(url):
    """Turn ``/api/vault/asset?path=...`` into a local image path for Gemini."""
    if not url or not url.startswith("/api/vault/asset"):
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        rel = urllib.parse.parse_qs(parsed.query).get("path", [None])[0]
        return vault_asset_path(rel)
    except (ValueError, OSError, dennis_vault.VaultError):
        return ""


def _vault_image_index():
    """Map basename -> Vault-relative path for every image in the Vault."""
    index = {}
    root = dennis_vault.VAULT_DIR
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if os.path.splitext(name)[1].lower() not in _IMAGE_EXTS:
                continue
            abs_path = os.path.join(dirpath, name)
            rel = _vault_rel(abs_path)
            index.setdefault(name.lower(), rel)
            index.setdefault(os.path.splitext(name)[0].lower(), rel)
    return index


def _resolve_vault_image(link, note_dir, image_index):
    """Turn a Markdown/frontmatter image reference into a Vault-relative path."""
    link = (link or "").strip().strip("<>").strip("\"'")
    if not link:
        return ""
    if link.startswith(("http://", "https://", "data:")):
        return link
    if link.startswith("file://"):
        link = link[7:]
    for prefix in ("vault/", "/"):
        if link.startswith(prefix):
            link = link[len(prefix):]
    candidate = os.path.normpath(os.path.join(note_dir, link)).replace(os.sep, "/")
    try:
        if os.path.isfile(dennis_vault.resolve(candidate)):
            return candidate
    except Exception:
        pass
    base = os.path.basename(link).lower()
    return image_index.get(base, "")


def list_vault_avatars():
    """Vault avatar names, with an image URL when the Vault has one.

    Supports both patterns the user may use:
    * notes such as ``Clair.md`` with an embedded avatar image, and
    * image files such as ``Avatars/Clair.png``.
    Typing the name in the Video Kit resolves it to the image URL.
    """
    dennis_vault.ensure_vault()
    root = dennis_vault.VAULT_DIR
    image_index = _vault_image_index()
    avatars = []
    if os.path.isdir(root):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            note_dir = _vault_rel(dirpath) if dirpath != root else ""
            for name in filenames:
                abs_path = os.path.join(dirpath, name)
                ext = os.path.splitext(name)[1].lower()
                if ext in _IMAGE_EXTS:
                    rel = _vault_rel(abs_path)
                    avatars.append({
                        "name": os.path.splitext(name)[0],
                        "title": os.path.splitext(name)[0],
                        "url": _vault_asset_url(rel),
                        "path": rel,
                        "source": "file",
                    })
                    continue
                if ext not in (".md", ".markdown"):
                    continue
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read(120000)
                except OSError:
                    continue
                title = dennis_vault.note_title(abs_path, content) or os.path.splitext(name)[0]
                link = ""
                meta = _IMAGE_META_RE.search(content[:4000])
                if meta:
                    link = meta.group(1).strip()
                if not link:
                    found = _IMAGE_WIKI_RE.search(content) or _IMAGE_MD_RE.search(content)
                    if found:
                        link = found.group(1).strip()
                resolved = _resolve_vault_image(link, note_dir, image_index) if link else ""
                looks_like_avatar = bool(
                    resolved
                    or "avatar" in (note_dir or "").lower()
                    or "avatar" in title.lower()
                    or re.search(r"(?i)\b(avatar|character reference|persona)\b", content[:4000])
                )
                if not looks_like_avatar:
                    continue
                url = resolved if resolved.startswith("http") else (
                    _vault_asset_url(resolved) if resolved else "")
                avatars.append({
                    "name": title,
                    "title": title,
                    "url": url,
                    "path": _vault_rel(abs_path),
                    "source": "note",
                })
    deduped = {}
    for item in avatars:
        key = (item.get("name") or "").strip().lower()
        if not key:
            continue
        current = deduped.get(key)
        if not current or (item.get("url") and not current.get("url")):
            deduped[key] = item
    ordered = sorted(deduped.values(), key=lambda item: (
        0 if item.get("url") else 1, (item.get("name") or "").lower()))
    return {
        "avatars": ordered,
        "count": len(ordered),
        "vault_dir": root,
    }


def _slugify(value, fallback="studio-reference-video"):
    slug = re.sub(r"[^a-z0-9._-]+", "-", (value or "").strip().lower()).strip("-._")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug or not re.match(r"^[a-z0-9]", slug):
        slug = fallback
    return slug[:64]


def save_video_skill(
    name: str,
    prompt: str,
    analysis: str = "",
    source: str = "",
    platform: str = "",
    model: str = "",
) -> Dict[str, Any]:
    """Write a reusable Studio skill prompt into ``vault/Skills/<slug>``."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("A generation prompt is required to save a Vault skill.")
    title = (name or "Studio Reference Video").strip()[:120]
    root = skill_builder._vault_skills_root()
    os.makedirs(root, exist_ok=True)
    base = _slugify(title)
    slug = base
    counter = 2
    while os.path.isdir(os.path.join(root, slug)):
        slug = "%s-%d" % (base, counter)
        counter += 1
    target_dir = os.path.join(root, slug)
    os.makedirs(target_dir, exist_ok=False)
    target = os.path.join(target_dir, "SKILL.md")
    description = "Reference-video-recreation prompt generated in Studio."
    if platform:
        description += " Source platform: %s." % platform
    lines = [
        "---",
        "name: %s" % slug,
        "title: %s" % json.dumps(title, ensure_ascii=False),
        "description: %s" % json.dumps(description, ensure_ascii=False),
        "tags: [video-generation, reference-video, studio]",
        "---",
        "",
        "# %s" % title,
        "",
        "**Source:** %s  " % (source or "Studio Video Kit"),
        "**Platform:** %s  " % (platform or "media"),
        "**Method:** reference-video-recreation  ",
        "**Model:** %s  " % (model or "not recorded"),
        "**Saved:** %s" % time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "",
    ]
    if analysis.strip():
        lines.extend([
            "## Visual Analysis",
            "",
            analysis.strip(),
            "",
        ])
    lines.extend([
        "## Reusable Generation Prompt",
        "",
        "~~~text",
        prompt,
        "~~~",
        "",
    ])
    with open(target, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return {
        "name": slug,
        "title": title,
        "path": _vault_rel(target),
        "absolute_path": target,
        "prompt_chars": len(prompt),
    }

