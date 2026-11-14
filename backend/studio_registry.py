"""Provider registry for Studio generation.

Provider Setup acts as the registry: the owner saves credentials for any
supported provider (Google AI, Higgsfield, fal.ai, Runway, Luma, MiniMax,
Replicate — plus the always-on free Pollinations engine), and every connected
provider becomes selectable in the per-step Provider dropdown with a
classified Model dropdown beside it (image vs video, grouped by mode).

Two views power the UI:

* ``registry()``        — the Setup page: providers, connection status and
                          credential field labels (never values).
* ``generation_catalog()`` — the step dropdowns: per provider, the image and
                          video models with a classification (mode) and the
                          input each model requires (``needs``).

Free engines stay first-class: "auto" routes to Google when a key exists and
Pollinations otherwise, so the workflow never *requires* a paid provider.
"""

from __future__ import annotations

from . import studio_catalog as catalog
from . import studio_secrets as secrets
from . import studio_free as free

#: Secrets ids differ from registry ids for Google (google_veo there).
_SECRETS_ID = {
    "google": "google_veo",
    "higgsfield": "higgsfield",
    "fal": "fal",
    "runway": "runway",
    "luma": "luma",
    "minimax": "minimax",
    "replicate": "replicate",
}

#: Human labels for registry providers (secrets labels otherwise).
_LABELS = {
    "auto": "Auto (free engines)",
    "pollinations": "Pollinations (free, no key)",
    "google": "Google AI — Gemini + Veo (Google Flow engine)",
    "higgsfield": "Higgsfield",
    "fal": "fal.ai",
    "runway": "Runway",
    "luma": "Luma AI",
    "minimax": "MiniMax / Hailuo",
    "replicate": "Replicate",
}

#: Providers whose engine adapter is wired into Studio generation. The rest
#: can store credentials today; their model catalogs are not wired yet.
_ADAPTER_READY = ("auto", "pollinations", "google", "higgsfield")


def _connected(provider_id):
    if provider_id in ("auto", "pollinations"):
        return True
    if provider_id == "google":
        return free.google_available()
    secrets_id = _SECRETS_ID.get(provider_id)
    if not secrets_id:
        return False
    try:
        status = secrets.status(secrets_id)
        return bool(status.get("configured"))
    except Exception:
        return False


def _fields(provider_id):
    secrets_id = _SECRETS_ID.get(provider_id)
    if not secrets_id:
        return []
    spec = secrets.PROVIDERS.get(secrets_id) or {}
    labels = spec.get("field_labels") or {}
    return [{"id": field, "label": labels.get(field, field)} for field in spec.get("fields") or ()]


def _env(provider_id):
    secrets_id = _SECRETS_ID.get(provider_id)
    if not secrets_id:
        return []
    spec = secrets.PROVIDERS.get(secrets_id) or {}
    return [name for names in (spec.get("env") or {}).values() for name in names]


def registry():
    """The Setup-page view: every provider, its status and credential fields."""
    providers = []
    for provider_id in ("auto", "pollinations", "google", "higgsfield",
                        "fal", "runway", "luma", "minimax", "replicate"):
        connected = _connected(provider_id)
        entry = {
            "id": provider_id,
            "label": _LABELS.get(provider_id, provider_id),
            "connected": connected,
            "fields": _fields(provider_id),
            "env": _env(provider_id),
            "adapter": provider_id in _ADAPTER_READY,
            "selectable": provider_id in ("auto", "pollinations") or connected,
        }
        if provider_id == "auto":
            entry["note"] = ("Routes each generation to the best free engine — "
                             "Google when a key exists, Pollinations otherwise.")
        elif provider_id == "pollinations":
            entry["note"] = "Keyless image generation, always available."
        elif provider_id == "google":
            entry["note"] = ("Free-tier key from aistudio.google.com. Gemini edits "
                             "images (Steps 1-3); Veo generates video (Step 5).")
        elif provider_id == "higgsfield":
            entry["note"] = ("Paid API. Unlocks the full classified model catalog "
                             "including reference-video motion transfer.")
        else:
            entry["note"] = ("Credentials can be saved here; the engine adapter for "
                             "this provider is not wired into generation yet.")
        if not connected and entry["fields"]:
            entry["note"] = "Not connected — save the credentials below."
        providers.append(entry)
    return {
        "providers": providers,
        "google_key_url": "https://aistudio.google.com/apikey",
    }


