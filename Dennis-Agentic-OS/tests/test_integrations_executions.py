"""Unit tests for the shared integration execution audit (stdlib unittest only).

Run:  python3 -m unittest tests.test_integrations_executions -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.integrations import executions, store


class ExecutionsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-intx-exec-")
        self._orig_file = executions.EXECUTIONS_FILE
        executions.EXECUTIONS_FILE = os.path.join(self.tmp, "executions.json")

    def tearDown(self):
        executions.EXECUTIONS_FILE = self._orig_file

    def test_record_and_list_roundtrip(self):
        row = executions.record(
            "composio",
            "list_channel_videos",
            "succeeded",
            domain="youtube",
            tool="YOUTUBE_LIST_CHANNEL_VIDEOS",
            target={"channel_id": "UC123"},
            agent="andromeda",
            module="cosmos.andromeda",
        )
        self.assertEqual(row["provider"], "composio")
        self.assertEqual(row["status"], "succeeded")
        self.assertTrue(row["id"].startswith("intx_"))
        self.assertIn("started_iso", row)
        rows = executions.list(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], row["id"])

    def test_record_requires_provider_action_status(self):
        with self.assertRaises(store.IntegrationError):
            executions.record("", "action", "succeeded")
        with self.assertRaises(store.IntegrationError):
            executions.record("composio", "", "succeeded")
        with self.assertRaises(store.IntegrationError):
            executions.record("composio", "action", "bogus_status")

    def test_filters(self):
        executions.record("composio", "a1", "succeeded", domain="youtube")
        executions.record("shopee", "a2", "failed", domain="affiliate",
                          error={"code": "10030", "message": "rate limited"})
        self.assertEqual(len(executions.list(provider="shopee")), 1)
        self.assertEqual(len(executions.list(status="failed")), 1)
        self.assertEqual(len(executions.list(domain="youtube")), 1)
        with self.assertRaises(store.IntegrationError):
            executions.list(status="nope")

    def test_summary_and_last_outcomes(self):
        executions.record("composio", "a1", "succeeded", domain="youtube")
        executions.record("composio", "a2", "failed", domain="youtube",
                          error={"code": "quota", "message": "quota exceeded"})
        summary = executions.summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["by_status"], {"succeeded": 1, "failed": 1})
        self.assertEqual(summary["last_success"]["action"], "a1")
        self.assertEqual(summary["last_failure"]["error"]["code"], "quota")

    def test_finish_updates_running_row(self):
        row = executions.record("shopee", "fetch_feed", "running")
        finished = executions.finish(row["id"], "succeeded", detail={"rows": 12})
        self.assertEqual(finished["status"], "succeeded")
        self.assertIsNotNone(finished["duration_ms"])
        self.assertEqual(finished["detail"]["rows"], 12)
        with self.assertRaises(store.IntegrationError):
            executions.finish("intx_missing", "succeeded")

    def test_detail_redacts_secret_looking_keys(self):
        executions.record(
            "composio", "a1", "succeeded",
            detail={"api_key": "super-secret-value", "count": 3},
        )
        raw = json.dumps(executions.list()[0])
        self.assertNotIn("super-secret-value", raw)
        self.assertIn("count", raw)

    def test_store_is_0600_and_bounded(self):
        executions.record("composio", "a1", "succeeded")
        self.assertEqual(oct(os.stat(executions.EXECUTIONS_FILE).st_mode & 0o777), "0o600")
        original_cap = executions.MAX_EXECUTIONS
        try:
            executions.MAX_EXECUTIONS = 3
            for i in range(5):
                executions.record("composio", "a%d" % i, "succeeded")
            self.assertLessEqual(len(executions.list(limit=200)), 3)
        finally:
            executions.MAX_EXECUTIONS = original_cap


if __name__ == "__main__":
    unittest.main()
