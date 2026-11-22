"""Tests for per-intelligence/per-division Agent+Model selections in COSMOS.

Covers the ``cosmos_llm`` store, the HTTP endpoints and the engine's routing:
a division with its own choice runs with that agent/model, everything else
inherits the shared Studio selection.

Run:  python3 -m unittest tests.test_cosmos_llm
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import cosmos_api  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_config  # noqa: E402
from backend import cosmos_engine as engine  # noqa: E402
from backend import cosmos_llm  # noqa: E402

ADS_TEMPLATE = {
    "id": "womens-apparel",
    "name": "Women's Apparel",
    "product_category": "Women's fashion and apparel",
    "target_audience": "Women aged 18-35",
    "product_interests": ["Dresses"],
    "creative_directions": ["Outfit demonstrations"],
    "merchants": ["Shopee", "Lazada"],
    "price_range": {"min": 200, "max": 2500, "currency": "PHP"},
    "video_format": "9:16 short-form",
    "product_restrictions": [],
}

CHANNEL = {
    "id": "ytch_test",
    "name": "Test Channel",
    "content_niche": "Small business finance",
    "target_audience": "Owners",
    "content_pillars": [{"id": "tax", "label": "Tax Intelligence", "allocation": 100}],
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


def ads_item(index):
    return {
        "name": "Floral midi dress %d" % index,
        "description": "A floral dress for casual outfits.",
        "category": "Dresses",
        "merchant": "Shopee",
        "price": 899,
        "currency": "PHP",
        "price_verified": False,
        "target_audience": "Women aged 18-35",
        "reason": "Matches the Dresses interest for women.",
        "sources": [],
        "relevance_score": 82,
        "affiliate_status": "unvalidated",
        "creative_format": "9:16 try-on",
        "evidence_status": "hypothesis",
    }


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "COSMOS_DIR", self.dir),
            mock.patch.object(cosmos_llm, "LLM_FILE", os.path.join(self.dir, "llm.json")),
            mock.patch.object(
                cosmos_llm.studio_llm, "selection",
                return_value={"agent_id": "studio-agent", "model": "studio-model"},
            ),
            mock.patch.object(
                cosmos_llm.agent_registry, "get_agent",
                side_effect=lambda agent_id: {"id": agent_id} if agent_id in ("opencode", "studio-agent") else None,
            ),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_empty_store_inherits_the_studio_selection(self):
        self.assertIsNone(cosmos_llm.get("andromeda", "ads"))
        self.assertEqual(
            cosmos_llm.resolve("andromeda", "ads"),
            {"agent_id": "studio-agent", "model": "studio-model"},
        )
        self.assertEqual(cosmos_llm.public_selections(), {})

    def test_configure_persists_one_division_only(self):
        selection = cosmos_llm.configure("andromeda", "ads", "opencode", "zai/glm-5.3-flash")
        self.assertEqual(selection, {"agent_id": "opencode", "model": "zai/glm-5.3-flash"})
        self.assertEqual(cosmos_llm.get("andromeda", "ads"), selection)
        # The sister division still inherits the Studio choice.
        self.assertIsNone(cosmos_llm.get("andromeda", "youtube"))
        self.assertEqual(
            cosmos_llm.resolve("andromeda", "youtube"),
            {"agent_id": "studio-agent", "model": "studio-model"},
        )
        self.assertEqual(
            cosmos_llm.public_selections(),
            {"andromeda": {"ads": selection}},
        )

    def test_configure_can_clear_a_model_to_agent_default(self):
        cosmos_llm.configure("polaris", "youtube", "opencode", "zai/glm-5.3-flash")
        selection = cosmos_llm.configure("polaris", "youtube", "opencode", "")
        self.assertEqual(selection, {"agent_id": "opencode", "model": ""})

    def test_configure_rejects_unknown_values(self):
        with self.assertRaises(common.CosmosError) as ctx:
            cosmos_llm.configure("ghost", "ads", "opencode", "")
        self.assertEqual(ctx.exception.code, "unknown_intelligence")
        with self.assertRaises(common.CosmosError) as ctx:
            cosmos_llm.configure("andromeda", "instagram", "opencode", "")
        self.assertEqual(ctx.exception.code, "unknown_division")
        with self.assertRaises(common.CosmosError) as ctx:
            cosmos_llm.configure("andromeda", "ads", "ghost", "")
        self.assertEqual(ctx.exception.code, "invalid_agent")


class EngineRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "EXECUTIONS_FILE", os.path.join(self.dir, "executions.json")),
            mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json")),
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json")),
            mock.patch.object(common, "COSMOS_DIR", self.dir),
            mock.patch.object(cosmos_llm, "LLM_FILE", os.path.join(self.dir, "llm.json")),
            mock.patch.object(
                cosmos_llm.studio_llm, "selection",
                return_value={"agent_id": "studio-agent", "model": "studio-model"},
            ),
            mock.patch.object(
                cosmos_llm.agent_registry, "get_agent",
                side_effect=lambda agent_id: {"id": agent_id} if agent_id in ("opencode", "studio-agent") else None,
            ),
            mock.patch.object(engine.cosmos_config, "effective", return_value=ADS_SNAPSHOT),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)
        self.calls = []
        self.complete_patch = mock.patch.object(
            engine.studio_llm, "complete",
            side_effect=self._capture_complete,
        )
        self.complete_patch.start()
        self.addCleanup(self.complete_patch.stop)

    def _capture_complete(self, messages, agent_id=None, model_id=None, **kwargs):
        self.calls.append({"agent_id": agent_id, "model_id": model_id})
        return {"content": json.dumps({"summary": "Five.", "items": [ads_item(i) for i in range(5)]})}

    def test_division_selection_routes_the_run(self):
        cosmos_llm.configure("andromeda", "ads", "opencode", "zai/glm-5.3-flash")
        execution = engine.run("andromeda", "ads", "Find dresses")
        self.assertEqual(execution["status"], "succeeded")
        self.assertEqual(self.calls[-1]["agent_id"], "opencode")
        self.assertEqual(self.calls[-1]["model_id"], "zai/glm-5.3-flash")
        self.assertEqual(execution["provider"], "opencode")
        self.assertEqual(execution["model"], "zai/glm-5.3-flash")

    def test_division_without_selection_inherits_the_studio_choice(self):
        execution = engine.run("andromeda", "ads", "Find dresses")
        self.assertEqual(execution["status"], "succeeded")
        self.assertEqual(self.calls[-1]["agent_id"], "studio-agent")
        self.assertEqual(self.calls[-1]["model_id"], "studio-model")
        self.assertEqual(execution["provider"], "studio-agent")

    def test_sister_divisions_route_independently(self):
        cosmos_llm.configure("andromeda", "ads", "opencode", "zai/glm-5.3-flash")
        engine.run("andromeda", "ads", "Find dresses")
        engine.run("andromeda", "youtube", "Find topics")
        self.assertEqual(self.calls[0]["agent_id"], "opencode")
        self.assertEqual(self.calls[1]["agent_id"], "studio-agent")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "COSMOS_DIR", self.dir),
            mock.patch.object(cosmos_llm, "LLM_FILE", os.path.join(self.dir, "llm.json")),
            mock.patch.object(common, "CONFIG_FILE", os.path.join(self.dir, "config.json")),
            mock.patch.object(common, "EXECUTIONS_FILE", os.path.join(self.dir, "executions.json")),
            mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json")),
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json")),
            mock.patch.object(cosmos_config.studio_workspaces, "get_settings", return_value={}),
            mock.patch.object(cosmos_api.studio_llm, "status", return_value={
                "agent_id": "studio-agent", "agent_name": "Studio Agent",
                "model": "studio-model", "configured": True,
            }),
            mock.patch.object(
                cosmos_api.studio_llm, "selection",
                return_value={"agent_id": "studio-agent", "model": "studio-model"},
            ),
            mock.patch.object(
                cosmos_api.cosmos_llm.agent_registry, "get_agent",
                side_effect=lambda agent_id: {"id": agent_id} if agent_id in ("opencode", "studio-agent") else None,
            ),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_llm_endpoint_round_trip(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/llm/andromeda/ads")
        self.assertEqual(status, 200)
        self.assertTrue(payload["inherited"])
        self.assertEqual(payload["selection"], {"agent_id": "studio-agent", "model": "studio-model"})

        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/llm/andromeda/ads",
            body={"agent_id": "opencode", "model": "zai/glm-5.3-flash"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["selection"], {"agent_id": "opencode", "model": "zai/glm-5.3-flash"})

        status, payload = cosmos_api.handle("GET", "/api/cosmos/llm/andromeda/ads")
        self.assertEqual(status, 200)
        self.assertFalse(payload["inherited"])
        self.assertEqual(payload["selection"]["agent_id"], "opencode")

    def test_overview_exposes_llm_selections(self):
        cosmos_llm.configure("helios", "youtube", "opencode", "")
        status, payload = cosmos_api.handle("GET", "/api/cosmos/overview")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["llm_selections"],
            {"helios": {"youtube": {"agent_id": "opencode", "model": ""}}},
        )

    def test_llm_endpoint_validates_inputs(self):
        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/llm/ghost/ads", body={"agent_id": "opencode"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "unknown_intelligence")
        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/llm/andromeda/ads", body={"agent_id": "ghost"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "invalid_agent")


if __name__ == "__main__":
    unittest.main()
