"""Unit tests for the COSMOS execution engine.

The LLM is always mocked: no provider is contacted and no network is used.

Run:  python3 -m unittest tests.test_cosmos_engine
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ads_backlog  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_config  # noqa: E402
from backend import cosmos_engine as engine  # noqa: E402
from backend import cosmos_store as store  # noqa: E402


ADS_TEMPLATE = {
    "id": "womens-apparel",
    "name": "Women's Apparel",
    "product_category": "Women's fashion and apparel",
    "target_audience": "Women aged 18-35",
    "product_interests": ["Dresses", "Casual outfits"],
    "creative_directions": ["Outfit demonstrations"],
    "merchants": ["Shopee", "Lazada"],
    "price_range": {"min": 200, "max": 2500, "currency": "PHP"},
    "video_format": "9:16 short-form",
    "product_restrictions": [],
}

YOUTUBE_TEMPLATE = {
    "id": "educational-video",
    "name": "Educational Video",
    "video_style": "Animated educational explainer",
    "format": "longform",
    "structure": ["Hook", "Explanation"],
    "audience_interests": ["Practical tutorials"],
    "restrictions": [],
}

CHANNEL = {
    "id": "ytch_test",
    "name": "The Business Money Blueprint",
    "content_niche": "Small Business Finance and Tax Education",
    "target_audience": "U.S. small business owners",
    "content_pillars": [{"id": "tax", "label": "Tax Intelligence", "allocation": 100}],
    "language": "en-US",
    "country": "US",
}

ADS_SNAPSHOT = {
    "domain": "ads",
    "template_id": "womens-apparel",
    "template": ADS_TEMPLATE,
    "workspace_settings": {},
    "backlog": {"counts": {}, "active_titles": []},
    "source_priority": ["explicit user instruction"],
    "captured_at": 1,
    "captured_iso": "2026-11-15T00:00:00",
}

YOUTUBE_SNAPSHOT = {
    "domain": "youtube",
    "template_id": "educational-video",
    "template": YOUTUBE_TEMPLATE,
    "channel": CHANNEL,
    "channel_id": "ytch_test",
    "backlog": {"counts": {}, "existing_titles": []},
    "source_priority": ["explicit user instruction"],
    "captured_at": 1,
    "captured_iso": "2026-11-15T00:00:00",
}


def ads_item(index, score=82, merchant="Shopee"):
    return {
        "name": "Floral midi dress %d" % index,
        "description": "A floral dress for casual outfits.",
        "category": "Dresses",
        "merchant": merchant,
        "price": 899,
        "currency": "PHP",
        "price_verified": False,
        "target_audience": "Women aged 18-35",
        "reason": "Matches the Dresses interest and outfit demonstrations for women.",
        "sources": [],
        "relevance_score": score,
        "affiliate_status": "unvalidated",
        "creative_format": "9:16 try-on",
        "evidence_status": "hypothesis",
    }


def youtube_item(index, pillar="Tax Intelligence", score=80):
    return {
        "title": "Small business tax tip %d" % index,
        "description": "A practical tax explanation for small business owners.",
        "pillar": pillar,
        "target_audience": "U.S. small business owners",
        "video_style": "Animated educational explainer",
        "reason": "Fits the Tax Intelligence pillar and channel niche.",
        "sources": [],
        "relevance_score": score,
        "opportunity": "Clear audience question",
        "competition": None,
        "audience_question": "How do I file this deduction?",
        "suggested_format": "longform",
        "search_intent": "informational",
        "commercial_relevance": "medium",
        "evidence_status": "hypothesis",
    }


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "EXECUTIONS_FILE", os.path.join(self.dir, "executions.json")),
            mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json")),
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json")),
            mock.patch.object(
                engine.cosmos_llm, "resolve",
                return_value={"agent_id": "test-agent", "model": "test-model"},
            ),
            mock.patch.object(engine.cosmos_config, "effective", side_effect=self._effective),
        ]
        for patch in self.patches:
            patch.start()
        self.ads_snapshot = dict(ADS_SNAPSHOT)
        self.youtube_snapshot = dict(YOUTUBE_SNAPSHOT)

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _effective(self, domain, template_id=None, channel_id=None):
        return self.ads_snapshot if domain == "ads" else self.youtube_snapshot

    def _complete(self, payload):
        return mock.patch.object(
            engine.studio_llm, "complete",
            return_value={"content": json.dumps(payload)},
        )

    def test_discovery_persists_five_qualified_recommendations(self):
        payload = {"summary": "Five dresses.", "items": [ads_item(i) for i in range(5)]}
        with self._complete(payload):
            execution = engine.run("andromeda", "ads", "Find five dresses")
        self.assertEqual(execution["status"], "succeeded")
        self.assertFalse(execution["insufficient_results"])
        self.assertEqual(len(execution["recommendation_ids"]), 5)
        recs = store.list_recommendations(agent_id="andromeda", division="ads")["recommendations"]
        self.assertEqual(len(recs), 5)
        self.assertEqual(recs[0]["config_snapshot"]["template_id"], "womens-apparel")
        self.assertEqual(recs[0]["payload"]["affiliate_status"], "unvalidated")
        self.assertEqual(recs[0]["payload"]["evidence_status"], "hypothesis")
        self.assertEqual(execution["model"], "test-model")

    def test_low_relevance_and_unrelated_items_are_filtered_honestly(self):
        unrelated = {
            "name": "Gaming laptop",
            "description": "A powerful gaming laptop",
            "category": "Electronics",
            "merchant": "Unknown Store",
            "reason": "Trending now",
            "relevance_score": 90,
        }
        payload = {"summary": "Two dresses.", "items": [ads_item(0), ads_item(1), unrelated, ads_item(2, score=20)]}
        with self._complete(payload):
            execution = engine.run("andromeda", "ads", "Find dresses")
        self.assertEqual(len(execution["recommendation_ids"]), 2)
        self.assertTrue(execution["insufficient_results"])
        self.assertEqual(execution["result"]["filtered_out"], 2)
        reasons = " ".join(entry["reason"] for entry in execution["result"]["filtered"])
        self.assertIn("below the 60 threshold", reasons)
        self.assertIn("does not match the active Ads", reasons)
        self.assertIn("No unrelated filler", execution["result"]["summary"])

    def test_youtube_qualification_uses_channel_pillar(self):
        off_topic = {
            "title": "Celebrity cooking challenge",
            "description": "A fun cooking competition with celebrity guests.",
            "pillar": "Cooking",
            "target_audience": "Food fans",
            "video_style": "Entertainment",
            "reason": "Popular trend",
            "sources": [],
            "relevance_score": 88,
            "audience_question": "Who won the challenge?",
            "suggested_format": "longform",
            "search_intent": "informational",
            "commercial_relevance": "low",
            "evidence_status": "hypothesis",
        }
        payload = {"summary": "Mixed.", "items": [youtube_item(i) for i in range(5)] + [off_topic]}
        with self._complete(payload):
            execution = engine.run("andromeda", "youtube", "Find tax topics")
        self.assertEqual(len(execution["recommendation_ids"]), 5)
        self.assertEqual(execution["result"]["filtered_out"], 1)

    def test_dedupe_against_existing_recommendation_and_within_run(self):
        first = ads_item(0)
        store.create_recommendation(
            "andromeda", "ads", "exe_old", "ads.discovery", "womens-apparel",
            self.ads_snapshot, first["name"], "old", first, [], 80,
            evidence_status="hypothesis",
        )
        payload = {"summary": "Some.", "items": [ads_item(0), ads_item(1), ads_item(1)]}
        with self._complete(payload):
            execution = engine.run("andromeda", "ads", "Find dresses")
        self.assertEqual(len(execution["recommendation_ids"]), 1)
        self.assertEqual(execution["duplicates_skipped"], 2)
        self.assertTrue(execution["insufficient_results"])

    def test_invalid_model_output_marks_execution_failed(self):
        with mock.patch.object(engine.studio_llm, "complete", return_value={"content": "not json at all"}):
            with self.assertRaises(common.CosmosError) as ctx:
                engine.run("andromeda", "ads", "Find dresses")
        self.assertEqual(ctx.exception.code, "invalid_model_output")
        execution = store.get_execution(ctx.exception.execution_id)
        self.assertEqual(execution["status"], "failed")
        self.assertEqual(execution["error"]["code"], "invalid_model_output")

    def test_llm_failure_marks_execution_failed(self):
        with mock.patch.object(engine.studio_llm, "complete", side_effect=RuntimeError("provider down")):
            with self.assertRaises(common.CosmosError) as ctx:
                engine.run("andromeda", "ads", "Find dresses")
        self.assertEqual(ctx.exception.status, 502)
        execution = store.get_execution(ctx.exception.execution_id)
        self.assertEqual(execution["status"], "failed")
        self.assertIn("provider down", execution["error"]["message"])

    def test_missing_items_list_is_invalid(self):
        payload = {"summary": "I did not produce items."}
        with self._complete(payload):
            with self.assertRaises(common.CosmosError) as ctx:
                engine.run("andromeda", "youtube", "Find topics")
        self.assertEqual(ctx.exception.code, "invalid_model_output")
        self.assertEqual(store.get_execution(ctx.exception.execution_id)["status"], "failed")

    def test_helios_drops_unsourced_measurements(self):
        payload = {
            "summary": "Performance.",
            "items": [{
                "subject": "TikTok product video",
                "period": "Last 7 days",
                "measurements": [
                    {"name": "Views", "value": 1200, "unit": "views", "basis": "measured", "source": "TikTok analytics screenshot"},
                    {"name": "Clicks", "value": 80, "unit": "clicks", "basis": "measured", "source": ""},
                    {"name": "Revenue", "value": 500, "unit": "PHP", "basis": "guess", "source": "model"},
                ],
                "insights": ["Views are stable."],
                "patterns": [],
                "next_actions": ["Keep the hook."],
            }],
        }
        with self._complete(payload):
            execution = engine.run("helios", "ads", "Analyse ads performance")
        item = execution["result"]["items"][0]
        self.assertEqual(len(item["measurements"]), 1)
        self.assertEqual(item["measurements"][0]["name"], "Views")
        self.assertTrue(any("omitted" in text for text in item["insights"]))
        self.assertEqual(execution["recommendation_ids"], [])

    def test_helios_with_no_measurements_says_data_is_missing(self):
        payload = {"summary": "No data.", "items": [{"subject": "YouTube video", "measurements": []}]}
        with self._complete(payload):
            execution = engine.run("helios", "youtube", "Analyse performance")
        item = execution["result"]["items"][0]
        self.assertEqual(item["measurements"], [])
        self.assertTrue(any("No valid measurements" in text for text in item["insights"]))

    def test_strategy_and_creative_runs_do_not_create_recommendations(self):
        strategy = {"summary": "Ranked.", "items": [{
            "subject": "Floral dress", "verdict": "proceed", "analysis": "Good fit",
            "strengths": ["Affordable"], "risks": ["Seasonal"], "priority": "P1",
            "recommended_actions": ["Test a try-on"], "evidence_status": "hypothesis",
        }]}
        with self._complete(strategy):
            execution = engine.run("polaris", "ads", "Rank dresses")
        self.assertEqual(execution["recommendation_ids"], [])
        self.assertEqual(execution["result"]["kind"], "analysis")
        self.assertEqual(execution["result"]["items"][0]["priority"], "P1")
        self.assertEqual(store.recommendation_counts(agent_id="polaris")["total"], 0)

    def test_execution_snapshot_survives_later_config_edits(self):
        payload = {"summary": "Five.", "items": [ads_item(i) for i in range(5)]}
        with self._complete(payload):
            execution = engine.run("andromeda", "ads", "Find dresses")
        frozen = execution["config_snapshot"]["template"]["name"]
        self.ads_snapshot = dict(ADS_SNAPSHOT)
        self.ads_snapshot["template"] = dict(ADS_TEMPLATE, name="Renamed later")
        with self._complete({"summary": "Again.", "items": [ads_item(i + 10) for i in range(5)]}):
            engine.run("andromeda", "ads", "Find dresses again")
        self.assertEqual(store.get_execution(execution["id"])["config_snapshot"]["template"]["name"], frozen)
        self.assertNotEqual(frozen, "Renamed later")


if __name__ == "__main__":
    unittest.main()
