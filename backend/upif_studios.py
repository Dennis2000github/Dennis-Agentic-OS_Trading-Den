"""UPIF Studio integration (Phase 9).

YouTube Studio and Ads Studio both call the SAME UPIF service. Studio-specific
configuration may change quality, format, resolution, duration and model
preference — it can never bypass the global routing policy, FREE ONLY or the
shared queue/asset library.
"""

from __future__ import annotations

from .upif_contracts import UPIFError
from .upif_service import SERVICE

#: Studio-specific defaults that may be tuned per Studio.
YOUTUBE_DEFAULTS = {
    "thumbnail": {"policy": "free_first", "aspect_ratio": "16:9",
                  "width": 1280, "height": 720, "quality": "high"},
    "scene_illustration": {"policy": "free_first", "aspect_ratio": "16:9"},
    "reference_image": {"policy": "free_first"},
}

ADS_DEFAULTS = {
    "product_image": {"policy": "free_first", "aspect_ratio": "1:1"},
    "product_background": {"policy": "free_first"},
    "lifestyle_scene": {"policy": "free_first", "aspect_ratio": "4:5"},
    "video_ad": {"policy": "free_first", "duration": 6},
}

#: Global safeguard state shared by both Studios.
GLOBAL = {"free_only": False, "paid_authorization": False}


class StudioGenerationError(UPIFError):
    pass


def set_free_only(enabled):
    GLOBAL["free_only"] = bool(enabled)


def authorize_paid(authorized):
    """Explicit owner authorization for paid generation (never automatic)."""
    GLOBAL["paid_authorization"] = bool(authorized)


def _merge(studio_defaults, request):
    merged = dict(studio_defaults)
    merged.update(request or {})
    return merged


def youtube_generate_image(prompt, mode="thumbnail", **request):
    params = _merge(YOUTUBE_DEFAULTS.get(mode, {}), request)
    return SERVICE.generate_image(prompt, studio_id="youtube", **params)


def youtube_generate_video(prompt, **request):
    params = _merge({"policy": "free_first"}, request)
    return SERVICE.generate_video(prompt, studio_id="youtube", **params)


def youtube_edit_image(prompt, reference_urls, **request):
    params = _merge({"policy": "free_first"}, request)
    return SERVICE.edit_image(prompt, reference_urls=reference_urls,
                              studio_id="youtube", **params)


def ads_generate_image(prompt, mode="product_image", **request):
    params = _merge(ADS_DEFAULTS.get(mode, {}), request)
    return SERVICE.generate_image(prompt, studio_id="ads", **params)


def ads_generate_video(prompt, **request):
    params = _merge({"policy": "free_first"}, request)
    return SERVICE.generate_video(prompt, studio_id="ads", **params)


def ads_edit_image(prompt, reference_urls, **request):
    params = _merge({"policy": "free_first"}, request)
    return SERVICE.edit_image(prompt, reference_urls=reference_urls,
                              studio_id="ads", **params)


def studio_status(studio_id):
    return {
        "studio": studio_id,
        "free_only": GLOBAL["free_only"],
        "paid_authorization": GLOBAL["paid_authorization"],
        "policy": SERVICE.router.policy,
        "queue": SERVICE.queue.stats(),
    }
