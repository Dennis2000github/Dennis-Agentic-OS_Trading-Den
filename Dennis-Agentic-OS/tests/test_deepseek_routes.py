"""HTTP tests for the DeepSeek Harness routes (real stdlib server).

The DeepSeek upstream is a local fake SSE server, so no provider call leaves the
machine. Keys and conversations are redirected into a temporary directory.

Run:  python3 -m unittest tests.test_deepseek_routes
"""

import http.client
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import auth as dennis_auth  # noqa: E402
from backend import deepseek_harness  # noqa: E402


class Upstream(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in ("Hi", " there"):
            frame = {"choices": [{"delta": {"content": text}}]}
            self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


class DeepSeekRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.token = dennis_auth.AUTH.token

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patch_dir = mock.patch.object(deepseek_harness, "_dir", lambda: self.dir)
        self.patch_dir.start()
        self.env = mock.patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_BASE_URL": "",
            "DEEPSEEK_MODEL": "",
            "DEEPSEEK_MODELS": "",
        })
        self.env.start()
        self.upstream = None

    def tearDown(self):
        if self.upstream is not None:
            self.upstream.shutdown()
            self.upstream.server_close()
        self.env.stop()
        self.patch_dir.stop()
        self.tmp.cleanup()

    def _request(self, method, path, body=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        headers = {}
        if auth:
            headers["X-Dennis-Token"] = self.token
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=raw, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def _json(self, method, path, body=None, auth=True):
        status, data = self._request(method, path, body, auth)
        return status, json.loads(data.decode("utf-8") or "{}")

    def _start_upstream(self):
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.upstream = httpd
        os.environ["DEEPSEEK_BASE_URL"] = "http://127.0.0.1:%d" % httpd.server_address[1]
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"

    # -- discovery -------------------------------------------------------
    def test_agent_dropdown_lists_deepseek_harness(self):
        status, data = self._json("GET", "/api/agents/availability")
        self.assertEqual(status, 200)
        by_id = {a["id"]: a for a in data["agents"]}
        self.assertIn("deepseek-harness", by_id)
        self.assertEqual(by_id["deepseek-harness"]["name"], "DeepSeek Harness")
        self.assertEqual(by_id["deepseek-harness"]["transport"], "chat")
        self.assertTrue(by_id["deepseek-harness"]["available"])

    def test_models_endpoint_uses_native_list(self):
        status, data = self._json("GET", "/api/agents/deepseek-harness/models")
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in data["models"]],
                         ["deepseek-chat", "deepseek-reasoner"])
        self.assertTrue(data["supports_live_model_switch"])
        self.assertEqual(len(data["providers"]), 1)
        self.assertEqual(data["providers"][0]["model_count"], 2)
        self.assertIn("provider_setup", data)
        self.assertEqual(data["provider_setup"].get("form"), "api_key")
        self.assertIn("DeepSeek", data["provider_setup"].get("hint", ""))

    def test_chat_agent_refuses_project_sessions(self):
        projects = self._json("GET", "/api/projects")[1]["projects"]
        status, data = self._json("POST", "/api/project-sessions", {
            "agent_id": "deepseek-harness",
            "project": projects[0]["path"] if projects else self.dir,
        })
        self.assertEqual(status, 400)
        self.assertEqual(data.get("code"), "chat_agent")

    # -- auth / setup ----------------------------------------------------
    def test_requires_owner_token(self):
        status, data = self._json("GET", "/api/deepseek/status", auth=False)
        self.assertEqual(status, 401)

    def test_credentials_roundtrip(self):
        status, data = self._json("GET", "/api/deepseek/status")
        self.assertEqual(status, 200)
        self.assertFalse(data["configured"])

        status, data = self._json("POST", "/api/deepseek/credentials",
                                  {"api_key": "sk-route-9999"})
        self.assertEqual(status, 200)
        self.assertTrue(data["credentials"]["configured"])
        self.assertEqual(data["credentials"]["hint"], "...9999")
        self.assertNotIn("api_key", json.dumps(data))

        status, data = self._json("DELETE", "/api/deepseek/credentials")
        self.assertEqual(status, 200)
        self.assertFalse(data["credentials"]["configured"])

    # -- conversations + streaming --------------------------------------
    def test_conversation_crud_and_stream(self):
        self._start_upstream()
        self._json("POST", "/api/deepseek/credentials", {"api_key": "sk-test"})

        status, created = self._json("POST", "/api/deepseek/conversations",
                                     {"model_id": "deepseek-chat"})
        self.assertEqual(status, 200)
        cid = created["conversation"]["id"]

        status, listed = self._json("GET", "/api/deepseek/conversations")
        self.assertEqual([c["id"] for c in listed["conversations"]], [cid])

        status, raw = self._request("POST",
                                    "/api/deepseek/conversations/%s/messages" % cid,
                                    {"content": "Hello"})
        self.assertEqual(status, 200)
        text = raw.decode("utf-8")
        self.assertIn('"type": "delta"', text)
        self.assertIn('"type": "done"', text)
        self.assertIn("Hi there", text)

        status, conv = self._json("GET", "/api/deepseek/conversations/%s" % cid)
        self.assertEqual([m["role"] for m in conv["conversation"]["messages"]],
                         ["user", "assistant"])
        self.assertEqual(conv["conversation"]["messages"][1]["content"], "Hi there")
        self.assertEqual(conv["conversation"]["title"], "Hello")

        status, deleted = self._json("DELETE", "/api/deepseek/conversations/%s" % cid)
        self.assertEqual(status, 200)
        status, _ = self._json("GET", "/api/deepseek/conversations/%s" % cid)
        self.assertEqual(status, 404)

    def test_send_without_key_is_actionable(self):
        status, created = self._json("POST", "/api/deepseek/conversations", {})
        cid = created["conversation"]["id"]
        status, data = self._json("POST",
                                  "/api/deepseek/conversations/%s/messages" % cid,
                                  {"content": "Hi"})
        self.assertEqual(status, 409)
        self.assertTrue(data.get("needs_key"))

    def test_unknown_conversation_is_404(self):
        self._start_upstream()
        status, data = self._json(
            "POST", "/api/deepseek/conversations/ds-missing/messages",
            {"content": "Hi"})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
