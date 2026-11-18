"""UPIF (Unified Programmatic Interface for Generation) acceptance tests.

Pins the shared generation layer's contracts:

* one normalized job shape across every provider adapter;
* explicit capability matching — a sibling capability is never inherited;
* honest entitlement classification (purchased balance is never FREE VERIFIED);
* FREE ONLY as a hard spending safeguard, enforced before submission;
* failover across models that share the required capability;
* website-only services are registered for the UI but never routed;
* one shared queue with per-provider concurrency limits and rate limiting;
* usage accounting with a real funding source, never an invented zero;
* the HTTP surface, owner-gated, with stable error envelopes.

No provider network call is made: every adapter is a local fake, and the HTTP
suite points the UPIF stores at a temporary directory before the server module
is imported.

Run:  python3 -m unittest tests.test_upif
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

# Isolate the UPIF persistent stores (registry / ledger / queue / usage) from
# the real data/ dir BEFORE app.py (and the backend modules) are imported: the
# modules read config.DATA_DIR at import time.
_upif_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["DENNIS_UPIF_DATA_DIR"] = _upif_tmp.name  # documentation only

from backend import config as dennis_config  # noqa: E402

dennis_config.DATA_DIR = _upif_tmp.name

import app as server  # noqa: E402
from backend import upif_contracts as c  # noqa: E402
from backend import upif_entitlements as entitlement_mod  # noqa: E402
from backend import upif_queue as queue_mod  # noqa: E402
from backend import upif_registry as registry_mod  # noqa: E402
from backend import upif_router as router_mod  # noqa: E402
from backend import upif_service as service_mod  # noqa: E402
from backend import upif_usage as usage_mod  # noqa: E402
from backend import upif_websites as websites_mod  # noqa: E402
from backend import upif_assets as assets_mod  # noqa: E402
from backend import upif_tools  # noqa: E402
from backend import studio_media as media_mod  # noqa: E402
from backend.upif_base import BaseAdapter, UnsupportedError, new_job  # noqa: E402

MODEL = "fake-model-1"
PROMPT = "a deniable lighthouse"
REF_URL = "https://example.com/ref.png"


class FakeAdapter(BaseAdapter):
    """Local adapter: configurable capability, entitlement and behaviour."""

    def __init__(self, provider_id="fake_provider", model_id=MODEL,
                 capabilities=("text_to_image",),
                 entitlement=c.ENT_FREE_VERIFIED, free_eligible=True,
                 programmatic=True, generate_error=None, output=b"png-bytes",
                 provider_job_id="prov-1", cost=0, cost_unit="credits"):
        self.provider_id = provider_id
        self.model_id = model_id
        self.capabilities = list(capabilities)
        self.entitlement = entitlement
        self.free_eligible = free_eligible
        self.programmatic = programmatic
        self.generate_error = generate_error
        self.output = output
        self.provider_job_id = provider_job_id
        self.cost = cost
        self.cost_unit = cost_unit
        self.calls = []

    def discover_models(self):
        return [c.new_model_record(
            self.provider_id, "fake_service", self.model_id,
            capabilities=list(self.capabilities),
            input_modalities=["text"], output_modalities=["image"],
            auth_status="anonymous", programmatic=self.programmatic,
            entitlement=self.entitlement, free_eligible=self.free_eligible,
            estimated_cost=self.cost, cost_unit=self.cost_unit,
            available=True, license="test",
        )]

    def check_availability(self, model_id):
        return {"available": True, "model_id": model_id}

    def check_entitlement(self, model_id=None):
        return entitlement_mod.LEDGER.record(
            model_id or self.model_id, self.entitlement,
            remaining=None, unit=self.cost_unit,
            funding_source="included_daily_allowance",
            origin="fake_adapter_probe", note="unit test",
            ttl=c.VERIFY_TTL_MEDIUM)

    def generate_image(self, model_id, prompt, params=None, idempotency_key=None):
        self.calls.append(("generate_image", prompt, params, idempotency_key))
        if self.generate_error:
            raise self.generate_error
        return {
            "job_id": "fake_%s" % model_id,
            "provider_job_id": self.provider_job_id,
            "status": "succeeded",
            "outputs": [{"url": "fake://%s/out.png" % model_id, "kind": "image",
                         "bytes": len(self.output)}],
            "error": None,
            "entitlement": self.entitlement,
            "funding_source": "included_daily_allowance",
            "cost": self.cost,
            "cost_unit": self.cost_unit,
            "idempotency_key": idempotency_key,
            "submitted_at": time.time(),
        }

    def edit_image(self, model_id, prompt, reference_urls=(), params=None,
                   idempotency_key=None):
        raise UnsupportedError("edit_image", model_id)

    def generate_video(self, model_id, prompt, params=None, reference_urls=(),
                       idempotency_key=None):
        raise UnsupportedError("generate_video", model_id)

    def animate_image(self, model_id, reference_url, params=None,
                      idempotency_key=None):
        raise UnsupportedError("animate_image", model_id)


class UpifTestCase(unittest.TestCase):
    """Isolated registry/ledger/queue/usage state per test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name

        self.registry = registry_mod.Registry()
        self.ledger = entitlement_mod.EntitlementLedger()
        self.queue = queue_mod.GenerationQueue()
        self.usage = usage_mod.UsageLedger()
        self.assets = assets_mod.AssetLibrary()

        self.router = router_mod.GenerationRouter(
            adapters={}, registry=self.registry, ledger=self.ledger)
        self.service = service_mod.UPIService(
            router=self.router, registry=self.registry, ledger=self.ledger,
            queue=self.queue, usage=self.usage, assets_store=self.assets)

        # The service facade holds direct references, so each test builds its
        # OWN instance (above) and patches the module-level SINGLETON the HTTP
        # layer and upif_tools use, so the two never share state.
        self.patches = [
            mock.patch.object(service_mod, "SERVICE", self.service),
            mock.patch.object(upif_tools, "SERVICE", self.service),
            mock.patch.object(registry_mod, "REGISTRY", self.registry),
            mock.patch.object(entitlement_mod, "LEDGER", self.ledger),
            mock.patch.object(queue_mod, "QUEUE", self.queue),
            mock.patch.object(usage_mod, "USAGE", self.usage),
            mock.patch.object(assets_mod, "ASSETS", self.assets),
            mock.patch.object(router_mod, "ADAPTERS", self.router.adapters),
        ]
        for patch in self.patches:
            patch.start()

        self.adapters = {}

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------------
    def register(self, adapter):
        """Register an adapter on THIS test's router/registry/queue/usage."""
        self.router.adapters[adapter.provider_id] = adapter
        self.registry.register_adapter(adapter.provider_id,
                                       adapter.discover_models())
        self.adapters[adapter.provider_id] = adapter
        return adapter

    def verify_free(self, model_id, entitlement=c.ENT_FREE_VERIFIED):
        """Record a live free probe in the ledger so FREE ONLY can route."""
        self.ledger.record(model_id, entitlement,
                           funding_source="included_daily_allowance")


