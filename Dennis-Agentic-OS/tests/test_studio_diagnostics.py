"""Tests for the Studio provider diagnostics service.

Every provider is checked through staged, non-billable verifications. These
tests mock all network access: no real provider is contacted.

Run:  python3 -m unittest tests.test_studio_diagnostics
"""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["HIGGSFIELD_API_KEY"] = ""
os.environ["HIGGSFIELD_SECRET"] = ""
os.environ["FAL_KEY"] = ""
os.environ["RUNWAYML_API_SECRET"] = ""
os.environ["LUMA_API_KEY"] = ""
os.environ["LUMA_AGENTS_API_KEY"] = ""
os.environ["MINIMAX_API_KEY"] = ""
os.environ["REPLICATE_API_TOKEN"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_diagnostics as diagnostics  # noqa: E402
from backend import studio_free as free  # noqa: E402
from backend import studio_providers as providers  # noqa: E402
from backend import studio_registry as registry  # noqa: E402
from backend import studio_secrets as secrets  # noqa: E402


def _ok_client_test():
    return {"ok": True, "message": "accepted"}


class BaseDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        patch = mock.patch.object(
            secrets, "CREDENTIALS_FILE",
            os.path.join(self.tmp.name, "credentials.json"),
        )
        patch.start()
        self.addCleanup(patch.stop)
        diagnostics._RESULTS.clear()


class StateContractTests(BaseDiagnosticsTests):
    def test_every_documented_state_exists(self):
        for state in ("NOT_CONFIGURED", "CONFIGURED", "CHECKING", "CONNECTED",
                      "AUTHENTICATION_FAILED", "NETWORK_ERROR", "TIMEOUT",
                      "RATE_LIMITED", "INSUFFICIENT_CREDITS",
                      "PROVIDER_UNAVAILABLE", "MODEL_UNAVAILABLE",
                      "PARTIALLY_OPERATIONAL", "OPERATIONAL", "UNKNOWN_ERROR"):
            self.assertTrue(hasattr(diagnostics, state), state)

    def test_result_contract_fields(self):
        result = diagnostics.check("pollinations")
        for field in ("provider", "label", "status", "stage", "message",
                      "error_code", "http_status", "retryable", "checked_at",
                      "response_time_ms", "recommended_action", "verified",
                      "checks", "adapter_wired"):
            self.assertIn(field, result)
        self.assertEqual(result["provider"], "pollinations")
        self.assertTrue(result["checked_at"].endswith("Z"))

    def test_check_records_have_stages(self):
        result = diagnostics.check("pollinations")
        stages = [c["stage"] for c in result["checks"]]
        self.assertEqual(stages, sorted(stages, key=diagnostics.STAGES.index))


class ConfiguredVsVerifiedTests(BaseDiagnosticsTests):
    def test_missing_credentials_are_not_configured(self):
        result = diagnostics.check("minimax")
        self.assertEqual(result["status"], "NOT_CONFIGURED")
        self.assertFalse(result["verified"]["authentication"])

    def test_presence_only_provider_never_claims_verified_auth(self):
        secrets.set_values("replicate", {"api_key": "r8_xyz"})
        result = diagnostics.check("replicate")
        # Credentials exist -> CONFIGURED at most; never CONNECTED/OPERATIONAL
        # because no non-billable verification endpoint is integrated.
        self.assertEqual(result["status"], "PARTIALLY_OPERATIONAL")
        self.assertFalse(result["verified"]["authentication"])
        self.assertFalse(result["verified"]["generation"])
        auth = [c for c in result["checks"] if c["stage"] == "authentication"][0]
        self.assertIn("Not verified", auth["message"])

    def test_configured_is_not_operational(self):
        secrets.set_values("minimax", {"api_key": "mm_key"})
        result = diagnostics.check("minimax")
        self.assertNotEqual(result["status"], "OPERATIONAL")
        self.assertNotEqual(result["status"], "CONNECTED")


class HiggsfieldDiagnosticsTests(BaseDiagnosticsTests):
    def _provider_error(self, code, message, status=401):
        return providers.ProviderError(code, message, status=status)

    def test_auth_failure_is_reported_at_the_authentication_stage(self):
        secrets.set_values("higgsfield", {"api_key": "k", "secret": "s"})
        with mock.patch.object(providers, "REGISTRY") as fake:
            instance = fake.get.return_value
            instance.descriptor.return_value = {
                "configured": True, "credentials": {"source": "stored"}}
            instance.test.side_effect = self._provider_error(
                "authentication_failed", "Invalid credentials", 401)
            result = diagnostics.check("higgsfield")
        self.assertEqual(result["status"], "AUTHENTICATION_FAILED")
        self.assertEqual(result["stage"], "authentication")
        self.assertEqual(result["error_code"], "authentication_failed")
        auth = [c for c in result["checks"] if c["stage"] == "authentication"][0]
        self.assertFalse(auth["ok"])
        self.assertIn("re-save", result["recommended_action"].lower())

    def test_successful_presign_verifies_auth_and_upload(self):
        secrets.set_values("higgsfield", {"api_key": "k", "secret": "s"})
        with mock.patch.object(providers, "REGISTRY") as fake:
            instance = fake.get.return_value
            instance.descriptor.return_value = {
                "configured": True, "credentials": {"source": "stored"}}
            instance.test.return_value = {"ok": True, "message": "Upload URL issued."}
            result = diagnostics.check("higgsfield")
        self.assertEqual(result["status"], "OPERATIONAL")
        self.assertTrue(result["verified"]["authentication"])
        self.assertTrue(result["verified"]["upload"])
        # Generation is billable: it must never be claimed by a check.
        self.assertFalse(result["verified"]["generation"])
        generation = [c for c in result["checks"] if c["stage"] == "generation"][0]
        self.assertIn("billable", generation["message"].lower())

    def test_missing_credentials_stage(self):
        result = diagnostics.check("higgsfield")
        self.assertEqual(result["status"], "NOT_CONFIGURED")
        self.assertEqual(result["stage"], "configuration")


class GoogleDiagnosticsTests(BaseDiagnosticsTests):
    def test_invalid_key_maps_to_authentication_failed(self):
        secrets.set_values("google_veo", {"api_key": "bad"})
        with mock.patch.object(free, "test_google_key") as test:
            test.side_effect = free.FreeError(
                "invalid_key", "Google AI rejected the key (API key not valid.)", 502)
            result = diagnostics.check("google")
        self.assertEqual(result["status"], "AUTHENTICATION_FAILED")
        self.assertEqual(result["stage"], "authentication")

    def test_valid_key_verifies_model_catalog(self):
        secrets.set_values("google_veo", {"api_key": "good"})
        with mock.patch.object(free, "test_google_key") as test:
            test.return_value = {"ok": True, "message": "accepted"}
            result = diagnostics.check("google")
        self.assertEqual(result["status"], "OPERATIONAL")
        self.assertTrue(result["verified"]["model_catalog"])
        self.assertFalse(result["verified"]["generation"])

    def test_network_error_is_retryable(self):
        secrets.set_values("google_veo", {"api_key": "good"})
        with mock.patch.object(free, "test_google_key") as test:
            test.side_effect = free.FreeError("network_error", "Could not reach Google AI.", 502)
            result = diagnostics.check("google")
        self.assertEqual(result["status"], "NETWORK_ERROR")
        self.assertTrue(result["retryable"])


class ClientProviderDiagnosticsTests(BaseDiagnosticsTests):
    def test_fal_auth_verified_but_generation_not_wired(self):
        secrets.set_values("fal", {"api_key": "fk"})
        with mock.patch("backend.studio_fal.FalClient.test_credentials",
                        return_value=_ok_client_test()):
            result = diagnostics.check("fal")
        self.assertEqual(result["status"], "PARTIALLY_OPERATIONAL")
        self.assertTrue(result["verified"]["authentication"])
        self.assertFalse(result["adapter_wired"])

    def test_fal_auth_failure_is_honest(self):
        secrets.set_values("fal", {"api_key": "fk"})
        from backend import studio_http
        with mock.patch("backend.studio_fal.FalClient.test_credentials") as test:
            test.side_effect = studio_http.ProviderRequestError(401, {
                "code": "authentication_failed",
                "message": "Invalid key",
                "retryable": False,
            })
            result = diagnostics.check("fal")
        self.assertEqual(result["status"], "AUTHENTICATION_FAILED")
        self.assertFalse(result["verified"]["authentication"])

    def test_runway_and_luma_follow_the_same_contract(self):
        secrets.set_values("runway", {"api_key": "rw"})
        secrets.set_values("luma", {"api_key": "lm"})
        with mock.patch("backend.studio_runway.RunwayClient.test_credentials",
                        return_value=_ok_client_test()), \
             mock.patch("backend.studio_luma.LumaClient.test_credentials",
                        return_value=_ok_client_test()):
            runway = diagnostics.check("runway")
            luma = diagnostics.check("luma")
        self.assertEqual(runway["status"], "PARTIALLY_OPERATIONAL")
        self.assertEqual(luma["status"], "PARTIALLY_OPERATIONAL")


class FreeEngineDiagnosticsTests(BaseDiagnosticsTests):
    def test_pollinations_reachability_timeout(self):
        with mock.patch.object(diagnostics, "_http_probe") as probe:
            probe.return_value = (diagnostics.TIMEOUT, "The host did not respond in time.", 5000, None)
            result = diagnostics.check("pollinations")
        self.assertEqual(result["status"], "TIMEOUT")
        reach = [c for c in result["checks"] if c["stage"] == "api_reachability"][0]
        self.assertTrue(reach["retryable"])

    def test_pollinations_reachability_network_error(self):
        with mock.patch.object(diagnostics, "_http_probe") as probe:
            probe.return_value = (diagnostics.NETWORK_ERROR, "Cannot reach the host: name resolution failed", 0, None)
            result = diagnostics.check("pollinations")
        self.assertEqual(result["status"], "NETWORK_ERROR")

    def test_auto_routes_to_google_when_key_present(self):
        secrets.set_values("google_veo", {"api_key": "good"})
        with mock.patch.object(free, "test_google_key") as test:
            test.return_value = {"ok": True, "message": "accepted"}
            result = diagnostics.check("auto")
        self.assertTrue(any(c["stage"] == "authentication" for c in result["checks"]))


class IsolationAndAggregateTests(BaseDiagnosticsTests):
    def test_check_all_never_raises_and_covers_every_provider(self):
        with mock.patch.object(diagnostics, "_verify_higgsfield",
                               side_effect=RuntimeError("boom")):
            data = diagnostics.check_all()
        ids = [d["provider"] for d in data["diagnostics"]]
        self.assertEqual(ids, ["auto", "pollinations", "google", "higgsfield",
                               "fal", "runway", "luma", "minimax", "replicate"])
        # The crashing provider is isolated, others still report real results.
        higgsfield = [d for d in data["diagnostics"] if d["provider"] == "higgsfield"][0]
        self.assertEqual(higgsfield["status"], "UNKNOWN_ERROR")
        pollinations = [d for d in data["diagnostics"] if d["provider"] == "pollinations"][0]
        self.assertNotEqual(pollinations["status"], "UNKNOWN_ERROR")

    def test_summary_is_static_and_has_no_network(self):
        secrets.set_values("runway", {"api_key": "rw"})
        data = diagnostics.summary()
        by_id = {d["provider"]: d for d in data["diagnostics"]}
        self.assertTrue(by_id["runway"]["configured"])
        self.assertFalse(by_id["minimax"]["configured"])
        self.assertTrue(by_id["higgsfield"]["adapter_wired"])
        self.assertFalse(by_id["runway"]["adapter_wired"])

    def test_last_check_is_cached_in_memory(self):
        diagnostics.check("pollinations")
        data = diagnostics.summary()
        entry = [d for d in data["diagnostics"] if d["provider"] == "pollinations"][0]
        self.assertIsNotNone(entry["last_check"])
        self.assertEqual(entry["last_status"], "OPERATIONAL")


class RegistryTestProviderHonestyTests(BaseDiagnosticsTests):
    def test_unwired_provider_with_client_verifies_auth(self):
        secrets.set_values("runway", {"api_key": "rw"})
        with mock.patch("backend.studio_runway.RunwayClient.test_credentials",
                        return_value=_ok_client_test()):
            result = registry.test_provider("runway")
        self.assertTrue(result["ok"])
        self.assertEqual(result["verified"], "authentication")

    def test_unwired_provider_with_client_reports_auth_failure(self):
        from backend import studio_http
        secrets.set_values("luma", {"api_key": "lm"})
        with mock.patch("backend.studio_luma.LumaClient.test_credentials") as test:
            test.side_effect = studio_http.ProviderRequestError(401, {
                "code": "authentication_failed",
                "message": "Invalid credentials",
                "retryable": False,
            })
            result = registry.test_provider("luma")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["verified"])

    def test_unwired_provider_without_client_is_not_claimed_verified(self):
        with mock.patch.object(registry, "_connected", return_value=True):
            result = registry.test_provider("minimax")
        self.assertTrue(result["ok"])  # credentials stored — that much is true
        self.assertIsNone(result["verified"])
        self.assertIn("not wired", result["message"])


if __name__ == "__main__":
    unittest.main()
