"""UPIF adapter: PicassoIA API (native REST + MCP where supported).

PicassoIA's zero-per-prediction pricing requires the Infinite subscription.
It is therefore registered PAID SUBSCRIPTION REQUIRED, never FREE VERIFIED,
and is only used when an active subscription is verified AND the user has
authorized the run. Under FREE ONLY it is always excluded.
"""

from __future__ import annotations

import time

from .upif_base import BaseAdapter, new_job, UnsupportedError
from .upif_contracts import (
    CAP_IMAGE_EDIT, CAP_IMAGE_TO_VIDEO, CAP_REFERENCE_VIDEO, CAP_TEXT_TO_IMAGE,
    CAP_TEXT_TO_VIDEO, CH_MCP, CH_NATIVE_REST, ENT_PAID_SUBSCRIPTION,
    ENT_UNKNOWN, UPIFError, VERIFY_TTL_SHORT, new_model_record,
)
from .upif_entitlements import LEDGER


class PicassoIaAdapter(BaseAdapter):
    provider_id = "picassoia"
    service_id = "picassoia_api"
    channels = (CH_NATIVE_REST, CH_MCP)

    def __init__(self, api_key="", subscription_active=False):
        self.api_key = api_key
        self.subscription_active = subscription_active

    # -- discovery ------------------------------------------------------------
    def discover_models(self):
        records = []
        for model_id, caps in (
            ("picassoia-image-v1", [CAP_TEXT_TO_IMAGE, CAP_IMAGE_EDIT]),
            ("picassoia-video-v1", [CAP_TEXT_TO_VIDEO, CAP_IMAGE_TO_VIDEO]),
            ("picassoia-ref-video-v1", [CAP_REFERENCE_VIDEO]),
        ):
            records.append(new_model_record(
                "picassoia", "picassoia_api", model_id,
                model_name=model_id, channel=CH_NATIVE_REST,
                capabilities=caps,
                input_modalities=["text", "image", "video"],
                output_modalities=["image", "video"],
                auth_status="anonymous", programmatic=True,
                entitlement=ENT_UNKNOWN, free_eligible=False,
                estimated_cost=0, cost_unit="usd",
                license="PicassoIA Terms (Zero pricing needs Infinite plan)"))
        return records

    def get_capabilities(self, model_id):
        for record in self.discover_models():
            if record["model_id"] == model_id:
                return record["capabilities"]
        return []

    def check_availability(self, model_id):
        return {"available": self.subscription_active and bool(self.api_key),
                "reason": "" if self.subscription_active and self.api_key
                else "subscription_required"}

    # -- entitlement ------------------------------------------------------------
    def check_entitlement(self, model_id=None):
        target = model_id or "picassoia-image-v1"
        if not self.subscription_active:
            LEDGER.record(target, ENT_UNKNOWN,
                          note="Infinite subscription not verified")
            return {"entitlement": ENT_UNKNOWN, "reason": "subscription_required"}
        return LEDGER.record(
            target, ENT_PAID_SUBSCRIPTION,
            remaining=None, unit="predictions",
            funding_source="subscription_entitlement",
            origin="picassoia_infinite_plan",
            note="Zero-per-prediction pricing requires the active Infinite "
                 "subscription; never free.",
            ttl=VERIFY_TTL_SHORT)

    # -- generation --------------------------------------------------------------
    def _guard(self, model_id):
        if not self.subscription_active:
            raise UPIFError("subscription_required",
                            "PicassoIA requires the Infinite subscription; FREE ONLY "
                            "excludes it.", 403)

    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        self._guard(model_id)
        job = new_job("picassoia", model_id, "generate_image", {"prompt": prompt},
                      idempotency_key)
        job["status"] = "failed"
        job["error"] = {"code": "not_implemented",
                        "message": "PicassoIA prediction submission is not wired in this deployment."}
        return job

    def edit_image(self, model_id, prompt, reference_urls=(), params=None,
                   idempotency_key=None):
        self._guard(model_id)
        raise UnsupportedError("edit_image", model_id)

    def generate_video(self, model_id, prompt, params=None, reference_urls=(),
                       idempotency_key=None):
        self._guard(model_id)
        raise UnsupportedError("generate_video", model_id)

    def animate_image(self, model_id, reference_url, params=None, idempotency_key=None):
        self._guard(model_id)
        raise UnsupportedError("animate_image", model_id)

    def get_status(self, job_id):
        raise UnsupportedError("get_status", job_id)

    def cancel(self, job_id):
        raise UnsupportedError("cancel", job_id)

    def retrieve_asset(self, job_id, asset_index=0):
        raise UnsupportedError("retrieve_asset", job_id)
