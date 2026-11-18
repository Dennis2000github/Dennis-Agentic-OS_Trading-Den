"""UPIF Capability-Based Generation Router (Phase 4).

The Generation Router is separate from the Agent Router. The Agent Router
picks the agent, framework and task; this router picks the *generation model*
that may execute the request.

Responsibilities:

* discover eligible models (dynamic, via adapters);
* match model capabilities explicitly (no inheritance across capabilities);
* validate authentication and verify entitlements per request;
* check quotas / remaining balance;
* rank models by policy;
* execute; monitor; perform compatible failover; return assets.

Policies: FREE FIRST (default) prefers verified free models and never falls
back to paid silently; FREE ONLY is a hard spending safeguard that rejects
any non-free model with a structured error; QUALITY FIRST / LOWEST COST /
FASTEST AVAILABLE rank by metadata; MANUAL MODEL / MANUAL PROVIDER pin the
selection and still respect FREE ONLY; LOCAL FIRST prefers local runtimes.
"""

from __future__ import annotations

import time

from . import upif_entitlements as entitlement_mod
from .upif_contracts import (
    CAP_IMAGE_EDIT, CAP_IMAGE_TO_IMAGE, CAP_IMAGE_TO_VIDEO, CAP_TEXT_TO_IMAGE,
    CAP_TEXT_TO_VIDEO, ENT_FREE_VERIFIED, ENT_FREE_TRIAL, ENT_PAID_API,
    ENT_PAID_SUBSCRIPTION, ENT_USER_FUNDED, FREE_ENTITLEMENTS, POLICY_FASTEST,
    POLICY_FREE_FIRST, POLICY_FREE_ONLY, POLICY_LOCAL_FIRST, POLICY_LOWEST_COST,
    POLICY_MANUAL_MODEL, POLICY_MANUAL_PROVIDER, POLICY_QUALITY_FIRST,
    UPIFError, effective_entitlement, is_free, modality_match, supports,
)
from .upif_entitlements import LEDGER
from .upif_registry import REGISTRY

#: Adapter registry — the router never talks to a provider directly.
ADAPTERS = {}


def register_adapter(adapter):
    ADAPTERS[adapter.provider_id] = adapter
    return adapter