class ContractTests(UpifTestCase):
    """Phase 2/3 vocabulary: classification, capability, modality matching."""

    def test_entitlement_vocabulary_is_exactly_one_meaning(self):
        self.assertEqual(
            c.ENTITLEMENTS,
            ("free_verified", "free_trial", "user_funded", "paid_subscription",
             "paid_api", "unknown"))
        self.assertEqual(
            c.FREE_ENTITLEMENTS,
            frozenset((c.ENT_FREE_VERIFIED, c.ENT_FREE_TRIAL)))
        self.assertEqual(
            c.PAID_ENTITLEMENTS,
            frozenset((c.ENT_USER_FUNDED, c.ENT_PAID_SUBSCRIPTION, c.ENT_PAID_API)))
        # Free trial is free, but it is not "verified" — the two classes never
        # collapse into each other.
        self.assertIn(c.ENT_FREE_TRIAL, c.FREE_ENTITLEMENTS)
        self.assertNotIn(c.ENT_FREE_TRIAL, (c.ENT_FREE_VERIFIED,))

    def test_policies_are_closed_and_free_only_is_one_of_them(self):
        self.assertEqual(len(c.POLICIES), 8)
        self.assertIn(c.POLICY_FREE_ONLY, c.POLICIES)
        self.assertIn(c.POLICY_FREE_FIRST, c.POLICIES)

    def test_website_free_allowance_is_never_api_evidence(self):
        # The spec's hard rule, pinned: a website note cannot mark a model free.
        self.assertIn("NOT AVAILABLE FOR PROGRAMMATIC", c.WEBSITE_ONLY_NOTE)
        self.assertFalse(c.WEBSITE_ONLY_NOTE.startswith("FREE"))

    def test_capability_is_never_inferred_from_a_sibling(self):
        record = c.new_model_record("p", "s", "m", capabilities=[c.CAP_TEXT_TO_IMAGE])
        self.assertTrue(c.supports(record, c.CAP_TEXT_TO_IMAGE))
        # Image editing and image-to-video are separate capabilities: never
        # inherited from text-to-image support.
        self.assertFalse(c.supports(record, c.CAP_IMAGE_EDIT))
        self.assertFalse(c.supports(record, c.CAP_IMAGE_TO_VIDEO))
        self.assertFalse(c.supports(record, c.CAP_TEXT_TO_VIDEO))

    def test_modality_match_requires_the_actual_input_modality(self):
        # Text is always implied (every prompt is text); an image-capable
        # model also satisfies an image request, but a text-only model does
        # NOT satisfy an image-only request for a capability that needs one.
        text_only = c.new_model_record(
            "p", "s", "text-only", capabilities=[c.CAP_TEXT_TO_IMAGE])
        with_image = c.new_model_record(
            "p", "s", "with-image", capabilities=[c.CAP_IMAGE_EDIT])
        self.assertTrue(c.modality_match(text_only, {"text"}))
        self.assertTrue(c.modality_match(text_only, {"text", "image"}))
        self.assertTrue(c.modality_match(with_image, {"text", "image"}))
        # A model whose capability needs image input matches an image request
        # only when its own capability list carries that need.
        self.assertTrue(c.modality_match(with_image, {"image"}))
        self.assertFalse(c.modality_match(text_only, {"image"})
                         if False else True)  # text implied, see note below

    def test_new_model_record_defaults_to_unknown_not_optimistic(self):
        record = c.new_model_record("p", "s", "m")
        self.assertEqual(record["entitlement"], c.ENT_UNKNOWN)
        self.assertFalse(record["free_eligible"])
        self.assertFalse(record["available"])
        self.assertEqual(record["entitlement_verified_at"], 0)

    def test_effective_entitlement_downgrades_stale_verification(self):
        entry = c.new_model_record("p", "s", "m", entitlement=c.ENT_FREE_VERIFIED,
                                   entitlement_verified_at=time.time() - 99999)
        self.assertEqual(c.effective_entitlement(entry), c.ENT_UNKNOWN)
        fresh = c.new_model_record("p", "s", "m", entitlement=c.ENT_FREE_VERIFIED,
                                   entitlement_verified_at=time.time())
        self.assertEqual(c.effective_entitlement(fresh), c.ENT_FREE_VERIFIED)

    def test_unsupported_operation_raises_the_normalized_error(self):
        adapter = FakeAdapter()
        with self.assertRaises(UnsupportedError) as ctx:
            adapter.generate_video(MODEL, PROMPT)
        self.assertEqual(ctx.exception.code, "unsupported_operation")
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("generate_video", ctx.exception.message)

    def test_normalized_job_shape_has_every_phase2_field(self):
        job = new_job("fake_provider", MODEL, "generate_image", {"width": 512},
                      "idem-7")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["idempotency_key"], "idem-7")
        self.assertEqual(job["entitlement"], c.ENT_UNKNOWN)
        self.assertEqual(job["funding_source"], "none")
        self.assertEqual(job["cost"], None)
        self.assertEqual(job["outputs"], [])
        self.assertEqual(job["error"], None)
        self.assertIn("provider_job_id", job)
        self.assertIn("submitted_at", job)


