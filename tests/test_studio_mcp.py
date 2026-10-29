"""Tests for the MCP stdio client and the optional MCP provider.

A tiny mock MCP server is written to a temporary directory; no external
package, network or credentials are required.

Run:  python3 -m unittest tests.test_studio_mcp
"""

import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_mcp
from backend import studio_providers as providers

MOCK_SERVER = r'''
import json
import os
import sys
import time

COUNTER = sys.argv[1] if len(sys.argv) > 1 else ""

TOOLS = [
    {"name": "list_models", "description": "List models", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "generate_video", "description": "Generate video", "inputSchema": {"type": "object", "properties": {"model_id": {"type": "string"}, "prompt": {"type": "string"}, "extra_params": {"type": "object"}}, "required": ["model_id"]}},
    {"name": "generate_image", "description": "Generate image", "inputSchema": {"type": "object", "properties": {"model_id": {"type": "string"}, "prompt": {"type": "string"}}}},
    {"name": "get_status", "description": "Status", "inputSchema": {"type": "object", "properties": {"job_handle": {"type": "string"}}}},
    {"name": "cancel_job", "description": "Cancel", "inputSchema": {"type": "object", "properties": {"job_handle": {"type": "string"}}}},
    {"name": "upload_image", "description": "Upload", "inputSchema": {"type": "object", "properties": {"data_base64": {"type": "string"}, "mime": {"type": "string"}}}},
    {"name": "preflight_check", "description": "Preflight", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "slow", "description": "Slow tool", "inputSchema": {"type": "object", "properties": {}}},
]

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

def result(req_id, payload):
    send({"jsonrpc": "2.0", "id": req_id, "result": payload})

def counter():
    try:
        with open(COUNTER) as fh:
            return int(fh.read().strip() or "0")
    except Exception:
        return 0

def bump():
    value = counter() + 1
    try:
        with open(COUNTER, "w") as fh:
            fh.write(str(value))
    except Exception:
        pass
    return value

sys.stderr.write("mock starting with Key abc:secret\n")
sys.stderr.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        message = json.loads(line)
    except ValueError:
        continue
    method = message.get("method")
    req_id = message.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "mock-higgsfield", "version": "1.0"}, "capabilities": {"tools": {}}}})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        result(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "slow":
            time.sleep(2)
            result(req_id, {"content": [{"type": "text", "text": "done"}]})
        elif name == "list_models":
            result(req_id, {"structuredContent": {"models": [
                {"id": "bytedance/seedance-2.5/text-to-video", "kind": "video", "supports": ["prompt", "duration"]},
                {"id": "mock/custom-video", "name": "Mock Custom Video", "kind": "video", "supports": ["prompt", "image_url"]},
            ]}})
        elif name in ("generate_video", "generate_image"):
            result(req_id, {"structuredContent": {"job_handle": "mcp-1", "status": "queued", "tool": name, "args": args}})
        elif name == "get_status":
            value = bump()
            if value < 2:
                result(req_id, {"structuredContent": {"status": "in_progress"}})
            else:
                result(req_id, {"structuredContent": {"status": "completed", "video_url": "https://cdn.example.com/mcp.mp4", "images": [{"url": "https://cdn.example.com/mcp.png"}]}})
        elif name == "cancel_job":
            result(req_id, {"structuredContent": {"ok": True}})
        elif name == "upload_image":
            result(req_id, {"structuredContent": {"url": "https://cdn.example.com/upload.png"}})
        elif name == "preflight_check":
            result(req_id, {"structuredContent": {"message": "mock ready"}})
        else:
            send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "unknown tool"}})
'''


class McpClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.script = os.path.join(self.tmp.name, "mock_mcp.py")
        with open(self.script, "w", encoding="utf-8") as fh:
            fh.write(MOCK_SERVER)
        self.counter = os.path.join(self.tmp.name, "counter.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def make_client(self, timeout=5):
        return studio_mcp.McpClient(sys.executable, [self.script, self.counter], timeout=timeout)

    def test_initialize_list_and_call(self):
        with self.make_client() as client:
            info = client.initialize()
            self.assertEqual(info["serverInfo"]["name"], "mock-higgsfield")
            tools = client.list_tools()
            names = {tool["name"] for tool in tools}
            self.assertIn("generate_video", names)
            result = client.call_tool("list_models", {})
            payload = studio_mcp.McpClient.payload(result)
            self.assertEqual(len(payload["models"]), 2)

    def test_stderr_is_bounded_and_redacted(self):
        with self.make_client() as client:
            client.initialize()
            time.sleep(0.1)
            tail = client.stderr_tail()
        self.assertTrue(tail)
        self.assertNotIn("abc:secret", "\n".join(tail))

    def test_timeout_raises_and_clears_pending(self):
        with self.make_client(timeout=0.2) as client:
            client.initialize()
            with self.assertRaises(studio_mcp.McpError) as ctx:
                client.call_tool("slow", {}, timeout=0.2)
            self.assertEqual(ctx.exception.code, "mcp_timeout")
            self.assertEqual(client._pending, {})

    def test_missing_command_is_reported(self):
        client = studio_mcp.McpClient("/nonexistent/mcp-server-xyz")
        with self.assertRaises(studio_mcp.McpError) as ctx:
            client.start()
        self.assertEqual(ctx.exception.code, "mcp_start_failed")


class McpConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [
            mock.patch.object(studio_mcp, "STUDIO_DIR", self.tmp.name),
            mock.patch.object(studio_mcp, "CONFIG_FILE", os.path.join(self.tmp.name, "mcp.json")),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def test_public_config_hides_env_values(self):
        studio_mcp.save_config(
            "uvx", ["--from", "higgsfield-mcp-unified", "higgsfield-mcp"],
            {"HIGGSFIELD_SECRET": "super-secret"},
        )
        public = studio_mcp.public_config()
        self.assertTrue(public["configured"])
        self.assertEqual(public["env_keys"], ["HIGGSFIELD_SECRET"])
        self.assertNotIn("super-secret", json.dumps(public))

    def test_rejects_unsafe_config(self):
        for command in ("", "bad\ncommand"):
            with self.assertRaises(studio_mcp.McpError):
                studio_mcp.save_config(command, [], {})
        with self.assertRaises(studio_mcp.McpError):
            studio_mcp.save_config("cmd", ["ok", 3], {})
        with self.assertRaises(studio_mcp.McpError):
            studio_mcp.save_config("cmd", [], {"BAD=KEY": "x"})

    def test_clear_config(self):
        studio_mcp.save_config("cmd", [], {})
        self.assertFalse(studio_mcp.clear_config()["configured"])
        self.assertEqual(studio_mcp.clear_config(), {
            "configured": False, "command": "", "args": [], "env_keys": [], "updated_at": None,
        })


class McpProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.script = os.path.join(self.tmp.name, "mock_mcp.py")
        with open(self.script, "w", encoding="utf-8") as fh:
            fh.write(MOCK_SERVER)
        self.counter = os.path.join(self.tmp.name, "counter.txt")
        self.patches = [
            mock.patch.object(studio_mcp, "STUDIO_DIR", self.tmp.name),
            mock.patch.object(studio_mcp, "CONFIG_FILE", os.path.join(self.tmp.name, "mcp.json")),
            mock.patch.object(
                providers.studio_media, "add_bytes",
                side_effect=lambda data, content_type, kind=None, filename="upload", meta=None: {
                    "id": "med_in", "kind": kind or "image", "filename": filename
                },
            ),
        ]
        for patch in self.patches:
            patch.start()
        studio_mcp.save_config(sys.executable, [self.script, self.counter], {})
        self.provider = providers.HiggsfieldMcpProvider()

    def tearDown(self):
        self.provider.disconnect()
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def test_connect_and_model_registry(self):
        descriptor = self.provider.connect()
        self.assertTrue(descriptor["connected"])
        self.assertIn("generate_video", descriptor["tools"])
        models = self.provider.models()
        ids = {model["id"] for model in models}
        self.assertIn("bytedance/seedance-2.5/text-to-video", ids)
        self.assertIn("mock/custom-video", ids)
        self.assertTrue(all(model["capabilities"]["estimate"] for model in models))
        custom = self.provider.model_detail("mock/custom-video")
        self.assertIn("image_url", {field["name"] for field in custom["fields"]})

    def test_submit_status_and_cancel(self):
        self.provider.connect()
        model = self.provider.model("bytedance/seedance-2.5/text-to-video")
        submitted = self.provider.submit(
            model, {"prompt": "a cat", "duration": 6}, "idem"
        )
        self.assertEqual(submitted["provider_request_id"], "mcp-1")
        first = self.provider.status({"provider_request_id": "mcp-1", "kind": "video"})
        self.assertEqual(first["state"], "running")
        second = self.provider.status({"provider_request_id": "mcp-1", "kind": "video"})
        self.assertEqual(second["state"], "succeeded")
        kinds = {output["kind"] for output in second["outputs"]}
        self.assertIn("video", kinds)
        self.assertIn("image", kinds)
        self.provider.cancel({
            "state": "queued", "provider_request_id": "mcp-1", "kind": "video",
        })

    def test_cancel_running_is_rejected(self):
        self.provider.connect()
        with self.assertRaises(providers.ProviderError) as ctx:
            self.provider.cancel({"state": "running", "provider_request_id": "mcp-1"})
        self.assertEqual(ctx.exception.status, 409)

    def test_upload_and_preflight(self):
        self.provider.connect()
        uploaded = self.provider.upload(b"bytes", "image/png", "a.png")
        self.assertEqual(uploaded["public_url"], "https://cdn.example.com/upload.png")
        self.assertEqual(uploaded["media"]["id"], "med_in")
        self.assertTrue(self.provider.test()["ok"])

    def test_not_configured(self):
        studio_mcp.clear_config()
        self.provider.disconnect()
        with self.assertRaises(providers.ProviderError) as ctx:
            self.provider.connect()
        self.assertEqual(ctx.exception.code, "mcp_not_configured")


if __name__ == "__main__":
    unittest.main()
