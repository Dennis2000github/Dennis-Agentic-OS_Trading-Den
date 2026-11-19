"""Tests for the unified model registry (backend/model_catalog.py).

Run:  python3 -m unittest tests.test_model_catalog

Covers namespaced ids, normalization (capabilities/pricing never invented),
discovery against a fake OpenAI-compatible endpoint, stale-on-failure
behaviour, manual registration and aliases. Stores live in temp dirs.
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import connections  # noqa: E402
from backend import freellmapi  # noqa: E402
from backend import model_catalog  # noqa: E402


class _FakeOpenAI(http.server.BaseHTTPRequestHandler):
    payload = {"data": [
        {"id": "gpt-4o-mini", "object": "model"},
        {"id": "mystery-model", "context_length": 1000000,
         "architecture": {"modality": "text->text"}, "pricing": {"prompt": "0"}},
        {"id": "paid-model", "context_length": 262144, "pricing": {"prompt": "0.0000015"}},
    ]}
    next_status = 200

    def log_message(self, *args):
        pass

    def do_GET(self):
        status = _FakeOpenAI.next_status
        _FakeOpenAI.next_status = 200
        body = json.dumps(_FakeOpenAI.payload if status == 200 else {"error": "x"}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ModelCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.HTTPServer(("127.0.0.1", 0), _FakeOpenAI)
        cls.port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-catalog-test-")
        self._old = (model_catalog.CATALOG_FILE, connections.CONNECTIONS_FILE,
                     freellmapi.CONFIG_FILE)
        model_catalog.CATALOG_FILE = os.path.join(self.tmp, "model_catalog.json")
        connections.CONNECTIONS_FILE = os.path.join(self.tmp, "connections.json")
        # Derived FreeLLMAPI connections must not touch the real settings.
        freellmapi.CONFIG_FILE = os.path.join(self.tmp, "freellmapi.json")

    def tearDown(self):
        (model_catalog.CATALOG_FILE, connections.CONNECTIONS_FILE,
         freellmapi.CONFIG_FILE) = self._old

    def _gateway(self):
        return connections.create_connection({
            "type": "gateway", "name": "Fake gw",
            "base_url": "http://127.0.0.1:%d/v1" % self.port,
            "provider_id": "custom_openai", "pricing": "free",
        })

    def test_internal_ids_roundtrip(self):
        self.assertTrue(model_catalog.is_connection_model("conn:conn-abc123:gpt-4o"))
        self.assertEqual(model_catalog.parse_internal_id("conn:conn-abc123:gpt-4o"),
                         ("conn-abc123", "gpt-4o"))
        # Native ids containing colons survive the split.
        self.assertEqual(model_catalog.parse_internal_id("conn:conn-abc123:org/model:beta"),
                         ("conn-abc123", "org/model:beta"))
        self.assertFalse(model_catalog.is_connection_model("claude-sonnet-4-5"))
        self.assertIsNone(model_catalog.parse_internal_id("conn::missing"))

    def test_parse_handles_colons_in_connection_ids(self):
        """Regression (2026-11-14): derived FreeLLMAPI connection ids contain
        colons (freellmapi:claude-code), which the old naive split turned
        into a non-existent connection 'freellmapi'."""
        freellmapi.set_config({"agent_id": "claude-code",
                               "base_url": "https://integrate.api.nvidia.com/v1",
                               "model": "z-ai/glm-5.3-flash",
                               "api_key": "nvapi_fake_key"})
        internal = model_catalog.internal_id("freellmapi:claude-code", "z-ai/glm-5.3-flash")
        self.assertEqual(internal, "conn:freellmapi:claude-code:z-ai/glm-5.3-flash")
        parsed = model_catalog.parse_internal_id(internal)
        self.assertEqual(parsed, ("freellmapi:claude-code", "z-ai/glm-5.3-flash"))
        # The full evaluate/env path resolves the real connection.
        record, conn = model_catalog.get_record(internal)
        self.assertIsNotNone(record)
        self.assertEqual(conn["id"], "freellmapi:claude-code")
        self.assertEqual(record["native_id"], "z-ai/glm-5.3-flash")

    def test_discovery_normalizes_without_inventing_metadata(self):
        conn = self._gateway()
        records = model_catalog.discover(conn["id"])
        by_native = {r["native_id"]: r for r in records}
        self.assertEqual(set(by_native), {"gpt-4o-mini", "mystery-model", "paid-model"})
        mini = by_native["gpt-4o-mini"]
        self.assertEqual(mini["id"], "conn:%s:gpt-4o-mini" % conn["id"])
        self.assertEqual(mini["source"], "discovery")
        self.assertEqual(mini["capabilities"]["image"], "unknown")  # no metadata -> unknown
        self.assertEqual(mini["pricing"], "free")  # connection-level class (metadata silent)
        mystery = by_native["mystery-model"]
        self.assertEqual(mystery["context_window"], 1000000)  # from upstream metadata
        self.assertEqual(mystery["pricing"], "free")  # upstream says 0 -> free
        self.assertEqual(mystery["capabilities"]["image"], "unsupported")
        paid = by_native["paid-model"]
        self.assertEqual(paid["pricing"], "paid")  # upstream says >0 -> paid

    def test_failed_refresh_marks_records_stale_not_deleted(self):
        conn = self._gateway()
        records = model_catalog.discover(conn["id"])
        self.assertEqual(len(records), 3)
        _FakeOpenAI.next_status = 500
        try:
            refreshed, error = model_catalog.catalog_records(conn["id"], refresh=True)
        finally:
            _FakeOpenAI.next_status = 200
        self.assertTrue(error)
        self.assertEqual(len(refreshed), 3)  # known models preserved
        self.assertTrue(all(r["availability"] == "stale" for r in refreshed))
        # Stale is distinct from confirmed unavailability.
        self.assertTrue(all(r["available"] for r in refreshed))

    def test_manual_registration_and_removal(self):
        conn = self._gateway()
        record = model_catalog.register_model(conn["id"], "my-custom-model", name="Custom")
        self.assertEqual(record["source"], "manual")
        records, _ = model_catalog.catalog_records(conn["id"])
        self.assertIn("my-custom-model", [r["native_id"] for r in records])
        # Manual ids persist on the connection so refreshes cannot lose them.
        fresh = connections.get_connection(conn["id"])
        self.assertIn("my-custom-model", fresh["model_ids"])
        model_catalog.remove_model(conn["id"], "my-custom-model")
        records, _ = model_catalog.catalog_records(conn["id"])
        self.assertNotIn("my-custom-model", [r["native_id"] for r in records])

    def test_alias_mapping(self):
        conn = self._gateway()
        model_catalog.set_alias(conn["id"], "deepseek/deepseek-chat", "deepseek-chat")
        fresh = connections.get_connection(conn["id"])
        self.assertEqual(model_catalog.alias_for(conn["id"], "deepseek/deepseek-chat"),
                         "deepseek-chat")
        self.assertEqual(fresh["aliases"]["deepseek/deepseek-chat"], "deepseek-chat")

    def test_discovery_requires_known_connection(self):
        with self.assertRaises(connections.ConnectionError):
            model_catalog.catalog_records("conn-does-not-exist")

    def test_get_record_for_unknown_model_returns_none(self):
        conn = self._gateway()
        record, c = model_catalog.get_record("conn:%s:nope" % conn["id"])
        self.assertIsNone(record)
        self.assertIsNotNone(c)


if __name__ == "__main__":
    unittest.main()
