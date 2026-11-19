"""UPIF — Unified Programmatic Interface for Generation: shared contracts.

This module holds the *vocabulary* of the generation layer. Every adapter,
the router and the API surface normalize through these constants, types and
helpers so that "FREE VERIFIED", "capability" and "entitlement" mean exactly
one thing across all providers.

Classification rules (Phase 3 of the UPIF spec):

* FREE VERIFIED    — actual API execution covered by an eligible free
                     allocation or reward balance, verified against the
                     provider. No paid balance required.
* FREE TRIAL       — temporary free allocation / promotional runs; tracked
                     with an expiration.
* USER-FUNDED      — an explicitly authorized user account pays.
* PAID SUBSCRIPTION— access requires an active paid subscription.
* PAID API         — generation consumes paid / pay-as-you-go credits.
* UNKNOWN          — entitlement cannot be reliably verified right now.

A website's free allowance is never evidence of free API access. A provider
being reachable is never evidence of free execution. Verification expiry
downgrades a model to UNKNOWN until revalidated.
"""

from __future__ import annotations

import time

__all__ = [
    "CH_NATIVE_REST", "CH_OPENAI_COMPATIBLE", "CH_ANTHROPIC_COMPATIBLE",
    "CH_SDK", "CH_MCP", "CH_GATEWAY", "CH_LOCAL", "CH_WEBSITE", "CHANNELS",
    "ENT_FREE_VERIFIED", "ENT_FREE_TRIAL", "ENT_USER_FUNDED",
    "ENT_PAID_SUBSCRIPTION", "ENT_PAID_API", "ENT_UNKNOWN", "ENTITLEMENTS",
    "FUND_DAILY_ALLOWANCE", "FUND_MONTHLY_ALLOWANCE", "FUND_PROMO",
    "FUND_REWARD", "FUND_USER_FUNDED", "FUND_PREPAID", "FUND_SUBSCRIPTION",
    "FUND_PAID_API", "FUND_NONE",
    "CAP_TEXT_TO_IMAGE", "CAP_IMAGE_EDIT", "CAP_IMAGE_TO_IMAGE",
    "CAP_TEXT_TO_VIDEO", "CAP_IMAGE_TO_VIDEO", "CAP_VIDEO_TO_VIDEO",
    "CAP_REFERENCE_VIDEO", "CAP_AUDIO", "CAP_INPUT_MODALITIES",
    "POLICY_FREE_FIRST", "POLICY_FREE_ONLY", "POLICY_QUALITY_FIRST",
    "POLICY_LOWEST_COST", "POLICY_FASTEST", "POLICY_MANUAL_MODEL",
    "POLICY_MANUAL_PROVIDER", "POLICY_LOCAL_FIRST", "POLICIES",
    "FREE_ENTITLEMENTS", "PAID_ENTITLEMENTS",
    "VERIFY_TTL_SHORT", "VERIFY_TTL_MEDIUM", "VERIFY_TTL_LONG",
    "AVAIL_PROGRAMMATIC", "AVAIL_WEBSITE", "AVAIL_BOTH",
    "AVAIL_AUTH_REQUIRED", "AVAIL_FREE_VERIFIED", "AVAIL_FREE_UNKNOWN",
    "AVAIL_PAID_ONLY", "AVAIL_UNAVAILABLE", "WEBSITE_ONLY_NOTE",
    "UPIFError", "now", "entitlement_expired", "effective_entitlement",
    "is_free", "is_paid", "supports", "modality_match", "new_model_record",
]

#: Integration channels — how an adapter reaches the service.
CH_NATIVE_REST = "native_rest"
CH_OPENAI_COMPATIBLE = "openai_compatible"
CH_ANTHROPIC_COMPATIBLE = "anthropic_compatible"
CH_SDK = "sdk"
CH_MCP = "mcp"
CH_GATEWAY = "gateway"
CH_LOCAL = "local"
CH_WEBSITE = "website"

CHANNELS = (
    CH_NATIVE_REST, CH_OPENAI_COMPATIBLE, CH_ANTHROPIC_COMPATIBLE, CH_SDK,
    CH_MCP, CH_GATEWAY, CH_LOCAL, CH_WEBSITE,
)

