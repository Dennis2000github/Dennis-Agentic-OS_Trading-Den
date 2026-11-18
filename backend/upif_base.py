"""UPIF normalized generation contract (Phase 2).

Every provider adapter subclasses :class:`BaseAdapter` and implements the
operations it *actually* supports. Unsupported operations raise
``UnsupportedError`` so the router never assumes cross-provider parity: a
model that edits images is not assumed to do image-to-video, and a model that
does text-to-video is not assumed to accept a reference video.

All adapters normalize to one result shape::

    {
        "job_id":        str,          # UPIF-level id
        "provider_job_id": str | None, # provider-side id for idempotency
        "status":        "queued"|"running"|"succeeded"|"failed"|"cancelled",
        "outputs":       [{"url":..., "kind": "image"|"video", "bytes": n}],
        "error":         None | {"code":..., "message":...},
        "entitlement":   "free_verified"|...,
        "funding_source":"included_daily_allowance"|...,
        "cost":          float | None,
        "cost_unit":     "",
        "idempotency_key": str | None,
        "submitted_at":  epoch,
    }
"""

from __future__ import annotations

import uuid

from .upif_contracts import (
    CAP_AUDIO, CAP_IMAGE_EDIT, CAP_IMAGE_TO_IMAGE, CAP_IMAGE_TO_VIDEO,
    CAP_REFERENCE_VIDEO, CAP_TEXT_TO_IMAGE, CAP_TEXT_TO_VIDEO,
    CAP_VIDEO_TO_VIDEO, ENT_UNKNOWN, UPIFError,
)
from .upif_entitlements import LEDGER

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

TERMINAL = frozenset((STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED))


class UnsupportedError(UPIFError):
    """The adapter does not implement this operation."""

    def __init__(self, operation, model_id):
        super().__init__("unsupported_operation",
                         "Model %s does not support %s." % (model_id, operation), 400)
        self.operation = operation
        self.model_id = model_id


class BaseAdapter:
    """Contract every UPIF provider adapter implements."""

    #: set by subclasses
    provider_id = ""
    service_id = ""
    channels = ()

    # -- discovery ---------------------------------------------------------
    def discover_models(self):
        """Return a list of model records (see upif_contracts.new_model_record)."""
        raise UnsupportedError("discover_models", self.provider_id)

    def get_capabilities(self, model_id):
        raise UnsupportedError("get_capabilities", model_id)

    def check_availability(self, model_id):
        raise UnsupportedError("check_availability", model_id)

    def check_entitlement(self, model_id):
        """Probe the provider and record the entitlement in the ledger."""
        raise UnsupportedError("check_entitlement", model_id)

    # -- generation ----------------------------------------------------------
    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        raise UnsupportedError("generate_image", model_id)

    def edit_image(self, model_id, prompt, reference_urls=(), params=None,
                   idempotency_key=None):
        raise UnsupportedError("edit_image", model_id)

    def generate_video(self, model_id, prompt, params=None,
                       reference_urls=(), idempotency_key=None):
        raise UnsupportedError("generate_video", model_id)

    def animate_image(self, model_id, reference_url, params=None,
                      idempotency_key=None):
        raise UnsupportedError("animate_image", model_id)

    # -- job control ---------------------------------------------------------
    def get_status(self, job_id):
        raise UnsupportedError("get_status", job_id)

    def cancel(self, job_id):
        raise UnsupportedError("cancel", job_id)

    def retrieve_asset(self, job_id, asset_index=0):
        raise UnsupportedError("retrieve_asset", job_id)


def new_job(provider_id, model_id, operation, params=None,
            idempotency_key=None):
    """Build a normalized in-flight job record."""
    return {
        "job_id": "upif_" + uuid.uuid4().hex[:12],
        "provider_id": provider_id,
        "model_id": model_id,
        "operation": operation,
        "params": dict(params or {}),
        "status": STATUS_QUEUED,
        "provider_job_id": None,
        "outputs": [],
        "error": None,
        "entitlement": ENT_UNKNOWN,
        "funding_source": "none",
        "cost": None,
        "cost_unit": "",
        "idempotency_key": idempotency_key,
        "submitted_at": None,
        "created_at": __import__("time").time(),
        "updated_at": __import__("time").time(),
    }


def capability_for(operation):
    """Map a UPIF operation to the capability it consumes."""
    return {
        "generate_image": CAP_TEXT_TO_IMAGE,
        "edit_image": CAP_IMAGE_EDIT,
        "image_to_image": CAP_IMAGE_TO_IMAGE,
        "generate_video": CAP_TEXT_TO_VIDEO,
        "animate_image": CAP_IMAGE_TO_VIDEO,
        "video_to_video": CAP_VIDEO_TO_VIDEO,
        "reference_to_video": CAP_REFERENCE_VIDEO,
        "generate_audio": CAP_AUDIO,
    }.get(operation, "")


def require_capability(model_record, operation):
    """Guard: the model must explicitly support the operation's capability."""
    cap = capability_for(operation)
    if cap and cap not in (model_record.get("capabilities") or []):
        raise UPIFError(
            "capability_mismatch",
            "Model %s does not support %s (needed for %s)."
            % (model_record.get("model_id"), cap, operation), 400)
    return cap


def free_gate(model_record, policy):
    """FREE ONLY enforcement — called by the router before submission.

    Returns the entitlement class when the run is allowed; raises otherwise.
    """
    from .upif_contracts import POLICY_FREE_ONLY, effective_entitlement, is_free
    entitlement = effective_entitlement(model_record)
    if policy == POLICY_FREE_ONLY and not is_free(model_record):
        raise UPIFError(
            "free_only_blocked",
            "FREE ONLY is enabled and %s is not a verified free model "
            "(entitlement: %s). No paid request will be submitted."
            % (model_record.get("model_id"), entitlement), 403)
    if LEDGER.is_exhausted(model_record.get("model_id")):
        raise UPIFError(
            "quota_exhausted",
            "Free quota for %s is exhausted; choose another model or disable "
            "FREE ONLY with explicit paid authorization." % model_record.get("model_id"),
            403)
    return entitlement
