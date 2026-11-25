"""Contract tests for the COSMOS HTTP dispatcher.

No server is started: ``handle(method, path, query, body)`` is called directly.

Run:  python3 -m unittest tests.test_cosmos_api
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ads_backlog  # noqa: E402
from backend import cosmos_api  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_config  # noqa: E402
from backend import cosmos_store as store  # noqa: E402
from backend.integrations import youtube as integrations_youtube  # noqa: E402


CHANNEL = {
    "id": "ytch_test",
    "name": "Test Channel",
    "tagline": "Testing",
    "content_niche": "Small business finance",
    "target_audience": "Owners",
    "content_pillars": [{"id": "tax", "label": "Tax Intelligence", "allocation": 100}],
}


class CosmosApiTests(unittest.TestCase):
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
            mock.patch.object(cosmos_api.studio_llm, "status", return_value={"model": "test-model", "provider": "test", "configured": True}),
            mock.patch.object(cosmos_api.youtube_channels, "active_channel", return_value=CHANNEL),
            mock.patch.object(cosmos_api.youtube_channels, "list_channels", return_value={"channels": [CHANNEL]}),
            mock.patch.object(cosmos_api.youtube_channels, "get_channel", return_value=CHANNEL),
            mock.patch.object(cosmos_api.youtube_topics, "list_topics", return_value={
                "counts": {"idea": 2}, "topics": [{"id": "ytt_1", "title": "Tax tips", "status": "idea"}], "total": 1,
            }),
            mock.patch.object(integrations_youtube, "connected_channel", return_value={
                "status": "connected", "id": "UCLive", "name": "Live Channel",
                "handle": "@live", "avatar_url": "https://yt3.example/live.jpg",
                "connection_id": "ca_live", "checked_at": "2026-11-25T00:00:00",
                "source": "composio.youtube.list_channels", "error": None,
            }),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def test_overview_returns_agents_config_channels_and_counts(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/overview")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["agents"]), 5)
        self.assertEqual(len(payload["config"]["ads_templates"]), 5)
        self.assertEqual(len(payload["config"]["youtube_templates"]), 5)
        self.assertEqual(payload["config"]["active_ads_template_id"], "womens-apparel")
        self.assertEqual(payload["channels"][0]["name"], "Test Channel")
        self.assertEqual(payload["youtube_channel"]["name"], "Live Channel")
        self.assertEqual(payload["youtube_channel"]["status"], "connected")
        self.assertTrue(payload["llm"]["configured"])
        self.assertEqual(payload["counts"]["youtube_backlog"]["idea"], 2)

    def test_agents_endpoints(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/agents")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["agents"]), 5)

        status, payload = cosmos_api.handle("GET", "/api/cosmos/agents/helios")
        self.assertEqual(status, 200)
        self.assertEqual(payload["agent"]["name"], "HELIOS")
        self.assertEqual(set(payload["agent"]["divisions"]), {"ads", "youtube"})

        status, payload = cosmos_api.handle("GET", "/api/cosmos/agents/andromeda/executions", {"division": ["ads"]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["executions"], [])

        status, payload = cosmos_api.handle("GET", "/api/cosmos/agents/andromeda/recommendations")
        self.assertEqual(status, 200)
        self.assertEqual(payload["recommendations"], [])

        status, payload = cosmos_api.handle("GET", "/api/cosmos/agents/unknown")
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "unknown_agent")

    def test_config_bootstrap_endpoint(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/config")
        self.assertEqual(status, 200)
        self.assertEqual(payload["config"]["active_youtube_template_id"], "educational-video")
        self.assertEqual(payload["channels"][0]["id"], "ytch_test")

    def test_execute_requires_division_and_delegates(self):
        status, payload = cosmos_api.handle("POST", "/api/cosmos/agents/andromeda/execute", {}, {"task": "Find dresses"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "division_required")

        fake = {"id": "exe_test", "status": "succeeded", "recommendation_ids": []}
        with mock.patch.object(cosmos_api.cosmos_engine, "run", return_value=fake) as run:
            status, payload = cosmos_api.handle(
                "POST", "/api/cosmos/agents/andromeda/execute", {},
                {"division": "ads", "task": "Find dresses", "source_notes": "Owner note"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["execution"]["id"], "exe_test")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["source_notes"], "Owner note")

    def test_recommendation_actions(self):
        with mock.patch.object(cosmos_api.cosmos_backlog, "draft", return_value={"ok": True}) as draft:
            status, payload = cosmos_api.handle("POST", "/api/cosmos/recommendations/rec_1/draft")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        draft.assert_called_once_with("rec_1")

        with mock.patch.object(cosmos_api.cosmos_backlog, "reject", return_value={"rejected": True}):
            status, payload = cosmos_api.handle("POST", "/api/cosmos/recommendations/rec_1/reject", {}, {"note": "no"})
        self.assertEqual(status, 200)

        with mock.patch.object(cosmos_api.cosmos_backlog, "validate_affiliate", return_value={"status": "requested"}):
            status, payload = cosmos_api.handle(
                "POST", "/api/cosmos/recommendations/rec_1/validate-affiliate", {}, {"confirmed": False},
            )
        self.assertEqual(status, 200)

        status, payload = cosmos_api.handle("POST", "/api/cosmos/recommendations/rec_1/unknown")
        self.assertEqual(status, 404)

    def test_template_patch_and_active_selection(self):
        status, payload = cosmos_api.handle(
            "PATCH", "/api/cosmos/templates/ads/womens-apparel", {}, {"name": "Women's Apparel PH"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["template"]["name"], "Women's Apparel PH")

        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/config/active", {}, {"domain": "ads", "template_id": "home-living"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["active_ads_template_id"], "home-living")

        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/config/active", {}, {"channel_id": "ytch_test"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["active_youtube_channel_id"], "ytch_test")

    def test_ads_backlog_routes(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/backlog/ads")
        self.assertEqual(status, 200)
        self.assertEqual(payload["products"], [])

        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/backlog/ads", {},
            {"name": "Floral dress", "merchant": "Shopee", "price": {"amount": 899, "currency": "PHP"}},
        )
        self.assertEqual(status, 200)
        product_id = payload["product"]["id"]
        self.assertEqual(payload["product"]["provenance"]["agent_id"], "owner")

        status, payload = cosmos_api.handle("GET", "/api/cosmos/backlog/ads/%s" % product_id)
        self.assertEqual(status, 200)
        self.assertEqual(payload["product"]["name"], "Floral dress")

        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/backlog/ads/%s/status" % product_id, {}, {"status": "reviewing"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["product"]["status"], "reviewing")

        status, payload = cosmos_api.handle(
            "PATCH", "/api/cosmos/backlog/ads/%s" % product_id, {}, {"name": "Floral maxi dress"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["product"]["name"], "Floral maxi dress")

        status, payload = cosmos_api.handle("DELETE", "/api/cosmos/backlog/ads/%s" % product_id)
        self.assertEqual(status, 200)
        self.assertEqual(payload["deleted"], product_id)

    def test_youtube_backlog_lists_with_channel_context(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/backlog/youtube")
        self.assertEqual(status, 200)
        self.assertEqual(payload["channel_id"], "ytch_test")
        self.assertEqual(payload["channel_name"], "Test Channel")
        self.assertEqual(payload["topics"][0]["id"], "ytt_1")
        self.assertIn("has_active_production", payload["topics"][0])

    def test_youtube_backlog_writes_go_through_cosmos(self):
        with mock.patch.object(
            cosmos_api.youtube_topics, "create_topic",
            return_value={"id": "ytt_2", "title": "New topic", "status": "idea"},
        ) as create:
            status, payload = cosmos_api.handle(
                "POST", "/api/cosmos/backlog/youtube", {}, {"title": "New topic"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["topic"]["id"], "ytt_2")
        create.assert_called_once()

        with mock.patch.object(
            cosmos_api.youtube_topics, "set_status",
            return_value={"id": "ytt_2", "title": "New topic", "status": "researching"},
        ) as set_status:
            status, payload = cosmos_api.handle(
                "POST", "/api/cosmos/backlog/youtube/ytt_2/status", {}, {"status": "researching"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["topic"]["status"], "researching")
        set_status.assert_called_once()

        with mock.patch.object(
            cosmos_api.youtube_topics, "update_topic",
            return_value={"id": "ytt_2", "title": "Renamed", "status": "idea"},
        ) as update:
            status, payload = cosmos_api.handle(
                "PATCH", "/api/cosmos/backlog/youtube/ytt_2", {}, {"title": "Renamed"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["topic"]["title"], "Renamed")
        update.assert_called_once()

        with mock.patch.object(
            cosmos_api.youtube_topics, "delete_topic", return_value={"deleted": "ytt_2"},
        ) as delete:
            status, payload = cosmos_api.handle(
                "DELETE", "/api/cosmos/backlog/youtube/ytt_2", {"channel_id": "ytch_test"}, None,
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["deleted"], "ytt_2")
        delete.assert_called_once()

    def test_youtube_topic_detail_includes_script_studio_and_genesis_links(self):
        topic = {
            "id": "ytt_1", "title": "Tax tips", "status": "scripted",
            "script_id": "yts_1", "project_id": "ytp_1", "channel_id": "ytch_test",
        }
        script = {"id": "yts_1", "title": "Tax tips script", "status": "approved", "sections": [{}], "word_count": 900}
        studio_project = {"id": "ytp_1", "title": "Tax tips project", "status": "approved", "stage": "assets"}
        genesis_project = {"id": "gp_1", "title": "Tax tips production", "status": "in_production", "stage": "assets"}
        with mock.patch.object(cosmos_api.youtube_topics, "get_topic", return_value=topic), \
                mock.patch.object(cosmos_api.youtube_topics, "find_duplicates", return_value=[]), \
                mock.patch.object(cosmos_api.youtube_scripts, "get_script", return_value=script), \
                mock.patch.object(cosmos_api.studio_workspaces, "get_project", return_value=studio_project), \
                mock.patch.object(cosmos_api.genesis, "active_source_index", return_value={"ytt_1": genesis_project}), \
                mock.patch.object(cosmos_api.genesis, "source_projects", return_value=[genesis_project]), \
                mock.patch.object(cosmos_api.genesis_store, "list_publications", return_value=[]):
            status, payload = cosmos_api.handle("GET", "/api/cosmos/backlog/youtube/ytt_1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["script"]["title"], "Tax tips script")
        self.assertEqual(payload["studio_project"]["status"], "approved")
        self.assertEqual(payload["genesis_projects"][0]["id"], "gp_1")
        self.assertTrue(payload["topic"]["has_active_production"])
        self.assertFalse(payload["queue_eligible"])

    def test_execution_and_recommendation_reads(self):
        execution = store.start_execution("andromeda", "ads", "ads.discovery", "Find", "m", "p", {}, "sha")
        store.finish_execution(execution["id"], "succeeded", result={"summary": "ok"})
        rec = store.create_recommendation(
            "andromeda", "ads", execution["id"], "ads.discovery", "womens-apparel",
            {}, "Floral dress", "why", {"name": "Floral dress"}, [], 80,
        )
        status, payload = cosmos_api.handle("GET", "/api/cosmos/executions/%s" % execution["id"])
        self.assertEqual(status, 200)
        self.assertEqual(payload["execution"]["status"], "succeeded")

        status, payload = cosmos_api.handle("GET", "/api/cosmos/recommendations/%s" % rec["id"])
        self.assertEqual(status, 200)
        self.assertEqual(payload["recommendation"]["title"], "Floral dress")
        self.assertEqual(payload["execution"]["id"], execution["id"])

    def test_unknown_route_and_method(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/nope")
        self.assertEqual(status, 404)
        status, payload = cosmos_api.handle("PUT", "/api/cosmos/overview")
        self.assertEqual(status, 405)
        self.assertEqual(payload["code"], "method_not_allowed")


if __name__ == "__main__":
    unittest.main()
