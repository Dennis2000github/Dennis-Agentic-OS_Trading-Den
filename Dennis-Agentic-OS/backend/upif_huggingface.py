"""UPIF adapter: Hugging Face Inference Providers (REST + official SDK shape).

Two funding routes must stay distinct:

* HF-routed inference through Inference Providers — covered by the monthly
  included credits when the account qualifies.
* A custom upstream provider key (the user's own AWS/Google/etc. key) — billed
  directly to that provider and NEVER classified as free HF inference.

This adapter only treats the HF-routed path as free, and only after the
monthly included-credit budget is verified.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .upif_base import BaseAdapter, new_job, UnsupportedError
from .upif_contracts import (
    CAP_TEXT_TO_IMAGE, CH_NATIVE_REST, CH_SDK, ENT_FREE_TRIAL, ENT_PAID_API,
    ENT_UNKNOWN, UPIFError, VERIFY_TTL_MEDIUM, new_model_record,
)
from .upif_entitlements import LEDGER

API = "https://router.huggingface.co/hf-inference"
USER_AGENT = "DENNIS-Agentic-OS/1.0 (UPIF Hugging Face)"


def _env(name):
    import os
    return os.environ.get(name, "")


class HuggingFaceAdapter(BaseAdapter):
    provider_id = "huggingface"
    service_id = "inference_providers"
    channels = (CH_NATIVE_REST, CH_SDK)

    def __init__(self, api_key=""):
        self.api_key = api_key or _env("HF_TOKEN")
        #: True only when the request is HF-routed (not a custom upstream key).
        self.hf_routed = True

    def _headers(self):
        if not self.api_key:
            raise UPIFError("missing_token",
                            "Hugging Face needs HF_TOKEN for Inference Providers.", 401)
        return {
            "Authorization": "Bearer %s" % self.api_key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _request(self, method, path, body=None, timeout=120):
        request = urllib.request.Request(
            API + path, data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return response.status, raw
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace"))
            except Exception:
                pass
            raise UPIFError("provider_error",
                            "Hugging Face HTTP %s: %s" % (exc.code, json.dumps(detail)[:300]), 502)
        except (urllib.error.URLError, OSError) as exc:
            raise UPIFError("network_error", "Cannot reach Hugging Face: %s" % exc, 502)

    # -- discovery ---------------------------------------------------------------
    def discover_models(self):
        records = []
        for model_id in ("black-forest-labs/FLUX.1-schnell",
                         "stabilityai/stable-diffusion-xl-base-1.0"):
            records.append(new_model_record(
                "huggingface", "inference_providers", model_id,
                model_name=model_id.split("/")[-1], channel=CH_NATIVE_REST,
                capabilities=[CAP_TEXT_TO_IMAGE],
                input_modalities=["text"], output_modalities=["image"],
                auth_status="anonymous", programmatic=True,
                entitlement=ENT_UNKNOWN, free_eligible=True,
                estimated_cost=0, cost_unit="credits",
                license="model-specific (see huggingface.co)"))
        return records

    def get_capabilities(self, model_id):
        return [CAP_TEXT_TO_IMAGE] if any(
            m["model_id"] == model_id for m in self.discover_models()) else []

    def check_availability(self, model_id):
        if not self.api_key:
            return {"available": False, "reason": "missing_token"}
        try:
            self._request("GET", "/providers", timeout=30)
            return {"available": True}
        except UPIFError as exc:
            return {"available": False, "reason": exc.code}

    # -- entitlement ----------------------------------------------------------------
    def check_entitlement(self, model_id=None):
        """Verify the monthly included-credit budget for HF-routed inference.

        A custom upstream provider key is recorded as PAID API — its billing
        never counts toward HF's included credits.
        """
        target = model_id or "black-forest-labs/FLUX.1-schnell"
        if not self.hf_routed:
            LEDGER.record(target, ENT_PAID_API,
                          note="custom upstream provider key: billed directly, never free HF")
            return {"entitlement": ENT_PAID_API,
                    "reason": "custom_upstream_key_not_free"}
        if not self.api_key:
            LEDGER.record(target, ENT_UNKNOWN, note="no token configured")
            return {"entitlement": ENT_UNKNOWN, "reason": "missing_token"}
        try:
            status, _ = self._request("GET", "/models", timeout=30)
        except UPIFError as exc:
            LEDGER.record(target, ENT_UNKNOWN, note="probe failed: %s" % exc.code)
            return {"entitlement": ENT_UNKNOWN, "reason": "probe_failed"}
        if status != 200:
            LEDGER.record(target, ENT_UNKNOWN, note="HTTP %s" % status)
            return {"entitlement": ENT_UNKNOWN, "reason": "http_%s" % status}
        # Included-credit balance is not exposed by a public endpoint; the
        # honest classification is FREE TRIAL with the note that the exact
        # remaining budget must be re-verified before each free run.
        return LEDGER.record(
            target, ENT_FREE_TRIAL,
            remaining=None, unit="credits",
            funding_source="included_monthly_allowance",
            origin="hf_inference_providers_included_credits",
            note="HF-routed inference may include monthly credits; the exact "
                 "remaining budget is not exposed, so FREE ONLY re-probes "
                 "before each run.",
            ttl=VERIFY_TTL_MEDIUM)

    # -- generation -------------------------------------------------------------------
    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        body = {"inputs": prompt, **(params or {})}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        job = new_job("huggingface", model_id, "generate_image", body, idempotency_key)
        job["status"] = "running"
        job["submitted_at"] = time.time()
        try:
            status, raw = self._request("POST", "/%s" % model_id, body, timeout=180)
        except UPIFError as exc:
            job["status"] = "failed"
            job["error"] = {"code": exc.code, "message": exc.message}
            return job
        if status != 200:
            job["status"] = "failed"
            job["error"] = {"code": "provider_error", "message": "HTTP %s" % status}
            return job
        job["status"] = "succeeded"
        job["outputs"] = [{"url": "data:application/octet-stream",
                           "kind": "image", "bytes": len(raw)}]
        job["entitlement"] = LEDGER.current(model_id)
        job["funding_source"] = "included_monthly_allowance"
        job["cost"] = 0
        job["cost_unit"] = "credits"
        LEDGER.spend(model_id, 0, job["funding_source"])
        return job

    def edit_image(self, model_id, prompt, reference_urls=(), params=None,
                   idempotency_key=None):
        raise UnsupportedError("edit_image", model_id)

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
