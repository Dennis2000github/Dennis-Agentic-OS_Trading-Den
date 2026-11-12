"""Independent Video Style and Video Format classifiers.

Style answers *what it looks like*; format answers *what kind of video it is
and how it is technically configured*.  They are separate objects, separate
prompts and separate output files.  Combining them is a validation error.

Deterministic technical values (aspect, fps, codec, audio) always come from
ffprobe/metadata and override model guesses.
"""

import json

from . import skill_analysis

STYLE_IDS = (
    "live_action", "talking_head", "ugc", "cinematic", "documentary",
    "2d_animation", "3d_animation", "cgi", "cartoon", "stickman",
    "motion_graphics", "typography_animation", "claymation", "stop_motion",
    "ai_generated", "hybrid",
)
STYLE_LABELS = {
    "live_action": "Live action", "talking_head": "Talking head", "ugc": "UGC",
    "cinematic": "Cinematic", "documentary": "Documentary",
    "2d_animation": "2D animation", "3d_animation": "3D animation",
    "cgi": "CGI", "cartoon": "Cartoon", "stickman": "Stickman",
    "motion_graphics": "Motion graphics",
    "typography_animation": "Typography animation",
    "claymation": "Claymation", "stop_motion": "Stop motion",
    "ai_generated": "AI-generated video", "hybrid": "Hybrid production",
}

FORMAT_IDS = (
    "explainer", "documentary", "tutorial", "how_to", "educational",
    "storytelling", "advertisement", "product_demo", "ugc_review",
    "affiliate", "listicle", "news_style", "commentary", "what_if",
    "interview",
)
FORMAT_LABELS = {
    "explainer": "Explainer", "documentary": "Documentary",
    "tutorial": "Tutorial", "how_to": "How-to", "educational": "Educational",
    "storytelling": "Storytelling", "advertisement": "Advertisement",
    "product_demo": "Product demonstration", "ugc_review": "UGC review",
    "affiliate": "Affiliate content", "listicle": "Listicle",
    "news_style": "News-style", "commentary": "Commentary",
    "what_if": "What-if", "interview": "Interview",
}

PLACEMENTS = ("feed", "reels", "shorts", "tiktok", "pre_roll", "mid_roll",
              "landing_page", "in_stream", "unknown")

STYLE_FIELDS = (
    "visual_language", "art_direction", "color_palette", "lighting",
    "composition", "camera_angles", "camera_movement", "character_design",
    "environment_design", "materials_textures", "animation_behavior",
    "visual_effects", "typography", "caption_style", "transitions",
    "sound_identity",
)


def _clean(value, limit=500):
    return " ".join(str(value or "").split())[:limit]


def _list(value, limit=14):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [_clean(item, 240) for item in value if _clean(item)][:limit]


def _confidence(value):
    return skill_analysis._confidence(value, default="low")


def _labels(ids, raw):
    values = []
    for item in raw or []:
        key = str(item or "").strip().lower().replace(" ", "_").replace("-", "_")
        if key in ids and key not in values:
            values.append(key)
    return values


def _id(value, ids, default):
    key = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in ids else default


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
STYLE_RULES = (
    "You are a video style classifier. Classify only what the evidence shows. "
    "Do not invent brand names, tools, artists or effects that cannot be "
    "inferred. If the production is mixed, use 'hybrid' and list the "
    "components. Confidence is low | medium | high. Return ONLY valid JSON.\n"
)

FORMAT_RULES = (
    "You are a video format classifier. Separate format from style. Do not "
    "describe visual style here. Use only the evidence and technical facts "
    "supplied. Return ONLY valid JSON.\n"
)


def style_prompt(evidence_text, technical=None, title=""):
    technical = technical or {}
    technical = {
        "aspect": technical.get("aspect"), "width": technical.get("width"),
        "height": technical.get("height"), "fps": technical.get("fps"),
        "duration_seconds": technical.get("duration_seconds"),
        "video_codec": technical.get("video_codec"),
    }
    return (
        STYLE_RULES +
        "\nTITLE: %s\nTECHNICAL: %s\n\nEVIDENCE:\n%s\n\n"
        "Allowed style ids: %s\n"
        "Return one JSON object:\n"
        "{\n"
        ' "style_id": "one allowed id",\n'
        ' "style_name": "human label",\n'
        ' "secondary_styles": ["allowed ids"],\n'
        ' "confidence": "low|medium|high",\n'
        ' "visual_language": "...", "art_direction": "...",\n'
        ' "color_palette": ["#rrggbb or colour names"],\n'
        ' "lighting": "...", "composition": "...",\n'
        ' "camera_angles": ["..."], "camera_movement": ["..."],\n'
        ' "character_design": "...", "environment_design": "...",\n'
        ' "materials_textures": "...", "animation_behavior": "...",\n'
        ' "visual_effects": ["..."], "typography": "...",\n'
        ' "caption_style": "...", "transitions": ["..."],\n'
        ' "sound_identity": "...",\n'
        ' "consistency_rules": ["rules that keep new videos on-style"],\n'
        ' "evidence": ["what in the source supports this"],\n'
        ' "limitations": ["..."]\n'
        "}\n" % (title or "unknown", json.dumps(technical), evidence_text,
                 ", ".join(STYLE_IDS))
    )


