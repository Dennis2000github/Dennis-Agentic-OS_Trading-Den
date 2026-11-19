"""UPIF bootstrap: register adapters, discover models, wire the router.

Importing this module once (from app.py) makes every adapter available to the
router and populates the registry with dynamically discovered models. It never
executes a generation and never requires credentials — providers without
credentials simply report ``available: False`` until configured.
"""

from __future__ import annotations

from . import upif_cloudflare
from . import upif_huggingface
from . import upif_picassoia
from . import upif_pollinations
from . import upif_puter
from . import upif_replicate
from . import upif_service
from . import upif_stability
from . import upif_websites


def _adapters():
    return (
        upif_cloudflare.CloudflareWorkersAIAdapter(),
        upif_pollinations.PollinationsAdapter(),
        upif_replicate.ReplicateAdapter(),
        upif_huggingface.HuggingFaceAdapter(),
        upif_stability.StabilityAdapter(),
        upif_puter.PuterAdapter(),
        upif_picassoia.PicassoIaAdapter(),
    )


def bootstrap():
    """Register all adapters and discover their models into the registry."""
    service = upif_service.SERVICE
    for adapter in _adapters():
        service.register_adapter(adapter)
    # Website-only services are registered for the UI but never routed.
    upif_websites.register_website_only(
        "example_website_only", "website",
        note=upif_websites.WEBSITE_ONLY_NOTE)
    return {
        "providers": [p["id"] for p in service.registry.providers()],
        "models": len(service.registry.models()),
        "adapters": [a.provider_id for a in _adapters()],
    }
