"""Tests for the Free/Paid routing policy (backend/routing.py).

Run:  python3 -m unittest tests.test_routing

Covers the three modes, per-agent overrides, free-first preference,
explicit paid-fallback authorization (credits are never consumed
silently) and honest no-route outcomes. Stores live in temp dirs.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import compatibility  # noqa: E402
from backend import connections  # noqa: E402
from backend import model_catalog  # noqa: E402
from backend import routing  # noqa: E402


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-routing-test-")
        self._old = (routing.ROUTING_FILE, connections.CONNECTIONS_FILE,
                     model_catalog.CATALOG_FILE)
        routing.ROUTING_FILE = os.path.join(self.tmp, "routing.json")
        connections.CONNECTIONS_FILE = os.path.join(self.tmp, "connections.json")
        model_catalog.CATALOG_FILE = os.path.join(self.tmp, "model_catalog.json")
        self._env = os.environ.pop("OPENAI_API_KEY", None)

    def tearDown(self):
        (routing.ROUTING_FILE, connections.CONNECTIONS_FILE,
         model_catalog.CATALOG_FILE) = self._old
        if self._env is not None:
            os.environ["OPENAI_API_KEY"] = self._env
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    def _gateway(self, pricing="free", key="sk-test-key-123456"):
        return connections.create_connection({
            "type": "gateway", "name": "Gw " + pricing,
            "base_url": "http://localhost:9999/v1",
            "provider_id": "custom_openai", "pricing": pricing, "api_key": key,
        })

    def test_default_mode_is_free_first_global(self):
        mode, source = routing.mode_for("codex")
        self.assertEqual(mode, routing.MODE_FREE_FIRST)
        self.assertEqual(source, "global")

    def test_agent_override_wins(self):
        routing.set_agent_override("codex", routing.MODE_PAID_FIRST)
        mode, source = routing.mode_for("codex")
        self.assertEqual(mode, routing.MODE_PAID_FIRST)
        self.assertEqual(source, "agent:codex")
        mode, _ = routing.mode_for("claude-code")
        self.assertEqual(mode, routing.MODE_FREE_FIRST)  # others unaffected
        routing.set_agent_override("codex", None)
        mode, source = routing.mode_for("codex")
        self.assertEqual(mode, routing.MODE_FREE_FIRST)

    def test_task_override_wins_over_agent(self):
        routing.set_agent_override("codex", routing.MODE_PAID_FIRST)
        routing.set_task_override("script-writing", routing.MODE_FREE_FIRST)
        mode, source = routing.mode_for("codex", task="script-writing")
        self.assertEqual(mode, routing.MODE_FREE_FIRST)
        self.assertEqual(source, "task:script-writing")

    def test_invalid_mode_rejected(self):
        with self.assertRaises(routing.RoutingError):
            routing.set_global("cheapest")
        with self.assertRaises(routing.RoutingError):
            routing.set_agent_override("codex", "sometimes")

    def test_free_first_resolves_free_connection(self):
        self._gateway("free")
        self._gateway("paid")
        decision = routing.resolve_route("codex")
        self.assertEqual(decision["status"], "resolved")
        self.assertEqual(decision["connection"]["pricing"], "free")
        # Paid candidate still visible in the ordered plan.
        self.assertEqual([c["pricing"] for c in decision["candidates"]], ["free", "paid"])

    def test_paid_only_requires_authorization(self):
        self._gateway("paid")
        decision = routing.resolve_route("codex")
        self.assertEqual(decision["status"], "authorization_required")
        self.assertIsNotNone(decision["connection"])

    def test_paid_fallback_flag_allows_paid(self):
        self._gateway("paid")
        routing.set_global(routing.MODE_FREE_FIRST, allow_paid_fallback=True)
        decision = routing.resolve_route("codex")
        self.assertEqual(decision["status"], "resolved")

    def test_paid_first_prefers_paid(self):
        self._gateway("free")
        self._gateway("paid")
        routing.set_global(routing.MODE_PAID_FIRST)
        decision = routing.resolve_route("codex")
        self.assertEqual(decision["status"], "resolved")
        self.assertEqual(decision["connection"]["pricing"], "paid")

    def test_authorization_grant_unblocks_paid_fallback(self):
        conn = self._gateway("paid")
        decision = routing.resolve_route("codex")
        self.assertEqual(decision["status"], "authorization_required")
        routing.authorize_paid_fallback(conn["id"], reason="user approved", agent_id="codex")
        decision = routing.resolve_route("codex")
        self.assertEqual(decision["status"], "resolved")
        self.assertEqual(decision["reason"], "Using recently authorized paid fallback.")

    def test_manual_mode_still_validates_explicit_choice(self):
        conn = self._gateway("paid")
        routing.set_global(routing.MODE_MANUAL)
        decision = routing.resolve_route("codex", connection_id=conn["id"])
        self.assertEqual(decision["status"], "resolved")
        # An incompatible explicit choice is rejected regardless of mode.
        decision = routing.resolve_route("antigravity", connection_id=conn["id"])
        self.assertEqual(decision["status"], "no_route")

    def test_no_connections_is_an_honest_no_route(self):
        decision = routing.resolve_route("codex")
        self.assertEqual(decision["status"], "no_route")
        self.assertEqual(decision["candidates"], [])

    def test_explicit_model_resolution(self):
        conn = self._gateway("free")
        model_id = model_catalog.internal_id(conn["id"], "gpt-4o-mini")
        model_catalog.register_model(conn["id"], "gpt-4o-mini")
        decision = routing.resolve_route("codex", model_id=model_id)
        self.assertEqual(decision["status"], "resolved")
        self.assertEqual(decision["model"]["native_id"], "gpt-4o-mini")

    def test_propose_fallback_excludes_failed_connection(self):
        free = self._gateway("free")
        self._gateway("paid")
        plan = routing.propose_fallback("codex", failed_connection_id=free["id"])
        self.assertEqual([c["pricing"] for c in plan["candidates"]], ["paid"])
        self.assertTrue(plan["authorization_required"])
        self.assertFalse(plan["paid_fallback_allowed"])

    def test_unknown_agent_raises(self):
        with self.assertRaises(routing.RoutingError):
            routing.resolve_route("")


if __name__ == "__main__":
    unittest.main()