def _higgsfield_needs(mode):
    """What input a catalog model requires, for step validation."""
    return {
        "text_to_image": "prompt",
        "text_to_video": "prompt",
        "image_edit": "image",
        "image_to_video": "image",
        "reference_to_video": "reference_video",
        "motion_transfer": "reference_video",
        "video_reference": "reference_video",
        "video_edit": "reference_video",
        "first_last_frame": "image",
        "object_swap": "image",
        "restyle": "reference_video",
        "video_extend": "reference_video",
    }.get(str(mode or ""), "prompt")


def _higgsfield_models(kind):
    """Classified Higgsfield catalog models: image or video, by mode."""
    out = []
    for model in catalog.list_models("higgsfield"):
        if model.get("kind") != kind:
            continue
        mode = str(model.get("mode") or "")
        if kind == "image" and mode not in ("text_to_image", "image_edit"):
            continue
        out.append({
            "id": model.get("id"),
            "label": model.get("label") or model.get("id"),
            "mode": mode,
            "mode_label": model.get("mode_label") or mode,
            "needs": _higgsfield_needs(mode),
        })
    out.sort(key=lambda item: (item["mode_label"], item["label"]))
    return out


def generation_catalog():
    """Per-provider classified models for the step dropdowns."""
    return {
        "providers": {
            "auto": {
                "image": [{"id": "auto", "label": "Auto — best free engine",
                           "mode": "auto", "mode_label": "Auto",
                           "needs": "any"}],
                "video": [dict(engine, needs="prompt_or_image")
                          for engine in free.VIDEO_ENGINES],
            },
            "pollinations": {
                "image": [
                    {"id": "pollinations-flux", "label": "Pollinations Flux",
                     "mode": "text_to_image", "mode_label": "Text → Image",
                     "needs": "prompt"},
                    {"id": "pollinations-turbo", "label": "Pollinations Turbo (fast)",
                     "mode": "text_to_image", "mode_label": "Text → Image",
                     "needs": "prompt"},
                ],
                "video": [],
            },
            "google": {
                "image": [{"id": "google-gemini-image",
                           "label": "Gemini 2.5 Flash Image",
                           "mode": "image_edit", "mode_label": "Text → Image + Edit",
                           "needs": "any"}],
                "video": [dict(engine, needs="prompt_or_image")
                          for engine in free.VIDEO_ENGINES],
            },
            "higgsfield": {
                "image": _higgsfield_models("image"),
                "video": _higgsfield_models("video"),
            },
            "fal": {"image": [], "video": []},
            "runway": {"image": [], "video": []},
            "luma": {"image": [], "video": []},
            "minimax": {"image": [], "video": []},
            "replicate": {"image": [], "video": []},
        },
    }


def save_credentials(provider_id, values):
    """Store credentials for a registry provider (values never echoed)."""
    if provider_id in ("auto", "pollinations"):
        return {"saved": False, "note": "This provider needs no credentials."}
    secrets_id = _SECRETS_ID.get(provider_id)
    if not secrets_id:
        raise ValueError("Unknown provider: %s" % provider_id)
    clean = {str(k): str(v or "").strip() for k, v in (values or {}).items() if str(v or "").strip()}
    if not clean:
        raise ValueError("Enter at least one credential value.")
    status = secrets.set_values(secrets_id, clean)
    return {"saved": True, "status": {
        "configured": bool(status.get("configured")),
        "source": status.get("source"),
    }}


def delete_credentials(provider_id):
    secrets_id = _SECRETS_ID.get(provider_id)
    if not secrets_id:
        raise ValueError("Unknown provider: %s" % provider_id)
    secrets.delete(secrets_id)
    return {"deleted": provider_id}


def test_provider(provider_id):
    """Validate a provider connection without running a generation."""
    if provider_id in ("auto", "pollinations"):
        return {"ok": True, "message": "Always available — no credentials needed."}
    if provider_id == "google":
        return free.test_google_key()
    if provider_id == "higgsfield":
        from . import studio_providers as providers
        return providers.REGISTRY.get("higgsfield").test()
    if not _connected(provider_id):
        raise ValueError("Save the credentials first, then test.")
    return {"ok": True,
            "message": "Credentials stored. The engine adapter for this provider "
                       "is not wired into generation yet."}