#: Entitlement classifications (see module docstring).
ENT_FREE_VERIFIED = "free_verified"
ENT_FREE_TRIAL = "free_trial"
ENT_USER_FUNDED = "user_funded"
ENT_PAID_SUBSCRIPTION = "paid_subscription"
ENT_PAID_API = "paid_api"
ENT_UNKNOWN = "unknown"

ENTITLEMENTS = (
    ENT_FREE_VERIFIED, ENT_FREE_TRIAL, ENT_USER_FUNDED,
    ENT_PAID_SUBSCRIPTION, ENT_PAID_API, ENT_UNKNOWN,
)

#: Funding sources recorded per operation (Phase 11 usage accounting).
FUND_DAILY_ALLOWANCE = "included_daily_allowance"
FUND_MONTHLY_ALLOWANCE = "included_monthly_allowance"
FUND_PROMO = "promotional_credit"
FUND_REWARD = "earned_reward_credit"
FUND_USER_FUNDED = "user_funded_balance"
FUND_PREPAID = "purchased_prepaid_credit"
FUND_SUBSCRIPTION = "subscription_entitlement"
FUND_PAID_API = "paid_api_billing"
FUND_NONE = "none"

#: Operation kinds a model can support. Capability detection is explicit:
#: supporting image editing never implies image-to-video support.
CAP_TEXT_TO_IMAGE = "text_to_image"
CAP_IMAGE_EDIT = "image_edit"
CAP_IMAGE_TO_IMAGE = "image_to_image"
CAP_TEXT_TO_VIDEO = "text_to_video"
CAP_IMAGE_TO_VIDEO = "image_to_video"
CAP_VIDEO_TO_VIDEO = "video_to_video"
CAP_REFERENCE_VIDEO = "reference_video"
CAP_AUDIO = "audio"

#: Capability -> input modality it requires (used by the router match).
CAP_INPUT_MODALITIES = {
    CAP_TEXT_TO_IMAGE: ("text",),
    CAP_IMAGE_EDIT: ("text", "image"),
    CAP_IMAGE_TO_IMAGE: ("text", "image"),
    CAP_TEXT_TO_VIDEO: ("text",),
    CAP_IMAGE_TO_VIDEO: ("image",),
    CAP_VIDEO_TO_VIDEO: ("video",),
    CAP_REFERENCE_VIDEO: ("text", "image", "video"),
    CAP_AUDIO: ("text",),
}

#: Routing policies (Phase 4).
POLICY_FREE_FIRST = "free_first"
POLICY_FREE_ONLY = "free_only"
POLICY_QUALITY_FIRST = "quality_first"
POLICY_LOWEST_COST = "lowest_cost"
POLICY_FASTEST = "fastest_available"
POLICY_MANUAL_MODEL = "manual_model"
POLICY_MANUAL_PROVIDER = "manual_provider"
POLICY_LOCAL_FIRST = "local_first"

POLICIES = (
    POLICY_FREE_FIRST, POLICY_FREE_ONLY, POLICY_QUALITY_FIRST,
    POLICY_LOWEST_COST, POLICY_FASTEST, POLICY_MANUAL_MODEL,
    POLICY_MANUAL_PROVIDER, POLICY_LOCAL_FIRST,
)

#: Free-entitlement classes under which a generation may run without paid
#: authorization. FREE FIRST prefers these; FREE ONLY *only* accepts these.
FREE_ENTITLEMENTS = frozenset((ENT_FREE_VERIFIED, ENT_FREE_TRIAL))

#: Entitlement classes that are NEVER acceptable under FREE ONLY.
PAID_ENTITLEMENTS = frozenset((
    ENT_USER_FUNDED, ENT_PAID_SUBSCRIPTION, ENT_PAID_API,
))

#: Verification TTLs (seconds). After expiry the model drops to UNKNOWN
#: until revalidated — the router then refuses to route it under FREE ONLY.
VERIFY_TTL_SHORT = 15 * 60        # entitlement / balance probes
VERIFY_TTL_MEDIUM = 60 * 60       # model catalog discovery
VERIFY_TTL_LONG = 24 * 60 * 60    # static capability metadata

