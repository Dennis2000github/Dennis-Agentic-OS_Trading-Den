"""Unit tests for the shared integration approval gate (stdlib unittest only).

Run:  python3 -m unittest tests.test_integrations_permissions -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_tools
from backend.integrations import permissions


class PermissionsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-intx-perm-")
        self.approvals = studio_tools.ApprovalStore(
            os.path.join(self.tmp, "approvals.json")
        )
        self._orig_store = permissions._APPROVALS
        permissions.set_approval_store(self.approvals)
        self._orig_executors = dict(permissions._EXECUTORS)
        self._orig_tool_executors = dict(studio_tools.TOOL_EXECUTORS)

    def tearDown(self):
        permissions._APPROVALS = self._orig_store
        permissions._EXECUTORS = self._orig_executors
        studio_tools.TOOL_EXECUTORS = self._orig_tool_executors

    def test_reads_run_autonomously(self):
        decision = permissions.classify("composio", "youtube", "youtube.read", False)
        self.assertEqual(decision["permission"], "read")
        self.assertFalse(decision["approval_required"])
        self.assertFalse(decision["hard_gate"])

    def test_mutating_requires_approval(self):
        decision = permissions.classify("composio", "youtube", "youtube.playlist_write", True)
        self.assertEqual(decision["permission"], "write")
        self.assertTrue(decision["approval_required"])
        self.assertFalse(decision["hard_gate"])

    def test_hard_gates_are_flagged(self):
        for action in ("youtube.upload", "youtube.publish", "youtube.delete",
                       "youtube.comment", "youtube.moderation", "youtube.live",
                       "youtube.channel_settings"):
            decision = permissions.classify("composio", "youtube", action, True)
            self.assertTrue(decision["approval_required"], action)
            self.assertTrue(decision["hard_gate"], action)

    def test_request_approval_creates_pending_record(self):
        record = permissions.request_approval(
            "composio.youtube.upload_video",
            {"title": "Test", "privacy": "private"},
            {"action": "upload", "title": "Test", "privacy": "private"},
            reason="Owner asked for an upload",
        )
        self.assertEqual(record["status"], "pending")
        pending = self.approvals.list(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["tool"], "composio.youtube.upload_video")
        self.assertFalse(permissions.approval_decided(record["id"]))

    def test_request_approval_validates_input(self):
        with self.assertRaises(Exception):
            permissions.request_approval("tool", "not-a-dict", {})
        with self.assertRaises(Exception):
            permissions.request_approval("", {}, {})

    def test_registered_executor_runs_on_approval(self):
        calls = []

        def executor(args):
            calls.append(args)
            return {"ok": True, "video_id": "abc123"}

        permissions.register_executor("composio.youtube.upload_video", executor)
        self.assertIn("composio.youtube.upload_video", permissions.registered_executors())
        record = permissions.request_approval(
            "composio.youtube.upload_video", {"title": "x"}, {"action": "upload"}
        )
        decided = studio_tools.StudioTools(None, None, approvals=self.approvals).decide(
            record["id"], "approve"
        )
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(decided["result"], {"ok": True, "video_id": "abc123"})
        # the queue injects the approval id so executors can link audit rows
        self.assertEqual(calls, [{"title": "x", "__approval_id__": record["id"]}])
        self.assertTrue(permissions.approval_decided(record["id"]))

    def test_rejection_never_executes(self):
        calls = []
        permissions.register_executor(
            "composio.youtube.delete_video", lambda args: calls.append(args)
        )
        record = permissions.request_approval(
            "composio.youtube.delete_video", {"video_id": "v1"}, {"action": "delete"}
        )
        decided = studio_tools.StudioTools(None, None, approvals=self.approvals).decide(
            record["id"], "reject"
        )
        self.assertEqual(decided["status"], "rejected")
        self.assertEqual(calls, [])

    def test_unregistered_tool_fails_closed(self):
        # An approval for a tool nobody registered must never execute: the
        # shared queue marks it failed with the unknown_tool error instead.
        record = permissions.request_approval(
            "composio.youtube.unknown_action", {}, {}
        )
        tools = studio_tools.StudioTools(None, None, approvals=self.approvals)
        decided = tools.decide(record["id"], "approve")
        self.assertEqual(decided["status"], "failed")
        self.assertEqual(decided["error"]["code"], "unknown_tool")


if __name__ == "__main__":
    unittest.main()
