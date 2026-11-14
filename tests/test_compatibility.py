"""Tests for the capability-based compatibility registry.

Run:  python3 -m unittest tests.test_compatibility

Validates the Agent + Source + Connection + Protocol + Model matrix using
the documented mechanisms (claude-code/Anthropic, codex/OpenAI verified in
daily use via FreeLLMAPI; everything else honestly not_tested/incompatible).
``evaluate`` returns a diagnostic dict whose ``status`` is the verdict.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import compatibility  # noqa: E402
from backend import connections  # noqa: E402
from backend import freellmapi  # noqa: E402
from backend import model_catalog  # noqa: E402


class CompatibilityTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-compat-test-")
        self._old = (connections.CONNECTIONS_FILE, model_catalog.CATALOG_FILE,
                     freellmapi.CONFIG_FILE)
        connections.CONNECTIONS_FILE = os.path.join(self.tmp, "connections.json")
        model_catalog.CATALOG_FILE = os.path.join(self.tmp, "model_catalog.json")
        # Derived FreeLLMAPI connections must not leak the user's real setup.
        freellmapi.CONFIG_FILE = os.path.join(self.tmp, "freellmapi.json")
        self._env_openai = os.environ.pop("OPENAI_API_KEY", None)
        self._env_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)

    def tearDown(self):
        (connections.CONNECTIONS_FILE, model_catalog.CATALOG_FILE,
         freellmapi.CONFIG_FILE) = self._old
        for var, value in (("OPENAI_API_KEY", self._env_openai),
                           ("ANTHROPIC_API_KEY", self._env_anthropic)):
            if value is not None:
                os.environ[var] = value
            else:
                os.environ.pop(var, None)

    def _gateway(self, protocol="openai", key="sk-test-key-123456"):
        return connections.create_connection({
            "type": "gateway", "name": "Test gw",
            "base_url": "http://localhost:9999/v1" if protocol == "openai"
                        else "http://localhost:9999",
            "provider_id": "custom_" + protocol, "protocol": protocol,
            "api_key": key,
        })

    def _eval(self, agent_id, conn, native="m"):
        return compatibility.evaluate(
            agent_id, model_catalog.internal_id(conn["id"], native))


class MatrixTests(CompatibilityTestBase):
    def test_claude_code_accepts_anthropic_gateway(self):
        conn = self._gateway("anthropic")
        model_catalog.register_model(conn["id"], "claude-sonnet-4-5")
        diag = self._eval("claude-code", conn, "claude-sonnet-4-5")
        self.assertEqual(diag["status"], compatibility.COMPATIBLE)
        self.assertEqual(diag["execution"], "cli_native")
        self.assertIn("ANTHROPIC_BASE_URL", diag["requirements"])

    def test_claude_code_rejects_openai_gateway(self):
        conn = self._gateway("openai")
        diag = self._eval("claude-code", conn, "gpt-4o")
        self.assertEqual(diag["status"], compatibility.INCOMPATIBLE)

    def test_codex_accepts_openai_gateway(self):
        conn = self._gateway("openai")
        model_catalog.register_model(conn["id"], "gpt-4o-mini")
        diag = self._eval("codex", conn, "gpt-4o-mini")
        self.assertEqual(diag["status"], compatibility.COMPATIBLE)

    def test_deepseek_harness_executes_in_process(self):
        conn = self._gateway("openai")
        model_catalog.register_model(conn["id"], "some-model")
        diag = self._eval("deepseek-harness", conn, "some-model")
        self.assertEqual(diag["status"], compatibility.COMPATIBLE)
        self.assertEqual(diag["execution"], "api")

    def test_unvalidated_agents_are_not_compatible(self):
        conn = self._gateway("openai")
        for agent in ("antigravity", "cursor", "hermes-cli", "gemini-cli", "goose"):
            diag = self._eval(agent, conn)
            self.assertEqual(diag["status"], compatibility.INCOMPATIBLE, agent)
        # opencode documents a mechanism but is not validated yet.
        diag = self._eval("opencode", conn)
        self.assertEqual(diag["status"], compatibility.NOT_TESTED)

    def test_missing_key_is_auth_required(self):
        conn = connections.create_connection({
            "type": "gateway", "base_url": "http://localhost:9999/v1",
            "provider_id": "custom_openai",  # no key_env, no key stored
        })
        diag = self._eval("codex", conn, "gpt-4o")
        self.assertEqual(diag["status"], compatibility.AUTH_REQUIRED)

    def test_unknown_connection_is_reported(self):
        diag = compatibility.evaluate("codex", "conn:conn-gone:model")
        self.assertEqual(diag["status"], compatibility.CONNECTION_UNAVAILABLE)

    def test_cli_native_ids_are_not_connection_evaluated(self):
        diag = compatibility.evaluate("codex", "gpt-5.2-codex")
        self.assertEqual(diag["execution"], "cli_native")

    def test_unregistered_model_on_valid_connection(self):
        conn = self._gateway("openai")
        # No discovery happened: the model is not known on the connection.
        diag = self._eval("codex", conn, "never-seen")
        self.assertEqual(diag["status"], compatibility.NOT_TESTED)
        self.assertIn("register", diag["reason"].lower())

    def test_registered_model_on_valid_connection_is_compatible(self):
        conn = self._gateway("openai")
        model_catalog.register_model(conn["id"], "gpt-4o-mini")
        diag = self._eval("codex", conn, "gpt-4o-mini")
        self.assertEqual(diag["status"], compatibility.COMPATIBLE)
        self.assertEqual(diag["model"]["native_id"], "gpt-4o-mini")

    def test_compatible_connections_lists_statuses(self):
        self._gateway("openai")
        rows = compatibility.compatible_connections("codex")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["compatibility"], compatibility.COMPATIBLE)
        rows = compatibility.compatible_connections("antigravity")
        self.assertEqual(rows[0]["compatibility"], compatibility.INCOMPATIBLE)

    def test_matrix_public_covers_registered_agents(self):
        rows = compatibility.matrix_public()
        ids = {r["agent_id"] for r in rows}
        self.assertIn("claude-code", ids)
        self.assertIn("codex", ids)
        self.assertIn("deepseek-harness", ids)
        for row in rows:
            for protocol, info in row["protocols"].items():
                self.assertIn(info["status"],
                              (compatibility.COMPATIBLE, compatibility.INCOMPATIBLE,
                               compatibility.NOT_TESTED))


if __name__ == "__main__":
    unittest.main()