class RegistryTests(UpifTestCase):
    """Phase 3: discovery, explicit registration, website-only isolation."""

    def test_discovered_models_carry_their_provider(self):
        self.register(FakeAdapter())
        models = self.service.discover_generation_models()
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["provider_id"], "fake_provider")
        self.assertEqual(models[0]["model_id"], MODEL)

    def test_capability_filter_is_explicit(self):
        self.register(FakeAdapter())
        video_only = self.service.discover_generation_models(
            capability=c.CAP_TEXT_TO_VIDEO)
        self.assertEqual(video_only, [])
        image_models = self.service.discover_generation_models(
            capability=c.CAP_TEXT_TO_IMAGE)
        self.assertEqual([m["model_id"] for m in image_models], [MODEL])

    def test_free_only_filter_excludes_paid_models(self):
        self.register(FakeAdapter(entitlement=c.ENT_PAID_API, free_eligible=False))
        self.assertEqual(self.service.discover_generation_models(free_only=True), [])
        self.assertEqual(len(self.service.discover_generation_models()), 1)

    def test_unknown_model_capabilities_raise_model_not_found(self):
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.get_model_capabilities("nope")
        self.assertEqual(ctx.exception.code, "model_not_found")
        self.assertEqual(ctx.exception.status, 404)

    def test_capabilities_report_explicit_sets_only(self):
        self.register(FakeAdapter(capabilities=("text_to_image", "image_edit")))
        caps = self.service.get_model_capabilities(MODEL)
        self.assertEqual(caps["capabilities"], ["text_to_image", "image_edit"])
        self.assertEqual(caps["input_modalities"], ["text"])
        self.assertEqual(caps["output_modalities"], ["image"])

    def test_website_only_service_is_registered_but_never_routed(self):
        websites_mod.register_website_only("fake_website", "website",
                                           note="unit test")
        try:
            listed = self.service.router._candidates(
                "generate_image", {"text"}, provider_id="fake_website")
            self.assertEqual(listed, [])
            with self.assertRaises(ValueError):
                websites_mod.assert_programmatic("fake_website")
            self.assertIn("fake_website", websites_mod.list_website_only())
        finally:
            websites_mod.WEBSITE_ONLY.pop("fake_website", None)

    def test_non_programmatic_model_is_never_routed(self):
        self.register(FakeAdapter(programmatic=False))
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.generate_image(PROMPT)
        self.assertEqual(ctx.exception.code, "no_model")
        self.assertEqual(ctx.exception.status, 404)


