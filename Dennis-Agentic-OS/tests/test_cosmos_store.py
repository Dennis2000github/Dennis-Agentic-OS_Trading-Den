"""Unit tests for the COSMOS execution and recommendation stores.

Run:  python3 -m unittest tests.test_cosmos_store
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_store as store  # noqa: E402


SNAPSHOT = {"domain": "ads", "template_id": "womens-apparel", "template": {"name": "Women's Apparel"}}


class ExecutionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "EXECUTIONS_FILE", os.path.join(self.dir, "executions.json")),
            mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json")),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _execution(self, agent="andromeda", division="ads", task="Find dresses"):
        return store.start_execution(
            agent, division, "%s.discovery" % division, task, "glm", "opencode", SNAPSHOT, "abc123"
        )

    def test_execution_lifecycle_and_snapshot(self):
        created = self._execution()
        self.assertEqual(created["status"], "running")
        finished = store.finish_execution(
            created["id"], "succeeded",
            result={"summary": "five products", "items": []},
            recommendation_ids=["rec_1"], duplicates_skipped=2, filtered_out=1,
        )
        self.assertEqual(finished["status"], "succeeded")
        self.assertIsNotNone(finished["duration_ms"])
        self.assertEqual(finished["recommendation_ids"], ["rec_1"])
        self.assertEqual(finished["config_snapshot"]["template_id"], "womens-apparel")

        read = store.get_execution(created["id"])
        self.assertEqual(read["result"]["summary"], "five products")
        self.assertEqual(read["duplicates_skipped"], 2)

    def test_execution_history_is_scoped_per_agent_and_division(self):
        ads = self._execution("andromeda", "ads")
        yt = self._execution("andromeda", "youtube", "Find topics")
        other = self._execution("polaris", "ads", "Evaluate")
        for item in (ads, yt, other):
            store.finish_execution(item["id"], "succeeded", result={"summary": "ok"})

        only_ads = store.list_executions(agent_id="andromeda", division="ads")
        self.assertEqual([row["id"] for row in only_ads["executions"]], [ads["id"]])
        only_polaris = store.list_executions(agent_id="polaris")
        self.assertEqual([row["id"] for row in only_polaris["executions"]], [other["id"]])
        all_rows = store.list_executions()
        self.assertEqual(all_rows["total"], 3)
        # list payload trims the large snapshot but the single read keeps it.
        self.assertNotIn("config_snapshot", all_rows["executions"][0])
        self.assertIn("config_snapshot", store.get_execution(ads["id"]))

    def test_recent_execution_context_only_uses_own_succeeded_runs(self):
        ok = self._execution("andromeda", "ads", "ok task")
        store.finish_execution(ok["id"], "succeeded", result={"summary": "ok"})
        failed = self._execution("andromeda", "ads", "failed task")
        store.finish_execution(failed["id"], "failed", error={"code": "x", "message": "no"})
        store.finish_execution(self._execution("polaris", "ads", "other")["id"], "succeeded", result={"summary": "other"})
        context = store.recent_execution_context("andromeda", "ads")
        self.assertEqual([row["task"] for row in context], ["ok task"])

    def test_unknown_execution_raises_404(self):
        with self.assertRaises(common.CosmosError) as ctx:
            store.get_execution("exe_missing")
        self.assertEqual(ctx.exception.status, 404)


class RecommendationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patch = mock.patch.object(common, "RECOMMENDATIONS_FILE", os.path.join(self.dir, "recommendations.json"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _rec(self, title="Floral midi dress", agent="andromeda", division="ads", merchant="Shopee"):
        payload = {"name": title, "merchant": merchant} if division == "ads" else {"title": title, "pillar": "Tax"}
        return store.create_recommendation(
            agent, division, "exe_1", "%s.discovery" % division, "womens-apparel",
            SNAPSHOT, title, "why", payload, [], 82,
            evidence_status="hypothesis",
        )

    def test_create_list_and_scope(self):
        ads = self._rec("Floral midi dress")
        yt = self._rec("Small business tax tips", division="youtube")
        other = self._rec("Other dress", agent="polaris")
        rows = store.list_recommendations(agent_id="andromeda", division="ads")
        ids = [row["id"] for row in rows["recommendations"]]
        self.assertIn(ads["id"], ids)
        self.assertNotIn(yt["id"], ids)
        self.assertNotIn(other["id"], ids)
        self.assertEqual(rows["counts"]["proposed"], 1)

    def test_dedupe_key_normalizes_name_and_merchant(self):
        first = self._rec("Floral Midi Dress!  ")
        key = store.recommendation_key("ads", {"name": "floral midi dress", "merchant": "SHOPEE"})
        self.assertEqual(first["dedupe_key"], key)
        self.assertIsNotNone(store.find_active_recommendation(key))

    def test_draft_then_reject_is_blocked_but_drafted_can_redraft(self):
        rec = self._rec()
        drafted = store.mark_drafted(rec["id"], "ads_product", "adp_1")
        self.assertEqual(drafted["status"], "drafted")
        again = store.mark_drafted(rec["id"], "ads_product", "adp_1")
        self.assertEqual(again["destination"]["id"], "adp_1")
        with self.assertRaises(common.CosmosError):
            store.mark_rejected(rec["id"], "no")

    def test_reject_is_terminal_for_drafting(self):
        rec = self._rec()
        store.mark_rejected(rec["id"], "Not a fit")
        rejected = store.get_recommendation(rec["id"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["user_decision"]["note"], "Not a fit")
        with self.assertRaises(common.CosmosError):
            store.mark_drafted(rec["id"], "ads_product", "adp_1")

    def test_refine_links_original_to_new_card(self):
        rec = self._rec()
        refined = self._rec("Refined dress")
        original = store.mark_refined(rec["id"], refined["id"])
        self.assertEqual(original["status"], "refined")
        self.assertEqual(original["refined_to"], refined["id"])

    def test_affiliate_validation_records_owner_confirmation_only(self):
        rec = self._rec()
        validated = store.set_affiliate_validation(rec["id"], {
            "status": "validated", "confirmed": True,
            "url": "https://shopee.ph/affiliate/123", "program": "Shopee Affiliate",
            "evidence": "owner screenshot", "note": None,
        })
        self.assertEqual(validated["payload"]["affiliate_status"], "validated")
        self.assertTrue(validated["affiliate_validation"]["confirmed"])
        pending = self._rec("Second dress")
        requested = store.set_affiliate_validation(pending["id"], {"status": "requested"})
        self.assertEqual(requested["payload"]["affiliate_status"], "pending")

    def test_counts_track_all_lifecycle_states(self):
        a = self._rec("A")
        b = self._rec("B")
        c = self._rec("C")
        store.mark_drafted(a["id"], "ads_product", "adp_1")
        store.mark_rejected(b["id"])
        store.mark_refined(c["id"], "rec_new")
        counts = store.recommendation_counts(agent_id="andromeda", division="ads")
        self.assertEqual(counts["drafted"], 1)
        self.assertEqual(counts["rejected"], 1)
        self.assertEqual(counts["refined"], 1)
        self.assertEqual(counts["total"], 3)


if __name__ == "__main__":
    unittest.main()