def format_prompt(evidence_text, technical=None, title="", platform=""):
    technical = technical or {}
    return (
        FORMAT_RULES +
        "\nTITLE: %s\nPLATFORM: %s\nTECHNICAL (authoritative): %s\n\nEVIDENCE:\n%s\n\n"
        "Allowed format ids: %s\n"
        "Return one JSON object:\n"
        "{\n"
        ' "format_id": "one allowed id",\n'
        ' "format_name": "human label",\n'
        ' "secondary_formats": ["allowed ids"],\n'
        ' "confidence": "low|medium|high",\n'
        ' "platform": "...", "placement": "feed|reels|shorts|tiktok|pre_roll|mid_roll|landing_page|in_stream|unknown",\n'
        ' "narrative_structure": ["ordered beats"],\n'
        ' "editing_pace": "...",\n'
        ' "caption_treatment": "...",\n'
        ' "output_configurations": [{"name":"...","aspect_ratio":"9:16",'
        '"resolution":"1080x1920","fps":30,"notes":"..."}],\n'
        ' "evidence": ["..."],\n'
        ' "limitations": ["..."]\n'
        "}\n" % (title or "unknown", platform or "unknown",
                 json.dumps(technical), evidence_text, ", ".join(FORMAT_IDS))
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def normalize_style(data, evidence=None):
    data = data if isinstance(data, dict) else {}
    style_id = _id(data.get("style_id"), STYLE_IDS, "hybrid")
    technical = ((evidence or {}).get("technical") or {})
    return {
        "style_id": style_id,
        "style_name": _clean(data.get("style_name"), 80) or STYLE_LABELS[style_id],
        "secondary_styles": _labels(STYLE_IDS, data.get("secondary_styles")),
        "confidence": _confidence(data.get("confidence")),
        "visual_language": _clean(data.get("visual_language")),
        "art_direction": _clean(data.get("art_direction")),
        "color_palette": _list(data.get("color_palette"), 12),
        "lighting": _clean(data.get("lighting")),
        "composition": _clean(data.get("composition")),
        "camera_angles": _list(data.get("camera_angles")),
        "camera_movement": _list(data.get("camera_movement")),
        "character_design": _clean(data.get("character_design")),
        "environment_design": _clean(data.get("environment_design")),
        "materials_textures": _clean(data.get("materials_textures")),
        "animation_behavior": _clean(data.get("animation_behavior")),
        "visual_effects": _list(data.get("visual_effects")),
        "typography": _clean(data.get("typography")),
        "caption_style": _clean(data.get("caption_style")),
        "transitions": _list(data.get("transitions")),
        "sound_identity": _clean(data.get("sound_identity")),
        "consistency_rules": _list(data.get("consistency_rules")),
        "evidence": _list(data.get("evidence")),
        "limitations": _list(data.get("limitations")),
        "technical_context": {
            "aspect": technical.get("aspect") or "",
            "fps": technical.get("fps") or 0,
        },
    }


def normalize_format(data, evidence=None, platform_hint=""):
    data = data if isinstance(data, dict) else {}
    format_id = _id(data.get("format_id"), FORMAT_IDS, "explainer")
    source = ((evidence or {}).get("source") or {})
    technical = ((evidence or {}).get("technical") or {})
    duration = technical.get("duration_seconds") or source.get("duration_seconds") or 0
    width = technical.get("width") or source.get("width") or 0
    height = technical.get("height") or source.get("height") or 0
    aspect = technical.get("aspect") or ""
    fps = technical.get("fps") or source.get("fps") or 0
    placement = _clean(data.get("placement"), 40).lower()
    if placement not in PLACEMENTS:
        placement = "unknown"
    configs = []
    for item in data.get("output_configurations") or []:
        if not isinstance(item, dict):
            continue
        configs.append({
            "name": _clean(item.get("name"), 80) or "primary",
            "aspect_ratio": _clean(item.get("aspect_ratio"), 20) or aspect,
            "resolution": _clean(item.get("resolution"), 30),
            "fps": _positive(item.get("fps")) or fps,
            "notes": _clean(item.get("notes"), 240),
        })
    if not configs:
        configs = default_outputs(width, height, fps)
    return {
        "format_id": format_id,
        "format_name": _clean(data.get("format_name"), 80) or FORMAT_LABELS[format_id],
        "secondary_formats": _labels(FORMAT_IDS, data.get("secondary_formats")),
        "confidence": _confidence(data.get("confidence")),
        "platform": _clean(data.get("platform"), 60) or platform_hint or "unknown",
        "placement": placement,
        "aspect_ratio": aspect,
        "resolution": ("%sx%s" % (width, height)) if width and height else "",
        "fps": fps,
        "duration_seconds": duration,
        "video_codec": technical.get("video_codec") or "",
        "container": technical.get("container") or "",
        "audio_codec": technical.get("audio_codec") or "",
        "audio_channels": technical.get("channels") or 0,
        "sample_rate": technical.get("sample_rate") or 0,
        "caption_treatment": _clean(data.get("caption_treatment")),
        "narrative_structure": _list(data.get("narrative_structure")),
        "editing_pace": _clean(data.get("editing_pace")),
        "output_configurations": configs[:6],
        "evidence": _list(data.get("evidence")),
        "limitations": _list(data.get("limitations")),
    }


def _positive(value):
    try:
        number = float(value)
        return round(number, 3) if number > 0 else 0
    except (TypeError, ValueError):
        return 0


def default_outputs(width, height, fps):
    """Derive sensible outputs from the source aspect; always multi-config."""
    width = int(width or 1080)
    height = int(height or 1920)
    aspect = "9:16" if height >= width else "16:9"
    if width and height and abs(width - height) <= 2:
        aspect = "1:1"
    fps = fps or 30
    configs = [{
        "name": "source-match",
        "aspect_ratio": aspect,
        "resolution": "%dx%d" % (width, height),
        "fps": fps,
        "notes": "Match the reference framing.",
    }]
    if aspect != "9:16":
        configs.append({"name": "vertical", "aspect_ratio": "9:16",
                        "resolution": "1080x1920", "fps": fps,
                        "notes": "Shorts/Reels/TikTok cut."})
    if aspect != "16:9":
        configs.append({"name": "landscape", "aspect_ratio": "16:9",
                        "resolution": "1920x1080", "fps": fps,
                        "notes": "YouTube/landing-page cut."})
    return configs


def validate_profiles(style, fmt):
    """Style and format must remain separate, non-empty objects."""
    errors = []
    if not isinstance(style, dict) or not style.get("style_id"):
        errors.append("style profile is missing style_id")
    if not isinstance(fmt, dict) or not fmt.get("format_id"):
        errors.append("format profile is missing format_id")
    if isinstance(style, dict) and isinstance(fmt, dict):
        if style.get("style_id") in FORMAT_IDS:
            errors.append("style_id collides with a format id")
        if fmt.get("format_id") in STYLE_IDS:
            errors.append("format_id collides with a style id")
    return errors


def style_text(style):
    lines = ["# Video Style", "",
             "- **Style:** %s (`%s`)" % (style.get("style_name"), style.get("style_id")),
             "- **Confidence:** %s" % style.get("confidence")]
    for key in STYLE_FIELDS:
        value = style.get(key)
        if isinstance(value, list):
            if value:
                lines.append("- **%s:** %s" % (key.replace("_", " ").title(), "; ".join(value)))
        elif value:
            lines.append("- **%s:** %s" % (key.replace("_", " ").title(), value))
    if style.get("consistency_rules"):
        lines += ["", "## Style consistency rules"]
        lines += ["- " + rule for rule in style["consistency_rules"]]
    if style.get("limitations"):
        lines += ["", "## Limitations"] + ["- " + item for item in style["limitations"]]
    return "\n".join(lines).strip() + "\n"


def format_text(fmt):
    lines = ["# Video Format", "",
             "- **Format:** %s (`%s`)" % (fmt.get("format_name"), fmt.get("format_id")),
             "- **Confidence:** %s" % fmt.get("confidence"),
             "- **Platform / placement:** %s / %s" % (fmt.get("platform"), fmt.get("placement")),
             "- **Aspect / resolution / fps:** %s / %s / %s" % (
                 fmt.get("aspect_ratio"), fmt.get("resolution"), fmt.get("fps")),
             "- **Duration:** %ss" % fmt.get("duration_seconds"),
             "- **Codec / container:** %s / %s" % (fmt.get("video_codec"), fmt.get("container")),
             "- **Audio:** %s, %s ch, %s Hz" % (
                 fmt.get("audio_codec"), fmt.get("audio_channels"), fmt.get("sample_rate")),
             "- **Captions:** %s" % (fmt.get("caption_treatment") or "unknown"),
             "- **Editing pace:** %s" % (fmt.get("editing_pace") or "unknown")]
    if fmt.get("narrative_structure"):
        lines += ["", "## Narrative structure"]
        lines += ["%d. %s" % (i + 1, beat) for i, beat in enumerate(fmt["narrative_structure"])]
    if fmt.get("output_configurations"):
        lines += ["", "## Output configurations", "",
                  "| Name | Aspect | Resolution | FPS | Notes |",
                  "|---|---|---|---|---|"]
        for config in fmt["output_configurations"]:
            lines.append("| %s | %s | %s | %s | %s |" % (
                config.get("name"), config.get("aspect_ratio"),
                config.get("resolution"), config.get("fps"),
                (config.get("notes") or "").replace("|", "/")))
    if fmt.get("limitations"):
        lines += ["", "## Limitations"] + ["- " + item for item in fmt["limitations"]]
    return "\n".join(lines).strip() + "\n"
