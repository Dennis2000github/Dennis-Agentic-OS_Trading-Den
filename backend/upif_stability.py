"""UPIF adapter: Stability AI Developer API (native REST).

Stability AI offers initial free credits as a limited trial entitlement.
This adapter:

* verifies the credit balance before submission;
* tracks remaining free credits and refuses to exceed them;
* never auto-purchases credits; a zero balance is an exclusion, not a
  silent switch to paid billing;
* records the per-operation credit estimate with each job.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .upif_base import BaseAdapter, new_job, UnsupportedError
from .upif_contracts import (
    CAP_IMAGE_EDIT, CAP_TEXT_TO_IMAGE, CH_NATIVE_REST, ENT_FREE_TRIAL,
    ENT_UNKNOWN, UPIFError, VERIFY_TTL_SHORT, new_model_record,
)
from .upif_entitlements import LEDGER

API = "https://api.stability.ai/v2beta"
USER_AGENT = "DENNIS-Agentic-OS/1.0 (UPIF Stability AI)"

#: Model -> per-operation credit estimate (free-tier units).
MODEL_COSTS = {
    "stable-image-ultra": 1,
    "stable-image-core": 1,
    "stable-image-fast": 1,
}


def _env(name):
    import os
    return os.environ.get(name, "")


class StabilityAdapter(BaseAdapter):
    provider_id = "stability"
    service_id = "developer_api"
    channels = (CH_NATIVE_REST,)

    def __init__(self, api_key=""):
        self.api_key = api_key or _env("STABILITY_API_KEY")

    # -- internal ------------------------------------------------------------
    def _headers(self):
        if not self.api_key:
            raise UPIFError("missing_token",
                            "Stability AI needs STABILITY_API_KEY.", 401)
        return {
            "Authorization": "Bearer %s" % self.api_key,
            "User-Agent": USER_AGENT,
        }

    def _request(self, method, path, body=None, timeout=120, raw=False):
        request = urllib.request.Request(
            API + path, data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw_data = response.read()
                if raw:
                    return response.status, raw_data
                return response.status, json.loads(raw_data) if raw_data else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace"))
            except Exception:
                pass
            raise UPIFError("provider_error",
                            "Stability AI HTTP %s: %s" % (exc.code, json.dumps(detail)[:300]), 502)
        except (urllib.error.URLError, OSError) as exc:
            raise UPIFError("network_error", "Cannot reach Stability AI: %s" % exc, 502)

    # -- discovery ---------------------------------------------------------------
    def discover_models(self):
        records = []
        for model_id in MODEL_COSTS:
            records.append(new_model_record(
                "stability", "developer_api", model_id,
                model_name=model_id, channel=CH_NATIVE_REST,
                capabilities=[CAP_TEXT_TO_IMAGE],
                input_modalities=["text"], output_modalities=["image"],
                auth_status="anonymous", programmatic=True,
                entitlement=ENT_UNKNOWN, free_eligible=True,
                estimated_cost=MODEL_COSTS[model_id], cost_unit="credits",
                license="Stability AI Community License"))
        return records

    def get_capabilities(self, model_id):
        return [CAP_TEXT_TO_IMAGE] if model_id in MODEL_COSTS else []

    def check_availability(self, model_id):
        if not self.api_key:
            return {"available": False, "reason": "missing_token"}
        try:
            self._request("GET", "/v2beta/models", timeout=30)
            return {"available": True}
        except UPIFError as exc:
            return {"available": False, "reason": exc.code}

    # -- entitlement -----------------------------------------------------------------
    def check_entitlement(self, model_id=None):
        """Verify the remaining free-credit balance (trial entitlement)."""
        target = model_id or "stable-image-ultra"
        if not self.api_key:
            LEDGER.record(target, ENT_UNKNOWN, note="no token configured")
            return {"entitlement": ENT_UNKNOWN, "reason": "missing_token"}
        try:
            status, account = self._request("GET", "/account/balance", timeout=30)
        except UPIFError as exc:
            LEDGER.record(target, ENT_UNKNOWN, note="balance probe failed: %s" % exc.code)
            return {"entitlement": ENT_UNKNOWN, "reason": "probe_failed"}
        remaining = account.get("credits") if isinstance(account, dict) else None
        free = (remaining is not None and float(remaining) > 0)
        entry = LEDGER.record(
            target, ENT_FREE_TRIAL if free else ENT_UNKNOWN,
            remaining=remaining, unit="credits",
            funding_source="promotional_credit",
            origin="stability_initial_free_credits",
            note="Initial free credits are a limited trial. Remaining: %s. "
                 "Never auto-purchased." % remaining,
            ttl=VERIFY_TTL_SHORT)
        if not free:
            LEDGER.exhaust(target, "free credits exhausted")
        return entry

    # -- generation ---------------------------------------------------------------------
    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        if model_id not in MODEL_COSTS:
            raise UPIFError("model_unknown", "Unknown Stability model: %s" % model_id, 404)
        if LEDGER.is_exhausted(model_id):
            raise UPIFError("free_credits_exhausted",
                            "Stability AI free credits for %s are exhausted; FREE ONLY "
                            "will not submit this model." % model_id, 403)
        cost = MODEL_COSTS[model_id]
        body = {"model": model_id, "prompt": prompt, **(params or {})}
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        job = new_job("stability", model_id, "generate_image", body, idempotency_key)
        job["status"] = "running"
        job["submitted_at"] = time.time()
        try:
            status, result = self._request("POST", "/v2beta/stable-image/generate",
                                           body, timeout=180, raw=True)
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
                           "kind": "image", "bytes": len(result)}]
        job["entitlement"] = LEDGER.current(model_id)
        job["funding_source"] = "promotional_credit"
        job["cost"] = cost
        job["cost_unit"] = "credits"
        LEDGER.spend(model_id, cost, job["funding_source"])
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