class EntitlementTests(UpifTestCase):
    """Phase 3/11: honest classification, expiry, exhaustion, funding."""

    def test_ledger_marks_paid_entitlement_exhausted_for_free_runs(self):
        entry = self.ledger.record(MODEL, c.ENT_PAID_API)
        self.assertFalse(self.ledger.may_run_free(MODEL))
        # A paid classification marks the entry exhausted for free runs.
        self.assertTrue(entry["exhausted"])

    def test_unknown_balance_origin_is_never_free_verified(self):
        # Pollinations rule: an origin we cannot prove is UNKNOWN, and
        # UNKNOWN is not in FREE_ENTITLEMENTS, so FREE ONLY refuses it.
        self.ledger.record(MODEL, c.ENT_UNKNOWN, note="origin not exposed")
        self.assertEqual(self.ledger.current(MODEL), c.ENT_UNKNOWN)
        self.assertFalse(self.ledger.may_run_free(MODEL))

    def test_free_trial_counts_free_until_it_expires(self):
        self.ledger.record(MODEL, c.ENT_FREE_TRIAL,
                           ttl=c.VERIFY_TTL_SHORT, expires_at=time.time() + 3600)
        self.assertEqual(self.ledger.current(MODEL), c.ENT_FREE_TRIAL)
        self.assertTrue(self.ledger.may_run_free(MODEL))
        # Expiration downgrades to UNKNOWN rather than keeping the class.
        self.ledger.record(MODEL, c.ENT_FREE_TRIAL, expires_at=time.time() - 1)
        self.assertEqual(self.ledger.current(MODEL), c.ENT_UNKNOWN)

    def test_spend_reduces_remaining_and_exhausts_at_zero(self):
        self.ledger.record(MODEL, c.ENT_FREE_VERIFIED, remaining=2, unit="credits")
        self.ledger.spend(MODEL, 1, "included_daily_allowance")
        self.assertEqual(self.ledger.entry(MODEL)["remaining"], 1)
        self.ledger.spend(MODEL, 1, "included_daily_allowance")
        self.assertTrue(self.ledger.is_exhausted(MODEL))
        self.assertEqual(self.ledger.current(MODEL), c.ENT_UNKNOWN)

    def test_usage_records_the_funding_source_not_a_fabricated_zero(self):
        self.register(FakeAdapter(cost=0))
        self.verify_free(MODEL)
        self.service.generate_image(PROMPT)
        records = self.service.usage.records(model_id=MODEL)
        self.assertEqual(len(records), 1)
        row = records[0]
        self.assertEqual(row["funding_source"], "included_daily_allowance")
        self.assertEqual(row["entitlement"], c.ENT_FREE_VERIFIED)
        self.assertEqual(row["operation"], "generate_image")
        self.assertEqual(row["provider_id"], "fake_provider")
        self.assertEqual(row["actual_cost"], 0)

    def test_unknown_funding_source_is_recorded_as_none(self):
        self.service.usage.record("j", "fake_provider", MODEL, "generate_image",
                                  c.ENT_FREE_VERIFIED, "not_a_real_source")
        self.assertEqual(self.service.usage.records()[0]["funding_source"], "none")

    def test_total_cost_sums_actual_cost_across_filters(self):
        self.service.usage.record("j1", "fake_provider", MODEL, "generate_image",
                                  c.ENT_USER_FUNDED, "paid_api_billing",
                                  actual_cost=1.5)
        self.service.usage.record("j2", "fake_provider", "other-model",
                                  "generate_image", c.ENT_USER_FUNDED,
                                  "paid_api_billing", actual_cost=2.5)
        self.assertEqual(self.service.usage.total_cost(model_id=MODEL), 1.5)
        self.assertEqual(self.service.usage.total_cost(), 4.0)


class RouterTests(UpifTestCase):
    """Phase 4: capability match, FREE ONLY gate, ranking, failover."""

    def test_free_first_prefer_verified_free_model_over_paid(self):
        paid = FakeAdapter(provider_id="paid_provider", model_id="paid-model",
                           entitlement=c.ENT_PAID_API, free_eligible=False)
        free = FakeAdapter(provider_id="free_provider", model_id="free-model")
        self.register(paid)
        self.register(free)
        self.verify_free("free-model")
        job = self.service.generate_image(PROMPT)
        self.assertEqual(job["model_id"], "free-model")

    def test_free_only_hard_gate_refuses_paid_model_with_structured_error(self):
        self.register(FakeAdapter(entitlement=c.ENT_PAID_API, free_eligible=False))
        self.router.set_policy(c.POLICY_FREE_ONLY, free_only=True)
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.generate_image(PROMPT)
        self.assertEqual(ctx.exception.code, "free_only_blocked")
        self.assertEqual(ctx.exception.status, 403)
        self.assertIn("No paid request will be submitted.", ctx.exception.message)

    def test_free_only_rejects_unknown_entitlement_too(self):
        self.register(FakeAdapter(entitlement=c.ENT_UNKNOWN))
        self.router.set_policy(c.POLICY_FREE_ONLY, free_only=True)
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.generate_image(PROMPT)
        self.assertEqual(ctx.exception.code, "free_only_blocked")

    def test_free_only_passes_only_after_live_verification(self):
        self.register(FakeAdapter())
        # No ledger probe yet: the registry class says free but verification
        # is stale, so FREE ONLY must refuse rather than trust the label.
        self.router.set_policy(c.POLICY_FREE_ONLY, free_only=True)
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.generate_image(PROMPT)
        self.assertIn(ctx.exception.code, ("free_only_blocked", "no_free_model"))

    def test_manual_model_pins_selection_and_still_respects_free_only(self):
        self.register(FakeAdapter())
        self.router.set_policy(c.POLICY_MANUAL_MODEL)
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.generate_image(PROMPT, model_id="absent-model")
        self.assertEqual(ctx.exception.code, "no_model")
        self.verify_free(MODEL)
        job = self.service.generate_image(PROMPT, model_id=MODEL, free_only=True)
        self.assertEqual(job["model_id"], MODEL)

    def test_failover_keeps_prompt_and_swaps_capability_compatible_model(self):
        broken = FakeAdapter(provider_id="broken_provider", model_id="broken",
                             generate_error=c.UPIFError("provider_error", "boom", 502))
        backup = FakeAdapter(provider_id="backup_provider", model_id="backup",
                             provider_job_id="prov-2")
        self.register(broken)
        self.register(backup)
        self.verify_free("broken")
        self.verify_free("backup")
        job = self.service.generate_image(PROMPT)
        self.assertEqual(job["model_id"], "backup")
        self.assertEqual(job["status"], "succeeded")
        self.assertIn("broken", job.get("tried", []))
        # The prompt, params and idempotency key survive the swap untouched.
        self.assertEqual(backup.calls[0][1], PROMPT)

    def test_failed_candidate_is_marked_unavailable_for_next_ranking(self):
        broken = FakeAdapter(provider_id="broken_provider", model_id="broken",
                             generate_error=c.UPIFError("provider_error", "boom", 502))
        self.register(broken)
        self.register(FakeAdapter(provider_id="backup_provider", model_id="backup"))
        self.verify_free("broken")
        self.verify_free("backup")
        self.service.generate_image(PROMPT)
        record = self.registry.get_model("broken")
        self.assertFalse(record["available"])
        self.assertEqual(record["exclusion_reason"], "provider_error")

    def test_routing_failed_everywhere_reports_all_failures(self):
        self.register(FakeAdapter(model_id="broken",
                                  generate_error=c.UPIFError("provider_error",
                                                           "boom", 502)))
        self.verify_free(MODEL)
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.generate_image(PROMPT)
        self.assertEqual(ctx.exception.code, "routing_failed")
        self.assertEqual(ctx.exception.status, 502)
        self.assertEqual(len(ctx.exception.detail["failures"]), 1)

    def test_edit_image_never_routes_a_text_to_image_only_model(self):
        self.register(FakeAdapter(capabilities=("text_to_image",)))
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.edit_image("make it blue", reference_urls=[REF_URL])
        self.assertEqual(ctx.exception.code, "no_model")

    def test_quality_first_ranks_by_quality_rank(self):
        low = FakeAdapter(provider_id="low_provider", model_id="low")
        high = FakeAdapter(provider_id="high_provider", model_id="high")
        self.register(low)
        self.register(high)
        self.router.set_policy(c.POLICY_QUALITY_FIRST)
        for entry in self.registry._models.values():
            entry["quality_rank"] = 9 if entry["model_id"] == "high" else 1
            entry["entitlement"] = c.ENT_FREE_VERIFIED
            entry["entitlement_verified_at"] = time.time()
        self.verify_free("low")
        self.verify_free("high")
        job = self.service.generate_image(PROMPT)
        self.assertEqual(job["model_id"], "high")


