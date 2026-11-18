"""UPIF service facade: one normalized execution contract for every caller.

YouTube Studio, Ads Studio, agents and the API all call THIS module — never a
provider client directly. It wires the registry, adapters, router, queue,
asset library and usage ledger together and implements the Phase 2 operations:

    discover_generation_models()
    get_model_capabilities()
    check_model_availability()
    check_model_entitlement()
    generate_image()
    edit_image()
    generate_video()
    animate_image()
    get_generation_status()
    cancel_generation()
    retrieve_generated_asset()
"""

from __future__ import annotations

import time

from . import upif_assets as assets
from . import upif_entitlements as entitlement_mod
from . import upif_queue as queue_mod
from . import upif_registry as registry_mod
from . import upif_router as router_mod
from . import upif_usage as usage_mod
from .upif_base import UnsupportedError
from .upif_contracts import (
    ENT_FREE_VERIFIED, ENT_UNKNOWN, POLICY_FREE_FIRST, POLICY_FREE_ONLY,
    UPIFError, effective_entitlement, is_free, supports,
)


class UPIService:
    def __init__(self, router=None, registry=None, ledger=None, queue=None,
                 usage=None, assets_store=None):
        self.router = router or router_mod.ROUTER
        self.registry = registry or registry_mod.REGISTRY
        self.ledger = ledger or entitlement_mod.LEDGER
        self.queue = queue or queue_mod.QUEUE
        self.usage = usage or usage_mod.USAGE
        self.assets_store = assets_store or assets.ASSETS

    # -- registration ------------------------------------------------------------
    def register_adapter(self, adapter):
        router_mod.register_adapter(adapter)
        self.router.adapters[adapter.provider_id] = adapter
        self.registry.register_adapter(adapter.provider_id,
                                       adapter.discover_models())

    # -- Phase 2 operations ---------------------------------------------------------
    def discover_generation_models(self, provider_id=None, capability=None,
                                   free_only=False):
        return self.registry.models(provider_id=provider_id,
                                    capability=capability,
                                    free_only=free_only)

    def get_model_capabilities(self, model_id):
        record = self.registry.get_model(model_id)
        if not record:
            raise UPIFError("model_not_found", "Unknown model: %s" % model_id, 404)
        return {"model_id": model_id,
                "capabilities": record.get("capabilities", []),
                "input_modalities": record.get("input_modalities", []),
                "output_modalities": record.get("output_modalities", [])}

    def check_model_availability(self, model_id):
        record = self.registry.get_model(model_id)
        if not record:
            raise UPIFError("model_not_found", "Unknown model: %s" % model_id, 404)
        adapter = self.router.adapters.get(record["provider_id"])
        if not adapter:
            raise UPIFError("no_adapter", "No adapter for %s" % record["provider_id"], 400)
        return adapter.check_availability(model_id)

    def check_model_entitlement(self, model_id):
        record = self.registry.get_model(model_id)
        if not record:
            raise UPIFError("model_not_found", "Unknown model: %s" % model_id, 404)
        adapter = self.router.adapters.get(record["provider_id"])
        if not adapter:
            raise UPIFError("no_adapter", "No adapter for %s" % record["provider_id"], 400)
        result = adapter.check_entitlement(model_id)
        return {"model_id": model_id,
                "entitlement": self.ledger.current(model_id),
                "entry": result}

    def verify_entitlement(self, model_id):
        """Re-run the live entitlement probe and return the fresh classification."""
        return self.check_model_entitlement(model_id)

    # -- generation -------------------------------------------------------------------
    def _submit(self, operation, prompt, params=None, reference_urls=None,
                provider_id=None, model_id=None, policy=None, free_only=None,
                agent_id=None, studio_id=None, project_id=None,
                idempotency_key=None):
        # Apply policy state BEFORE candidate selection: FREE ONLY must gate
        # the candidate list, not just the final submission.
        if free_only is not None:
            self.router.free_only = bool(free_only)
        if policy:
            self.router.policy = policy
        candidates = self.router._candidates(
            operation,
            self.router._modalities_for(operation, reference_urls),
            provider_id, model_id, policy)
        if not candidates:
            if self.router.free_only:
                raise UPIFError(
                    "no_free_model",
                    "No eligible free model supports this request under FREE ONLY.",
                    403)
            raise UPIFError("no_model", "No programmatic model supports this request.", 404)
        chosen = candidates[0]
        queue_request = {
            "operation": operation, "prompt": prompt,
            "provider_id": chosen["provider_id"], "model_id": chosen["model_id"],
            "channel": chosen.get("channel"), "entitlement": effective_entitlement(chosen),
            "estimated_cost": chosen.get("estimated_cost"),
            "policy": policy or self.router.policy, "free_only": self.router.free_only,
            "agent_id": agent_id, "studio_id": studio_id, "project_id": project_id,
            "idempotency_key": idempotency_key,
            "reference_urls": reference_urls or [], "params": params or {},
        }
        job = self.queue.admit(queue_request)
        # Execute inline (single-process deployment); the queue owns state.
        self.queue.start(job["job_id"])
        try:
            result = self.router.execute(
                operation, prompt, params=params, reference_urls=reference_urls,
                provider_id=chosen["provider_id"], model_id=chosen["model_id"],
                policy=policy, free_only=free_only, idempotency_key=idempotency_key)
        except UPIFError as exc:
            self.queue.fail(job["job_id"], exc.code, exc.message)
            self.usage.record(job["job_id"], chosen["provider_id"],
                              chosen["model_id"], operation,
                              effective_entitlement(chosen), "none",
                              note="failed: %s" % exc.code)
            raise
        self.queue.complete(
            job["job_id"], outputs=result.get("outputs") or [],
            provider_job_id=result.get("provider_job_id"),
            actual_cost=result.get("cost"), entitlement=result.get("entitlement"),
            funding_source=result.get("funding_source"))
        # Normalize the entitlement on the returned job from the ledger — the
        # adapter may have probed after submission.
        if not result.get("entitlement") or result["entitlement"] == ENT_UNKNOWN:
            result["entitlement"] = self.ledger.current(chosen["model_id"])
        self.usage.record(job["job_id"], chosen["provider_id"], chosen["model_id"],
                          operation, result.get("entitlement") or "unknown",
                          result.get("funding_source") or "none",
                          estimated_cost=chosen.get("estimated_cost"),
                          actual_cost=result.get("cost"),
                          cost_unit=result.get("cost_unit", ""))
        result["job_id"] = job["job_id"]
        result["queue_status"] = "succeeded"
        return result

    def generate_image(self, prompt, **kwargs):
        return self._submit("generate_image", prompt, **kwargs)

    def edit_image(self, prompt, reference_urls=(), **kwargs):
        return self._submit("edit_image", prompt,
                            reference_urls=reference_urls, **kwargs)

    def generate_video(self, prompt, **kwargs):
        return self._submit("generate_video", prompt, **kwargs)

    def animate_image(self, reference_url, prompt="animate", **kwargs):
        return self._submit("animate_image", prompt,
                            reference_urls=[reference_url], **kwargs)

    # -- job control ---------------------------------------------------------------------
    def get_generation_status(self, job_id):
        return self.queue.get(job_id)

    def cancel_generation(self, job_id):
        return self.queue.cancel(job_id)

    def retrieve_generated_asset(self, job_id, asset_index=0):
        job = self.queue.get(job_id)
        if not job:
            raise UPIFError("job_not_found", "Unknown job: %s" % job_id, 404)
        outputs = job.get("outputs") or []
        if asset_index >= len(outputs):
            raise UPIFError("asset_not_found", "Job %s has no output %d."
                            % (job_id, asset_index), 404)
        return outputs[asset_index]

    # -- dashboard -------------------------------------------------------------------------
    def dashboard(self):
        providers = self.registry.providers()
        models = self.registry.models()
        free_models = [m for m in models if is_free(m)]
        return {
            "policy": self.router.policy,
            "free_only": self.router.free_only,
            "providers": providers,
            "image_models": [m for m in models if supports(m, "text_to_image")],
            "video_models": [m for m in models if supports(m, "text_to_video")],
            "free_models": free_models,
            "queue": self.queue.stats(),
            "recent_usage": self.usage.records(limit=20),
            "jobs": self.queue.list(limit=10),
            "entitlements": self.ledger.summary(),
        }


SERVICE = UPIService()
