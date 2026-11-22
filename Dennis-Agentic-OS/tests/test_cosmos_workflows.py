"""End-to-end COSMOS workflows with a mocked LLM.

Proves the full path: configuration -> execution -> recommendation card ->
draft into the correct backlog, plus agent isolation and snapshot immutability.

Run:  python3 -m unittest tests.test_cosmos_workflows
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ads_backlog  # noqa: E402
from backend import cosmos_backlog  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_config  # noqa: E402
from backend import cosmos_engine as engine  # noqa: E402
from backend import cosmos_store as store  # noqa: E402
from backend import youtube_topics  # noqa: E402


CHANNEL = {
    "id": "ytch_test",
    "name": "The Business Money Blueprint",
    "tagline": "Build Smarter Businesses.",
    "description": "Small business finance education.",
    "content_niche": "Small Business Finance and Tax Education",
    "target_audience": "U.S. small business owners",
    "audience_age_focus": "25-54",
    "language": "en-US",
    "country": "US",
    "content_pillars": [
        {"id": "tax", "label": "Tax Intelligence", "allocation": 60},
        {"id": "finance", "label": "Business Finance", "allocation": 40},
    ],
    "publishing": {},
}


def ads_items():
    return [{
        "name": "Floral midi dress %d" % index,
        "description": "A floral dress for casual outfits.",
        "category": "Dresses",
        "merchant": "Shopee",
        "price": 899,
        "target_audience": "Women aged 18-35",
        "reason": "Matches Dresses and casual outfits for women.",
        "sources": [],
        "relevance_score": 82,
        "affiliate_status": "unvalidated",
        "creative_format": "9:16 try-on",
    } for index in range(5)]


def youtube_items():
    return [{
        "title": "Small business tax tip %d" % index,
        "description": "A practical tax explanation for small business owners.",
        "pillar": "Tax Intelligence",
        "target_audience": "U.S. small business owners",
        "video_style": "Animated educational explainer",
        "reason": "Fits the Tax Intelligence pillar.",
        "sources": [],
        "relevance_score": 85,
        "audience_question": "How does this deduction work?",
        "suggested_format": "longform",
        "search_intent": "informational",
        "commercial_relevance": "medium",
    } for index in range(5)]


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "CONFIG_FILE", os.path.join(self.dir, "config.json")),
            mock.patch.object(common, "EXECUTIONS_FILE", os.path.join(self.dir, "executions.json")),
            mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json")),
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json")),
            mock.patch.object(cosmos_config.studio_workspaces, "get_settings", return_value={}),
            mock.patch.object(cosmos_config, "_resolve_channel", return_value=CHANNEL),
            mock.patch.object(
                engine.cosmos_llm, "resolve",
                return_value={"agent_id": "test-agent", "model": "test-model"},
            ),
            mock.patch.object(cosmos_backlog.youtube_channels, "get_channel", return_value={"id": "ytch_test"}),
            mock.patch.object(cosmos_backlog.youtube_channels, "active_channel", return_value={"id": "ytch_test"}),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _run(self, agent, division, items, task="Run task"):
        payload = {"summary": "Found %d." % len(items), "items": items}
        with mock.patch.object(engine.studio_llm, "complete", return_value={"content": json.dumps(payload)}):
            return engine.run(agent, division, task)

    def test_ads_discovery_to_product_backlog(self):
        execution = self._run("andromeda", "ads", ads_items(), "Find five dresses")
        self.assertEqual(execution["status"], "succeeded")
        self.assertEqual(len(execution["recommendation_ids"]), 5)
        recommendation = store.get_recommendation(execution["recommendation_ids"][0])
        self.assertEqual(recommendation["division"], "ads")
        self.assertEqual(recommendation["payload"]["affiliate_status"], "unvalidated")
        self.assertEqual(recommendation["config_snapshot"]["template"]["name"], "Women's Apparel")

        drafted = cosmos_backlog.draft(recommendation["id"])
        product = ads_backlog.get_product(drafted["destination"]["id"])
        self.assertEqual(product["provenance"]["agent_id"], "andromeda")
        self.assertEqual(product["provenance"]["execution_id"], execution["id"])
        self.assertEqual(product["status"], "drafted")
        self.assertEqual(ads_backlog.counts()["total"], 1)
        # Nothing was auto-published.
        self.assertEqual(ads_backlog.counts()["published"], 0)

    def test_youtube_discovery_to_existing_topic_backlog(self):
        execution = self._run("andromeda", "youtube", youtube_items(), "Find tax topics")
        self.assertEqual(len(execution["recommendation_ids"]), 5)
        recommendation = store.get_recommendation(execution["recommendation_ids"][0])
        self.assertEqual(recommendation["config_snapshot"]["channel"]["id"], "ytch_test")

        with mock.patch.object(
            youtube_topics, "create_topic",
            return_value={"id": "ytt_test", "title": recommendation["title"], "status": "idea"},
        ) as create:
            drafted = cosmos_backlog.draft(recommendation["id"])
        self.assertEqual(drafted["destination"]["type"], "youtube_topic")
        payload = create.call_args.args[1]
        self.assertEqual(payload["title"], recommendation["title"])
        self.assertEqual(payload["pillar"], "Tax Intelligence")
        self.assertEqual(payload["status"] if "status" in payload else "idea", "idea")
        self.assertIn(recommendation["id"], payload["notes"])

    def test_template_edit_does_not_rewrite_historical_execution(self):
        execution = self._run("andromeda", "ads", ads_items(), "Find dresses")
        original_name = store.get_execution(execution["id"])["config_snapshot"]["template"]["name"]
        cosmos_config.update_template("ads", "womens-apparel", {"name": "Renamed after the run"})
        reloaded = store.get_execution(execution["id"])
        self.assertEqual(reloaded["config_snapshot"]["template"]["name"], original_name)
        self.assertNotEqual(original_name, "Renamed after the run")
        freshness = cosmos_config.effective("ads")
        self.assertEqual(freshness["template"]["name"], "Renamed after the run")

    def test_agents_have_isolated_history(self):
        andromeda_execution = self._run("andromeda", "ads", ads_items(), "Find dresses")
        polaris_execution = self._run("polaris", "ads", [{
            "subject": "Floral dress",
            "verdict": "proceed",
            "analysis": "Strong fit.",
            "strengths": ["Affordable"],
            "risks": ["Seasonal"],
            "priority": "P1",
            "recommended_actions": ["Test a try-on"],
        }], "Evaluate the products")

        andromeda_rows = store.list_executions(agent_id="andromeda")["executions"]
        polaris_rows = store.list_executions(agent_id="polaris")["executions"]
        self.assertEqual([row["id"] for row in andromeda_rows], [andromeda_execution["id"]])
        self.assertEqual([row["id"] for row in polaris_rows], [polaris_execution["id"]])
        self.assertEqual(store.recommendation_counts(agent_id="polaris")["total"], 0)

    def test_insufficient_results_never_fabricates_cards(self):
        execution = self._run("andromeda", "ads", ads_items()[:3], "Find five dresses")
        self.assertTrue(execution["insufficient_results"])
        self.assertEqual(len(execution["recommendation_ids"]), 3)
        self.assertEqual(len(store.list_recommendations(agent_id="andromeda", division="ads")["recommendations"]), 3)
        self.assertIn("No unrelated filler", execution["result"]["summary"])

    def test_separate_divisions_have_separate_recommendation_feeds(self):
        self._run("andromeda", "ads", ads_items(), "Find dresses")
        self._run("andromeda", "youtube", youtube_items(), "Find topics")
        ads_rows = store.list_recommendations(agent_id="andromeda", division="ads")
        youtube_rows = store.list_recommendations(agent_id="andromeda", division="youtube")
        self.assertEqual(ads_rows["total"], 5)
        self.assertEqual(youtube_rows["total"], 5)
        for row in ads_rows["recommendations"]:
            self.assertEqual(row["division"], "ads")
            self.assertIn("name", row["payload"])
        for row in youtube_rows["recommendations"]:
            self.assertEqual(row["division"], "youtube")
            self.assertIn("title", row["payload"])


if __name__ == "__main__":
    unittest.main()