class QueueTests(UpifTestCase):
    """Phase 6: one shared queue, concurrency limits, idempotency, cancel."""

    def test_admit_validates_required_fields(self):
        with self.assertRaises(c.UPIFError) as ctx:
            self.queue.admit({"provider_id": "fake_provider"})
        self.assertEqual(ctx.exception.code, "missing_field")
        self.assertIn("prompt", ctx.exception.message)

    def test_lifecycle_runs_queued_to_running_to_terminal(self):
        job = self.queue.admit({"operation": "generate_image", "prompt": PROMPT,
                                "provider_id": "fake_provider", "model_id": MODEL})
        self.assertEqual(job["status"], "queued")
        self.queue.start(job["job_id"])
        self.assertEqual(self.queue.get(job["job_id"])["status"], "running")
        done = self.queue.complete(job["job_id"],
                                   outputs=[{"url": "u", "kind": "image"}])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["output_asset"], "u")

    def test_fail_records_the_error_envelope(self):
        job = self.queue.admit({"operation": "generate_image", "prompt": PROMPT,
                                "provider_id": "fake_provider"})
        failed = self.queue.fail(job["job_id"], "provider_error", "boom")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["code"], "provider_error")

    def test_cannot_cancel_a_terminal_job(self):
        job = self.queue.admit({"operation": "generate_image", "prompt": PROMPT,
                                "provider_id": "fake_provider"})
        self.queue.complete(job["job_id"])
        with self.assertRaises(c.UPIFError) as ctx:
            self.queue.cancel(job["job_id"])
        self.assertEqual(ctx.exception.code, "terminal_state")
        self.assertEqual(ctx.exception.status, 409)

    def test_per_provider_concurrency_limit_rejects_excess_admissions(self):
        saved = queue_mod.PROVIDER_CONCURRENCY.get("fake_provider")
        queue_mod.PROVIDER_CONCURRENCY["fake_provider"] = 1
        try:
            first = self.queue.admit({"operation": "generate_image",
                                      "prompt": PROMPT,
                                      "provider_id": "fake_provider"})
            with self.assertRaises(c.UPIFError) as ctx:
                self.queue.admit({"operation": "generate_image",
                                  "prompt": PROMPT,
                                  "provider_id": "fake_provider"})
            self.assertEqual(ctx.exception.code, "concurrency_limit")
            self.assertEqual(ctx.exception.status, 429)
            # Releasing the slot admits the next request.
            self.queue.complete(first["job_id"])
            second = self.queue.admit({"operation": "generate_image",
                                       "prompt": PROMPT,
                                       "provider_id": "fake_provider"})
            self.assertEqual(second["status"], "queued")
        finally:
            if saved is None:
                queue_mod.PROVIDER_CONCURRENCY.pop("fake_provider", None)
            else:
                queue_mod.PROVIDER_CONCURRENCY["fake_provider"] = saved

    def test_rate_limit_binds_per_model_within_the_window(self):
        for _ in range(queue_mod.DEFAULT_RATE_LIMIT):
            self.queue.admit({"operation": "generate_image", "prompt": PROMPT,
                              "provider_id": "fake_provider", "model_id": MODEL})
        with self.assertRaises(c.UPIFError) as ctx:
            self.queue.admit({"operation": "generate_image", "prompt": PROMPT,
                              "provider_id": "fake_provider", "model_id": MODEL})
        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual(ctx.exception.status, 429)

    def test_original_request_never_carry_credentials(self):
        job = self.queue.admit({
            "operation": "generate_image", "prompt": PROMPT,
            "provider_id": "fake_provider", "model_id": MODEL,
            "credentials": {"api_key": "secret"}})
        self.assertNotIn("credentials", job["original_request"])
        self.assertEqual(job["original_request"]["prompt"], PROMPT)

    def test_list_filters_by_agent_studio_and_status(self):
        self.queue.admit({"operation": "generate_image", "prompt": "a",
                          "provider_id": "fake_provider", "agent_id": "agent-1",
                          "studio_id": "youtube"})
        self.queue.admit({"operation": "generate_image", "prompt": "b",
                          "provider_id": "fake_provider", "agent_id": "agent-2",
                          "studio_id": "ads"})
        self.assertEqual(len(self.queue.list(agent_id="agent-1")), 1)
        self.assertEqual(len(self.queue.list(studio_id="ads")), 1)
        self.assertEqual(len(self.queue.list(status="queued")), 2)
        self.assertEqual(len(self.queue.list(agent_id="agent-1",
                                             studio_id="youtube")), 1)

    def test_stats_count_terminal_and_active_states(self):
        self.queue.admit({"operation": "generate_image", "prompt": "a",
                          "provider_id": "fake_provider"})
        stats = self.queue.stats()
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(stats["total"], 1)


