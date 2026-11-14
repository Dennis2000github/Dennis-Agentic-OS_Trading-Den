"""Tests for the unified execution router (backend/execution_router.py).

Run:  python3 -m unittest tests.test_execution_router

Covers env resolution for connection-scoped models (the generalized
FreeLLMAPI mechanism), FreeLLMAPI behaviour staying untouched, native id
translation for CLI argv, the in-process DeepSeek override and loud failure
when a route cannot be resolved. Stores live in temp dirs.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import agent_registry  # noqa: E402
from backend import compatibility  # noqa: E402
from backend import connections  # noqa: E402
from backend import deepseek_harness  # noqa: E402
from backend import execution_router  # noqa: E402
from backend import freellmapi  # noqa: E402
from backend import model_catalog  # noqa: E402


class ExecutionRouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-router-test-")
        self._old = (connections.CONNECTIONS_FILE, model_catalog.CATALOG_FILE,
                     freellmapi.CONFIG_FILE)
        connections.CONNECTIONS_FILE = os.path.join(self.tmp, "connections.json")
        model_catalog.CATALOG_FILE = os.path.join(self.tmp, "model_catalog.json")
        freellmapi.CONFIG_FILE = os.path.join(self.tmp, "freellmapi.json")
        self._env = {}
        # Scrub ambient credentials AND the connection env vars: the router
        # starts from os.environ, and a host process that itself runs on a
        # FreeLLMAPI/gateway connection (e.g. Claude Code) exports
        # ANTHROPIC_BASE_URL/AUTH_TOKEN/MODEL, which would otherwise leak
        # into the "plain" env assertions below (2026-11-15).
        for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                    "DEEPSEEK_BASE_URL",
                    "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_MODEL", "ANTHROPIC_CUSTOM_MODEL_OPTION",
                    "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
                    "OPENAI_BASE_URL"):
            self._env[var] = os.environ.pop(var, None)
        # Keep the in-process DeepSeek client away from real credentials.
        self._old_ds_dir = deepseek_harness._dir
        deepseek_harness._dir = lambda: os.path.join(self.tmp, "deepseek")

    def tearDown(self):
        deepseek_harness._dir = self._old_ds_dir
        (connections.CONNECTIONS_FILE, model_catalog.CATALOG_FILE,
         freellmapi.CONFIG_FILE) = self._old
        for var, value in self._env.items():
            if value is not None:
                os.environ[var] = value
            else:
                os.environ.pop(var, None)

    def _connection(self, protocol="anthropic", base="http://localhost:9999"):
        conn = connections.create_connection({
            "type": "gateway", "name": "Router test",
            "base_url": base + "/v1" if protocol == "openai" else base,
            "provider_id": "custom_" + protocol, "protocol": protocol,
            "api_key": "sk-router-test-key-999",
        })
        model_catalog.register_model(conn["id"], "test-model-id")
        return conn

    def _conn_model(self, conn, native="test-model-id"):
        return model_catalog.internal_id(conn["id"], native)


    # -- env resolution -------------------------------------------------------
    def test_claude_code_gets_anthropic_env_for_connection_model(self):
        conn = self._connection("anthropic")
        env = execution_router.apply_env({}, "claude-code", self._conn_model(conn))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], conn["base_url"])
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "sk-router-test-key-999")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")  # cleared, as FreeLLMAPI does
        self.assertEqual(env["ANTHROPIC_MODEL"], "test-model-id")
        # Declares the gateway model so Claude Code skips local validation
        # ("Model ... isn't recognized" warning) for custom ids.
        self.assertEqual(env["ANTHROPIC_CUSTOM_MODEL_OPTION"], "test-model-id")

    def test_codex_gets_openai_env_for_connection_model(self):
        conn = self._connection("openai")
        env = execution_router.apply_env({}, "codex", self._conn_model(conn))
        self.assertEqual(env["OPENAI_BASE_URL"], conn["base_url"])
        self.assertEqual(env["OPENAI_API_KEY"], "sk-router-test-key-999")

    def test_cli_native_model_env_untouched_without_freellmapi(self):
        env = execution_router.apply_env({"EXISTING": "1"}, "claude-code", "sonnet")
        self.assertEqual(env.get("EXISTING"), "1")
        self.assertNotIn("ANTHROPIC_BASE_URL", env)

    def test_freellmapi_behaviour_unchanged_for_its_model(self):
        freellmapi.set_config({"agent_id": "claude-code",
                               "base_url": "https://api.groq.com/openai/v1",
                               "model": "llama-3.3-70b-versatile",
                               "api_key": "gsk_freellm_key"})
        env = execution_router.apply_env({}, "claude-code", "llama-3.3-70b-versatile")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.groq.com/openai/v1")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "gsk_freellm_key")

    def test_incompatible_agent_route_fails_loudly(self):
        conn = self._connection("openai")
        with self.assertRaises(execution_router.RoutingError):
            execution_router.apply_env({}, "claude-code", self._conn_model(conn))

    def test_missing_connection_route_fails_loudly(self):
        with self.assertRaises(execution_router.RoutingError):
            execution_router.apply_env({}, "codex", "conn:conn-gone:model")

    def test_route_via_agent_registry_env(self):
        """The session launch path picks up connection env transparently."""
        conn = self._connection("anthropic")
        env = agent_registry.get_augmented_env("claude-code", self._conn_model(conn))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], conn["base_url"])
        # CLI-native models still pass through with just PATH augmentation.
        plain = agent_registry.get_augmented_env("claude-code", "sonnet")
        self.assertNotIn("ANTHROPIC_BASE_URL", plain)

    # -- native id translation -------------------------------------------------
    def test_native_model_id_translation(self):
        conn = self._connection("anthropic", base="http://localhost:9999")
        internal = self._conn_model(conn)
        self.assertEqual(execution_router.native_model_id(internal), "test-model-id")
        self.assertEqual(execution_router.native_model_id("claude-sonnet-4-5"),
                         "claude-sonnet-4-5")

    def test_validate_model_for_agent(self):
        conn = self._connection("anthropic")
        ok, diag = execution_router.validate_model_for_agent(
            "claude-code", self._conn_model(conn))
        self.assertTrue(ok)
        ok, diag = execution_router.validate_model_for_agent(
            "codex", self._conn_model(conn))
        self.assertFalse(ok)
        self.assertEqual(diag["status"], compatibility.INCOMPATIBLE)

    # -- in-process DeepSeek override -------------------------------------------
    def test_deepseek_override_routes_in_process_client(self):
        conn = self._connection("openai", base="http://127.0.0.1:4010")
        with execution_router.api_endpoint_override(self._conn_model(conn)) as resolved:
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved["base_url"], "http://127.0.0.1:4010/v1")
            self.assertEqual(resolved["model"], "test-model-id")
            self.assertEqual(deepseek_harness.base_url(), "http://127.0.0.1:4010/v1")
            key, source = deepseek_harness.api_key()
            self.assertEqual(key, "sk-router-test-key-999")
            self.assertEqual(source, "connection")
            self.assertTrue(deepseek_harness.has_model("test-model-id"))
            # The connection-scoped id is accepted for persistence.
            self.assertTrue(deepseek_harness.has_model(self._conn_model(conn)))
        # Outside the block the default behaviour is restored.
        self.assertEqual(deepseek_harness.base_url(), deepseek_harness.DEFAULT_BASE_URL)
        key, source = deepseek_harness.api_key()
        self.assertIsNone(key)
        self.assertEqual(source, "none")

    def test_deepseek_override_is_none_for_non_connection_models(self):
        with execution_router.api_endpoint_override("deepseek-chat") as resolved:
            self.assertIsNone(resolved)

    def test_deepseek_override_rejects_incompatible_protocols(self):
        conn = self._connection("anthropic")
        with execution_router.api_endpoint_override(self._conn_model(conn)) as resolved:
            self.assertIsNone(resolved)  # in-process client is OpenAI-dialect only

    def test_route_summary(self):
        conn = self._connection("anthropic")
        summary = execution_router.route_summary("claude-code", self._conn_model(conn))
        self.assertEqual(summary["execution"], "cli_native")
        self.assertEqual(summary["connection"]["id"], conn["id"])
        self.assertEqual(summary["compatibility"], "compatible")
        plain = execution_router.route_summary("claude-code", "sonnet")
        self.assertEqual(plain["execution"], "cli_native")

    def test_context_window_env_when_known(self):
        """A discovered model's context window reaches Claude Code as
        CLAUDE_CODE_MAX_CONTEXT_TOKENS (correct compaction + no warning)."""
        conn = self._connection("anthropic")
        model_catalog.register_model(conn["id"], "windowed-model",
                                     metadata={"context_length": 1048576})
        env = execution_router.apply_env({}, "claude-code",
                                         self._conn_model(conn, "windowed-model"))
        self.assertEqual(env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"), "1048576")
        # Unknown window: the var is simply omitted (never guessed).
        env2 = execution_router.apply_env({}, "claude-code", self._conn_model(conn))
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", env2)

    def test_derived_freellmapi_connection_full_path(self):
        """Regression (2026-11-14): the user hit 'Connection freellmapi no
        longer exists' because the derived connection id contains a colon
        and the old parser split on the first colon. The whole path —
        parse, evaluate, switch validation, env injection — must resolve."""
        freellmapi.set_config({"agent_id": "claude-code",
                               "base_url": "https://integrate.api.nvidia.com/v1",
                               "model": "z-ai/glm-5.3-flash",
                               "api_key": "nvapi_fake_key_9876"})
        model_id = model_catalog.internal_id("freellmapi:claude-code",
                                             "z-ai/glm-5.3-flash")
        # The exact model-switch validation used by the session route.
        ok, diag = execution_router.validate_model_for_agent("claude-code", model_id)
        self.assertTrue(ok, diag.get("reason"))
        self.assertEqual(diag["status"], compatibility.COMPATIBLE)
        # Env injection matches the legacy FreeLLMAPI behaviour.
        env = execution_router.apply_env({}, "claude-code", model_id)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(env["ANTHROPIC_MODEL"], "z-ai/glm-5.3-flash")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "nvapi_fake_key_9876")
        # And the runtime gets the plain native id, never the conn: id.
        self.assertEqual(execution_router.native_model_id(model_id), "z-ai/glm-5.3-flash")


if __name__ == "__main__":
    unittest.main()
