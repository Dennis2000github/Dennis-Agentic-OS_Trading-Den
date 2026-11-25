"""Unit tests for the YouTube domain service (stdlib unittest only).

The Composio adapter is mocked; the gate, audit, executor and schema
validation logic run for real.

Run:  python3 -m unittest tests.test_integrations_youtube -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_tools
from backend.integrations import executions, permissions, youtube, youtube_api


class YoutubeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-intx-yt-")
        self._orig = (executions.EXECUTIONS_FILE, permissions._APPROVALS)
        executions.EXECUTIONS_FILE = os.path.join(self.tmp, "executions.json")
        self.approvals = studio_tools.ApprovalStore(
            os.path.join(self.tmp, "approvals.json"))
        permissions.set_approval_store(self.approvals)
        self._orig_state = youtube.connection_state
        youtube._CHANNEL_CACHE["payload"] = None
        youtube._CHANNEL_CACHE["expires"] = 0.0

    def tearDown(self):
        (executions.EXECUTIONS_FILE, permissions._APPROVALS) = self._orig
        youtube.connection_state = self._orig_state
        youtube._CHANNEL_CACHE["payload"] = None
        youtube._CHANNEL_CACHE["expires"] = 0.0

    # -- op table ------------------------------------------------------------
    def test_op_table_shape(self):
        self.assertIn("upload_video", youtube.OPS)
        self.assertEqual(youtube.OPS["upload_video"]["slug"], "YOUTUBE_UPLOAD_VIDEO")
        self.assertTrue(youtube.OPS["upload_video"]["mutating"])
        self.assertEqual(youtube.OPS["upload_video"]["action_class"], "youtube.upload")
        self.assertIn("analytics", youtube.READ_OPS)
        self.assertNotIn("upload_video", youtube.READ_OPS)
        # the current comment-thread action, not a deprecated one
        self.assertEqual(youtube.OPS["list_comment_threads"]["slug"],
                         "YOUTUBE_LIST_COMMENT_THREADS2")

    def test_executors_registered_for_every_write_op(self):
        for op in youtube.WRITE_OPS:
            self.assertIsNotNone(
                permissions.executor("integration.youtube." + op), op)

    def test_unknown_op_rejected(self):
        with self.assertRaises(youtube.YoutubeError) as ctx:
            youtube.run("not_an_op", {})
        self.assertEqual(ctx.exception.code, "op_unknown")
        self.assertEqual(ctx.exception.status, 404)

    # -- guarded autonomy ----------------------------------------------------
    def test_read_op_runs_when_transport_available(self):
        schema = {"slug": "YOUTUBE_LIST_CHANNELS", "version": "v1",
                  "input_parameters": {"properties": {"mine": {}}}}
        with mock.patch.object(youtube, "connection_state",
                               return_value={"execution_available": True,
                                             "blocked_reason": "", "connection": {},
                                             "session": {"status": "active"}}), \
             mock.patch.object(youtube.adapter, "tool_schema", return_value=schema), \
             mock.patch.object(youtube.adapter, "execute",
                               return_value={"items": [{"id": "UC1"}]}) as exec_mock:
            result = youtube.run("list_channels", {"mine": True})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["items"][0]["id"], "UC1")
        exec_mock.assert_called_once()
        rows = executions.list(provider="composio", domain="youtube")
        self.assertEqual(rows[0]["status"], "succeeded")
        self.assertEqual(rows[0]["action"], "list_channels")

    def test_read_op_blocked_reports_honestly(self):
        with mock.patch.object(youtube, "connection_state",
                               return_value={"execution_available": False,
                                             "blocked_reason": "session not linked",
                                             "connection": {"status": "ACTIVE"},
                                             "session": {"status": "initiated"}}):
            result = youtube.run("list_channels", {"mine": True})
        self.assertEqual(result["status"], "execution_blocked")
        self.assertEqual(result["blocked_reason"], "session not linked")

    def test_write_op_never_executes_without_approval(self):
        schema = {"slug": "YOUTUBE_UPLOAD_VIDEO", "version": "v1",
                  "input_parameters": {"properties": {"videoFilePath": {}}}}
        with mock.patch.object(youtube, "connection_state",
                               return_value={"execution_available": True,
                                             "blocked_reason": "", "connection": {},
                                             "session": {"status": "active"}}), \
             mock.patch.object(youtube.adapter, "tool_schema", return_value=schema), \
             mock.patch.object(youtube.adapter, "execute") as exec_mock:
            with self.assertRaises(youtube.YoutubeError) as ctx:
                youtube.run("upload_video", {"videoFilePath": "/tmp/missing.mp4"})
        # file validation fails before any approval is even created
        exec_mock.assert_not_called()
        self.assertEqual(ctx.exception.code, "file_not_found")
        self.assertEqual(self.approvals.list(status="pending"), [])

    def test_write_op_creates_pending_approval(self):
        import tempfile as tf
        real_file = tf.NamedTemporaryFile(delete=False, suffix=".mp4")
        real_file.write(b"video")
        real_file.close()
        try:
            schema = {"slug": "YOUTUBE_UPLOAD_VIDEO", "version": "v1",
                      "input_parameters": {"properties": {
                          "videoFilePath": {}, "title": {}, "privacyStatus": {}}}}
            with mock.patch.object(youtube.adapter, "tool_schema", return_value=schema):
                result = youtube.run("upload_video", {
                    "videoFilePath": real_file.name,
                    "title": "Test", "privacyStatus": "private"})
            self.assertEqual(result["status"], "approval_required")
            self.assertEqual(result["tool"], "YOUTUBE_UPLOAD_VIDEO")
            pending = self.approvals.list(status="pending")
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["tool"], "integration.youtube.upload_video")
            # The owner must SEE what they approve: the preview shows the local
            # file path and target privacy — redaction covers secrets, not
            # decision-relevant facts.
            preview = json.dumps(pending[0]["preview"])
            self.assertIn(real_file.name, preview)
            self.assertIn("private", preview)
            self.assertIn("owner approval", preview)
        finally:
            os.unlink(real_file.name)

    def test_approved_write_executes_with_audit_linkage(self):
        import tempfile as tf
        real_file = tf.NamedTemporaryFile(delete=False, suffix=".mp4")
        real_file.write(b"video")
        real_file.close()
        try:
            schema = {"slug": "YOUTUBE_UPLOAD_VIDEO", "version": "v1",
                      "input_parameters": {"properties": {
                          "videoFilePath": {}, "title": {}, "privacyStatus": {}}}}
            with mock.patch.object(youtube.adapter, "tool_schema", return_value=schema):
                created = youtube.run("upload_video", {
                    "videoFilePath": real_file.name, "title": "T",
                    "privacyStatus": "private"})
            approval_id = created["approval"]["id"]
            # owner approves through the shared queue
            decided = self.approvals._get_raw(approval_id)
            self.assertEqual(decided["status"], "pending")
            self.approvals.update(approval_id, status="approved")
            with mock.patch.object(youtube, "connection_state",
                                   return_value={"execution_available": True,
                                                 "blocked_reason": "", "connection": {},
                                                 "session": {"status": "active"}}), \
                 mock.patch.object(youtube.adapter, "tool_schema", return_value=schema), \
                 mock.patch.object(youtube.adapter, "execute",
                                   return_value={"videoId": "vid123"}) as exec_mock:
                result = youtube._execute_write(
                    "upload_video",
                    {"videoFilePath": real_file.name, "title": "T",
                     "privacyStatus": "private"},
                    approval_id=approval_id)
            self.assertEqual(result["status"], "ok")
            exec_mock.assert_called_once()
            rows = executions.list(provider="composio", domain="youtube",
                                   status="succeeded")
            self.assertEqual(rows[0]["approval_id"], approval_id)
        finally:
            os.unlink(real_file.name)

    def test_unapproved_write_fails_closed(self):
        with self.assertRaises(youtube.YoutubeError) as ctx:
            youtube._execute_write("delete_video", {"videoId": "x"},
                                   approval_id="apv_never_approved")
        self.assertEqual(ctx.exception.code, "approval_required")

    def test_upload_limit_is_not_retried(self):
        from backend.integrations.composio_adapter import ComposioAdapterError
        approval = self.approvals.create(
            "integration.youtube.delete_video", {"videoId": "x"}, {"action": "delete"})
        self.approvals.update(approval["id"], status="approved")
        with mock.patch.object(youtube, "connection_state",
                               return_value={"execution_available": True,
                                             "blocked_reason": "", "connection": {},
                                             "session": {"status": "active"}}), \
             mock.patch.object(youtube.adapter, "tool_schema", return_value={
                 "slug": "YOUTUBE_DELETE_VIDEO", "input_parameters": {"properties": {}}}), \
             mock.patch.object(youtube.adapter, "execute",
                               side_effect=ComposioAdapterError(
                                   "tool_failed",
                                   "YOUTUBE_DELETE_VIDEO failed: uploadLimitExceeded reached")):
            with self.assertRaises(youtube.YoutubeError) as ctx:
                youtube._execute_write("delete_video", {"videoId": "x"},
                                       approval_id=approval["id"])
        self.assertEqual(ctx.exception.code, "channel_upload_limit")
        self.assertEqual(ctx.exception.status, 429)
        failed = executions.list(provider="composio", domain="youtube", status="failed")
        self.assertEqual(failed[0]["error"]["code"], "channel_upload_limit")

    def test_unknown_argument_fails_before_execution(self):
        schema = {"slug": "YOUTUBE_LIST_CHANNELS",
                  "input_parameters": {"properties": {"mine": {}}}}
        with mock.patch.object(youtube, "connection_state",
                               return_value={"execution_available": True,
                                             "blocked_reason": "", "connection": None,
                                             "session": None}), \
             mock.patch.object(youtube.adapter, "tool_schema", return_value=schema):
            with self.assertRaises(youtube.YoutubeError) as ctx:
                youtube.run("list_channels", {"bogus_param": 1})
        self.assertEqual(ctx.exception.code, "unknown_argument")
        rows = executions.list(provider="composio", domain="youtube")
        self.assertEqual(len(rows), 0)

    # -- shared connected-channel identity ----------------------------------
    def test_connected_channel_parses_live_shape_and_caches(self):
        live = {"status": "ok", "data": {"items": [{
            "id": "UCLive",
            "snippet": {
                "localized": {"title": "Dennis Channel"},
                "customUrl": "@dennis",
                "thumbnails": {
                    "high": {"url": "javascript:alert(1)"},
                    "medium": {"url": "https://yt3.example/medium.jpg"},
                },
            },
        }]}}
        state = {"execution_available": True, "blocked_reason": "",
                 "connection": {"id": "ca_live"}}
        with mock.patch.object(youtube, "connection_state", return_value=state), \
             mock.patch.object(youtube, "run", return_value=live) as run:
            first = youtube.connected_channel(refresh=True)
            second = youtube.connected_channel()
        self.assertEqual(first["status"], "connected")
        self.assertEqual(first["id"], "UCLive")
        self.assertEqual(first["name"], "Dennis Channel")
        self.assertEqual(first["handle"], "@dennis")
        self.assertEqual(first["avatar_url"], "https://yt3.example/medium.jpg")
        self.assertEqual(first["connection_id"], "ca_live")
        self.assertIsNone(first["error"])
        self.assertEqual(second, first)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][1]["mine"], True)
        self.assertEqual(run.call_args.kwargs["module"],
                         "integrations.youtube.channel_identity")

    def test_connected_channel_reports_not_connected_without_calling_tool(self):
        state = {"execution_available": False,
                 "blocked_reason": "session not linked",
                 "connection": {"id": "ca_old"}}
        with mock.patch.object(youtube, "connection_state", return_value=state), \
             mock.patch.object(youtube, "run") as run:
            result = youtube.connected_channel(refresh=True)
        self.assertEqual(result["status"], "not_connected")
        self.assertEqual(result["connection_id"], "ca_old")
        self.assertEqual(result["error"], "session not linked")
        run.assert_not_called()


class YoutubeChannelApiTests(unittest.TestCase):
    def test_channel_route_forwards_refresh(self):
        identity = {"status": "connected", "name": "Dennis Channel"}
        with mock.patch.object(youtube_api.youtube, "connected_channel",
                               return_value=identity) as resolver:
            status, payload = youtube_api.handle(
                "GET", "/api/integrations/youtube/channel", {"refresh": ["1"]}, None)
        self.assertEqual(status, 200)
        self.assertEqual(payload, identity)
        resolver.assert_called_once_with(refresh=True)


if __name__ == "__main__":
    unittest.main()