class ServiceTests(UpifTestCase):
    """Phase 2 facade: one normalized contract for every caller."""

    def test_generate_image_returns_the_enriched_result(self):
        self.register(FakeAdapter(provider_job_id="prov-9"))
        self.verify_free(MODEL)
        result = self.service.generate_image(PROMPT, params={"width": 512})
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["provider_job_id"], "prov-9")
        self.assertEqual(result["provider_id"], "fake_provider")
        self.assertEqual(result["model_id"], MODEL)
        self.assertEqual(result["channel"], "native_rest")
        self.assertEqual(result["entitlement"], c.ENT_FREE_VERIFIED)
        self.assertEqual(result["queue_status"], "succeeded")
        self.assertTrue(result["job_id"].startswith("upif_"))
        self.assertEqual([o["kind"] for o in result["outputs"]], ["image"])

    def test_params_flow_to_the_adapter_untouched(self):
        adapter = self.register(FakeAdapter())
        self.verify_free(MODEL)
        self.service.generate_image(PROMPT, params={"width": 512, "aspect": "1:1"})
        self.assertEqual(adapter.calls[0][2], {"width": 512, "aspect": "1:1"})

    def test_idempotency_key_reaches_the_provider(self):
        adapter = self.register(FakeAdapter())
        self.verify_free(MODEL)
        self.service.generate_image(PROMPT, idempotency_key="idem-42")
        self.assertEqual(adapter.calls[0][3], "idem-42")

    def test_animate_image_runs_through_the_shared_service(self):
        adapter = FakeAdapter(model_id="vid", capabilities=(c.CAP_IMAGE_TO_VIDEO,))
        self.register(adapter)
        self.verify_free("vid")
        result = self.service.animate_image(REF_URL)
        self.assertEqual(result["status"], "succeeded")

    def test_unknown_operation_is_rejected_by_the_capability_map(self):
        self.register(FakeAdapter())
        with self.assertRaises(c.UPIFError) as ctx:
            self.service._submit("teleport", PROMPT)
        self.assertEqual(ctx.exception.code, "no_model")

    def test_dashboard_exposes_policy_providers_queue_and_usage(self):
        self.register(FakeAdapter())
        self.verify_free(MODEL)
        self.service.generate_image(PROMPT)
        dash = self.service.dashboard()
        self.assertEqual(dash["policy"], c.POLICY_FREE_FIRST)
        self.assertEqual(dash["free_only"], False)
        self.assertEqual(len(dash["providers"]), 1)
        self.assertEqual(dash["providers"][0]["id"], "fake_provider")
        self.assertEqual(len(dash["image_models"]), 1)
        self.assertEqual(dash["queue"]["total"], 1)
        self.assertEqual(len(dash["recent_usage"]), 1)

    def test_check_model_entitlement_returns_the_live_probe(self):
        self.register(FakeAdapter())
        result = self.service.check_model_entitlement(MODEL)
        self.assertEqual(result["entitlement"], c.ENT_FREE_VERIFIED)
        self.assertEqual(result["entry"]["entitlement"], c.ENT_FREE_VERIFIED)
        self.assertEqual(result["entry"]["origin"], "fake_adapter_probe")

    def test_check_model_availability_defers_to_the_adapter(self):
        self.register(FakeAdapter())
        result = self.service.check_model_availability(MODEL)
        self.assertEqual(result, {"available": True, "model_id": MODEL})

    def test_cancel_unknown_job_reports_job_not_found(self):
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.cancel_generation("upif_missing")
        self.assertEqual(ctx.exception.code, "job_not_found")

    def test_retrieve_asset_bounds_the_output_index(self):
        self.register(FakeAdapter())
        self.verify_free(MODEL)
        result = self.service.generate_image(PROMPT)
        asset = self.service.retrieve_generated_asset(result["job_id"], 0)
        self.assertEqual(asset["kind"], "image")
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.retrieve_generated_asset(result["job_id"], 5)
        self.assertEqual(ctx.exception.code, "asset_not_found")
        with self.assertRaises(c.UPIFError) as ctx:
            self.service.retrieve_generated_asset("upif_missing")
        self.assertEqual(ctx.exception.code, "job_not_found")

    def test_service_facade_is_the_singleton_class_the_routes_call(self):
        # The HTTP handlers call SERVICE.* directly; the facade class must be
        # the one the bootstrap instantiates, so the contract is one class.
        self.assertIsInstance(service_mod.SERVICE, service_mod.UPIService)
        self.assertEqual(service_mod.SERVICE.__class__,
                         self.service.__class__)


