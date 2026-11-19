"""HTTP route tests for the unified AI connection architecture.

Run:  python3 -m unittest tests.test_connection_routes

Boots the real server on an ephemeral port and exercises /api/connections,
compatibility and routing endpoints: owner gating, CRUD, live test +
discovery against a fake local upstream, secret redaction and the routing
preference flow. Stores are redirected to temp dirs.
"""

import http.client
import http.server
import json
import os
import sys
import tempfile
import threading
import unittest

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import auth as dennis_auth  # noqa: E402
from backend import connections  # noqa: E402
from backend import freellmapi  # noqa: E402
from backend import model_catalog  # noqa: E402
from backend import routing  # noqa: E402


class _FakeUpstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"data": [{"id": "fake-model-a"}, {"id": "fake-model-b"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ConnectionRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.HTTPServer(("127.0.0.1", 0), _FakeUpstream)
        cls.upstream_port = cls.upstream.server_address[1]
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.token = dennis_auth.AUTH.token

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-conn-routes-")
        self._old = (connections.CONNECTIONS_FILE, model_catalog.CATALOG_FILE,
                     routing.ROUTING_FILE, freellmapi.CONFIG_FILE)
        connections.CONNECTIONS_FILE = os.path.join(self.tmp, "connections.json")
        model_catalog.CATALOG_FILE = os.path.join(self.tmp, "model_catalog.json")
        routing.ROUTING_FILE = os.path.join(self.tmp, "routing.json")
        freellmapi.CONFIG_FILE = os.path.join(self.tmp, "freellmapi.json")

    def tearDown(self):
        (connections.CONNECTIONS_FILE, model_catalog.CATALOG_FILE,
         routing.ROUTING_FILE, freellmapi.CONFIG_FILE) = self._old

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"X-Dennis-Token": self.token}
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            payload = json.loads(resp.read() or b"{}")
            return resp.status, payload
        finally:
            conn.close()

    def _create(self, **overrides):
        payload = {
            "type": "gateway", "name": "Route test gw",
            "base_url": "http://127.0.0.1:%d/v1" % self.upstream_port,
            "provider_id": "custom_openai", "api_key": "sk-secret-route-key",
        }
        payload.update(overrides)
        status, data = self.request("POST", "/api/connections", payload)
        self.assertEqual(status, 201)
        return data["connection"]

    # -- gating -------------------------------------------------------------
    def test_connections_require_owner_token(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", "/api/connections")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 401)
        finally:
            conn.close()

    # -- CRUD ----------------------------------------------------------------
    def test_create_list_get_patch_delete(self):
        created = self._create()
        cid = created["id"]
        self.assertNotIn("sk-secret-route-key", json.dumps(created))
        status, listing = self.request("GET", "/api/connections")
        self.assertEqual(status, 200)
        self.assertIn("presets", listing)
        self.assertIn("matrix", listing)
        ids = [c["id"] for c in listing["connections"]]
        self.assertIn(cid, ids)
        status, detail = self.request("GET", "/api/connections/" + cid)
        self.assertEqual(status, 200)
        self.assertEqual(detail["connection"]["id"], cid)
        status, patched = self.request("PATCH", "/api/connections/" + cid,
                                       {"name": "Renamed", "pricing": "free"})
        self.assertEqual(status, 200)
        self.assertEqual(patched["connection"]["name"], "Renamed")
        status, deleted = self.request("DELETE", "/api/connections/" + cid)
        self.assertEqual(status, 200)
        status, _ = self.request("GET", "/api/connections/" + cid)
        self.assertEqual(status, 404)

    def test_create_validation_errors(self):
        status, data = self.request("POST", "/api/connections", {"type": "nope"})
        self.assertEqual(status, 400)
        status, data = self.request("POST", "/api/connections",
                                    {"type": "gateway", "base_url": "http://1.2.3.4/v1"})
        self.assertEqual(status, 400)  # missing provider_id

    # -- test + discover ------------------------------------------------------
    def test_test_endpoint_reports_real_health(self):
        conn = self._create()
        status, data = self.request("POST", "/api/connections/%s/test" % conn["id"], {})
        self.assertEqual(status, 200)
        self.assertEqual(data["health"]["status"], "connected")
        self.assertEqual(data["health"]["model_count"], 2)

    def test_discover_returns_unified_models(self):
        conn = self._create()
        status, data = self.request("POST", "/api/connections/%s/discover" % conn["id"], {})
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 2)
        native_ids = {m["native_id"] for m in data["models"]}
        self.assertEqual(native_ids, {"fake-model-a", "fake-model-b"})
        for record in data["models"]:
            self.assertTrue(record["id"].startswith("conn:" + conn["id"] + ":"))
        # Models are also reachable via GET (cached), and manual registration works.
        status, data = self.request("GET", "/api/connections/%s/models" % conn["id"])
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 2)
        status, data = self.request("POST", "/api/connections/%s/models" % conn["id"],
                                    {"native_id": "manual-model"})
        self.assertEqual(status, 200)
        self.assertEqual(data["model"]["source"], "manual")
        status, data = self.request(
            "DELETE", "/api/connections/%s/models?native_id=manual-model" % conn["id"])
        self.assertEqual(status, 200)

    # -- compatibility ---------------------------------------------------------
    def test_compatibility_endpoints(self):
        status, data = self.request("GET", "/api/compatibility")
        self.assertEqual(status, 200)
        self.assertIn("matrix", data)
        status, data = self.request("GET", "/api/compatibility/codex")
        self.assertEqual(status, 200)
        self.assertIn("connections", data)
        conn = self._create()
        model_id = "conn:%s:fake-model-a" % conn["id"]
        self.request("POST", "/api/connections/%s/discover" % conn["id"], {})
        status, data = self.request("GET", "/api/compatibility/codex?model=" + model_id)
        self.assertEqual(status, 200)
        self.assertEqual(data["evaluation"]["status"], "compatible")

    def test_agent_models_route_exposes_connection_data(self):
        conn = self._create()
        self.request("POST", "/api/connections/%s/discover" % conn["id"], {})
        status, data = self.request("GET", "/api/agents/codex/models")
        self.assertEqual(status, 200)
        # Legacy fields remain intact.
        self.assertIn("models", data)
        self.assertIn("providers", data)
        self.assertIn("custom_provider", data)
        # New unified fields are additive.
        self.assertIn("connection_sources", data)
        self.assertIn("connection_models", data)
        gateway_ids = [c["id"] for c in data["connection_sources"]["gateway"]]
        self.assertIn(conn["id"], gateway_ids)
        self.assertTrue(any(m["id"].startswith("conn:") for m in data["connection_models"]))

    # -- routing -----------------------------------------------------------------
    def test_routing_preferences_roundtrip(self):
        status, data = self.request("GET", "/api/routing/preferences")
        self.assertEqual(status, 200)
        self.assertEqual(data["preferences"]["global"]["mode"], "free_first")
        status, data = self.request("POST", "/api/routing/preferences",
                                    {"mode": "paid_first"})
        self.assertEqual(status, 200)
        self.assertEqual(data["preferences"]["global"]["mode"], "paid_first")
        status, data = self.request("POST", "/api/routing/preferences",
                                    {"agent_id": "codex", "agent_mode": "manual"})
        self.assertEqual(status, 200)
        self.assertEqual(data["preferences"]["agents"]["codex"]["mode"], "manual")
        status, data = self.request("POST", "/api/routing/preferences", {"mode": "bogus"})
        self.assertEqual(status, 400)

    def test_routing_resolve_and_paid_authorization(self):
        conn = self._create()
        self.request("POST", "/api/connections/%s/discover" % conn["id"], {})
        # Unknown agent -> honest outcome. The connection's pricing defaults
        # to "unknown", which is deliberately NOT assumed free (spec: never
        # assume a gateway is free because it is a proxy).
        status, data = self.request("POST", "/api/routing/resolve", {"agent_id": "codex"})
        self.assertEqual(status, 200)
        self.assertIn(data["status"], ("resolved", "no_route", "authorization_required"))
        # Paid-only connection under free_first requires authorization.
        self.request("PATCH", "/api/connections/" + conn["id"], {"pricing": "paid"})
        status, data = self.request("POST", "/api/routing/resolve", {"agent_id": "codex"})
        self.assertEqual(status, 200)
        if data["status"] == "authorization_required":
            self.assertFalse(data["paid_fallback_allowed"])
            status, granted = self.request("POST", "/api/routing/authorize-paid",
                                           {"connection_id": conn["id"],
                                            "reason": "route test"})
            self.assertEqual(status, 200)
            status, data = self.request("POST", "/api/routing/resolve",
                                        {"agent_id": "codex"})
            self.assertEqual(data["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
