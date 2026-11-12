"""HTTP tests for the FreeLLMAPI catalog + settings routes.

The catalog is pointed at a temporary README, and saved connections are
redirected into a temporary data directory, so no real settings are touched.

Run:  python3 -m unittest tests.test_freellm_routes -v
"""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import auth as dennis_auth  # noqa: E402
from backend import config as dennis_config  # noqa: E402
from backend import freellm_catalog  # noqa: E402
from backend import freellmapi  # noqa: E402


README = """# awesome-free-llm-apis
Last updated: 2026-10-08
<!-- BEGIN_PERMANENT_FREE -->
| Provider | Free Models | Credit Card? | Max Context | Modalities | Get API Key |
|---|---|---|---|---|---|
| Groq | 12 | No | 262K | reasoning, text | <a href="https://console.groq.com/keys">→</a> |
<!-- END_PERMANENT_FREE -->
<!-- BEGIN_QUICK_REF -->
| Provider | Base URL | Get API Key | Credit Card? |
|---|---|---|---|
| Groq | `https://api.groq.com/openai/v1` | <a href="https://console.groq.com/keys">Get Key →</a> | No |
<!-- END_QUICK_REF -->
<!-- BEGIN_BEST_MODELS -->
| Provider | Best Free Model | Model ID | Max Context | Rate Limit |
|---|---|---|---|---|
| Groq | <a href="https://freellm.net/models/groq/kimi/">Kimi K2</a> | `moonshotai/kimi-k2-instruct` | 131K | 30 RPM |
<!-- END_BEST_MODELS -->
<!-- BEGIN_TOP_MODELS -->
| Model | Provider | Context | Weekly Usage |
|---|---|---|---|
| <a href="https://freellm.net/models/groq/kimi/">Kimi K2</a> | Groq | 131K | 10B tokens |
<!-- END_TOP_MODELS -->
"""


class FreeLLMAPIRouteTests(unittest.TestCase):
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
        self.tmp = tempfile.mkdtemp(prefix="dennis-freellm-routes-")
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(self.repo)
        with open(os.path.join(self.repo, "README.md"), "w", encoding="utf-8") as fh:
            fh.write(README)
        self.patch_catalog = mock.patch.object(
            freellm_catalog, "candidate_paths", return_value=[self.repo]
        )
        self.patch_catalog.start()
        freellm_catalog._CACHE.update({"path": None, "mtime": None, "data": None})
        self.patch_config = mock.patch.object(
            freellmapi, "CONFIG_FILE", os.path.join(self.tmp, "freellmapi.json")
        )
        self.patch_config.start()
        self.patch_data = mock.patch.object(dennis_config, "DATA_DIR", self.tmp)
        self.patch_data.start()

    def tearDown(self):
        self.patch_data.stop()
        self.patch_config.stop()
        self.patch_catalog.stop()
        freellm_catalog._CACHE.update({"path": None, "mtime": None, "data": None})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _json(self, method, path, body=None, auth=True):
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
        return resp.status, json.loads(data.decode("utf-8") or "{}")

    def test_catalog_requires_owner_token(self):
        status, _data = self._json("GET", "/api/freellmapi/catalog", auth=False)
        self.assertEqual(status, 401)

    def test_catalog_returns_providers_models_and_agent_status(self):
        status, data = self._json("GET", "/api/freellmapi/catalog")
        self.assertEqual(status, 200)
        catalog = data["catalog"]
        self.assertTrue(catalog["ok"])
        self.assertEqual(catalog["counts"]["providers"], 1)
        self.assertEqual(catalog["providers"][0]["base_url"], "https://api.groq.com/openai/v1")
        self.assertEqual(catalog["providers"][0]["models"][0]["id"], "moonshotai/kimi-k2-instruct")
        self.assertEqual(set(data["agents"]), {"claude-code", "codex"})
        self.assertFalse(data["agents"]["claude-code"]["configured"])

    def test_connect_and_clear_an_agent(self):
        status, data = self._json("POST", "/api/freellmapi", {
            "agent_id": "claude-code",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "moonshotai/kimi-k2-instruct",
            "api_key": "sk-test-1234",
        })
        self.assertEqual(status, 200)
        self.assertTrue(data["settings"]["configured"])
        self.assertEqual(data["settings"]["key_hint"], "…1234")

        status, catalog = self._json("GET", "/api/freellmapi/catalog")
        self.assertEqual(status, 200)
        self.assertTrue(catalog["agents"]["claude-code"]["configured"])
        self.assertFalse(catalog["agents"]["codex"]["configured"])

        status, cleared = self._json("DELETE", "/api/freellmapi?agent_id=claude-code")
        self.assertEqual(status, 200)
        self.assertFalse(cleared["settings"]["configured"])

    def test_blank_key_is_only_reused_for_the_same_endpoint(self):
        first = {
            "agent_id": "codex",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "moonshotai/kimi-k2-instruct",
            "api_key": "sk-groq-1234",
        }
        status, _data = self._json("POST", "/api/freellmapi", first)
        self.assertEqual(status, 200)

        status, data = self._json("POST", "/api/freellmapi", {
            "agent_id": "codex",
            "base_url": "https://api.cerebras.ai/v1",
            "model": "llama3.1-70b",
            "api_key": "",
        })
        self.assertEqual(status, 400)
        self.assertIn("API key", data["error"])

        status, data = self._json("POST", "/api/freellmapi", {
            "agent_id": "codex",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "llama-3.3-70b",
            "api_key": "",
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["key_hint"], "…1234")

    def test_invalid_agent_is_rejected(self):
        status, data = self._json("POST", "/api/freellmapi", {
            "agent_id": "not-an-agent",
            "base_url": "https://api.groq.com/openai/v1",
            "model": "moonshotai/kimi-k2-instruct",
            "api_key": "sk-test-1234",
        })
        self.assertEqual(status, 400)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
