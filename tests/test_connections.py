"""Tests for the unified connection registry (backend/connections.py).

Run:  python3 -m unittest tests.test_connections

Covers CRUD + validation, secret redaction, derived FreeLLMAPI connections,
and honest live tests against a fake local OpenAI-/Anthropic-compatible
endpoint (connection health must come from real probes, never from saved
keys). All stores are redirected to temp dirs; no real credentials exist.
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import config as dennis_config  # noqa: E402
from backend import connections  # noqa: E402
from backend import freellmapi  # noqa: E402


class _FakeUpstream(http.server.BaseHTTPRequestHandler):
    """OpenAI- and Anthropic-compatible model listing for connection tests."""

    openai_key = "sk-fake-openai-key-123456"
    anthropic_key = "sk-ant-fake-key-654321"
    openai_models = {"data": [{"id": "gpt-4o-mini", "object": "model"},
                              {"id": "gpt-4.1-mini", "object": "model"}]}
    anthropic_models = {"data": [{"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"}]}
    next_status = 200

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        status = _FakeUpstream.next_status
        _FakeUpstream.next_status = 200
        # Anthropic-compatible probe: {base}/v1/models with x-api-key.
        if self.path.endswith("/v1/models") and not self.path.endswith("/openai/v1/models"):
            key = self.headers.get("x-api-key") or ""
            if status == 200 and key != _FakeUpstream.anthropic_key:
                return self._send(401, {"error": "bad key"})
            return self._send(status, self.anthropic_models)
        # OpenAI-compatible probe: {base}/models with Bearer auth. The test
        # bases use an /openai prefix so the two probes can share one server.
        if self.path.endswith("/openai/v1/models"):
            auth = self.headers.get("authorization") or ""
            if status == 200 and auth != "Bearer " + _FakeUpstream.openai_key:
                return self._send(401, {"error": "bad key"})
            return self._send(status, self.openai_models)
        return self._send(404, {"error": "not found"})


class ConnectionsTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-connections-test-")
        self._old_files = (connections.CONNECTIONS_FILE, freellmapi.CONFIG_FILE)
        connections.CONNECTIONS_FILE = os.path.join(self.tmp, "connections.json")
        freellmapi.CONFIG_FILE = os.path.join(self.tmp, "freellmapi.json")
        # Env keys would silently win over store entries; keep tests hermetic.
        self._env = {}
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
            self._env[var] = os.environ.pop(var, None)

    def tearDown(self):
        connections.CONNECTIONS_FILE, freellmapi.CONFIG_FILE = self._old_files
        for var, value in self._env.items():
            if value is not None:
                os.environ[var] = value
            else:
                os.environ.pop(var, None)

    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.HTTPServer(("127.0.0.1", 0), _FakeUpstream)
        cls.upstream_port = cls.upstream.server_address[1]
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()


class ConnectionStoreTests(ConnectionsTestBase):
    def test_create_requires_type_base_url_and_provider(self):
        with self.assertRaises(connections.ConnectionError):
            connections.create_connection({"name": "no-type", "base_url": "https://x.dev/v1",
                                           "provider_id": "x"})
        with self.assertRaises(connections.ConnectionError):
            connections.create_connection({"type": "gateway", "base_url": "", "provider_id": "x"})
        with self.assertRaises(connections.ConnectionError):
            connections.create_connection({"type": "gateway", "base_url": "https://x.dev/v1",
                                           "provider_id": ""})
        conn = connections.create_connection({"type": "gateway", "name": "Mine",
                                              "base_url": "https://api.example.com/v1/",
                                              "provider_id": "custom_openai"})
        self.assertTrue(conn["id"].startswith("conn-"))
        self.assertEqual(conn["base_url"], "https://api.example.com/v1")  # trailing / stripped

    def test_base_url_validation(self):
        with self.assertRaises(connections.ConnectionError):
            connections.create_connection({"type": "gateway", "base_url": "ftp://x",
                                           "provider_id": "p"})
        with self.assertRaises(connections.ConnectionError):
            connections.create_connection({"type": "gateway", "base_url": "https://user:pw@x.dev/v1",
                                           "provider_id": "p"})
        with self.assertRaises(connections.ConnectionError):
            connections.create_connection({"type": "gateway", "base_url": "https://{key}@x.dev",
                                           "provider_id": "p"})
        with self.assertRaises(connections.ConnectionError):
            connections.create_connection({"type": "gateway", "base_url": "http://insecure.dev/v1",
                                           "provider_id": "p"})
        local = connections.create_connection({"type": "gateway", "base_url": "http://localhost:4000/v1",
                                               "provider_id": "litellm"})
        self.assertEqual(local["base_url"], "http://localhost:4000/v1")  # local http allowed

    def test_public_view_redacts_key(self):
        conn = connections.create_connection({"type": "provider", "name": "Direct",
                                              "base_url": "https://api.openai.com/v1",
                                              "provider_id": "openai",
                                              "api_key": "sk-super-secret-value"})
        view = connections.public_view(conn)
        self.assertNotIn("sk-super-secret-value", json.dumps(view))
        self.assertEqual(view["auth"]["hint"], "…alue")
        self.assertTrue(view["auth"]["configured"])

    def test_env_key_wins_over_store(self):
        conn = connections.create_connection({"type": "provider", "base_url": "https://api.groq.com/openai/v1",
                                              "provider_id": "groq", "key_env": "GROQ_API_KEY",
                                              "api_key": "stored"})
        os.environ["GROQ_API_KEY"] = "env-key"
        try:
            key, source = connections.resolve_key(conn)
            self.assertEqual(key, "env-key")
            self.assertEqual(source, "env:GROQ_API_KEY")
        finally:
            os.environ.pop("GROQ_API_KEY", None)

    def test_update_and_delete(self):
        conn = connections.create_connection({"type": "gateway", "base_url": "https://a.dev/v1",
                                              "provider_id": "custom_openai"})
        updated = connections.update_connection(conn["id"], {"name": "Renamed",
                                                             "pricing": "free"})
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(updated["pricing"], "free")
        self.assertEqual(updated["base_url"], "https://a.dev/v1")  # untouched field kept
        result = connections.delete_connection(conn["id"])
        self.assertEqual(result["deleted"], conn["id"])
        with self.assertRaises(connections.ConnectionError):
            connections.delete_connection(conn["id"])

    def test_derived_freellmapi_connections(self):
        freellmapi.set_config({"agent_id": "claude-code",
                               "base_url": "https://api.groq.com/openai/v1",
                               "model": "llama-3.3-70b-versatile",
                               "api_key": "gsk_fake_1234"})
        derived = [c for c in connections.list_connections() if c["id"] == "freellmapi:claude-code"]
        self.assertEqual(len(derived), 1)
        conn = derived[0]
        self.assertEqual(conn["type"], "gateway")
        self.assertEqual(conn["protocol"], "anthropic")  # claude-code speaks Anthropic
        self.assertEqual(conn["model_ids"], ["llama-3.3-70b-versatile"])
        view = connections.public_view(conn)
        self.assertNotIn("gsk_fake_1234", json.dumps(view))
        # Deleting the derived connection clears the underlying settings.
        connections.delete_connection("freellmapi:claude-code")
        self.assertIsNone(freellmapi.get("claude-code"))


class ConnectionHealthTests(ConnectionsTestBase):
    def test_openai_protocol_test_connected(self):
        conn = connections.create_connection({
            "type": "gateway", "name": "Fake OpenAI gw",
            "base_url": "http://127.0.0.1:%d/openai/v1" % self.upstream_port,
            "provider_id": "custom_openai", "api_key": _FakeUpstream.openai_key,
        })
        result = connections.test_connection(conn["id"])
        self.assertEqual(result["status"], connections.STATUS_CONNECTED)
        self.assertEqual(result["model_count"], 2)
        self.assertGreaterEqual(result["latency_ms"], 0)
        health = connections.get_health(conn["id"])
        self.assertEqual(health["status"], connections.STATUS_CONNECTED)

    def test_anthropic_protocol_test_connected(self):
        conn = connections.create_connection({
            "type": "gateway", "name": "Fake Anthropic gw",
            "base_url": "http://127.0.0.1:%d" % self.upstream_port,
            "provider_id": "custom_anthropic", "protocol": "anthropic",
            "api_key": _FakeUpstream.anthropic_key,
        })
        result = connections.test_connection(conn["id"])
        self.assertEqual(result["status"], connections.STATUS_CONNECTED)
        self.assertEqual(result["model_count"], 1)

    def test_auth_failure_is_reported_honestly(self):
        conn = connections.create_connection({
            "type": "gateway", "base_url": "http://127.0.0.1:%d/openai/v1" % self.upstream_port,
            "provider_id": "custom_openai", "api_key": "wrong-key",
        })
        result = connections.test_connection(conn["id"])
        self.assertEqual(result["status"], connections.STATUS_AUTH_FAILED)

    def test_unreachable_endpoint(self):
        conn = connections.create_connection({
            "type": "gateway", "base_url": "http://127.0.0.1:9/x",  # closed port
            "provider_id": "custom_openai", "api_key": "k",
        })
        result = connections.test_connection(conn["id"], timeout=1.0)
        self.assertEqual(result["status"], connections.STATUS_UNAVAILABLE)

    def test_rate_limited(self):
        conn = connections.create_connection({
            "type": "gateway", "base_url": "http://127.0.0.1:%d/openai/v1" % self.upstream_port,
            "provider_id": "custom_openai", "api_key": _FakeUpstream.openai_key,
        })
        _FakeUpstream.next_status = 429
        result = connections.test_connection(conn["id"])
        self.assertEqual(result["status"], connections.STATUS_RATE_LIMITED)

    def test_effective_status_never_trusts_stale_success(self):
        conn = connections.create_connection({
            "type": "gateway", "base_url": "http://127.0.0.1:%d/openai/v1" % self.upstream_port,
            "provider_id": "custom_openai", "api_key": _FakeUpstream.openai_key,
        })
        connections.test_connection(conn["id"])
        # Expire the cached health by rewriting its timestamp.
        connections.set_health(conn["id"], {
            "status": connections.STATUS_CONNECTED, "checked_at": "2000-01-01T00:00:00",
            "latency_ms": 1, "detail": "",
        })
        self.assertEqual(connections.effective_status(conn), connections.STATUS_CONFIGURED)

    def test_auth_required_when_no_key_and_preset_expects_one(self):
        conn = connections.create_connection({
            "type": "provider", "base_url": "https://api.groq.com/openai/v1",
            "provider_id": "groq", "key_env": "GROQ_API_KEY",
        })
        self.assertEqual(connections.effective_status(conn), connections.STATUS_AUTH_REQUIRED)


if __name__ == "__main__":
    unittest.main()
