"""Provider-neutral contracts for Studio generation jobs.

Everything that must be true regardless of which provider executes a request
lives here: schema inspection, parameter validation, state normalization,
output discovery, error normalization, request hashing and secret redaction.

The module is deliberately dependency-free and has no knowledge of HTTP or of
any particular provider. Adapters translate provider payloads into these
shapes; the UI only ever sees normalized data.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re

KINDS = ("image", "video")

# Display order for mode groups in the UI.
MODES = (
    "text_to_image",
    "image_edit",
    "text_to_video",
    "image_to_video",
    "reference_to_video",
    "first_last_frame",
    "video_edit",
    "video_extend",
    "video_reference",
    "motion_transfer",
    "object_swap",
    "restyle",
    "character_training",
)

MODE_LABELS = {
    "text_to_image": "Text to image",
    "image_edit": "Image edit",
    "text_to_video": "Text to video",
    "image_to_video": "Image to video",
    "reference_to_video": "Reference to video",
    "first_last_frame": "First / last frame",
    "video_edit": "Video edit",
    "video_extend": "Video extend",
    "video_reference": "Video reference",
    "motion_transfer": "Motion transfer",
    "object_swap": "Object swap",
    "restyle": "Restyle",
    "character_training": "Character training",
}

#: Internal lifecycle. ``queued`` covers both the "Queued" and "Submitted"
#: moments of the external contract: a job is *submitted* once
#: ``provider_request_id`` is set, and it stays ``queued`` until the provider
#: starts work. Keeping one state preserves jobs stored by earlier versions.
STATES = ("queued", "running", "succeeded", "failed", "cancelled")
TERMINAL_STATES = frozenset(("succeeded", "failed", "cancelled"))

#: Human labels for the normalized lifecycle, shared by API and UI.
LIFECYCLE_LABELS = {
    "queued": "Queued",
    "submitted": "Submitted",
    "running": "Processing",
    "succeeded": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
}

#: Provider capability profiles. Adapters merge these into their descriptor and
#: into every model's ``capabilities`` so the UI can gate controls truthfully.
#: ``cancel`` is ``none``, ``queued_only`` or ``any``; ``progress`` means the
#: provider reports a real 0-100 value (never a fabricated one).
PROVIDER_CAPABILITIES = {
    "higgsfield": {
        "cancel": "queued_only",
        "progress": False,
        "estimate": False,
        "upload": True,
        "webhook": True,
        "polling": True,
        "video_models": True,
        "image_models": True,
    },
    "fal": {
        "cancel": "queued_only",
        "progress": False,
        "estimate": False,
        "upload": True,
        "webhook": True,
        "polling": True,
        "video_models": True,
        "image_models": False,
    },
    "runway": {
        "cancel": "any",
        "progress": True,
        "estimate": False,
        "upload": True,
        "webhook": False,
        "polling": True,
        "video_models": True,
        "image_models": False,
    },
    "luma": {
        "cancel": "any",
        "progress": False,
        "estimate": False,
        "upload": False,
        "webhook": True,
        "polling": True,
        "video_models": True,
        "image_models": False,
    },
    "google_veo": {
        "cancel": "none",
        "progress": False,
        "estimate": False,
        "upload": False,
        "webhook": False,
        "polling": True,
        "video_models": True,
        "image_models": False,
    },
    "minimax": {
        "cancel": "none",
        "progress": False,
        "estimate": False,
        "upload": False,
        "webhook": True,
        "polling": True,
        "video_models": True,
        "image_models": False,
    },
    "replicate": {
        "cancel": "any",
        "progress": False,
        "estimate": False,
        "upload": True,
        "webhook": True,
        "polling": True,
        "video_models": True,
        "image_models": False,
    },
}

_STATE_ALIASES = {
    "queued": "queued",
    "pending": "queued",
    "waiting": "queued",
    "created": "queued",
    "starting": "queued",
    "submitted": "queued",
    "in_queue": "queued",
    "in_progress": "running",
    "inprogress": "running",
    "dreaming": "running",
    "processing": "running",
    "running": "running",
    "generating": "running",
    "started": "running",
    "preparing": "running",
    "completed": "succeeded",
    "complete": "succeeded",
    "succeeded": "succeeded",
    "success": "succeeded",
    "done": "succeeded",
    "finished": "succeeded",
    "ok": "succeeded",
    "failed": "failed",
    "failure": "failed",
    "error": "failed",
    "nsfw": "failed",
    "rejected": "failed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}

_STATUS_TRANSIENT = frozenset((408, 425, 429, 500, 502, 503, 504))


class ContractError(Exception):
    """A request contract violation that is safe to show to the owner."""

    def __init__(self, code, message, field=None, status=400, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.status = status
        self.retryable = retryable

    def as_error(self):
        data = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.field:
            data["field"] = self.field
        return data


def _fail(code, message, field=None):
    raise ContractError(code, message, field=field)


# ---------------------------------------------------------------------------
# JSON-schema subset used by the pinned catalog
# ---------------------------------------------------------------------------
def resolve_ref(node, root):
    """Resolve a local ``$ref``. Returns the node unchanged when absent."""
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return node
    if not ref.startswith("#/"):
        _fail("unsupported_schema", "Only local schema references are supported.")
    target = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or part not in target:
            _fail("unsupported_schema", "Schema reference %s cannot be resolved." % ref)
        target = target[part]
    merged = {k: v for k, v in node.items() if k != "$ref"}
    if isinstance(target, dict):
        merged = dict(target, **merged)
    return merged


def expand_schema(node, root):
    """Resolve ``$ref`` and flatten ``allOf`` for a single schema node."""
    node = resolve_ref(node, root)
    if not isinstance(node, dict) or "allOf" not in node:
        return node

    merged = {k: v for k, v in node.items() if k != "allOf"}
    properties = dict(merged.get("properties") or {})
    required = list(merged.get("required") or [])
    for sub in node.get("allOf") or []:
        part = expand_schema(sub, root)
        if not isinstance(part, dict):
            continue
        properties.update(part.get("properties") or {})
        for name in part.get("required") or []:
            if name not in required:
                required.append(name)
        for key, value in part.items():
            if key not in ("properties", "required"):
                merged.setdefault(key, value)
    if properties:
        merged["properties"] = properties
    if required:
        merged["required"] = required
    return merged


def _type_names(schema):
    declared = schema.get("type")
    if isinstance(declared, str):
        return (declared,)
    if isinstance(declared, list):
        return tuple(str(x) for x in declared)
    return ()


def _check_type(value, type_name):
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "null":
        return value is None
    return True


def _type_label(type_name):
    return {
        "string": "text",
        "integer": "a whole number",
        "number": "a number",
        "boolean": "true or false",
        "array": "a list",
        "object": "an object",
        "null": "null",
    }.get(type_name, type_name)


def _matches(schema, value, root):
    """True when ``value`` satisfies ``schema`` (used for if/then/else)."""
    try:
        _validate(schema, value, "value", root, partial=True)
        return True
    except ContractError:
        return False


def _validate_object(schema, value, path, root, partial=False):
    properties = schema.get("properties")
    required = schema.get("required") or []
    if isinstance(properties, dict):
        for name in required:
            if name not in value:
                _fail("missing_field", "%s is required." % _join(path, name), _join(path, name))
        additional = schema.get("additionalProperties", False)
        for key, item in value.items():
            if key in properties:
                _validate(properties[key], item, _join(path, key), root)
            elif partial:
                # ``if`` / ``then`` / ``else`` schemas constrain a subset of the
                # object; keys they do not mention are not errors.
                continue
            elif additional is False:
                _fail(
                    "unknown_field",
                    "%s is not supported by this model." % _join(path, key),
                    _join(path, key),
                )
            elif isinstance(additional, dict):
                _validate(additional, item, _join(path, key), root)
    min_props = schema.get("minProperties")
    max_props = schema.get("maxProperties")
    if isinstance(min_props, int) and len(value) < min_props:
        _fail("invalid_field", "%s needs at least %d entries." % (path, min_props), path)
    if isinstance(max_props, int) and len(value) > max_props:
        _fail("invalid_field", "%s accepts at most %d entries." % (path, max_props), path)
    return value


def _validate(schema, value, path, root, partial=False):
    schema = expand_schema(schema, root)
    if not isinstance(schema, dict):
        return value

    # Branching schemas: accept the first branch that validates.
    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list) and branches:
            errors = []
            for branch in branches:
                try:
                    return _validate(branch, value, path, root, partial=partial)
                except ContractError as exc:
                    errors.append(exc)
            if value is None and any(
                "null" in _type_names(expand_schema(b, root)) for b in branches
            ):
                return None
            first = errors[0] if errors else None
            message = first.message if first else "%s has an unsupported value." % path
            _fail(first.code if first else "invalid_field", message, path)

    types = _type_names(schema)
    if types and not any(_check_type(value, t) for t in types):
        _fail(
            "invalid_type",
            "%s must be %s." % (path, " or ".join(_type_label(t) for t in types)),
            path,
        )

    if value is None:
        return None

    if "const" in schema and value != schema["const"]:
        _fail("invalid_choice", "%s has an unsupported value." % path, path)

    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        _fail(
            "invalid_choice",
            "%s must be one of: %s." % (path, ", ".join(str(x) for x in enum[:12])),
            path,
        )

    if isinstance(value, str):
        min_len = schema.get("minLength")
        max_len = schema.get("maxLength")
        if isinstance(min_len, int) and len(value) < min_len:
            _fail("invalid_field", "%s is too short." % path, path)
        if isinstance(max_len, int) and len(value) > max_len:
            _fail("invalid_field", "%s is too long (max %d characters)." % (path, max_len), path)
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if not re.search(pattern, value):
                    _fail("invalid_field", "%s has an invalid format." % path, path)
            except re.error:
                pass
        if schema.get("format") in ("uri", "url"):
            if not re.match(r"^https?://[^\s]+$", value, re.I):
                _fail("invalid_url", "%s must be an http(s) URL." % path, path)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            _fail("invalid_field", "%s must be at least %s." % (path, minimum), path)
        if isinstance(maximum, (int, float)) and value > maximum:
            _fail("invalid_field", "%s must be at most %s." % (path, maximum), path)
        multiple = schema.get("multipleOf")
        if isinstance(multiple, (int, float)) and multiple:
            quotient = value / multiple
            if abs(quotient - round(quotient)) > 1e-9:
                _fail("invalid_field", "%s must be a multiple of %s." % (path, multiple), path)

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            _fail("invalid_field", "%s needs at least %d item(s)." % (path, min_items), path)
        if isinstance(max_items, int) and len(value) > max_items:
            _fail("invalid_field", "%s accepts at most %d item(s)." % (path, max_items), path)
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(items, item, "%s[%d]" % (path, index), root)
        if schema.get("uniqueItems"):
            seen = []
            for item in value:
                marker = json.dumps(item, sort_keys=True)
                if marker in seen:
                    _fail("invalid_field", "%s must not contain duplicates." % path, path)
                seen.append(marker)

    if isinstance(value, dict):
        _validate_object(schema, value, path, root, partial=partial)

    # Conditional constraints are common in the catalog (duration / resolution
    # pairs). Evaluate them after the base schema so errors are deterministic.
    condition = schema.get("if")
    if isinstance(condition, dict):
        if _matches(condition, value, root):
            if isinstance(schema.get("then"), dict):
                _validate(schema["then"], value, path, root, partial=True)
        elif isinstance(schema.get("else"), dict):
            _validate(schema["else"], value, path, root, partial=True)

    return value


def _join(path, name):
    return "%s.%s" % (path, name) if path else name


def validate_params(model, params):
    """Validate and return a deep copy of ``params`` for ``model``.

    Unknown fields are rejected, never silently dropped. Optional fields that
    are absent stay absent; defaults are not injected server-side so the
    provider sees exactly what the owner confirmed.
    """
    if not isinstance(params, dict):
        _fail("invalid_params", "Parameters must be an object.")
    schema = model.get("schema") or {}
    if not isinstance(schema, dict) or not schema.get("properties"):
        _fail("invalid_model", "This model has no input schema.")
    cleaned = copy.deepcopy(params)
    schema = expand_schema(schema, schema)
    properties = schema.get("properties") or {}
    additional = schema.get("additionalProperties", False)
    for key in cleaned:
        if key in properties:
            continue
        if additional is True or isinstance(additional, dict):
            continue
        _fail(
            "unknown_field",
            "params.%s is not supported by this model." % key,
            "params.%s" % key,
        )
    _validate(schema, cleaned, "params", model.get("schema") or {})
    return cleaned


# ---------------------------------------------------------------------------
# UI field derivation
# ---------------------------------------------------------------------------
_ADVANCED = frozenset(
    (
        "seed",
        "negative_prompt",
        "cfg_scale",
        "guidance_scale",
        "nsfw_check",
        "moderation",
        "enable_thinking",
        "prompt_extend",
        "enhance_prompt",
        "safety_tolerance",
        "bitrate_mode",
        "webhook_url",
        "webhook_secret",
    )
)
_URL_HINTS = ("url", "image", "video", "audio", "file")


def _prettify(name):
    text = str(name).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name


def _pick_display(schema, root):
    """Return a non-null branch for display purposes."""
    schema = expand_schema(schema, root)
    if "type" not in schema and isinstance(schema.get("anyOf"), list):
        for branch in schema["anyOf"]:
            branch = expand_schema(branch, root)
            if "null" not in _type_names(branch):
                return branch
    return schema


def _is_url_name(name):
    lowered = str(name).lower()
    return lowered.endswith("_url") or lowered.endswith("_urls") or "webhook" in lowered


def control_for(name, schema, root=None):
    root = root if root is not None else schema
    types = _type_names(schema)
    kind = types[0] if types else None
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return "select"
    if kind == "boolean":
        return "toggle"
    if kind in ("integer", "number"):
        return "number"
    if kind == "array":
        items = expand_schema(schema.get("items") or {}, root)
        item_types = _type_names(items)
        if "string" in item_types and (_is_url_name(name) or _is_url_name(schema.get("title", ""))):
            return "url_list"
        return "json"
    if kind == "object":
        return "json"
    if kind == "string":
        if schema.get("format") in ("uri", "url") or _is_url_name(name):
            return "url"
        return "text"
    return "text"


def derive_fields(model):
    """Return top-level form fields for a model, in schema order."""
    root = model.get("schema") or {}
    properties = root.get("properties") or {}
    required = set(root.get("required") or [])
    fields = []
    for name, raw in properties.items():
        schema = _pick_display(raw, root)
        control = control_for(name, schema, root)
        enum = schema.get("enum")
        if isinstance(enum, list):
            enum = [x for x in enum if x is not None]
            if not enum:
                enum = None
        field = {
            "name": name,
            "title": schema.get("title") or _prettify(name),
            "type": (_type_names(schema) or ("string",))[0],
            "control": control,
            "required": name in required,
            "advanced": name in _ADVANCED or control == "json",
        }
        for key in (
            "description",
            "default",
            "enum",
            "minimum",
            "maximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "format",
            "multipleOf",
        ):
            value = schema.get(key)
            if value is not None:
                field[key] = value
        if control in ("url", "url_list") and "description" not in field:
            field["description"] = "Public https URL, or upload a file."
        fields.append(field)
    # Stable sort keeps schema order inside the basic and advanced groups.
    fields.sort(key=lambda f: f["advanced"])
    return fields


def _enum_values(schema, root):
    schema = _pick_display(schema, root)
    values = schema.get("enum")
    return [v for v in values if v is not None] if isinstance(values, list) else []


def derive_summary(model):
    """Documented input/output facts used for filtering and the model card.

    These are documentation facts, not estimates: no duration, cost or progress
    value is invented here.
    """
    root = model.get("schema") or {}
    props = root.get("properties") or {}
    kinds = {name: _type_names(_pick_display(prop, root)) for name, prop in props.items()}
    names = set(props)

    durations = _enum_values(props.get("duration") or {}, root)
    duration_prop = _pick_display(props.get("duration") or {}, root)
    aspect_ratios = _enum_values(props.get("aspect_ratio") or {}, root)
    resolutions = _enum_values(props.get("resolution") or {}, root)

    image_inputs = sorted(names & {
        "image_url", "image_urls", "input_images", "first_frame_url",
        "end_image_url", "reference_image_url", "character_image_url",
    })
    video_inputs = sorted(names & {"video_url", "video_urls", "reference_video_url"})
    audio_inputs = sorted(names & {"audio_url", "audio_urls", "sound"})

    max_images = None
    image_list = _pick_display(props.get("image_urls") or props.get("input_images") or {}, root)
    if isinstance(image_list.get("maxItems"), int):
        max_images = image_list["maxItems"]

    summary = {
        "prompt": "prompt" in names,
        "accepts": {
            "image": bool(image_inputs),
            "video": bool(video_inputs),
            "audio": bool(audio_inputs),
        },
        "image_fields": image_inputs,
        "video_fields": video_inputs,
        "audio_fields": audio_inputs,
        "durations": durations,
        "duration_min": duration_prop.get("minimum"),
        "duration_max": duration_prop.get("maximum"),
        "aspect_ratios": aspect_ratios,
        "resolutions": resolutions,
        "max_images": max_images,
        "field_count": len(props),
        "outputs_audio": bool(names & {"audio", "audios", "sound"}),
        "parameterized": True,
    }
    return summary


def provider_capabilities(provider="higgsfield"):
    """The capability profile for ``provider`` (higgsfield defaults)."""
    base = dict(PROVIDER_CAPABILITIES.get("higgsfield") or {})
    base.update(PROVIDER_CAPABILITIES.get(provider) or {})
    base["provider"] = provider
    return base


def capabilities_for(model, provider="higgsfield"):
    """Provider capabilities for a model.

    The profile comes from ``PROVIDER_CAPABILITIES``; a catalog entry may
    override individual keys (``capabilities``) when a single model differs
    from its provider, and unavailable models advertise no submission rights.
    """
    kind = model.get("kind")
    props = (model.get("schema") or {}).get("properties") or {}
    profile = provider_capabilities(provider)
    overrides = model.get("capabilities")
    if isinstance(overrides, dict):
        profile.update({k: v for k, v in overrides.items() if k != "outputs"})
    available = model.get("availability", "available") == "available"
    return {
        "cancel": profile.get("cancel", "queued_only"),
        "progress": bool(profile.get("progress")),
        "estimate": bool(profile.get("estimate")),
        "upload": bool(profile.get("upload")),
        "webhook": bool(profile.get("webhook")),
        "polling": bool(profile.get("polling")),
        "submit": available,
        "provider": provider,
        "verification": model.get("verification", "documented"),
        "outputs": {
            "image": kind == "image",
            "video": kind == "video",
            "audio": any(name in props for name in ("audio", "audios", "sound")),
        },
    }


# ---------------------------------------------------------------------------
# Request identity, state and output normalization
# ---------------------------------------------------------------------------
def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def request_hash(provider, model_id, params):
    return hashlib.sha256(
        canonical_json(
            {"provider": provider, "model": model_id, "params": params}
        ).encode("utf-8")
    ).hexdigest()


def map_state(provider, raw_state, raw_error=None):
    """Normalize a provider state.

    Returns ``(state, error)``. Unknown states are reported as failures with a
    distinct code instead of being left to poll forever.
    """
    text = str(raw_state or "").strip().lower().replace("-", "_").replace(" ", "_")
    state = _STATE_ALIASES.get(text)
    if state is None:
        return "failed", {
            "code": "unknown_state",
            "message": "The provider returned an unrecognized state: %s" % (raw_state or "empty"),
            "retryable": False,
        }
    if text == "nsfw":
        return state, {
            "code": "content_moderation",
            "message": "The provider rejected this input or output under its content policy.",
            "retryable": False,
        }
    if state == "failed":
        message = redact_text(_error_text(raw_error) or "Generation failed.")
        return state, {
            "code": "generation_failed",
            "message": message,
            "retryable": False,
        }
    return state, None


def _error_text(raw):
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("message", "detail", "error", "reason"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return canonical_json(raw)[:500]
    if isinstance(raw, list):
        return canonical_json(raw)[:500]
    return str(raw)


_URL_SCHEME = re.compile(r"^https?://", re.I)


def _output_kind(url, fallback):
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    if re.search(r"\.(png|jpe?g|webp|gif|avif|heic)$", path):
        return "image"
    if re.search(r"\.(mp4|mov|webm|m4v|mkv)$", path):
        return "video"
    if re.search(r"\.(mp3|wav|m4a|aac|ogg|flac)$", path):
        return "audio"
    if re.search(r"\.(zip|fbx|ply|glb|gltf|jsx)$", path):
        return "file"
    return fallback


def normalize_outputs(payload, kind="video"):
    """Collect http(s) output URLs from a provider status payload."""
    found = []
    seen = set()

    def add(url, hint):
        if not isinstance(url, str):
            return
        url = url.strip()
        if not _URL_SCHEME.match(url) or url in seen:
            return
        seen.add(url)
        found.append({"url": url, "kind": _output_kind(url, hint), "content_type": None})

    def walk(node, hint, depth=0):
        if depth > 6 or node is None:
            return
        if isinstance(node, str):
            add(node, hint)
            return
        if isinstance(node, list):
            for item in node:
                walk(item, hint, depth + 1)
            return
        if not isinstance(node, dict):
            return
        hint = node.get("type") if node.get("type") in KINDS else hint
        url = node.get("url") or node.get("public_url")
        if isinstance(url, str):
            add(url, hint)
        for key in ("images", "image", "videos", "video", "audios", "audio", "outputs", "results", "files", "artifacts", "output"):
            if key in node:
                child_hint = "image" if key.startswith("image") else "video" if key.startswith("video") else hint
                walk(node[key], child_hint, depth + 1)

    for key in ("images", "image", "videos", "video", "audios", "audio", "outputs", "results", "files", "artifacts", "artifact", "output"):
        if isinstance(payload, dict) and key in payload:
            hint = "image" if key.startswith("image") else "video" if key.startswith("video") else kind
            walk(payload[key], hint)
    if not found and isinstance(payload, dict):
        walk(payload, kind)
    return found


def error_from_response(status, body):
    """Map an HTTP error response to a normalized, redacted error."""
    message = _error_text(body) or "The provider returned HTTP %s." % status
    message = redact_text(message)[:600]
    code, retryable = {
        400: ("invalid_request", False),
        401: ("authentication_failed", False),
        403: ("insufficient_credits", False),
        404: ("not_found", False),
        409: ("conflict", False),
        422: ("validation_failed", False),
        423: ("model_blocked", True),
        429: ("rate_limited", True),
        500: ("provider_error", True),
        502: ("provider_error", True),
        503: ("model_unavailable", True),
        504: ("provider_error", True),
    }.get(int(status), ("provider_error", int(status) >= 500))
    return {"code": code, "message": message, "retryable": retryable}


def is_retryable_status(status):
    return int(status) in _STATUS_TRANSIENT


_SECRET_KEY = re.compile(
    r"(secret|token|password|passwd|authorization|api[_-]?key|credential|bearer)", re.I
)
_AUTH_HEADER = re.compile(r"(Key|Bearer)\s+[A-Za-z0-9._:\-]+", re.I)
_URL_TOKEN = re.compile(r"([?&](?:token|key|api_key|signature)=)[^&\s]+", re.I)


def redact_text(text):
    if not isinstance(text, str):
        text = str(text or "")
    text = _AUTH_HEADER.sub(lambda m: m.group(1) + " [redacted]", text)
    text = _URL_TOKEN.sub(lambda m: m.group(1) + "[redacted]", text)
    return text


def redact(value, depth=0):
    """Recursively redact credential-looking keys for logs/debug output."""
    if depth > 8:
        return "[depth-limit]"
    if isinstance(value, dict):
        return {
            key: ("[redacted]" if _SECRET_KEY.search(str(key)) else redact(item, depth + 1))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value[:200]]
    if isinstance(value, str):
        return redact_text(value)
    return value


def sanitize_filename(name, fallback="file"):
    base = re.split(r"[\\/]", str(name or ""))[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    if not base or base in (".", ".."):
        base = fallback
    return base[:120]


def extension_for(content_type, kind="image"):
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/ogg": ".ogg",
        "application/zip": ".zip",
    }
    if content_type in mapping:
        return mapping[content_type]
    return {"image": ".png", "video": ".mp4", "audio": ".mp3", "file": ".bin"}.get(kind, ".bin")