class GenerationRouter:
    def __init__(self, adapters=None, registry=None, ledger=None):
        self.adapters = dict(adapters or ADAPTERS)
        self.registry = registry or REGISTRY
        self.ledger = ledger or LEDGER
        self.policy = POLICY_FREE_FIRST
        self.free_only = False
        self.history = []

    # -- policy -----------------------------------------------------------------
    def set_policy(self, policy, free_only=False):
        if policy not in (POLICY_FREE_FIRST, POLICY_FREE_ONLY, POLICY_QUALITY_FIRST,
                          POLICY_LOWEST_COST, POLICY_FASTEST, POLICY_MANUAL_MODEL,
                          POLICY_MANUAL_PROVIDER, POLICY_LOCAL_FIRST):
            raise UPIFError("bad_policy", "Unknown routing policy: %s" % policy, 400)
        self.policy = policy
        self.free_only = bool(free_only)

    # -- candidate selection -------------------------------------------------------
    def discover(self, capability=None, provider_id=None):
        return self.registry.models(capability=capability, provider_id=provider_id)

    def _candidates(self, operation, modalities, provider_id=None, model_id=None,
                    policy=None):
        """Eligible models for an operation, ranked by the active policy."""
        policy = policy or self.policy
        cap_needed = self._capability_for(operation)
        entries = self.discover(capability=cap_needed, provider_id=provider_id)
        if model_id:
            entries = [e for e in entries if e["model_id"] == model_id]
        # Explicit capability + modality match; never infer sibling caps.
        entries = [e for e in entries
                   if supports(e, cap_needed) and modality_match(e, modalities)]
        # Programmatic route only; website-only services are never routed.
        entries = [e for e in entries if e.get("programmatic") and not e.get("website_only")]
        # FREE ONLY is a hard gate.
        if self.free_only or policy == POLICY_FREE_ONLY:
            free_entries = [e for e in entries if is_free(e)]
            if not free_entries and entries:
                # Models existed but none were verified free: report the paid
                # exclusion explicitly instead of a generic "no model".
                raise UPIFError(
                    "free_only_blocked",
                    "FREE ONLY: %s exists but is not a verified free model "
                    "(entitlement: %s). No paid request will be submitted."
                    % (entries[0]["model_id"], effective_entitlement(entries[0])), 403)
            entries = free_entries
        return self._rank(entries, policy)

    def _capability_for(self, operation):
        return {
            "generate_image": CAP_TEXT_TO_IMAGE,
            "edit_image": CAP_IMAGE_EDIT,
            "image_to_image": CAP_IMAGE_TO_IMAGE,
            "generate_video": CAP_TEXT_TO_VIDEO,
            "animate_image": CAP_IMAGE_TO_VIDEO,
        }.get(operation, "")

    def _rank(self, entries, policy):
        if policy == POLICY_QUALITY_FIRST:
            return sorted(entries, key=lambda e: (e.get("quality_rank") or 0), reverse=True)
        if policy == POLICY_LOWEST_COST:
            return sorted(entries, key=lambda e: (e.get("estimated_cost")
                                                  if e.get("estimated_cost") is not None
                                                  else float("inf")))
        if policy == POLICY_FASTEST:
            return sorted(entries, key=lambda e: (e.get("latency_rank") or 99))
        if policy == POLICY_LOCAL_FIRST:
            return sorted(entries, key=lambda e: (0 if e.get("channel") == "local" else 1))
        # FREE FIRST / MANUAL / default: free models first, then availability.
        return sorted(entries, key=lambda e: (0 if is_free(e) else 1,
                                              0 if e.get("available") else 1))

    # -- entitlement verification ---------------------------------------------------
    def verify_entitlement(self, model_id, provider_id=None):
        """Ask the provider adapter for a live entitlement probe."""
        entry = self.registry.get_model(model_id)
        if not entry:
            raise UPIFError("model_not_found", "Unknown model: %s" % model_id, 404)
        adapter = self.adapters.get(entry["provider_id"])
        if not adapter:
            raise UPIFError("no_adapter", "No adapter for %s" % entry["provider_id"], 400)
        return adapter.check_entitlement(model_id)

    # -- execution ---------------------------------------------------------------------
    def _run(self, operation, model_entry, prompt, params, reference_urls,
             idempotency_key):
        adapter = self.adapters.get(model_entry["provider_id"])
        if not adapter:
            raise UPIFError("no_adapter", "No adapter for %s" % model_entry["provider_id"], 400)
        # Re-verify entitlement immediately before submission (FREE ONLY safety).
        current = effective_entitlement(model_entry)
        if self.free_only and current not in FREE_ENTITLEMENTS:
            raise UPIFError(
                "free_only_blocked",
                "FREE ONLY: %s/%s is not verified free (entitlement: %s). No paid "
                "request will be submitted." % (model_entry["provider_id"],
                                                model_entry["model_id"], current), 403)
        if self.ledger.is_exhausted(model_entry["model_id"]):
            raise UPIFError("quota_exhausted",
                            "Free quota for %s is exhausted." % model_entry["model_id"], 403)
        kwargs = {"idempotency_key": idempotency_key}
        if operation == "generate_image":
            return adapter.generate_image(model_entry["model_id"], prompt, params,
                                          idempotency_key)
        if operation == "edit_image":
            return adapter.edit_image(model_entry["model_id"], prompt, reference_urls,
                                      params, idempotency_key)
        if operation == "generate_video":
            return adapter.generate_video(model_entry["model_id"], prompt, params,
                                          reference_urls, idempotency_key)
        if operation == "animate_image":
            return adapter.animate_image(model_entry["model_id"], reference_urls[0] if
                                         reference_urls else None, params,
                                         idempotency_key)
        raise UPIFError("unknown_operation", "Unknown operation: %s" % operation, 400)

    def execute(self, operation, prompt, params=None, reference_urls=None,
                provider_id=None, model_id=None, policy=None, free_only=None,
                idempotency_key=None):
        """Route and execute one generation request with compatible failover.

        Failover preserves the original prompt, reference assets, aspect ratio,
        dimensions, style and output requirements; it only swaps in a model
        that supports the same capability and modalities.
        """
        if free_only is not None:
            self.free_only = bool(free_only)
        modalities = self._modalities_for(operation, reference_urls)
        candidates = self._candidates(operation, modalities, provider_id, model_id,
                                      policy)
        if not candidates:
            if self.free_only:
                raise UPIFError(
                    "no_free_model",
                    "No eligible free model supports this request under FREE ONLY. "
                    "No paid fallback will be attempted.", 403)
            raise UPIFError("no_model",
                            "No programmatic model supports this request.", 404)

        errors = []
        for entry in candidates:
            try:
                job = self._run(operation, entry, prompt, params,
                                reference_urls or [], idempotency_key)
            except UPIFError as exc:
                errors.append({"model_id": entry["model_id"],
                               "provider_id": entry["provider_id"],
                               "code": exc.code, "message": exc.message})
                self.registry.mark_unavailable(entry["model_id"], exc.code)
                continue
            if job.get("status") in ("succeeded", "running", "queued"):
                self.history.append({
                    "operation": operation, "model_id": entry["model_id"],
                    "provider_id": entry["provider_id"],
                    "policy": self.policy, "free_only": self.free_only,
                    "status": job.get("status"), "at": time.time(),
                })
                return self._enrich(job, entry, candidates)
            errors.append({"model_id": entry["model_id"],
                           "provider_id": entry["provider_id"],
                           "code": job.get("error", {}).get("code", "failed"),
                           "message": job.get("error", {}).get("message", "failed")})
        raise UPIFError(
            "routing_failed",
            "Every eligible model failed under policy %s: %s"
            % (self.policy, errors[:3]), 502, detail={"failures": errors})

    def _modalities_for(self, operation, reference_urls):
        modalities = {"text"}
        if operation in ("edit_image", "image_to_image") and reference_urls:
            modalities.add("image")
        if operation == "animate_image":
            modalities.add("image")
        return modalities

    def _enrich(self, job, model_entry, candidates):
        job["provider_id"] = model_entry["provider_id"]
        job["model_id"] = model_entry["model_id"]
        job["channel"] = model_entry.get("channel")
        job["entitlement"] = model_entry.get("entitlement")
        job["tried"] = [c["model_id"] for c in candidates]
        return job

    # -- monitoring ---------------------------------------------------------------------
    def get_status(self, provider_id, job_id):
        adapter = self.adapters.get(provider_id)
        if not adapter:
            raise UPIFError("no_adapter", "No adapter for %s" % provider_id, 400)
        return adapter.get_status(job_id)

    def cancel(self, provider_id, job_id):
        adapter = self.adapters.get(provider_id)
        if not adapter:
            raise UPIFError("no_adapter", "No adapter for %s" % provider_id, 400)
        return adapter.cancel(job_id)

    def retrieve_asset(self, provider_id, job_id, asset_index=0):
        adapter = self.adapters.get(provider_id)
        if not adapter:
            raise UPIFError("no_adapter", "No adapter for %s" % provider_id, 400)
        return adapter.retrieve_asset(job_id, asset_index)


ROUTER = GenerationRouter()
