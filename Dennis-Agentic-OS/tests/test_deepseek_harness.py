"""Unit tests for the native DeepSeek Harness agent.

No real DeepSeek request is made: the streaming client is pointed at a local
throw-away HTTP server via ``DEEPSEEK_BASE_URL``.

Run:  python3 -m unittest tests.test_deepseek_harness
"""

import http.server
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import agent_registry
from backend import deepseek_harness as dh
from backend.model_sources import deepseek as ds_source
from backend.model_sources import registry as model_registry


class FakeUpstream(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.last_request = body
        self.server.last_auth = self.headers.get("Authorization")
        if self.server.fail_code:
            raw = json.dumps({"error": {"message": "invalid api key"}}).encode()
            self.send_response(self.server.fail_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        frames = [
            {"choices": [{"delta": {"reasoning_content": "thinking"}}]},
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {"content": " world"}}]},
        ]
        for frame in frames:
            self.wfile.write(("data: " + json.dumps(frame) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *args):
        pass


class DeepSeekHarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.env = mock.patch.dict(os.environ, {
            "DEEPSEEK_API_KEY": "",
            "DEEPSEEK_BASE_URL": "",
            "DEEPSEEK_MODEL": "",
            "DEEPSEEK_MODELS": "",
        })
        self.env.start()
        self.patch_dir = mock.patch.object(dh, "_dir", lambda: self.dir)
        self.patch_dir.start()
        self.upstream = None

    def tearDown(self):
        if self.upstream is not None:
            self.upstream.shutdown()
            self.upstream.server_close()
        self.patch_dir.stop()
        self.env.stop()
        self.tmp.cleanup()

    def _serve_upstream(self, fail_code=0):
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
        httpd.fail_code = fail_code
        httpd.last_request = None
        httpd.last_auth = None
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.upstream = httpd
        os.environ["DEEPSEEK_BASE_URL"] = "http://127.0.0.1:%d" % httpd.server_address[1]
        os.environ["DEEPSEEK_API_KEY"] = "sk-test"
        return httpd

    # -- credentials -----------------------------------------------------
    def test_status_without_key(self):
        status = dh.status()
        self.assertFalse(status["configured"])
        self.assertEqual(status["source"], "none")
        self.assertEqual(status["default_model"], "deepseek-chat")

    def test_store_key_is_private_and_reported_without_value(self):
        status = dh.set_api_key("sk-secret-1234")
        self.assertTrue(status["configured"])
        self.assertEqual(status["source"], "stored")
        self.assertEqual(status["hint"], "...1234")
        self.assertNotIn("api_key", status)

        path = os.path.join(self.dir, "credentials.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, "r", encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["api_key"], "sk-secret-1234")

        self.assertFalse(dh.clear_api_key()["configured"])

    def test_environment_key_wins(self):
        dh.set_api_key("sk-stored")
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}):
            key, source = dh.api_key()
            status = dh.status()
        self.assertEqual((key, source), ("sk-env", "env"))
        self.assertEqual(status["source"], "env")

    # -- models ----------------------------------------------------------
    def test_model_default_and_overrides(self):
        self.assertEqual([m["id"] for m in dh.models()],
                         ["deepseek-chat", "deepseek-reasoner"])
        self.assertTrue(dh.models()[0]["is_default"])
        with mock.patch.dict(os.environ, {
                "DEEPSEEK_MODEL": "deepseek-reasoner",
                "DEEPSEEK_MODELS": "ds-private"}):
            models = dh.models()
        by_id = {m["id"]: m for m in models}
        self.assertEqual(set(by_id), {"deepseek-chat", "deepseek-reasoner", "ds-private"})
        self.assertTrue(by_id["deepseek-reasoner"]["is_default"])
        self.assertFalse(by_id["deepseek-chat"]["is_default"])

    def test_model_source_registered(self):
        self.assertIsInstance(model_registry.get_source("deepseek-harness"),
                              ds_source.DeepSeekModelSource)
        models = ds_source.DeepSeekModelSource().list_models(None)
        self.assertEqual(models[0]["id"], "deepseek-chat")

    # -- conversations ---------------------------------------------------
    def test_conversation_lifecycle_and_title(self):
        conv = dh.create_conversation("local")
        self.assertEqual(conv["title"], "New chat")

        msg = dh._append_message("local", conv["id"], "user",
                                 "Explain vector databases in one paragraph")
        self.assertEqual(msg["role"], "user")
        reloaded = dh.get_conversation("local", conv["id"])
        self.assertEqual(reloaded["title"],
                         "Explain vector databases in one paragraph")
        self.assertEqual(len(reloaded["messages"]), 1)

        listed = dh.list_conversations("local")
        self.assertEqual([c["id"] for c in listed], [conv["id"]])
        self.assertEqual(listed[0]["message_count"], 1)

        self.assertFalse(dh.delete_conversation("local", "missing"))
        self.assertTrue(dh.delete_conversation("local", conv["id"]))
        self.assertEqual(dh.list_conversations("local"), [])

    def test_unknown_model_is_rejected(self):
        with self.assertRaises(dh.ChatError):
            dh.create_conversation("local", model_id="gpt-4")

    # -- streaming -------------------------------------------------------
    def test_stream_persists_both_messages(self):
        upstream = self._serve_upstream()
        conv = dh.create_conversation("local")
        exchange = dh.prepare_exchange("local", conv["id"], "Hi")
        events = list(exchange.events())
        types = [e["type"] for e in events]
        self.assertEqual(types, ["start", "reasoning", "delta", "delta", "done"])
        self.assertEqual("".join(e.get("text", "") for e in events if e["type"] == "delta"),
                         "Hello world")
        self.assertEqual(upstream.last_auth, "Bearer sk-test")
        self.assertEqual(upstream.last_request["model"], "deepseek-chat")
        self.assertIn("system", [m["role"] for m in upstream.last_request["messages"]])

        saved = dh.get_conversation("local", conv["id"])
        self.assertEqual([m["role"] for m in saved["messages"]], ["user", "assistant"])
        self.assertEqual(saved["messages"][1]["content"], "Hello world")
        self.assertEqual(saved["messages"][1]["reasoning"], "thinking")
        self.assertEqual(saved["title"], "Hi")

    def test_upstream_error_is_surfaced_and_user_text_kept(self):
        self._serve_upstream(fail_code=401)
        conv = dh.create_conversation("local")
        exchange = dh.prepare_exchange("local", conv["id"], "Hi")
        events = list(exchange.events())
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("invalid api key", events[-1]["message"])
        saved = dh.get_conversation("local", conv["id"])
        self.assertEqual([m["role"] for m in saved["messages"]], ["user"])

    def test_one_stream_per_conversation(self):
        self._serve_upstream()
        conv = dh.create_conversation("local")
        first = dh.prepare_exchange("local", conv["id"], "one")
        try:
            with self.assertRaises(dh.ChatBusy):
                dh.prepare_exchange("local", conv["id"], "two")
        finally:
            first.close()
        # Once released, the next turn is accepted again.
        second = dh.prepare_exchange("local", conv["id"], "three")
        second.close()

    def test_missing_key_is_not_configured(self):
        conv = dh.create_conversation("local")
        with self.assertRaises(dh.NotConfigured):
            dh.prepare_exchange("local", conv["id"], "Hi")

    def test_blank_message_is_rejected(self):
        conv = dh.create_conversation("local")
        with self.assertRaises(dh.ChatError):
            dh.prepare_exchange("local", conv["id"], "   ")


class DeepSeekAgentRegistryTests(unittest.TestCase):
    def test_chat_agent_is_listed_and_available_without_binary(self):
        with mock.patch.object(agent_registry, "load_agents", lambda: []), \
                mock.patch.object(agent_registry, "detect_installed", lambda **kw: []):
            agents = agent_registry.available_agents()
            agent = agent_registry.get_agent("deepseek-harness")
        by_id = {a["id"]: a for a in agents}
        self.assertIn("deepseek-harness", by_id)
        self.assertEqual(by_id["deepseek-harness"]["transport"], "chat")
        self.assertTrue(by_id["deepseek-harness"]["available"])
        self.assertIsNotNone(agent)
        self.assertEqual(agent["name"], "DeepSeek Harness")

    def test_availability_does_not_require_a_binary(self):
        ok, reason = agent_registry.availability(
            {"id": "deepseek-harness", "transport": "chat"})
        self.assertTrue(ok)
        self.assertEqual(reason, "Ready")

    def test_profile_marks_live_model_switch(self):
        profile = agent_registry.profile("deepseek-harness")
        self.assertEqual(profile["transport"], "chat")
        self.assertTrue(profile["supports_live_model_switch"])


if __name__ == "__main__":
    unittest.main()
