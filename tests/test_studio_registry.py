"""Unit tests for the Studio provider registry and generation catalog.

Run:  python3 -m unittest tests.test_studio_registry
"""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")
os.environ["GEMINI_API_KEY"] = ""
os.environ["HIGGSFIELD_API_KEY"] = ""
os.environ["HIGGSFIELD_SECRET"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_registry as registry  # noqa: E402
from backend import studio_secrets as secrets  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_registry_lists_every_provider(self):
        data = registry.registry()
        ids = [p["id"] for p in data["providers"]]
        self.assertEqual(ids, ["auto", "pollinations", "google", "higgsfield",
                               "fal", "runway", "luma", "minimax", "replicate"])

    def test_free_providers_are_always_connected(self):
        data = registry.registry()
        by_id = {p["id"]: p for p in data["providers"]}
        self.assertTrue(by_id["auto"]["connected"])
        self.assertTrue(by_id["pollinations"]["connected"])
        self.assertFalse(by_id["pollinations"]["fields"])

    def test_credential_fields_never_leak_values(self):
        with mock.patch.object(secrets, "status", return_value={"configured": True}):
            data = registry.registry()
        by_id = {p["id"]: p for p in data["providers"]}
        self.assertEqual([f["id"] for f in by_id["higgsfield"]["fields"]],
                         ["api_key", "secret"])
        for provider in data["providers"]:
            for field in provider["fields"]:
                self.assertNotIn("value", field)

    def test_connected_flags_follow_secrets(self):
        with mock.patch.object(registry.free, "google_api_key", return_value="key"), \
             mock.patch.object(secrets, "status",
                               side_effect=lambda pid: {"configured": pid == "fal"}):
            data = registry.registry()
        by_id = {p["id"]: p for p in data["providers"]}
        self.assertTrue(by_id["google"]["connected"])
        self.assertTrue(by_id["fal"]["connected"])
        self.assertFalse(by_id["higgsfield"]["connected"])

    def test_adapter_ready_only_for_wired_providers(self):
        data = registry.registry()
        by_id = {p["id"]: p for p in data["providers"]}
        self.assertTrue(by_id["google"]["adapter"])
        self.assertTrue(by_id["higgsfield"]["adapter"])
        self.assertFalse(by_id["runway"]["adapter"])
        self.assertFalse(by_id["replicate"]["selectable"])


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = registry.generation_catalog()["providers"]

    def test_every_registry_provider_has_a_catalog_entry(self):
        data = registry.registry()
        for provider in data["providers"]:
            self.assertIn(provider["id"], self.catalog)

    def test_free_engines_are_classified(self):
        auto = self.catalog["auto"]
        self.assertEqual(auto["image"][0]["id"], "auto")
        self.assertTrue(all("needs" in engine for engine in auto["video"]))
        pollinations = self.catalog["pollinations"]
        self.assertEqual([m["id"] for m in pollinations["image"]],
                         ["pollinations-flux", "pollinations-turbo"])
        self.assertEqual(pollinations["video"], [])

    def test_google_catalog(self):
        google = self.catalog["google"]
        self.assertEqual([m["id"] for m in google["image"]],
                         ["google-gemini-image"])
        self.assertTrue(any("veo" in m["id"] for m in google["video"]))

    def test_higgsfield_catalog_is_classified_by_mode(self):
        hf = self.catalog["higgsfield"]
        self.assertTrue(hf["image"])
        self.assertTrue(hf["video"])
        modes = {m["mode"] for m in hf["image"]}
        self.assertTrue(modes <= {"text_to_image", "image_edit"})
        for model in hf["image"] + hf["video"]:
            self.assertIn(model["needs"], ("prompt", "image", "reference_video"))

    def test_reference_models_need_a_reference_video(self):
        hf = self.catalog["higgsfield"]
        r2v = [m for m in hf["video"] if m["mode"] == "reference_to_video"]
        self.assertTrue(r2v)
        self.assertTrue(all(m["needs"] == "reference_video" for m in r2v))
        i2v = [m for m in hf["video"] if m["mode"] == "image_to_video"]
        self.assertTrue(i2v)
        self.assertTrue(all(m["needs"] == "image" for m in i2v))

    def test_adapters_without_catalogs_stay_empty(self):
        for provider in ("fal", "runway", "luma", "minimax", "replicate"):
            self.assertEqual(self.catalog[provider]["image"], [])
            self.assertEqual(self.catalog[provider]["video"], [])


class CredentialsTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for target in ("STUDIO_DIR", "CREDENTIALS_FILE"):
            patch = mock.patch.object(
                secrets, target,
                self.tmp.name if target == "STUDIO_DIR"
                else os.path.join(self.tmp.name, "credentials.json"),
            )
            patch.start()
            self.addCleanup(patch.stop)
        registry._UPLOAD_HASH_CACHE.clear() if hasattr(registry, "_UPLOAD_HASH_CACHE") else None

    def test_free_providers_need_no_credentials(self):
        result = registry.save_credentials("pollinations", {"api_key": "x"})
        self.assertFalse(result["saved"])

    def test_save_and_delete_google_credentials(self):
        result = registry.save_credentials("google", {"api_key": " AIza-test "})
        self.assertTrue(result["saved"])
        self.assertTrue(result["status"]["configured"])
        self.assertTrue(registry._connected("google"))
        registry.delete_credentials("google")
        self.assertFalse(registry._connected("google"))

    def test_save_rejects_empty_values(self):
        with self.assertRaises(ValueError):
            registry.save_credentials("fal", {"api_key": "   "})

    def test_save_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            registry.save_credentials("nope", {"api_key": "x"})

    def test_test_provider_validates_connection(self):
        with mock.patch.object(registry.free, "google_api_key", return_value=""):
            with self.assertRaises(Exception):
                registry.test_provider("google")
        with mock.patch.object(registry.free, "test_google_key",
                               return_value={"ok": True, "message": "accepted"}):
            self.assertEqual(registry.test_provider("google")["ok"], True)
        self.assertEqual(registry.test_provider("pollinations")["ok"], True)

    def test_test_provider_honest_about_missing_adapters(self):
        with mock.patch.object(registry, "_connected", return_value=True):
            result = registry.test_provider("runway")
        self.assertTrue(result["ok"])
        self.assertIn("not wired", result["message"])


if __name__ == "__main__":
    unittest.main()
