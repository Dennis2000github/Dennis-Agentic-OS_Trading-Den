"""Tests for the pinned Studio catalog loader.

The catalog is committed, so these tests are offline and deterministic.

Run:  python3 -m unittest tests.test_studio_catalog
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_catalog as cat
from backend import studio_contracts as contracts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        cat.reset_cache()

    def test_info_matches_snapshot(self):
        info = cat.info()
        self.assertEqual(info["schema_version"], 1)
        self.assertEqual(info["provider"], "higgsfield")
        self.assertEqual(info["api_base"], "https://api.higgsfield.ai")
        self.assertEqual(info["model_count"], 84)
        self.assertRegex(info["sha256"], r"^[0-9a-f]{64}$")

    def test_models_are_unique_and_complete(self):
        models = cat.load()["models"]
        ids = [m["id"] for m in models]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            {"image": sum(1 for m in models if m["kind"] == "image"),
             "video": sum(1 for m in models if m["kind"] == "video")},
            {"image": 17, "video": 67},
        )
        for model in models:
            self.assertTrue(model["schema"]["properties"], model["id"])
            self.assertTrue(model["endpoint"], model["id"])
            detail = cat.model_detail(model["id"])
            self.assertTrue(detail["fields"], model["id"])
            self.assertTrue(detail["summary"]["parameterized"], model["id"])

    def test_public_list_excludes_schema_but_includes_capabilities(self):
        models = cat.list_models()
        self.assertEqual(len(models), 84)
        first = models[0]
        self.assertNotIn("schema", first)
        self.assertIn("summary", first)
        self.assertFalse(first["capabilities"]["progress"])
        self.assertFalse(first["capabilities"]["estimate"])
        self.assertIn("mode_label", first)

    def test_filters(self):
        videos = cat.list_models(kind="video")
        self.assertEqual(len(videos), 67)
        self.assertTrue(all(m["kind"] == "video" for m in videos))
        seedance = cat.list_models(query="seedance 2.5")
        self.assertTrue(seedance)
        self.assertTrue(all("seedance" in m["id"] or "seedance" in m["label"].lower() for m in seedance))
        modes = {m["id"] for m in cat.modes()}
        self.assertIn("text_to_video", modes)
        self.assertIn("image_to_video", modes)
        self.assertIn("character_training", modes)
        self.assertEqual(sum(m["count"] for m in cat.modes()), 84)

    def test_unknown_model_is_an_error(self):
        with self.assertRaises(cat.CatalogError):
            cat.get_model("does/not/exist")

    def test_known_endpoint_ids(self):
        for model_id in (
            "bytedance/seedance-2.5/text-to-video",
            "kling-video/v3.0/std/text-to-video",
            "higgsfield-ai/soul/v2/standard",
        ):
            model = cat.get_model(model_id)
            self.assertEqual(model["endpoint"], model_id)
            self.assertIn(model["kind"], contracts.KINDS)

    def test_catalog_has_no_duplicate_required_unknowns(self):
        # Every required field must exist in the schema; a broken snapshot would
        # otherwise produce forms that can never be submitted.
        for model in cat.load()["models"]:
            props = model["schema"].get("properties") or {}
            for name in model["schema"].get("required") or []:
                self.assertIn(name, props, "%s requires unknown %s" % (model["id"], name))

    def test_snapshot_file_is_committed_and_pinned(self):
        path = os.path.join(ROOT, "backend", "catalog", "higgsfield-0.1.0.json")
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["catalog_version"], "higgsfield-0.1.0")
        self.assertFalse(payload["source"]["experimental_web_backend"])


if __name__ == "__main__":
    unittest.main()
