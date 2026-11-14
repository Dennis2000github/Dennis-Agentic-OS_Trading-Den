"""Unit tests for recommendation review and backlog routing.

Run:  python3 -m unittest tests.test_cosmos_backlog
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ads_backlog  # noqa: E402
from backend import cosmos_backlog  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_store as store  # noqa: E402
from backend import youtube_topics  # noqa: E402


SNAPSHOT_ADS = {
    "domain": "ads",
    "template_id": "womens-apparel",
    "template": {"name": "Women's Apparel"},
    "captured_at": 1,
    "captured_iso": "2026-11-15T00:00:00",
}

SNAPSHOT_YOUTUBE = {
    "domain": "youtube",
    "template_id": "educational-video",
    "template": {"name": "Educational Video"},
    "channel": {"id": "ytch_test", "name": "Test Channel"},
    "channel_id": "ytch_test",
    "captured_at": 1,
    "captured_iso": "2026-11-15T00:00:00",
}


class BacklogRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json")),
            mock.patch.object(common, "EXECUTIONS_FILE", os.path.join(self.dir, "executions.json")),
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json")),
            mock.patch.object(cosmos_backlog.youtube_channels, "get_channel", return_value={"id": "ytch_test"}),
            mock.patch.object(cosmos_backlog.youtube_channels, "active_channel", return_value={"id": "ytch_test"}),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _ads_rec(self, title="Floral midi dress"):
        payload = {
            "name": title,
            "merchant": "Shopee",
            "description": "A floral dress.",
            "category": "Dresses",
            "reason": "Fits the template.",
            "sources": [],
            "relevance_score": 84,
            "affiliate_status": "unvalidated",
            "evidence_status": "hypothesis",
        }
        return store.create_recommendation(
            "andromeda", "ads", "exe_1", "ads.discovery", "womens-apparel",
            SNAPSHOT_ADS, title, "why", payload, [], 84,
        )

    def _youtube_rec(self, title="Small business tax tips"):
        payload = {
            "title": title,
            "description": "A practical tax explainer.",
            "pillar": "Tax Intelligence",
            "reason": "Fits the channel.",
            "sources": [],
            "relevance_score": 86,
            "audience_question": "How do I file this?",
            "search_intent": "informational",
            "commercial_relevance": "medium",
            "evidence_status": "hypothesis",
        }
        return store.create_recommendation(
            "andromeda", "youtube", "exe_2", "youtube.discovery", "educational-video",
            SNAPSHOT_YOUTUBE, title, "why", payload, [], 86,
        )

    def test_ads_draft_creates_backlog_row_with_provenance_and_is_idempotent(self):
        rec = self._ads_rec()
        result = cosmos_backlog.draft(rec["id"])
        self.assertFalse(result["already_drafted"])
        self.assertEqual(result["destination"]["type"], "ads_product")
        product = ads_backlog.get_product(result["destination"]["id"])
        self.assertEqual(product["name"], "Floral midi dress")
        self.assertEqual(product["provenance"]["recommendation_id"], rec["id"])
        self.assertEqual(product["provenance"]["agent_id"], "andromeda")
        self.assertEqual(product["provenance"]["config_snapshot"]["template_id"], "womens-apparel")

        updated = store.get_recommendation(rec["id"])
        self.assertEqual(updated["status"], "drafted")
        self.assertEqual(updated["destination"]["id"], product["id"])

        again = cosmos_backlog.draft(rec["id"])
        self.assertTrue(again["already_drafted"])
        self.assertEqual(again["destination"]["id"], product["id"])
        self.assertEqual(ads_backlog.counts()["total"], 1)

    def test_ads_draft_duplicate_is_reported_without_losing_the_card(self):
        rec = self._ads_rec()
        ads_backlog.create_product({"name": "Floral midi dress", "merchant": "Shopee"})
        with self.assertRaises(common.CosmosError) as ctx:
            cosmos_backlog.draft(rec["id"])
        self.assertEqual(ctx.exception.code, "duplicate_product")
        stored = store.get_recommendation(rec["id"])
        self.assertEqual(stored["status"], "proposed")
        self.assertIn("already exists", stored["last_draft_error"])

    def test_youtube_draft_uses_existing_topic_backlog(self):
        rec = self._youtube_rec()
        topic = {"id": "ytt_test", "title": "Small business tax tips", "status": "idea"}
        with mock.patch.object(youtube_topics, "create_topic", return_value=topic) as create:
            result = cosmos_backlog.draft(rec["id"])
        self.assertEqual(result["destination"]["type"], "youtube_topic")
        self.assertEqual(result["destination"]["id"], "ytt_test")
        args, kwargs = create.call_args
        self.assertEqual(args[0], "ytch_test")
        payload = args[1]
        self.assertEqual(payload["title"], "Small business tax tips")
        self.assertEqual(payload["pillar"], "Tax Intelligence")
        self.assertEqual(payload["priority"], "P2")
        self.assertIn("COSMOS", payload["notes"])
        self.assertIn(rec["id"], payload["notes"])
        self.assertEqual(store.get_recommendation(rec["id"])["status"], "drafted")

    def test_reject_never_inserts_into_a_backlog(self):
        rec = self._ads_rec()
        result = cosmos_backlog.reject(rec["id"], "Not a fit")
        self.assertTrue(result["rejected"])
        self.assertEqual(ads_backlog.counts()["total"], 0)
        self.assertEqual(store.get_recommendation(rec["id"])["status"], "rejected")
        with self.assertRaises(common.CosmosError):
            cosmos_backlog.draft(rec["id"])

    def test_refine_runs_same_agent_and_links_cards(self):
        rec = self._ads_rec()
        refined = self._ads_rec("Refined floral dress")
        with mock.patch.object(
            cosmos_backlog.cosmos_engine, "run",
            return_value={"id": "exe_new", "recommendation_ids": [refined["id"]]},
        ) as run:
            result = cosmos_backlog.refine(rec["id"], "Make the reason more specific.")
        self.assertEqual(result["recommendation"]["id"], refined["id"])
        self.assertEqual(result["original"]["status"], "refined")
        self.assertEqual(result["original"]["refined_to"], refined["id"])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["expected_items"], 1)
        self.assertEqual(kwargs["focus"]["owner_instruction"], "Make the reason more specific.")
        self.assertEqual(kwargs["focus"]["recommendation"]["id"], rec["id"])

    def test_refine_without_result_changes_nothing(self):
        rec = self._ads_rec()
        with mock.patch.object(cosmos_backlog.cosmos_engine, "run", return_value={"recommendation_ids": []}):
            with self.assertRaises(common.CosmosError) as ctx:
                cosmos_backlog.refine(rec["id"], "Try again")
        self.assertEqual(ctx.exception.code, "refine_no_result")
        self.assertEqual(store.get_recommendation(rec["id"])["status"], "proposed")

    def test_affiliate_validation_is_separate_and_explicit(self):
        rec = self._ads_rec()
        requested = cosmos_backlog.validate_affiliate(rec["id"], {"confirmed": False})
        self.assertEqual(requested["status"], "requested")
        self.assertEqual(store.get_recommendation(rec["id"])["payload"]["affiliate_status"], "pending")

        with self.assertRaises(common.CosmosError) as ctx:
            cosmos_backlog.validate_affiliate(rec["id"], {"confirmed": True, "program": "Shopee Affiliate"})
        self.assertEqual(ctx.exception.code, "affiliate_url_required")

        with self.assertRaises(common.CosmosError) as ctx:
            cosmos_backlog.validate_affiliate(rec["id"], {"confirmed": True, "url": "https://shopee.ph/x"})
        self.assertEqual(ctx.exception.code, "affiliate_program_required")

        validated = cosmos_backlog.validate_affiliate(rec["id"], {
            "confirmed": True,
            "url": "https://shopee.ph/affiliate/123",
            "program": "Shopee Affiliate",
            "evidence": "Owner screenshot",
        })
        self.assertEqual(validated["status"], "validated")
        self.assertIn("No external provider", validated["note"])
        self.assertEqual(store.get_recommendation(rec["id"])["payload"]["affiliate_status"], "validated")

    def test_affiliate_validation_rejects_youtube_cards(self):
        rec = self._youtube_rec()
        with self.assertRaises(common.CosmosError) as ctx:
            cosmos_backlog.validate_affiliate(rec["id"], {"confirmed": False})
        self.assertEqual(ctx.exception.code, "wrong_division")


if __name__ == "__main__":
    unittest.main()
