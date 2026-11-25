"""Unit tests for the shared integration registry (stdlib unittest only).

Run:  python3 -m unittest tests.test_integrations_registry -v
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.integrations import executions, registry, store


class RegistryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-intx-reg-")
        self._orig = (registry.OVERRIDES_FILE, store.INTEGRATIONS_DIR,
                      executions.EXECUTIONS_FILE,
                      registry._composio_keys, registry._composio_connections)
        registry.OVERRIDES_FILE = os.path.join(self.tmp, "registry.json")
        store.INTEGRATIONS_DIR = self.tmp
        executions.EXECUTIONS_FILE = os.path.join(self.tmp, "executions.json")
        self._shopee_env = {k: os.environ.get(k) for k in
                            ("SHOPEE_APP_ID", "SHOPEE_AFFILIATE_SECRET")}
        for key in self._shopee_env:
            os.environ.pop(key, None)

    def tearDown(self):
        (registry.OVERRIDES_FILE, store.INTEGRATIONS_DIR,
         executions.EXECUTIONS_FILE,
         registry._composio_keys, registry._composio_connections) = self._orig
        for key, value in self._shopee_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # -- providers ---------------------------------------------------------
    def test_both_providers_registered(self):
        view = registry.registry()
        ids = {p["provider"] for p in view["providers"]}
        self.assertEqual(ids, {"shopee", "composio"})

    def test_shopee_reports_not_configured_without_credentials(self):
        view = registry.provider_view("shopee")
        self.assertEqual(view["status"], "not_configured")
        self.assertTrue(view["warning"])
        self.assertFalse(view["detail"]["credentials"]["configured"])

    def test_shopee_configured_from_env(self):
        os.environ["SHOPEE_APP_ID"] = "123456"
        os.environ["SHOPEE_AFFILIATE_SECRET"] = "demo"
        view = registry.provider_view("shopee")
        self.assertEqual(view["status"], "connected")
        self.assertEqual(view["detail"]["credentials"]["source"], "env")

    def test_shopee_configured_from_store(self):
        cred_dir = os.path.join(self.tmp, "shopee")
        os.makedirs(cred_dir, mode=0o700, exist_ok=True)
        store.write_json(os.path.join(cred_dir, "credentials.json"),
                         {"app_id": "123456", "secret": "demo"})
        view = registry.provider_view("shopee")
        self.assertEqual(view["status"], "connected")
        self.assertEqual(view["detail"]["credentials"]["source"], "store")

    def test_composio_not_configured_without_key(self):
        registry._composio_keys = lambda: {"api_key_set": False, "consumer_key_set": False}
        view = registry.provider_view("composio")
        self.assertEqual(view["status"], "not_configured")

    def test_composio_youtube_connection_drives_domain_status(self):
        registry._composio_keys = lambda: {"api_key_set": True, "consumer_key_set": True}
        registry._composio_connections = lambda: {
            "connections": [
                {"id": "ca_1", "toolkit": "youtube", "status": "ACTIVE",
                 "created_at": "2026-11-09"},
                {"id": "ca_2", "toolkit": "canva", "status": "EXPIRED",
                 "created_at": "2026-11-09"},
            ],
            "warning": "",
        }
        view = registry.provider_view("composio")
        self.assertEqual(view["status"], "active")
        self.assertEqual(view["detail"]["youtube"]["toolkit"], "youtube")

    def test_composio_expired_connection_is_reported_honestly(self):
        registry._composio_keys = lambda: {"api_key_set": True, "consumer_key_set": True}
        registry._composio_connections = lambda: {
            "connections": [{"id": "ca_1", "toolkit": "youtube", "status": "EXPIRED",
                             "created_at": ""}],
            "warning": "",
        }
        view = registry.provider_view("composio")
        self.assertEqual(view["status"], "expired")

    def test_composio_unreachable_surfaces_warning(self):
        registry._composio_keys = lambda: {"api_key_set": True, "consumer_key_set": True}
        def boom():
            raise RuntimeError("network down")
        registry._composio_connections = boom
        view = registry.provider_view("composio")
        self.assertEqual(view["status"], "unreachable")
        self.assertIn("network down", view["warning"])

    # -- capabilities ------------------------------------------------------
    def test_capabilities_declare_permissions_and_honest_readiness(self):
        # Readiness is derived from the live transport, never hardcoded. Pin
        # the transport here so the contract test stays offline-stable.
        original = registry._youtube_execution_state
        registry._youtube_execution_state = lambda: {
            "execution_available": False, "blocked_reason": "session not linked"}
        try:
            view = registry.registry()
        finally:
            registry._youtube_execution_state = original
        caps = {c["id"]: c for c in view["capabilities"]}
        self.assertIn("composio.youtube.read", caps)
        self.assertIn("composio.youtube.write", caps)
        self.assertIn("shopee.catalog", caps)
        self.assertFalse(caps["composio.youtube.read"]["ready"])
        self.assertFalse(caps["shopee.catalog"]["ready"])         # no credentials
        self.assertFalse(caps["composio.youtube.write"]["approval_required"] is False)
        self.assertTrue(caps["composio.youtube.write"]["approval_required"])
        self.assertFalse(caps["composio.youtube.read"]["approval_required"])

    def test_youtube_read_becomes_ready_when_transport_is_active(self):
        original = registry._youtube_execution_state
        registry._youtube_execution_state = lambda: {
            "execution_available": True, "blocked_reason": ""}
        try:
            view = registry.registry()
        finally:
            registry._youtube_execution_state = original
        caps = {c["id"]: c for c in view["capabilities"]}
        self.assertTrue(caps["composio.youtube.read"]["ready"])
        self.assertTrue(caps["composio.youtube.write"]["ready"])

    # -- overrides and validation -----------------------------------------
    def test_disable_enable_persists(self):
        registry.set_enabled("shopee", False)
        view = registry.provider_view("shopee")
        self.assertFalse(view["enabled"])
        self.assertEqual(view["status"], "disabled")
        registry.set_enabled("shopee", True)
        self.assertEqual(registry.provider_view("shopee")["status"], "not_configured")
        self.assertEqual(oct(os.stat(registry.OVERRIDES_FILE).st_mode & 0o777), "0o600")

    def test_validate_records_result_and_audit(self):
        registry._composio_keys = lambda: {"api_key_set": True, "consumer_key_set": True}
        registry._composio_connections = lambda: {
            "connections": [{"id": "ca_1", "toolkit": "youtube", "status": "ACTIVE",
                             "created_at": ""}],
            "warning": "",
        }
        result = registry.validate("composio")
        self.assertTrue(result["validation"]["ok"])
        view = registry.provider_view("composio")
        self.assertIsNotNone(view["last_validated_iso"])
        self.assertEqual(view["last_validation"]["status"], "active")
        audit = executions.list(provider="composio")
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["action"], "validate")

    def test_validate_shopee_without_credentials_fails_honestly(self):
        result = registry.validate("shopee")
        self.assertFalse(result["validation"]["ok"])
        self.assertEqual(result["validation"]["status"], "not_configured")

    def test_unknown_provider_raises(self):
        with self.assertRaises(store.IntegrationError):
            registry.provider_view("nope")
        with self.assertRaises(store.IntegrationError):
            registry.set_enabled("nope", True)

    # -- api dispatcher ----------------------------------------------------
    def test_api_overview_and_detail(self):
        from backend.integrations import api
        status, payload = api.handle("GET", "/api/integrations")
        self.assertEqual(status, 200)
        self.assertIn("providers", payload)
        status, payload = api.handle("GET", "/api/integrations/providers/shopee")
        self.assertEqual(status, 200)
        self.assertEqual(payload["provider"], "shopee")
        status, _ = api.handle("GET", "/api/integrations/providers/nope")
        self.assertEqual(status, 404)
        status, _ = api.handle("GET", "/api/integrations/unknown")
        self.assertEqual(status, 404)
        status, _ = api.handle("DELETE", "/api/integrations")
        self.assertEqual(status, 405)

    def test_api_enable_disable(self):
        from backend.integrations import api
        status, payload = api.handle("POST", "/api/integrations/providers/shopee/disable")
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])
        status, payload = api.handle("POST", "/api/integrations/providers/shopee/enable")
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])


if __name__ == "__main__":
    unittest.main()