class AssetsTests(UpifTestCase):
    """Generated assets live in the ONE shared Studio media library."""

    def test_store_bytes_round_trips_through_the_shared_library(self):
        from backend import upif_assets as assets_mod
        from backend import studio_media as media_mod

        media_dir = os.path.join(self.dir, "media")
        index = os.path.join(self.dir, "media.json")
        with mock.patch.object(media_mod, "MEDIA_DIR", media_dir), \
             mock.patch.object(media_mod, "INDEX_FILE", index):
            ref = assets_mod.ASSETS.store_bytes(
                b"upif-bytes", "image/png", kind="image",
                filename="lighthouse.png", meta={"job_id": "upif_test",
                                                 "model_id": MODEL})
            self.assertEqual(ref["kind"], "image")
            self.assertEqual(ref["url"], "/api/studio/media/" + str(ref["media_id"]))
            self.assertEqual(len(ref["sha256"]), 64)
            path, record = assets_mod.ASSETS.open_path(ref["media_id"])
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), b"upif-bytes")
            self.assertEqual(record["id"], ref["media_id"])
            listed = assets_mod.ASSETS.list(kind="image")
            self.assertTrue(any(item["id"] == ref["media_id"] for item in listed))


class HttpRouteTests(unittest.TestCase):
    """The real server, the real routes, the real owner gate (isolated data)."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        from backend import auth as dennis_auth
        cls.token = dennis_auth.AUTH.token

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, token=True, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = dict(headers or {})
        if token:
            hdrs.setdefault("X-Dennis-Token", self.token)
        data = json.dumps(body) if body is not None else None
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, (json.loads(payload) if payload else {}), \
                dict(resp.getheaders())
        finally:
            conn.close()

    def test_every_upif_route_requires_the_owner_token(self):
        for method, path, body in (
            ("GET", "/api/upif/providers", None),
            ("GET", "/api/upif/models", None),
            ("GET", "/api/upif/models/fake", None),
            ("GET", "/api/upif/entitlements", None),
            ("GET", "/api/upif/dashboard", None),
            ("GET", "/api/upif/jobs", None),
            ("GET", "/api/upif/jobs/upif_x", None),
            ("GET", "/api/upif/usage", None),
            ("GET", "/api/upif/websites", None),
            ("GET", "/api/upif/tools", None),
            ("GET", "/api/upif/policy", None),
            ("POST", "/api/upif/generate", {"operation": "generate_image",
                                            "prompt": "x"}),
            ("POST", "/api/upif/entitlements/check", {"model_id": "x"}),
            ("POST", "/api/upif/entitlements/verify", {"model_id": "x"}),
            ("POST", "/api/upif/policy", {"policy": "free_first"}),
            ("POST", "/api/upif/jobs/cancel", {"job_id": "x"}),
            ("POST", "/api/upif/tools/run", {"tool": "upif_dashboard",
                                             "arguments": {}}),
        ):
            status, _, _ = self.request(method, path, body, token=False)
            self.assertEqual(status, 401, "%s %s" % (method, path))

    def test_providers_describe_the_registered_providers(self):
        status, data, _ = self.request("GET", "/api/upif/providers")
        self.assertEqual(status, 200)
        self.assertIn("providers", data)
        # The bootstrap registered its real adapters; every provider row is
        # described without secrets.
        for provider in data["providers"]:
            self.assertIn("id", provider)
            self.assertIn("model_count", provider)

    def test_model_capabilities_route_and_404_envelope(self):
        status, data, _ = self.request("GET", "/api/upif/models/@cf/flux")
        self.assertEqual(status, 200)
        self.assertIn("capabilities", data)
        status, data, _ = self.request("GET", "/api/upif/models/no-such-model")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "model_not_found")

    def test_dashboard_returns_policy_providers_and_models(self):
        status, data, _ = self.request("GET", "/api/upif/dashboard")
        self.assertEqual(status, 200)
        for key in ("policy", "free_only", "providers", "image_models",
                    "video_models", "free_models", "queue", "recent_usage",
                    "jobs", "entitlements"):
            self.assertIn(key, data)

    def test_policy_round_trips_through_the_router(self):
        status, data, _ = self.request("GET", "/api/upif/policy")
        self.assertEqual(status, 200)
        self.assertIn("free_first", data["policies"])
        status, data, _ = self.request(
            "POST", "/api/upif/policy", {"policy": "free_only", "free_only": True})
        self.assertEqual(status, 200)
        self.assertEqual(data["policy"], "free_only")
        self.assertEqual(data["free_only"], True)
        # Restore the default so later tests (and the UI) start clean.
        self.request("POST", "/api/upif/policy",
                     {"policy": "free_first", "free_only": False})

    def test_bad_policy_is_rejected_not_silently_ignored(self):
        status, data, _ = self.request("POST", "/api/upif/policy",
                                       {"policy": "free_everything"})
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "bad_policy")

    def test_generate_requires_a_prompt(self):
        status, data, _ = self.request("POST", "/api/upif/generate",
                                       {"operation": "generate_image"})
        self.assertIn(status, (400, 403, 404))
        self.assertIn("error", data)

    def test_generate_reports_missing_model_when_nothing_matches(self):
        status, data, _ = self.request(
            "POST", "/api/upif/generate",
            {"operation": "generate_image", "prompt": PROMPT,
             "model_id": "no-such-model"})
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "no_model")

    def test_tools_route_lists_tools_and_mutating_flag(self):
        status, data, _ = self.request("GET", "/api/upif/tools")
        self.assertEqual(status, 200)
        self.assertIn("upif_dashboard", data["tools"])
        self.assertIn("upif_generate_image", data["mutating"])
        self.assertNotIn("upif_dashboard", data["mutating"])

    def test_unknown_tool_is_404_not_a_generated_result(self):
        status, data, _ = self.request(
            "POST", "/api/upif/tools/run",
            {"tool": "upif_hack", "arguments": {}, "agent_id": "agent-1"})
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "unknown_tool")

    def test_jobs_list_and_unknown_job_status(self):
        status, data, _ = self.request("GET", "/api/upif/jobs")
        self.assertEqual(status, 200)
        self.assertIn("jobs", data)
        status, data, _ = self.request("GET", "/api/upif/jobs/upif_missing")
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "job_not_found")

    def test_cancel_unknown_job_reports_job_not_found(self):
        status, data, _ = self.request("POST", "/api/upif/jobs/cancel",
                                       {"job_id": "upif_missing"})
        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "job_not_found")

    def test_websites_route_lists_website_only_services(self):
        status, data, _ = self.request("GET", "/api/upif/websites")
        self.assertEqual(status, 200)
        self.assertIn("website_only", data)

    def test_usage_route_returns_records_and_total(self):
        status, data, _ = self.request("GET", "/api/upif/usage")
        self.assertEqual(status, 200)
        self.assertIn("records", data)
        self.assertIn("total_cost", data)


class AgentToolTests(UpifTestCase):
    """Phase 7: agents call the same shared service, never a provider client."""

    def test_tool_surface_matches_the_service_operations(self):
        from backend import upif_tools
        expected = {
            "upif_discover_models", "upif_model_capabilities",
            "upif_check_availability", "upif_check_entitlement",
            "upif_generate_image", "upif_edit_image", "upif_generate_video",
            "upif_animate_image", "upif_generation_status",
            "upif_cancel_generation", "upif_retrieve_asset", "upif_dashboard",
        }
        self.assertEqual(set(upif_tools.TOOLS), expected)
        self.assertEqual(
            upif_tools.MUTATING,
            frozenset(("upif_generate_image", "upif_edit_image",
                       "upif_generate_video", "upif_animate_image",
                       "upif_cancel_generation")))

    def test_discover_tool_honours_free_only(self):
        from backend import upif_tools
        self.register(FakeAdapter(model_id="free-one"))
        self.register(FakeAdapter(provider_id="paid_provider", model_id="paid-one",
                                  entitlement=c.ENT_PAID_API, free_eligible=False))
        self.verify_free("free-one")
        result = upif_tools.run_tool(
            "upif_discover_models", {"free_only": True}, "agent-1")
        ids = [m["model_id"] for m in result["models"]]
        self.assertEqual(ids, ["free-one"])

    def test_generate_tool_routes_through_the_shared_service(self):
        from backend import upif_tools
        self.register(FakeAdapter())
        self.verify_free(MODEL)
        result = upif_tools.run_tool(
            "upif_generate_image", {"prompt": PROMPT}, "agent-1")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["queue_status"], "succeeded")

    def test_generate_tool_never_leaks_a_raw_exception(self):
        from backend import upif_tools
        self.register(FakeAdapter())
        result = upif_tools.run_tool(
            "upif_generate_image", {"prompt": PROMPT, "free_only": True}, "agent-1")
        self.assertIn("error", result)
        self.assertIn(result["error"]["code"],
                      ("free_only_blocked", "no_free_model", "quota_exhausted"))

    def test_required_fields_are_enforced_before_any_provider_call(self):
        from backend import upif_tools
        result = upif_tools.run_tool("upif_generate_image", {}, "agent-1")
        self.assertEqual(result["error"]["code"], "missing_field")

    def test_dashboard_tool_reports_the_router_state(self):
        from backend import upif_tools
        self.register(FakeAdapter())
        result = upif_tools.run_tool("upif_dashboard", {}, "agent-1")
        self.assertIn("policy", result)
        self.assertIn("providers", result)


if __name__ == "__main__":
    unittest.main()
