"""Unit tests for the Composio provider adapter (stdlib unittest only).

Network access is fully mocked: SSE frames, tool schemas and session state
are exercised through patched transports, mirroring the live-verified
response shapes.

Run:  python3 -m unittest tests.test_integrations_composio_adapter -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import composio
from backend.integrations import composio_adapter as adapter


def _sse(frame, request_id=None):
    """Return the parsed MCP frame a mocked ``_rpc`` would hand back.

    (``_rpc`` performs HTTP + SSE parsing and returns the matching frame as a
    dict; tests stub it at that boundary.)
    """
    frame = dict(frame)
    if request_id is not None:
        frame["id"] = request_id
    return frame


def _text_result(payload, request_id=None):
    """A frame whose result carries one JSON text block (tool-router shape)."""
    return _sse({"result": {"content": [
        {"type": "text", "text": json.dumps(payload)}]}}, request_id=request_id)


class AdapterTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-intx-adapter-")
        self._orig_store = composio.STORE_FILE
        composio.STORE_FILE = os.path.join(self.tmp, "composio.json")
        composio.save_keys("ck_test", "sk_test")
        # seed a tool-router session so _session_url() resolves
        store_data = composio._read_store()
        store_data["session_mcp"] = {
            "url": "https://backend.composio.dev/mcp/test-session",
            "user_id": "dennis-os-local",
            "created_at": "2026-11-25T00:00:00",
        }
        composio._write_store(store_data)

    def tearDown(self):
        composio.STORE_FILE = self._orig_store
        adapter._STATE_CACHE.clear()

    # -- REST discovery ------------------------------------------------------
    def test_tool_schema_parses_live_shape(self):
        live = {"items": [{
            "slug": "YOUTUBE_LIST_CHANNELS",
            "name": "List Channels",
            "description": "List channels",
            "version": "20261116_01",
            "available_versions": ["20261116_01"],
            "input_parameters": {"type": "object", "properties": {
                "mine": {"type": "boolean"}, "part": {"type": "string"}}},
            "output_parameters": {},
            "scopes": ["https://www.googleapis.com/auth/youtube.readonly"],
            "deprecated": False, "is_deprecated": False, "no_auth": False,
        }]}
        with mock.patch.object(composio, "_http_json", return_value=live):
            schema = adapter.tool_schema("YOUTUBE_LIST_CHANNELS")
        self.assertEqual(schema["slug"], "YOUTUBE_LIST_CHANNELS")
        self.assertEqual(schema["version"], "20261116_01")
        self.assertIn("mine", schema["input_parameters"]["properties"])
        self.assertIn("youtube.readonly", schema["scopes"][0])

    def test_tool_schema_unknown_tool(self):
        with mock.patch.object(composio, "_http_json", return_value={"items": []}):
            with self.assertRaises(adapter.ComposioAdapterError) as ctx:
                adapter.tool_schema("YOUTUBE_NOT_REAL")
        self.assertEqual(ctx.exception.code, "tool_unknown")
        self.assertEqual(ctx.exception.status, 404)

    def test_resolve_connection_prefers_active(self):
        conns = {"connections": [
            {"id": "ca_old", "toolkit": "youtube", "status": "EXPIRED", "created_at": "d1"},
            {"id": "ca_good", "toolkit": "youtube", "status": "ACTIVE", "created_at": "d2"},
            {"id": "ca_other", "toolkit": "canva", "status": "ACTIVE", "created_at": "d3"},
        ]}
        with mock.patch.object(composio, "connections", return_value=conns):
            conn = adapter.resolve_connection("youtube")
        self.assertEqual(conn["id"], "ca_good")
        self.assertEqual(conn["status"], "ACTIVE")

    def test_resolve_connection_reports_expired_honestly(self):
        conns = {"connections": [
            {"id": "ca_old", "toolkit": "youtube", "status": "EXPIRED", "created_at": "d1"},
        ]}
        with mock.patch.object(composio, "connections", return_value=conns):
            with self.assertRaises(adapter.ComposioAdapterError) as ctx:
                adapter.resolve_connection("youtube")
        self.assertEqual(ctx.exception.code, "connection_expired")

    def test_resolve_connection_missing(self):
        with mock.patch.object(composio, "connections", return_value={"connections": []}):
            with self.assertRaises(adapter.ComposioAdapterError) as ctx:
                adapter.resolve_connection("youtube")
        self.assertEqual(ctx.exception.code, "connection_missing")

    # -- SSE parsing ---------------------------------------------------------
    def test_parse_frames_sse_and_plain_json(self):
        frames = adapter._parse_frames('event: message\ndata: {"id": 1, "result": 1}\n\n')
        self.assertEqual(frames[0]["result"], 1)
        frames = adapter._parse_frames('{"id": 2, "result": {"ok": true}}')
        self.assertEqual(frames[0]["id"], 2)

    def test_parse_frames_transport_error(self):
        with self.assertRaises(adapter.ComposioAdapterError) as ctx:
            adapter._parse_frames("HTTPERR:502 bad gateway")
        self.assertEqual(ctx.exception.code, "unreachable")

    # -- execution -----------------------------------------------------------
    def test_execute_single_success(self):
        tool_result = {"successful": True, "log_id": "log_1", "data": {"results": [
            {"tool_slug": "YOUTUBE_LIST_CHANNELS", "successful": True,
             "data": {"items": [{"id": "UC123"}]}, "error": None}]}}
        with mock.patch.object(adapter, "_rpc", return_value=_text_result(tool_result, request_id=9)):
            data = adapter.execute("YOUTUBE_LIST_CHANNELS", {"mine": True})
        # execute() returns the tool's own data payload
        self.assertEqual(data["items"][0]["id"], "UC123")

    def test_execute_unwraps_nested_response_shape(self):
        # Live tool-router rows wrap success/data inside `response`. The top
        # level still says successful=true, but the nested flag is the one that
        # decides whether COSMOS may render the channel identity.
        tool_result = {"successful": True, "log_id": "log_nested", "data": {"results": [
            {"tool_slug": "YOUTUBE_LIST_CHANNELS", "index": 0,
             "response": {"successful": True,
                          "data": {"items": [{"id": "UCLive"}]}}}
        ]}}
        with mock.patch.object(adapter, "_rpc", return_value=_text_result(tool_result, request_id=9)):
            rows = adapter.execute_tools([
                {"tool_slug": "YOUTUBE_LIST_CHANNELS", "arguments": {"mine": True}}
            ])
        self.assertTrue(rows[0]["successful"])
        self.assertEqual(rows[0]["data"]["items"][0]["id"], "UCLive")
        self.assertEqual(rows[0]["log_id"], "log_nested")

    def test_nested_failure_string_is_not_treated_as_success(self):
        tool_result = {"successful": True, "data": {"results": [
            {"tool_slug": "YOUTUBE_LIST_CHANNELS", "successful": True,
             "response": {"successful": "False", "error": "token expired"}}
        ]}}
        with mock.patch.object(adapter, "_rpc", return_value=_text_result(tool_result, request_id=9)):
            with self.assertRaises(adapter.ComposioAdapterError) as ctx:
                adapter.execute("YOUTUBE_LIST_CHANNELS", {})
        self.assertEqual(ctx.exception.code, "tool_failed")
        self.assertIn("token expired", ctx.exception.message)

    def test_execute_tool_failure_raises(self):
        tool_result = {"successful": False, "error": "1 out of 1 tools failed",
                       "data": {"results": [
                           {"tool_slug": "YOUTUBE_X", "error": "boom"}]}}
        with mock.patch.object(adapter, "_rpc", return_value=_text_result(tool_result, request_id=9)):
            with self.assertRaises(adapter.ComposioAdapterError) as ctx:
                adapter.execute("YOUTUBE_X", {})
        self.assertEqual(ctx.exception.code, "tool_failed")
        self.assertIn("boom", ctx.exception.message)

    def test_execute_requires_key_and_items(self):
        os.environ.pop(composio.API_ENV, None)
        composio.clear_keys()
        with self.assertRaises(adapter.ComposioAdapterError) as ctx:
            adapter.execute("YOUTUBE_X", {})
        self.assertEqual(ctx.exception.code, "not_configured")
        composio.save_keys("ck_test", "sk_test")
        with self.assertRaises(adapter.ComposioAdapterError):
            adapter.execute_tools([])

    def test_execute_jsonrpc_error(self):
        with mock.patch.object(adapter, "_rpc", return_value=_sse(
                {"error": {"code": -32603, "message": "invalid"}}, request_id=9)):
            with self.assertRaises(adapter.ComposioAdapterError) as ctx:
                adapter.execute("YOUTUBE_X", {})
        self.assertEqual(ctx.exception.code, "execution_failed")

    # -- session connection state -------------------------------------------
    def test_session_state_active_and_initiated(self):
        def make(state_text):
            payload = {"data": {"results": {"youtube": {
                "status": state_text, "redirect_url": "https://link"}}}}
            return _sse({"result": {"content": [
                {"type": "text", "text": json.dumps(payload)}]}}, request_id=7)

        with mock.patch.object(adapter, "_rpc", return_value=make("active")):
            state = adapter.session_connection_state("youtube")
        self.assertEqual(state["status"], "active")
        adapter._STATE_CACHE.clear()
        with mock.patch.object(adapter, "_rpc", return_value=make("initiated")):
            state = adapter.session_connection_state("youtube")
        self.assertEqual(state["status"], "initiated")
        self.assertEqual(state["redirect_url"], "https://link")
        # second call served from the 60s cache
        with mock.patch.object(adapter, "_rpc", side_effect=AssertionError("no rpc expected")):
            state = adapter.session_connection_state("youtube")
        self.assertEqual(state["status"], "initiated")

    def test_connect_link_discloses_without_opening(self):
        payload = {"data": {"results": {"youtube": {
            "status": "initiated", "redirect_url": "https://connect/link"}}}}
        with mock.patch.object(adapter, "_rpc", return_value=_text_result(payload, request_id=8)):
            link = adapter.connect_link("youtube")
        self.assertEqual(link["redirect_url"], "https://connect/link")
        self.assertIn("open this link once", link["note"].lower())


if __name__ == "__main__":
    unittest.main()
