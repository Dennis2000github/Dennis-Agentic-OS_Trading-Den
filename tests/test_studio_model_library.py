"""Unit tests for the Studio Fashion model library.

Run:  python3 -m unittest tests.test_studio_model_library
"""

import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_model_library as library  # noqa: E402


class StudioModelLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [
            mock.patch.object(library, "STUDIO_DIR", self.tmp.name),
            mock.patch.object(library, "LIBRARY_FILE", os.path.join(self.tmp.name, "model-library.json")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_add_and_list_models(self):
        added = library.add_model("https://cdn.example/model.jpg", "My model", "generated", "a prompt")
        self.assertTrue(added["id"].startswith("um_"))
        self.assertEqual(added["source"], "generated")
        self.assertEqual(added["prompt"], "a prompt")
        state = library.list_library()
        self.assertEqual([m["url"] for m in state["custom"]], ["https://cdn.example/model.jpg"])

    def test_add_rejects_non_http_url(self):
        with self.assertRaises(library.LibraryError):
            library.add_model("javascript:alert(1)")

    def test_add_dedupes_same_url(self):
        library.add_model("https://cdn.example/model.jpg", "One", "upload")
        again = library.add_model("https://cdn.example/model.jpg", "Two", "upload")
        state = library.list_library()
        self.assertEqual(len(state["custom"]), 1)
        self.assertEqual(again["name"], "One")

    def test_delete_model(self):
        added = library.add_model("https://cdn.example/model.jpg", "My model", "upload")
        result = library.delete_model(added["id"])
        self.assertEqual(result["deleted"], added["id"])
        self.assertEqual(library.list_library()["custom"], [])
        with self.assertRaises(library.LibraryError):
            library.delete_model(added["id"])

    def test_hidden_presets_toggle(self):
        library.set_preset_hidden("MD001", True)
        library.set_preset_hidden("MD002", True)
        self.assertEqual(library.list_library()["hidden_presets"], ["MD001", "MD002"])
        library.set_preset_hidden("MD001", False)
        self.assertEqual(library.list_library()["hidden_presets"], ["MD002"])

    def test_hidden_presets_requires_id(self):
        with self.assertRaises(library.LibraryError):
            library.set_preset_hidden("", True)

    def test_file_permissions(self):
        library.add_model("https://cdn.example/model.jpg", "My model", "upload")
        mode = stat.S_IMODE(os.stat(library.LIBRARY_FILE).st_mode)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
