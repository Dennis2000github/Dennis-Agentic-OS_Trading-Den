"""Tests for COSMOS autonomy: modes, owner-feedback loop, five-card cap.

Covers ``cosmos_modes`` (autonomous/override per intelligence+division),
``cosmos_feedback`` (reject/refine preset recording + prompt context) and
``cosmos_store.enforce_proposed_cap`` (the five-card board), plus the HTTP
endpoints that drive them.

Run:  python3 -m unittest tests.test_cosmos_autonomy
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import cosmos_api  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_config  # noqa: E402
from backend import cosmos_feedback  # noqa: E402
from backend import cosmos_modes  # noqa: E402
from backend import cosmos_store as store  # noqa: E402

CHANNEL = {
    "id": "ytch_test",
    "name": "Test Channel",
    "content_niche": "Small business finance",
    "target_audience": "Owners",
}


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "CONFIG_FILE", os.path.join(self.dir, "config.json")),
            mock.patch.object(common, "EXECUTIONS_FILE", os.path.join(self.dir, "executions.json")),
            mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json")),
            mock.patch.object(cosmos_modes, "MODES_FILE", os.path.join(self.dir, "modes.json")),
            mock.patch.object(cosmos_feedback, "FEEDBACK_FILE", os.path.join(self.dir, "feedback.json")),
            mock.patch.object(cosmos_config.studio_workspaces, "get_settings", return_value={}),
            mock.patch.object(cosmos_api.studio_llm, "status",
                              return_value={"model": "t", "provider": "t", "configured": True}),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    # ------------------------------------------------------------- modes
    def test_default_mode_is_autonomous_without_storage(self):
        self.assertEqual(cosmos_modes.get("andromeda", "ads")["mode"], "autonomous")
        self.assertTrue(cosmos_modes.is_autonomous("andromeda", "youtube"))

    def test_set_mode_persists_and_validates(self):
        entry = cosmos_modes.set_mode("andromeda", "ads", "override")
        self.assertEqual(entry["mode"], "override")
        self.assertIsNone(entry["next_run_iso"])
        self.assertFalse(cosmos_modes.is_autonomous("andromeda", "ads"))
        with self.assertRaises(common.CosmosError):
            cosmos_modes.set_mode("andromeda", "ads", "yolo")
        with self.assertRaises(common.CosmosError):
            cosmos_modes.set_mode("nobody", "ads", "override")

    def test_due_rearms_after_record_run(self):
        self.assertTrue(cosmos_modes.due("pleiades", "ads"))
        cosmos_modes.record_run("pleiades", "ads", "cycle")
        self.assertFalse(cosmos_modes.due("pleiades", "ads"))
        entry = cosmos_modes.get("pleiades", "ads")
        self.assertEqual(entry["last_trigger"], "cycle")
        self.assertIsNotNone(entry["next_run_iso"])

    def test_override_divisions_are_never_due(self):
        cosmos_modes.set_mode("helios", "youtube", "override")
        self.assertFalse(cosmos_modes.due("helios", "youtube"))

    def test_claim_release_guards_double_runs(self):
        self.assertTrue(cosmos_modes.try_claim("quasar", "ads"))
        self.assertFalse(cosmos_modes.try_claim("quasar", "ads"))
        cosmos_modes.release("quasar", "ads")
        self.assertTrue(cosmos_modes.try_claim("quasar", "ads"))
        cosmos_modes.release("quasar", "ads")

    # ---------------------------------------------------------- feedback
    def test_presets_are_exposed(self):
        self.assertTrue(cosmos_feedback.presets("reject"))
        self.assertTrue(cosmos_feedback.presets("refine"))
        with self.assertRaises(common.CosmosError):
            cosmos_feedback.presets("approve")

    def test_record_and_prompt_context(self):
        cosmos_feedback.record("andromeda", "ads", "rec_1", "reject",
                               preset_id="price_out_of_range", note="too pricey")
        cosmos_feedback.record("andromeda", "ads", "rec_2", "refine",
                               preset_id="seasonal_angle")
        context = cosmos_feedback.recent_context("andromeda", "ads")
        self.assertEqual(len(context), 2)
        self.assertEqual(context[0]["preset"], "Seasonal angle (Christmas, birthdays, festivals)")
        self.assertEqual(context[1]["preset"], "Price outside my range")
        # scoped: other divisions/agents see nothing
        self.assertEqual(cosmos_feedback.recent_context("andromeda", "youtube"), [])
        self.assertEqual(cosmos_feedback.recent_context("polaris", "ads"), [])

    # --------------------------------------------------------- five-card cap
    def _mkrec(self, index):
        return store.create_recommendation(
            agent_id="andromeda", division="ads", execution_id="exe_x",
            capability="ads.discovery", template_id="womens-apparel", config_snapshot={},
            title="Product %d" % index, summary="s", payload={}, sources=[],
            relevance_score=80, opportunity=None, competition=None,
            evidence_status="hypothesis")

    def test_cap_keeps_five_newest_proposals(self):
        for index in range(7):
            self._mkrec(index)
        archived = store.enforce_proposed_cap("andromeda", "ads", 5)
        self.assertEqual(len(archived), 2)
        listed = store.list_recommendations(agent_id="andromeda", division="ads",
                                            status="proposed", limit=50)
        self.assertEqual(len(listed["recommendations"]), 5)
        # newest survive
        titles = {item["title"] for item in listed["recommendations"]}
        self.assertIn("Product 6", titles)
        self.assertNotIn("Product 0", titles)
        # archived cards remain in history
        archived_rows = store.list_recommendations(agent_id="andromeda", division="ads",
                                                   status="archived", limit=50)
        self.assertEqual(len(archived_rows["recommendations"]), 2)

    def test_cap_is_noop_under_limit(self):
        for index in range(3):
            self._mkrec(index)
        self.assertEqual(store.enforce_proposed_cap("andromeda", "ads", 5), [])

    # ------------------------------------------------------------ HTTP
    def test_config_exposes_autonomy(self):
        status, payload = cosmos_api.handle("GET", "/api/cosmos/config")
        self.assertEqual(status, 200)
        self.assertIn("autonomy", payload)

    def test_autonomy_endpoint_roundtrip(self):
        status, payload = cosmos_api.handle("POST", "/api/cosmos/config/autonomy", {},
                                            {"agent_id": "andromeda", "division": "ads",
                                             "mode": "override"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["mode"], "override")
        status, payload = cosmos_api.handle("GET", "/api/cosmos/config")
        self.assertEqual(payload["autonomy"]["andromeda"]["ads"]["mode"], "override")

    def test_autonomy_endpoint_validates(self):
        status, payload = cosmos_api.handle("POST", "/api/cosmos/config/autonomy", {},
                                            {"agent_id": "andromeda", "division": "ads",
                                             "mode": "sometimes"})
        self.assertEqual(status, 400)
        status, payload = cosmos_api.handle("POST", "/api/cosmos/config/autonomy", {},
                                            {"division": "ads", "mode": "override"})
        self.assertEqual(status, 400)

    def test_platform_registration_roundtrip(self):
        status, payload = cosmos_api.handle("POST", "/api/cosmos/config/ads/platform", {},
                                            {"platform": "shopee", "handle": "owner-1",
                                             "region": "PH"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["platform"]["handle"], "owner-1")
        status, payload = cosmos_api.handle("GET", "/api/cosmos/config")
        self.assertTrue(payload["config"]["ads_platforms"]["shopee"]["connected"])

    def test_platform_registration_validates(self):
        status, payload = cosmos_api.handle("POST", "/api/cosmos/config/ads/platform", {},
                                            {"platform": "amazon"})
        self.assertEqual(status, 400)

    def test_reject_records_preset_feedback(self):
        self._mkrec(1)
        listed = store.list_recommendations(agent_id="andromeda", division="ads",
                                            status="proposed", limit=1)
        rec_id = listed["recommendations"][0]["id"]
        status, payload = cosmos_api.handle(
            "POST", "/api/cosmos/recommendations/%s/reject" % rec_id, {},
            {"preset": "weak_demand", "note": "no evidence"})
        self.assertEqual(status, 200)
        context = cosmos_feedback.recent_context("andromeda", "ads")
        self.assertEqual(context[0]["preset"], "Weak demand evidence")
        self.assertEqual(context[0]["note"], "no evidence")


if __name__ == "__main__":
    unittest.main()
