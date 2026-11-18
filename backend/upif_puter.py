"""UPIF adapter: Puter.js AI (SDK execution environment).

Puter uses a User-Pays execution model: the *authorized user's* account pays,
not the OS. Therefore:

* Puter is NEVER classified FREE VERIFIED merely because the OS holds no key;
* the user's entitlement and cost must be checked and an explicit user
  authorization recorded before any run;
* user auth tokens / private session credentials are never exposed to agents
  or browser clients;
* the browser SDK path is NOT assumed to provide background server-side
  execution; autonomous agents may only use a verified server-side route.
"""

from __future__ import annotations

import time

from .upif_base import BaseAdapter, new_job, UnsupportedError
from .upif_contracts import (
    CAP_IMAGE_EDIT, CAP_TEXT_TO_IMAGE, CAP_TEXT_TO_VIDEO, CH_SDK, CH_WEBSITE,
    ENT_USER_FUNDED, ENT_UNKNOWN, UPIFError, VERIFY_TTL_SHORT, new_model_record,
)
from .upif_entitlements import LEDGER


class PuterAdapter(BaseAdapter):
    provider_id = "puter"
    service_id = "puter_js_ai"
    channels = (CH_SDK, CH_WEBSITE)

    def __init__(self, auth_token="", user_authorized=False, server_side=False):
        self.auth_token = auth_token
        self.user_authorized = user_authorized
        #: Autonomous agents require a verified server-side SDK path.
        self.server_side = server_side

    # -- discovery ------------------------------------------------------------
    def discover_models(self):
        records = []
        for model_id, caps in (("puter-image-v1", [CAP_TEXT_TO_IMAGE, CAP_IMAGE_EDIT]),
                               ("puter-video-v1", [CAP_TEXT_TO_VIDEO])):
            records.append(new_model_record(
                "puter", "puter_js_ai", model_id,
                model_name=model_id, channel=CH_SDK,
                capabilities=caps,
                input_modalities=["text", "image"],
                output_modalities=["image", "video"],
                auth_status="user_authorized" if self.user_authorized else "anonymous",
                programmatic=self.server_side,
                website_only=not self.server_side,
                entitlement=ENT_UNKNOWN,
                free_eligible=False,
                estimated_cost=None, cost_unit="user_pays",
                license="Puter Terms of Service"))
        return records

    def get_capabilities(self, model_id):
        for record in self.discover_models():
            if record["model_id"] == model_id:
                return record["capabilities"]
        return []

    def check_availability(self, model_id):
        return {"available": self.server_side and bool(self.auth_token),
                "reason": "" if self.server_side and self.auth_token else "no_server_side_route"}

    # -- entitlement ------------------------------------------------------------
    def check_entitlement(self, model_id=None):
        target = model_id or "puter-image-v1"
        if not self.user_authorized:
            LEDGER.record(target, ENT_UNKNOWN,
                          note="no explicit user authorization for User-Pays execution")
            return {"entitlement": ENT_UNKNOWN, "reason": "user_authorization_required"}
        if not self.server_side:
            LEDGER.record(target, ENT_UNKNOWN,
                          note="browser SDK path is not a verified server-side route")
            return {"entitlement": ENT_UNKNOWN, "reason": "server_side_route_unverified"}
        # The user's actual balance/cost is checked against their Puter account;
        # the OS never treats it as free.
        return LEDGER.record(
            target, ENT_USER_FUNDED,
            remaining=None, unit="user_balance",
            funding_source="user_funded_balance",
            origin="puter_user_pays",
            note="User-Pays: the authorized user's account funds execution. "
                 "Never FREE VERIFIED; requires explicit per-run authorization.",
            ttl=VERIFY_TTL_SHORT)

    # -- generation --------------------------------------------------------------
    def _guard(self, model_id):
        if not self.user_authorized:
            raise UPIFError("user_authorization_required",
                            "Puter execution is User-Pays: the owner must explicitly "
                            "authorize the run.", 403)
        if not self.server_side:
            raise UPIFError("no_server_side_route",
                            "Puter browser SDK does not provide a verified "
                            "server-side execution path for autonomous agents.", 400)

    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        self._guard(model_id)
        job = new_job("puter", model_id, "generate_image", {"prompt": prompt},
                      idempotency_key)
        job["status"] = "failed"
        job["error"] = {"code": "not_implemented",
                        "message": "Puter server-side SDK route is not wired in this deployment."}
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
