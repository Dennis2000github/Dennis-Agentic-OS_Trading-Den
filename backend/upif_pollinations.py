"""UPIF adapter: Pollinations AI Generation API (REST + OpenAI-compatible).

Pollinations is an important free fallback: text-to-image works keyless, and
accounts can hold Pollen balance. The spec's hard rule is enforced here:

* Purchased Pollen is NEVER used under FREE ONLY.
* Only reward/quest-earned Pollen counts as FREE VERIFIED.
* Pollinations' public API does not expose balance *origin*, so a balance we
  cannot prove is reward-funded is classified UNKNOWN, never FREE VERIFIED.

Image editing uses the ``kontext`` model; only publicly reachable reference
URLIs are accepted (the keyless REST path cannot upload private bytes).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .upif_base import BaseAdapter, new_job, UnsupportedError
from .upif_contracts import (
    CAP_IMAGE_EDIT, CAP_TEXT_TO_IMAGE, CH_MCP, CH_NATIVE_REST,
    CH_OPENAI_COMPATIBLE, ENT_FREE_VERIFIED, ENT_UNKNOWN, UPIFError,
    VERIFY_TTL_MEDIUM, new_model_record,
)
from .upif_entitlements import LEDGER

IMAGE_API = "https://image.pollinations.ai/prompt/"
EDIT_MODEL = "kontext"
USER_AGENT = "DENNIS-Agentic-OS/1.0 (UPIF Pollinations)"


class PollinationsAdapter(BaseAdapter):
    provider_id = "pollinations"
    service_id = "generation_api"
    channels = (CH_NATIVE_REST, CH_OPENAI_COMPATIBLE, CH_MCP)

    def __init__(self, api_key=""):
        self.api_key = api_key

    # -- internal ------------------------------------------------------------
    def _public_url(self, url):
        return bool(url) and str(url).startswith(("http://", "https://"))

    def _fetch(self, url, timeout=180):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise UPIFError("provider_error",
                            "Pollinations HTTP %s" % exc.code, 502)
        except (urllib.error.URLError, OSError) as exc:
            raise UPIFError("network_error", "Cannot reach Pollinations: %s" % exc, 502)

    # -- discovery -------------------------------------------------------------
    def discover_models(self):
        return [
            new_model_record(
                "pollinations", "generation_api", "flux",
                model_name="Flux (default)", channel=CH_NATIVE_REST,
                capabilities=[CAP_TEXT_TO_IMAGE],
                input_modalities=["text"], output_modalities=["image"],
                auth_status="anonymous", programmatic=True,
                entitlement=ENT_UNKNOWN, free_eligible=True,
                estimated_cost=0, cost_unit="credits",
                license="model-specific (see pollinations.ai/models)"),
            new_model_record(
                "pollinations", "generation_api", "turbo",
                model_name="Turbo", channel=CH_NATIVE_REST,
                capabilities=[CAP_TEXT_TO_IMAGE],
                input_modalities=["text"], output_modalities=["image"],
                auth_status="anonymous", programmatic=True,
                entitlement=ENT_UNKNOWN, free_eligible=True,
                estimated_cost=0, cost_unit="credits",
                license="model-specific"),
            new_model_record(
                "pollinations", "generation_api", EDIT_MODEL,
                model_name="Kontext (image edit)", channel=CH_NATIVE_REST,
                capabilities=[CAP_IMAGE_EDIT],
                input_modalities=["text", "image"], output_modalities=["image"],
                auth_status="anonymous", programmatic=True,
                entitlement=ENT_UNKNOWN, free_eligible=True,
                estimated_cost=0, cost_unit="credits",
                license="model-specific"),
        ]

    def get_capabilities(self, model_id):
        for record in self.discover_models():
            if record["model_id"] == model_id:
                return record["capabilities"]
        return []

    def check_availability(self, model_id):
        try:
            self._fetch(IMAGE_API + urllib.parse.quote("test"), timeout=30)
            return {"available": True}
        except UPIFError as exc:
            return {"available": False, "reason": exc.code}

    # -- entitlement -----------------------------------------------------------
    def check_entitlement(self, model_id=None):
        """Pollinations has no public balance-origin endpoint.

        Keyless generation is FREE VERIFIED (no account balance consumed).
        Any *account* Pollen balance cannot be proven reward-funded, so it is
        recorded UNKNOWN and never used under FREE ONLY.
        """
        target = model_id or "flux"
        return LEDGER.record(
            target, ENT_FREE_VERIFIED,
            remaining=None, unit="credits",
            funding_source="included_daily_allowance",
            origin="keyless_free_generation",
            note="Keyless REST generation is free. Account Pollen balance origin "
                 "is not exposed by the API, so purchased Pollen is never used.",
            ttl=VERIFY_TTL_MEDIUM,
        )

    # -- generation --------------------------------------------------------------
    def _url(self, prompt, model, aspect="", ref_urls=(), seed=None, width=None,
             height=None):
        url = IMAGE_API + urllib.parse.quote(str(prompt))
        query = {}
        if model:
            query["model"] = model
        if width:
            query["width"] = width
        if height:
            query["height"] = height
        if aspect:
            query["aspect"] = aspect
        if seed is not None:
            query["seed"] = seed
        for ref in ref_urls:
            query.setdefault("ref", ref)
        if self.api_key:
            query["api_key"] = self.api_key
        if query:
            url += "?" + urllib.parse.urlencode(query)
        return url

    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        params = params or {}
        model = model_id if model_id in ("flux", "turbo") else "flux"
        url = self._url(prompt, model, params.get("aspect_ratio"),
                        seed=params.get("seed"), width=params.get("width"),
                        height=params.get("height"))
        job = new_job("pollinations", model_id, "generate_image", params, idempotency_key)
        job["status"] = "running"
        job["submitted_at"] = time.time()
        job["idempotency_key"] = idempotency_key
        data, content_type = self._fetch(url)
        if not content_type.startswith("image/"):
            job["status"] = "failed"
            job["error"] = {"code": "no_output", "message": "Pollinations returned no image."}
            return job
        job["status"] = "succeeded"
        job["outputs"] = [{"url": url, "kind": "image", "bytes": len(data)}]
        job["entitlement"] = LEDGER.current(model_id)
        job["funding_source"] = "included_daily_allowance"
        job["cost"] = 0
        job["cost_unit"] = "credits"
        LEDGER.spend(model_id, 0, job["funding_source"])
        return job

    def edit_image(self, model_id, prompt, reference_urls=(), params=None,
                   idempotency_key=None):
        refs = [u for u in (reference_urls or []) if self._public_url(u)]
        if not refs:
            raise UPIFError("references_unsupported",
                            "Pollinations keyless editing needs publicly reachable "
                            "reference URLs; uploaded private images are not accepted.", 400)
        url = self._url(prompt, EDIT_MODEL, (params or {}).get("aspect_ratio"), ref_urls=refs)
        job = new_job("pollinations", model_id, "edit_image", {"reference_urls": refs},
                      idempotency_key)
        job["status"] = "running"
        job["submitted_at"] = time.time()
        data, content_type = self._fetch(url)
        if not content_type.startswith("image/"):
            job["status"] = "failed"
            job["error"] = {"code": "no_output", "message": "Pollinations edit returned no image."}
            return job
        job["status"] = "succeeded"
        job["outputs"] = [{"url": url, "kind": "image", "bytes": len(data)}]
        job["entitlement"] = LEDGER.current(EDIT_MODEL)
        job["funding_source"] = "included_daily_allowance"
        job["cost"] = 0
        job["cost_unit"] = "credits"
        return job

    def generate_video(self, model_id, prompt, params=None, reference_urls=(),
                       idempotency_key=None):
        raise UnsupportedError("generate_video", model_id)

    def animate_image(self, model_id, reference_url, params=None, idempotency_key=None):
        raise UnsupportedError("animate_image", model_id)

    def get_status(self, job_id):
        raise UnsupportedError("get_status", job_id)

    def cancel(self, job_id):
        raise UnsupportedError("cancel", job_id)

    def retrieve_asset(self, job_id, asset_index=0):
        raise UnsupportedError("retrieve_asset", job_id)
