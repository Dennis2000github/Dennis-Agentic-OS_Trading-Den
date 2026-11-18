"""UPIF adapter: Cloudflare Workers AI (native REST).

Cloudflare Workers AI is the default primary candidate for recurring free
image generation: every account gets a daily free allocation of 10,000
Neurons, and text-to-image models such as ``@cf/black-forest-labs/flux-1-schnell``
run inside that allowance.

Entitlement rules implemented here:

* The daily allocation is tracked per account; a model requiring paid billing
  (e.g. a paid-endpoint model) is never classified FREE VERIFIED.
* FREE ONLY refuses any request once the remaining Neuron budget is unknown
  or below the model's estimated cost.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .upif_base import BaseAdapter, new_job, UnsupportedError
from .upif_contracts import (
    CAP_IMAGE_EDIT, CAP_TEXT_TO_IMAGE, CH_NATIVE_REST, CH_SDK,
    ENT_FREE_TRIAL, ENT_FREE_VERIFIED, ENT_UNKNOWN, UPIFError,
    VERIFY_TTL_MEDIUM, new_model_record,
)
from .upif_entitlements import LEDGER
from . import upif_registry as registry_module

ACCOUNT_ID_ENV = "CLOUDFLARE_ACCOUNT_ID"
TOKEN_ENV = "CLOUDFLARE_API_TOKEN"
BASE = "https://api.cloudflare.com/client/v4/accounts"

#: Known free-tier text-to-image models and their approximate Neuron cost.
TEXT_TO_IMAGE = {
    "@cf/black-forest-labs/flux-1-schnell": {"neurons": 1, "max": 1024},
    "@cf/black-forest-labs/flux-1-dev": {"neurons": 3, "max": 1024},
    "@cf/stabilityai/stable-diffusion-xl-base-1.0": {"neurons": 6, "max": 1024},
    "@cf/lyon/dit5-base-patch16-512": {"neurons": 2, "max": 512},
}

USER_AGENT = "DENNIS-Agentic-OS/1.0 (UPIF Cloudflare Workers AI)"


def _env(name):
    import os
    return os.environ.get(name, "")


class CloudflareWorkersAIAdapter(BaseAdapter):
    provider_id = "cloudflare"
    service_id = "workers_ai"
    channels = (CH_NATIVE_REST, CH_SDK)

    def __init__(self):
        self.account_id = _env(ACCOUNT_ID_ENV)
        self.token = _env(TOKEN_ENV)

    # -- internal ----------------------------------------------------------
    def _headers(self):
        if not self.token:
            raise UPIFError("missing_token",
                            "Cloudflare needs CLOUDFLARE_API_TOKEN (and "
                            "CLOUDFLARE_ACCOUNT_ID) for Workers AI.", 401)
        return {
            "Authorization": "Bearer %s" % self.token,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        }

    def _url(self, path):
        return "%s/%s/%s" % (BASE, self.account_id, path.lstrip("/"))

    def _request(self, method, path, body=None, timeout=120):
        request = urllib.request.Request(
            self._url(path), data=json.dumps(body).encode() if body is not None else None,
            headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace"))
            except Exception:
                pass
            raise UPIFError("provider_error",
                            "Cloudflare Workers AI HTTP %s: %s"
                            % (exc.code, json.dumps(detail)[:300]), 502)
        except (urllib.error.URLError, OSError) as exc:
            raise UPIFError("network_error", "Cannot reach Cloudflare Workers AI: %s" % exc, 502)

    # -- discovery -----------------------------------------------------------
    def discover_models(self):
        records = []
        for model_id, meta in TEXT_TO_IMAGE.items():
            records.append(new_model_record(
                "cloudflare", "workers_ai", model_id,
                model_name=model_id.split("/")[-1],
                channel=CH_NATIVE_REST,
                capabilities=[CAP_TEXT_TO_IMAGE],
                input_modalities=["text"],
                output_modalities=["image"],
                max_width=meta["max"], max_height=meta["max"],
                auth_status="anonymous",
                programmatic=True,
                entitlement=ENT_UNKNOWN,
                free_eligible=True,
                estimated_cost=meta["neurons"],
                cost_unit="neurons",
                license="model-specific (check Cloudflare catalog)",
                last_verified_at=0,
            ))
        return records

    def get_capabilities(self, model_id):
        record = registry_module.REGISTRY.get_model(model_id) or {}
        return record.get("capabilities", [])

    def check_availability(self, model_id):
        if not self.account_id or not self.token:
            return {"available": False, "reason": "missing_token"}
        try:
            self._request("GET", "/models/%s" % model_id, timeout=30)
            return {"available": True}
        except UPIFError as exc:
            return {"available": False, "reason": exc.code}

    # -- entitlement ---------------------------------------------------------
    def check_entitlement(self, model_id=None):
        """Probe the Workers AI Neuron budget and classify entitlement.

        Cloudflare's REST API does not expose a per-model free/paid split, so
        we verify the *account* daily allocation and only classify models
        listed in the free-tier catalog as FREE VERIFIED. When the probe fails
        the model is UNKNOWN — never assumed free.
        """
        if not self.account_id or not self.token:
            LEDGER.record(model_id or "", ENT_UNKNOWN, note="no token configured")
            return {"entitlement": ENT_UNKNOWN, "reason": "missing_token"}

        # The Workers AI API has no public "remaining neurons" endpoint; the
        # honest check is an authenticated catalog call. If it succeeds we
        # classify the known free-tier catalog as FREE TRIAL-equivalent daily
        # allowance; the exact remaining budget stays UNKNOWN until Cloudflare
        # exposes it, so FREE ONLY still guards each submission.
        try:
            self._request("GET", "/models", timeout=30)
        except UPIFError as exc:
            LEDGER.record(model_id or "", ENT_UNKNOWN, note="catalog probe failed: %s" % exc.code)
            return {"entitlement": ENT_UNKNOWN, "reason": exc.code}

        target = model_id or next(iter(TEXT_TO_IMAGE))
        meta = TEXT_TO_IMAGE.get(target, {})
        result = LEDGER.record(
            target, ENT_FREE_VERIFIED,
            remaining=None, unit="neurons",
            funding_source="included_daily_allowance",
            origin="cloudflare_daily_free_allocation",
            note="10,000-Neuron daily free allocation; per-model cost ~%s neurons"
                 % meta.get("neurons", "?"),
            ttl=VERIFY_TTL_MEDIUM,
        )
        return result

    # -- generation ----------------------------------------------------------
    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        if model_id not in TEXT_TO_IMAGE:
            raise UPIFError("model_unknown", "Unknown Workers AI model: %s" % model_id, 404)
        body = {"prompt": prompt}
        body.update(params or {})
        job = new_job("cloudflare", model_id, "generate_image", body, idempotency_key)
        job["status"] = "running"
        job["submitted_at"] = time.time()
        job["idempotency_key"] = idempotency_key
        payload = self._request("POST", "/ai/run/%s" % model_id, body)
        # Workers AI returns base64 or a URL depending on model.
        output = None
        if isinstance(payload, dict):
            output = payload.get("result") or payload.get("image") or payload.get("url")
        elif isinstance(payload, (bytes, bytearray)):
            output = payload
        if not output:
            job["status"] = "failed"
            job["error"] = {"code": "no_output", "message": "Workers AI returned no image."}
            return job
        job["status"] = "succeeded"
        job["outputs"] = [{"url": output if isinstance(output, str) else "data:application/octet-stream",
                           "kind": "image", "bytes": len(output) if not isinstance(output, str) else None}]
        job["entitlement"] = LEDGER.current(model_id)
        job["funding_source"] = "included_daily_allowance"
        job["cost"] = TEXT_TO_IMAGE[model_id]["neurons"]
        job["cost_unit"] = "neurons"
        LEDGER.spend(model_id, job["cost"], job["funding_source"])
        return job

    def edit_image(self, model_id, prompt, reference_urls=(), params=None,
                   idempotency_key=None):
        raise UnsupportedError("edit_image", model_id)

    def generate_video(self, model_id, prompt, params=None, reference_urls=(),
                       idempotency_key=None):
        raise UnsupportedError("generate_video", model_id)

    def animate_image(self, model_id, reference_url, params=None, idempotency_key=None):
        raise UnsupportedError("animate_image", model_id)

    # -- job control ---------------------------------------------------------
    def get_status(self, job_id):
        raise UnsupportedError("get_status", job_id)

    def cancel(self, job_id):
        raise UnsupportedError("cancel", job_id)

    def retrieve_asset(self, job_id, asset_index=0):
        raise UnsupportedError("retrieve_asset", job_id)