#: Provider-level availability statuses (Phase 8 UI vocabulary).
AVAIL_PROGRAMMATIC = "programmatic_available"
AVAIL_WEBSITE = "website_available"
AVAIL_BOTH = "both_available"
AVAIL_AUTH_REQUIRED = "authentication_required"
AVAIL_FREE_VERIFIED = "free_entitlement_verified"
AVAIL_FREE_UNKNOWN = "free_entitlement_unknown"
AVAIL_PAID_ONLY = "paid_only"
AVAIL_UNAVAILABLE = "temporarily_unavailable"

WEBSITE_ONLY_NOTE = (
    "WEBSITE ONLY — NOT AVAILABLE FOR PROGRAMMATIC AGENT EXECUTION"
)


class UPIFError(Exception):
    """Normalized UPIF error; message is safe to show to the owner."""

    def __init__(self, code, message, status=400, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = int(status or 400)
        self.detail = detail or {}

    def as_error(self):
        return {"code": self.code, "message": self.message,
                "status": self.status, "detail": self.detail}


def now():
    return time.time()


def entitlement_expired(model_entry, ttl=VERIFY_TTL_SHORT):
    """True when the entitlement check is older than ``ttl`` seconds."""
    checked = model_entry.get("entitlement_verified_at") or 0
    return (now() - checked) > ttl


def effective_entitlement(model_entry, ttl=VERIFY_TTL_SHORT):
    """Return the entitlement, downgraded to UNKNOWN when verification expired.

    The ledger holds the live probe result; the registry record holds the
    adapter's static classification. When the registry record is stale, the
    fresher ledger probe wins, so a model verified moments ago routes as free
    even before its registry record is refreshed.
    """
    if entitlement_expired(model_entry, ttl):
        from .upif_entitlements import LEDGER
        entry = LEDGER.entry(model_entry.get("model_id"))
        if entry:
            age = time.time() - entry.get("verified_at", 0)
            if age <= entry.get("ttl", VERIFY_TTL_SHORT) and not entry.get("exhausted"):
                return entry.get("entitlement") or ENT_UNKNOWN
        return ENT_UNKNOWN
    return model_entry.get("entitlement") or ENT_UNKNOWN


def is_free(model_entry, ttl=VERIFY_TTL_SHORT):
    """True when the model is currently classified as a free entitlement."""
    return effective_entitlement(model_entry, ttl) in FREE_ENTITLEMENTS


def is_paid(model_entry, ttl=VERIFY_TTL_SHORT):
    """True when the model is currently classified as a paid entitlement."""
    return effective_entitlement(model_entry, ttl) in PAID_ENTITLEMENTS


def supports(model_entry, capability):
    """Explicit capability check — never inferred from a sibling capability."""
    return capability in (model_entry.get("capabilities") or [])


def modality_match(model_entry, modalities):
    """True when the model supports at least one capability needing ``modalities``."""
    wanted = set(modalities or ())
    for cap in model_entry.get("capabilities") or []:
        needed = set(CAP_INPUT_MODALITIES.get(cap, ()))
        if needed and needed.issubset(wanted | {"text"}):
            return True
    return False


def new_model_record(provider_id, service_id, model_id, **overrides):
    """Build a model registry record with every required Phase 3 field."""
    record = {
        "provider_id": provider_id,
        "service_id": service_id,
        "model_id": model_id,
        "model_name": model_id,
        "model_version": "",
        "channel": CH_NATIVE_REST,
        "capabilities": [],
        "input_modalities": ["text"],
        "output_modalities": ["image"],
        "parameter_schema": {},
        "max_width": 0,
        "max_height": 0,
        "max_duration_seconds": 0,
        "supports_reference_image": False,
        "supports_reference_video": False,
        "auth_status": "anonymous",
        "programmatic": True,
        "website_only": False,
        "entitlement": ENT_UNKNOWN,
        "entitlement_verified_at": 0,
        "free_eligible": False,
        "remaining_quota": None,
        "quota_unit": "",
        "rate_limit": None,
        "estimated_cost": None,
        "cost_unit": "",
        "available": False,
        "commercial_restrictions": [],
        "license": "",
        "last_verified_at": 0,
        "exclusion_reason": "",
        "pollen_origin": "",
    }
    record.update(overrides)
    return record
