"""Ads Studio: the Collart-style five-step production workflow for Studio.

The workflow was reverse-engineered from Collart AI's
``/en/studio/ai-fashion-video`` page (three steps: pick a model, add outfits,
choose a reference video + output settings). Collart composes one long
reference-to-video prompt that names the model as ``@Image1``, an optional scene
as ``@Image2``, each outfit's product photos as the following ``@Image``
references, and the uploaded clip as ``@Video1``.

The Dennis Ads Studio workflow reorders this into five steps — 1 Model,
2 Product/Props, 3 Dressing, 4 Reference Video, 5 Production — where every
step except the model identity is skippable:

* Product/Props (step 2) and Dressing (step 3) only contribute images.
* Reference Video (step 4) is optional; without it generation runs on an
  image-to-video or text-to-video model instead of a reference-to-video one.

This module keeps the composition contract but executes it on the owner's own
Studio providers instead of Collart's private endpoint. It is deliberately
dependency-light and side-effect free so the HTTP layer can validate a draft
before anything is queued.
"""

from __future__ import annotations

import json
import os
import re

from . import studio_contracts as contracts

MAX_OUTFITS = 5
MAX_OUTFIT_IMAGES = 5
MAX_REFERENCE_SECONDS = 15

#: Modes whose documented inputs match the reference-video workflow: reference
#: images plus a motion/reference clip. Edit/extend modes also expose those
#: fields but expect a source video to edit, not a model + outfits.
FASHION_MODES = ("reference_to_video", "motion_transfer", "video_reference")

#: Modes used when the owner skips Step 4 (no reference clip). Generation then
#: runs image-to-video (model/product images available) or text-to-video.
PRODUCTION_MODES = ("image_to_video", "text_to_video")

#: Pinned preset catalog (see scripts/sync_fashion_presets.py). Runtime never
#: contacts Collart; it reads this file.
PRESETS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "catalog", "fashion-presets-0.1.0.json",
)

#: Collart engine tag -> Studio model when that model is available.
ENGINE_MODEL_MAP = {
    "seedance": "bytedance/seedance-2.0/reference-to-video",
    "wan30": "alibaba/wan-3.0/reference-to-video",
    "grok": "xai/grok-imagine-video/v1.5/reference-to-video",
}

#: Preset motion type -> curated template hint id.
PRESET_TYPE_TEMPLATE = {
    "try-on": "try_on",
    "mirror": "mirror_selfie",
    "transition": "outfit_transition",
    "dance": "dance",
}

_presets_cache = None

#: Curated motion templates. ``hint`` is appended to the composed prompt; it is
#: direction the owner can edit, never a hidden provider parameter.
TEMPLATES = (
    {
        "id": "try_on",
        "label": "Try-On Video",
        "hint": "A polished e-commerce try-on: the model turns naturally to show the front, side and back of the outfit.",
    },
    {
        "id": "mirror_selfie",
        "label": "Mirror Selfie",
        "hint": "A handheld mirror-selfie look: the model films themselves in a full-length mirror, adjusting the outfit with natural, confident gestures.",
    },
    {
        "id": "outfit_transition",
        "label": "Outfit Transitions",
        "hint": "Fast, clean outfit transitions: match each cut or transition in the reference video to the next outfit in order.",
    },
    {
        "id": "dance",
        "label": "Dance Video",
        "hint": "An energetic short dance clip with the full outfit readable in frame throughout the movement.",
    },
    {
        "id": "runway",
        "label": "Runway Walk",
        "hint": "A runway walk toward camera with confident posture, natural arm swing and a steady full-body framing.",
    },
)

_TEMPLATE_BY_ID = {item["id"]: item for item in TEMPLATES}
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


class FashionError(Exception):
    """A fashion draft problem that is safe to show to the owner."""

    def __init__(self, code, message, field=None, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.status = status

    def as_error(self):
        data = {"code": self.code, "message": self.message}
        if self.field:
            data["field"] = self.field
        return data


def _fail(code, message, field=None):
    raise FashionError(code, message, field=field)


def _as_url(value, field, required=False):
    """Accept an upload result object or a plain URL string."""
    if isinstance(value, dict):
        value = value.get("url") or value.get("public_url")
    if value in (None, ""):
        if required:
            _fail("missing_asset", "This asset is required.", field=field)
        return ""
    if not isinstance(value, str) or not _URL_RE.match(value.strip()):
        _fail("invalid_url", "Only public http(s) URLs can be used here.", field=field)
    return value.strip()


def _outfit_images(outfit, index):
    images = None
    if isinstance(outfit, dict):
        images = outfit.get("images")
        if images is None:
            images = outfit.get("image_urls")
        if images is None:
            images = outfit.get("products")
        if images is None:
            # Preset-backed outfits may defer their image to the catalog.
            images = []
    elif isinstance(outfit, list):
        images = outfit
    if isinstance(images, str):
        images = [images]
    if not isinstance(images, list):
        _fail(
            "invalid_outfit",
            "Outfit %d needs a list of clothing images." % (index + 1),
            field="draft.outfits.%d" % index,
        )
    urls = []
    for item in images:
        url = _as_url(item, "draft.outfits.%d" % index)
        if url:
            urls.append(url)
    return urls


def normalize_draft(payload):
    """Validate the owner's three-step draft and return a plain dictionary.

    The shape is stable regardless of which provider executes the job::

        {
          "model": "https://…",
          "scene_image": "https://…" | "",
          "outfits": [{"name": "Look 1", "images": ["https://…"]}],  # optional
          "reference_video": "https://…",                             # optional
          "template": "try_on" | "",
          "prompt": "optional direction",
          "resolution": "720p" | "",
          "duration": 5 | None,
          "aspect_ratio": "9:16" | "",
          "generate_audio": True,
        }
    """
    if not isinstance(payload, dict):
        _fail("invalid_draft", "The fashion draft must be an object.")

    model_preset = payload.get("model_preset") or payload.get("modelPreset") or ""
    if model_preset and (not isinstance(model_preset, str) or not model_preset.strip()):
        _fail("invalid_model_preset", "Model preset id must be text.",
              field="draft.model_preset")
    model_preset = str(model_preset).strip()

    # A preset id may stand in for the model photo; it is resolved later.
    model = _as_url(
        payload.get("model") or payload.get("model_image") or payload.get("modelImage"),
        "draft.model",
    )
    if not model and not model_preset:
        _fail("missing_asset", "This asset is required.", field="draft.model")

    scene = _as_url(
        payload.get("scene_image") or payload.get("scene") or payload.get("sceneImage"),
        "draft.scene_image",
    )

    raw_outfits = payload.get("outfits")
    if raw_outfits is None:
        raw_outfits = []
    if not isinstance(raw_outfits, list):
        _fail("invalid_outfits", "Outfits must be a list.", field="draft.outfits")
    if len(raw_outfits) > MAX_OUTFITS:
        _fail(
            "too_many_outfits",
            "Up to %d outfits can be used in one video." % MAX_OUTFITS,
            field="draft.outfits",
        )

    outfits = []
    for index, raw in enumerate(raw_outfits):
        preset_id = ""
        if isinstance(raw, dict):
            preset_id = raw.get("preset") or ""
            if preset_id and (not isinstance(preset_id, str) or not preset_id.strip()):
                _fail("invalid_outfit_preset", "Outfit preset id must be text.",
                      field="draft.outfits.%d.preset" % index)
            preset_id = str(preset_id).strip()
        images = _outfit_images(raw, index)
        if not images and not preset_id:
            _fail(
                "missing_outfit_images",
                "Outfit %d needs at least one clothing image." % (index + 1),
                field="draft.outfits.%d.images" % index,
            )
        if len(images) > MAX_OUTFIT_IMAGES:
            _fail(
                "too_many_outfit_images",
                "Each outfit can use up to %d clothing images." % MAX_OUTFIT_IMAGES,
                field="draft.outfits.%d.images" % index,
            )
        name = "Look %d" % (index + 1)
        if isinstance(raw, dict) and isinstance(raw.get("name"), str) and raw["name"].strip():
            name = raw["name"].strip()[:80]
        outfits.append({"name": name, "images": images, "preset": preset_id})

    preset = payload.get("preset") or ""
    if preset and (not isinstance(preset, str) or not preset.strip()):
        _fail("invalid_preset", "Reference preset id must be text.",
              field="draft.preset")
    preset = str(preset).strip()

    # A preset id may stand in for the reference clip; it is resolved later.
    # Step 4 is skippable: without a reference clip generation runs on an
    # image-to-video / text-to-video model (see prepare_job).
    reference = _as_url(
        payload.get("reference_video") or payload.get("referenceVideo")
        or payload.get("video"),
        "draft.reference_video",
    )

    template = payload.get("template") or ""
    if template and template not in _TEMPLATE_BY_ID:
        _fail("unknown_template", "Unknown motion template.", field="draft.template")

    prompt = payload.get("prompt") or ""
    if not isinstance(prompt, str):
        _fail("invalid_prompt", "Prompt direction must be text.", field="draft.prompt")

    resolution = payload.get("resolution") or ""
    if resolution and not isinstance(resolution, str):
        _fail("invalid_resolution", "Resolution must be text.", field="draft.resolution")

    duration = payload.get("duration")
    if duration not in (None, ""):
        if isinstance(duration, bool) or not isinstance(duration, int):
            _fail("invalid_duration", "Duration must be a whole number of seconds.",
                  field="draft.duration")
    else:
        duration = None

    aspect_ratio = payload.get("aspect_ratio") or ""
    if aspect_ratio and not isinstance(aspect_ratio, str):
        _fail("invalid_aspect_ratio", "Aspect ratio must be text.", field="draft.aspect_ratio")

    generate_audio = payload.get("generate_audio", True)
    if not isinstance(generate_audio, bool):
        _fail("invalid_audio", "Background audio must be true or false.",
              field="draft.generate_audio")

    return {
        "model": model,
        "model_preset": model_preset,
        "scene_image": scene,
        "outfits": outfits,
        "reference_video": reference,
        "preset": preset,
        "template": template,
        "prompt": prompt.strip()[:2000],
        "resolution": resolution.strip(),
        "duration": duration,
        "aspect_ratio": aspect_ratio.strip(),
        "generate_audio": generate_audio,
    }


def resolve_draft(draft):
    """Fill preset-backed assets from the pinned catalog and re-validate.

    Preset ids only stand in for assets the owner did not upload; an explicit
    URL always wins. Returns a resolved copy carrying ``preset_meta``.
    """
    resolved = dict(draft)
    resolved["outfits"] = [dict(outfit) for outfit in draft["outfits"]]
    options = preset_options()
    preset = None

    if draft.get("preset"):
        preset = _preset_by_id(options["videos"], draft["preset"])
        if not preset or not preset.get("video"):
            _fail("unknown_preset", "Unknown reference-video preset.",
                  field="draft.preset")
        if not resolved.get("reference_video"):
            resolved["reference_video"] = preset["video"]
        resolved["preset_meta"] = preset
        if resolved.get("duration") is None and preset.get("duration"):
            try:
                resolved["duration"] = int(preset["duration"])
            except (TypeError, ValueError):
                pass

    if draft.get("model_preset") and not resolved.get("model"):
        model_preset = _preset_by_id(options["models"], draft["model_preset"])
        if not model_preset or not model_preset.get("cover"):
            _fail("unknown_model_preset", "Unknown model preset.",
                  field="draft.model_preset")
        resolved["model"] = model_preset["cover"]

    for index, outfit in enumerate(resolved["outfits"]):
        if outfit.get("preset") and not outfit["images"]:
            preset_outfit = _preset_by_id(options["outfits"], outfit["preset"])
            if not preset_outfit or not preset_outfit.get("cover"):
                _fail("unknown_outfit_preset", "Unknown clothing preset.",
                      field="draft.outfits.%d.preset" % index)
            outfit["images"] = [preset_outfit["cover"]]

    if not resolved.get("model"):
        _fail("missing_asset", "This asset is required.", field="draft.model")

    if preset and preset.get("outfits") and len(resolved["outfits"]) != preset["outfits"]:
        _fail(
            "preset_outfit_count",
            "This preset changes outfits %d times. Add exactly %d outfit%s."
            % (preset["outfits"], preset["outfits"],
               "" if preset["outfits"] == 1 else "s"),
            field="draft.outfits",
        )
    return resolved


def preset_template_id(draft):
    """Curated template hint implied by the selected preset's motion type."""
    if draft.get("template"):
        return draft["template"]
    for motion in (draft.get("preset_meta") or {}).get("type") or []:
        template_id = PRESET_TYPE_TEMPLATE.get(str(motion).lower())
        if template_id:
            return template_id
    return ""


def image_urls(draft):
    """Provider images in prompt order: model, scene, then outfit products."""
    urls = [draft["model"]]
    if draft.get("scene_image"):
        urls.append(draft["scene_image"])
    for outfit in draft["outfits"]:
        urls.extend(outfit["images"])
    return urls


def _outfit_reference(first, count):
    if count <= 1:
        return "@Image%d" % first
    return "@Image%d-@Image%d" % (first, first + count - 1)


def build_prompt(draft, template_hint=""):
    """Compose the generation prompt used by the Ads Studio workflow."""
    outfits = draft["outfits"]
    has_scene = bool(draft.get("scene_image"))
    has_video = bool(draft.get("reference_video"))
    has_outfits = bool(outfits)
    first_outfit_image = 3 if has_scene else 2
    if has_outfits:
        parts = [
            "@Image1 is the fashion model who MUST appear in the generated video. "
            "The person in the generated video MUST be this exact person from @Image1: "
            "preserve their face identity, facial structure, skin color, hair color and "
            "body proportions with 100% accuracy. Do NOT use the appearance of any "
            "person from the reference video."
        ]
    elif draft.get("model"):
        parts = [
            "@Image1 is the primary identity reference and MUST appear in the "
            "generated video. Preserve the identity shown in @Image1 with 100% "
            "accuracy."
        ]
    else:
        parts = []

    if not has_video and draft.get("model"):
        parts.append(
            "No reference video was provided: generate the video from the "
            "attached reference image(s) and the prompt direction below."
        )

    if has_scene:
        parts.append(
            "@Image2 is a SCENE REFERENCE ONLY. Use it only for the background "
            "environment, location type, spatial layout, props, lighting mood and "
            "color palette. Prioritize @Image2 as the primary background reference "
            "over the background from @Video1. Do NOT copy any person, clothing, "
            "face, body or foreground subject from @Image2. The generated person "
            "must still come exclusively from @Image1, and the outfit content must "
            "still come exclusively from the outfit reference image(s)."
        )

    cursor = first_outfit_image
    for index, outfit in enumerate(outfits):
        ref = _outfit_reference(cursor, len(outfit["images"]))
        if len(outfit["images"]) == 1:
            parts.append(
                "%s is outfit %d: a clothing reference. Dress the model in every "
                "garment and accessory shown, reproducing their exact silhouette, "
                "cut, fit, color, pattern, material, texture, trims, hardware, "
                "logos and visible styling details." % (ref, index + 1)
            )
        else:
            parts.append(
                "%s are the clothing references for outfit %d. Combine every "
                "garment and accessory shown into one complete, realistic outfit. "
                "Keep each item's exact silhouette, cut, fit, color, pattern, "
                "material, texture, trims, hardware, logos and visible styling "
                "details. Do not omit, duplicate, replace, simplify or invent any "
                "item." % (ref, index + 1)
            )
        cursor += len(outfit["images"])

    if len(outfits) > 1:
        if has_video:
            parts.append(
                "There are %d complete outfits in total. The model must change between "
                "these outfits during the video. Dress the model in each outfit "
                "sequentially, in the same order as the images, and exactly replicate "
                "the outfit-changing rhythm, timing and transition style from "
                "@Video1. When the person in @Video1 switches outfit, switch to the "
                "corresponding outfit in the same order." % len(outfits)
            )
        else:
            parts.append(
                "There are %d complete outfits in total. The model must change "
                "between these outfits during the video, in the same order as the "
                "images." % len(outfits)
            )

    if has_video:
        parts.append(
            "@Video1 is a MOTION REFERENCE ONLY. Use it solely for movements, poses, "
            "timing and expressions. The person appearing in the generated video MUST "
            "be the model from @Image1, NOT the person in @Video1. COMPLETELY DISCARD "
            "the face, appearance, skin tone and body shape of the person in @Video1. "
            "Replicate the movements, poses and actions from @Video1 exactly, and "
            "synchronize facial micro-expressions: match eye gaze direction, blinking "
            "rhythm and lip movement, rendered on the @Image1 model's face. Ignore "
            "subtitles, text and watermarks in the reference video, and keep one "
            "consistent environment for the whole video."
        )

    if template_hint:
        parts.append("Template direction: %s" % template_hint.strip())

    if draft.get("prompt"):
        parts.append("Additional creative direction: %s" % draft["prompt"])

    if has_outfits:
        parts.append(
            "Create a professional e-commerce fashion video with clean, realistic "
            "detail and a stable full-body composition."
        )
    else:
        parts.append(
            "Create a professional video with clean, realistic detail and a "
            "stable composition."
        )
    return " ".join(part.strip() for part in parts if part and part.strip())


# ---------------------------------------------------------------------------
# Model discovery and parameter planning
# ---------------------------------------------------------------------------
def _summary(model):
    summary = model.get("summary")
    if isinstance(summary, dict):
        return summary
    return contracts.derive_summary(model)


def fashion_models(models):
    """Return video models that can take the model + outfits + reference clip."""
    picked = []
    for model in models or []:
        if model.get("kind") != "video":
            continue
        if model.get("mode") not in FASHION_MODES:
            continue
        summary = _summary(model)
        accepts = summary.get("accepts") or {}
        if not accepts.get("image") or not accepts.get("video"):
            continue
        if not summary.get("prompt"):
            continue
        if "image_urls" not in (summary.get("image_fields") or []):
            continue
        if summary.get("max_images") is not None and summary["max_images"] < 2:
            continue
        picked.append({
            "id": model.get("id"),
            "label": model.get("label") or model.get("id"),
            "mode": model.get("mode"),
            "mode_label": model.get("mode_label") or model.get("mode"),
            "resolutions": list(summary.get("resolutions") or []),
            "durations": list(summary.get("durations") or []),
            "duration_min": summary.get("duration_min"),
            "duration_max": summary.get("duration_max"),
            "aspect_ratios": list(summary.get("aspect_ratios") or []),
            "max_images": summary.get("max_images"),
            "accepts": dict(accepts),
            "tags": list(model.get("tags") or []),
        })

    def rank(item):
        mode = item.get("mode")
        return (
            0 if mode == "reference_to_video" else 1,
            0 if mode == "motion_transfer" else 1,
            item.get("label") or "",
        )

    picked.sort(key=rank)
    return picked


def production_models(models, has_image=True):
    """Video models for generation when Step 4 (reference clip) was skipped.

    With model/product images available the draft maps to image-to-video
    models; with nothing but a prompt it maps to text-to-video models.
    """
    wanted = "image_to_video" if has_image else "text_to_video"
    picked = []
    for model in models or []:
        if model.get("kind") != "video":
            continue
        if model.get("mode") != wanted:
            continue
        summary = _summary(model)
        accepts = summary.get("accepts") or {}
        if wanted == "image_to_video":
            fields = [name for name in (summary.get("image_fields") or [])
                      if "end_" not in name]
            if not accepts.get("image") or not fields:
                continue
        if not summary.get("prompt"):
            continue
        picked.append({
            "id": model.get("id"),
            "label": model.get("label") or model.get("id"),
            "mode": model.get("mode"),
            "mode_label": model.get("mode_label") or model.get("mode"),
            "resolutions": list(summary.get("resolutions") or []),
            "durations": list(summary.get("durations") or []),
            "duration_min": summary.get("duration_min"),
            "duration_max": summary.get("duration_max"),
            "aspect_ratios": list(summary.get("aspect_ratios") or []),
            "max_images": summary.get("max_images"),
            "accepts": dict(accepts),
            "tags": list(model.get("tags") or []),
        })

    picked.sort(key=lambda item: item.get("label") or "")
    return picked


def _enum_values(model, name):
    root = model.get("schema") or {}
    prop = contracts.expand_schema((root.get("properties") or {}).get(name) or {}, root)
    values = prop.get("enum")
    return [value for value in values if value is not None] if isinstance(values, list) else []


def _default_resolution(model):
    values = _enum_values(model, "resolution")
    for preferred in ("720p", "1080p"):
        if preferred in values:
            return preferred
    return values[0] if values else ""


def _default_aspect_ratio(model):
    values = _enum_values(model, "aspect_ratio")
    for preferred in ("9:16", "3:4", "adaptive"):
        if preferred in values:
            return preferred
    return values[0] if values else ""


def _default_duration(model, summary):
    durations = summary.get("durations") or []
    if durations:
        if 5 in durations:
            return 5
        return durations[0]
    minimum = summary.get("duration_min")
    maximum = summary.get("duration_max")
    value = 5
    if minimum is not None:
        value = max(value, int(minimum))
    if maximum is not None:
        value = min(value, int(maximum))
    return value if minimum is not None or maximum is not None else None


def _pick_preferred(values, preferred, fallback):
    for value in preferred:
        if value in values:
            return value
    return values[0] if values else fallback


def build_config(models, provider_id="higgsfield"):
    """Public configuration for the Fashion Video tab."""
    model_options = fashion_models(models)
    available = {item["id"] for item in model_options}
    preset_data = preset_options()
    presets = []
    for video in preset_data["videos"]:
        entry = dict(video)
        entry["suggested_model_id"] = suggest_model_id(video, available)
        presets.append(entry)
    default = model_options[0] if model_options else None
    defaults = {"model_id": default["id"] if default else "", "resolution": "",
                "duration": None, "aspect_ratio": "", "generate_audio": True}
    if default:
        defaults["resolution"] = _pick_preferred(
            default.get("resolutions") or [], ("720p", "1080p"), ""
        )
        defaults["aspect_ratio"] = _pick_preferred(
            default.get("aspect_ratios") or [], ("9:16", "3:4", "adaptive"), ""
        )
        durations = default.get("durations") or []
        if durations:
            defaults["duration"] = 5 if 5 in durations else durations[0]
        else:
            minimum = default.get("duration_min")
            maximum = default.get("duration_max")
            value = 5
            if minimum is not None:
                value = max(value, int(minimum))
            if maximum is not None:
                value = min(value, int(maximum))
            defaults["duration"] = value if minimum is not None or maximum is not None else None
    return {
        "provider": provider_id,
        "workflow": "fashion_video",
        "models": model_options,
        "production_models": {
            "image": production_models(models, has_image=True),
            "text": production_models(models, has_image=False),
        },
        "defaults": defaults,
        "limits": {
            "outfits": MAX_OUTFITS,
            "outfit_images": MAX_OUTFIT_IMAGES,
            "reference_seconds": MAX_REFERENCE_SECONDS,
        },
        "templates": [dict(item) for item in TEMPLATES],
        "presets": presets,
        "preset_filters": preset_data["filters"],
        "model_presets": preset_data["models"],
        "outfit_presets": preset_data["outfits"],
    }


def plan_job(model, draft, template_hint=""):
    """Map a normalized draft onto the model schema, with clear errors.

    Only parameters the model actually documents are emitted, so a provider
    never receives a field it did not ask for.
    """
    if not isinstance(model, dict) or not model.get("schema"):
        _fail("invalid_model", "This model has no input schema.", status=400)

    root = model.get("schema") or {}
    props = contracts.expand_schema(root, root).get("properties") or {}
    summary = _summary(model)

    urls = image_urls(draft)
    max_images = summary.get("max_images")
    if isinstance(max_images, int) and len(urls) > max_images:
        _fail(
            "too_many_images",
            "This model accepts up to %d reference images; this draft needs %d. "
            "Remove an outfit or use fewer clothing photos." % (max_images, len(urls)),
            field="draft.outfits",
        )

    params = {
        "prompt": build_prompt(draft, template_hint),
    }

    if draft.get("reference_video"):
        if urls:
            params["image_urls"] = urls
        if "video_urls" in props:
            params["video_urls"] = [draft["reference_video"]]
        elif "video_url" in props:
            params["video_url"] = draft["reference_video"]
        elif "reference_video_url" in props:
            params["reference_video_url"] = draft["reference_video"]
        else:
            _fail(
                "invalid_model",
                "This model does not document a reference-video input.",
                status=400,
            )
    else:
        # Reference clip skipped (Step 4): image-to-video / text-to-video.
        if urls:
            field = None
            for name in ("image_urls", "image_url", "input_images"):
                if name in props:
                    field = name
                    break
            if not field:
                _fail(
                    "invalid_model",
                    "This model does not document an image input.",
                    status=400,
                )
            prop = contracts.expand_schema(props.get(field) or {}, root)
            if prop.get("type") == "array":
                params[field] = urls
            else:
                params[field] = urls[0]
        if not params.get("prompt"):
            _fail("missing_prompt", "Describe the video in the prompt.",
                  field="draft.prompt")

    if "resolution" in props and draft.get("resolution"):
        allowed = _enum_values(model, "resolution")
        if allowed and draft["resolution"] not in allowed:
            _fail(
                "invalid_resolution",
                "Resolution must be one of %s." % ", ".join(allowed),
                field="draft.resolution",
            )
        params["resolution"] = draft["resolution"]
    elif "resolution" in props:
        default = _default_resolution(model)
        if default:
            params["resolution"] = default

    if "aspect_ratio" in props:
        allowed = _enum_values(model, "aspect_ratio")
        chosen = draft.get("aspect_ratio") or _default_aspect_ratio(model)
        if chosen:
            if allowed and chosen not in allowed:
                _fail(
                    "invalid_aspect_ratio",
                    "Aspect ratio must be one of %s." % ", ".join(allowed),
                    field="draft.aspect_ratio",
                )
            params["aspect_ratio"] = chosen

    if "duration" in props:
        prop = contracts.expand_schema(props.get("duration") or {}, root)
        chosen = draft.get("duration")
        allowed = prop.get("enum")
        if chosen is None:
            chosen = _default_duration(model, summary)
        if chosen is not None:
            if isinstance(allowed, list) and allowed and chosen not in allowed:
                _fail(
                    "invalid_duration",
                    "Duration must be one of %s seconds."
                    % ", ".join(str(item) for item in allowed),
                    field="draft.duration",
                )
            minimum = prop.get("minimum")
            maximum = prop.get("maximum")
            if minimum is not None and chosen < minimum:
                _fail(
                    "invalid_duration",
                    "Duration must be at least %s seconds." % minimum,
                    field="draft.duration",
                )
            if maximum is not None and chosen > maximum:
                _fail(
                    "invalid_duration",
                    "Duration must be at most %s seconds." % maximum,
                    field="draft.duration",
                )
            params["duration"] = chosen

    if "generate_audio" in props:
        params["generate_audio"] = draft.get("generate_audio", True)

    return params


def prepare_job(provider, body):
    """Validate a job request against ``provider`` and return the plan."""
    if not isinstance(body, dict):
        _fail("invalid_request", "The request body must be an object.")
    provider_id = body.get("provider") or getattr(provider, "id", "higgsfield")
    draft = resolve_draft(normalize_draft(body.get("draft") or body))

    all_models = provider.models(kind="video")
    if draft.get("reference_video"):
        # Step 4 provided a reference clip: the reference-to-video pool.
        options = fashion_models(all_models)
        if not options:
            _fail(
                "no_fashion_models",
                "No configured video model accepts a model image, outfit images and a "
                "reference clip.",
                status=409,
            )
    else:
        # Step 4 skipped: image-to-video when reference images exist,
        # otherwise text-to-video.
        options = production_models(all_models, has_image=bool(image_urls(draft)))
        if not options:
            _fail(
                "no_production_models",
                "No configured video model supports generation without a "
                "reference clip.",
                status=409,
            )
    by_id = {item["id"]: item for item in options}
    model_id = body.get("model_id")
    if not model_id and draft.get("reference_video") and draft.get("preset_meta"):
        # Presets were tuned for a specific engine; follow it when available.
        model_id = suggest_model_id(draft["preset_meta"], by_id.keys())
    if not model_id:
        model_id = options[0]["id"]
    if model_id not in by_id:
        _fail("unknown_model", "This model is not available for Fashion Video.",
              field="model_id")

    raw_model = provider.model(model_id)
    template_id = preset_template_id(draft)
    hint = _TEMPLATE_BY_ID[template_id]["hint"] if template_id in _TEMPLATE_BY_ID else ""
    params = plan_job(raw_model, draft, hint)
    params = contracts.validate_params(raw_model, params)
    image_count = len(params.get("image_urls") or [])
    if not image_count and isinstance(params.get("image_url"), str):
        image_count = 1
    return {
        "provider": provider_id,
        "model_id": model_id,
        "model": by_id[model_id],
        "draft": draft,
        "params": params,
        "image_count": image_count,
    }


def _template_hint(draft):
    template = _TEMPLATE_BY_ID.get(draft.get("template") or "")
    return template["hint"] if template else ""


# ---------------------------------------------------------------------------
# Presets (pinned Collart-fashion catalog)
# ---------------------------------------------------------------------------
def load_presets(path=PRESETS_FILE):
    """Read the pinned preset catalog; returns empty lists when absent."""
    global _presets_cache
    if _presets_cache is not None:
        return _presets_cache
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    presets = {
        "videos": data.get("videos") or [],
        "models": data.get("models") or [],
        "outfits": data.get("outfits") or [],
    }
    _presets_cache = presets
    return presets


def preset_options():
    videos = [item for item in load_presets()["videos"] if item.get("video")]
    models = [item for item in load_presets()["models"] if item.get("cover")]
    outfits = [item for item in load_presets()["outfits"] if item.get("cover")]

    def values(items, *keys):
        found = []
        for item in items:
            for key in keys:
                for value in item.get(key) or []:
                    if value not in found:
                        found.append(value)
        return sorted(found)

    return {
        "videos": videos,
        "models": models,
        "outfits": outfits,
        "filters": {
            "engine": values(videos, "engines"),
            "type": values(videos, "type"),
            "style": values(videos, "style"),
            "model": values(videos, "model"),
            "identity": values(models, "identity"),
            "clothing": values(outfits, "clothing"),
        },
    }


def suggest_model_id(preset, available_ids):
    """First Studio model matching the preset's engine tag, when present."""
    for engine in preset.get("engines") or []:
        model_id = ENGINE_MODEL_MAP.get(str(engine).lower())
        if model_id and model_id in available_ids:
            return model_id
    return None


def _preset_by_id(items, preset_id):
    for item in items:
        if item.get("id") == preset_id:
            return item
    return None
