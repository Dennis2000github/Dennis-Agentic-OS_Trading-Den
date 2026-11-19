"""Unit tests for COSMOS configuration templates and effective snapshots.

Run:  python3 -m unittest tests.test_cosmos_config
"""

import copy
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_config as config  # noqa: E402


CHANNEL = {
    "id": "ytch_test",
    "name": "Test Channel",
    "tagline": "Testing",
    "description": "A channel for tests.",
    "content_niche": "Small business finance",
    "target_audience": "Small business owners",
    "audience_age_focus": "25-54",
    "language": "en-US",
    "country": "US",
    "content_pillars": [{"id": "tax", "label": "Tax Intelligence", "allocation": 50}],
    "publishing": {},
}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "CONFIG_FILE", os.path.join(self.dir, "config.json")),
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json")),
            mock.patch.object(config.studio_workspaces, "get_settings", return_value={}),
            mock.patch.object(config, "_resolve_channel", return_value=CHANNEL),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def test_defaults_are_five_editable_templates_per_domain(self):
        data = config.config_public()
        self.assertEqual(len(data["ads_templates"]), 5)
        self.assertEqual(len(data["youtube_templates"]), 5)
        self.assertEqual(
            [t["name"] for t in data["ads_templates"]],
            ["Women's Apparel", "Beauty & Skincare", "Electronics & Gadgets", "Home & Living", "Health & Lifestyle"],
        )
        self.assertEqual(
            [t["name"] for t in data["youtube_templates"]],
            ["Educational Video", "Documentary / Explainer", "Faceless YouTube", "Entertainment / Storytelling", "Product Review"],
        )
        for template in data["youtube_templates"]:
            self.assertIn(template["format"], ("longform", "shorts"))
            self.assertTrue(template["structure"])

    def test_editing_a_template_persists_and_leaves_others(self):
        before = config.get_template("ads", "womens-apparel")
        updated = config.update_template("ads", "womens-apparel", {
            "name": "Women's Apparel PH",
            "product_interests": ["Dresses", "Linen sets"],
            "price_range": {"min": 500, "max": 900, "currency": "PHP"},
        })
        self.assertEqual(updated["name"], "Women's Apparel PH")
        self.assertEqual(updated["product_interests"], ["Dresses", "Linen sets"])
        again = config.get_template("ads", "womens-apparel")
        self.assertEqual(again["name"], "Women's Apparel PH")
        self.assertNotEqual(before["name"], again["name"])
        other = config.get_template("ads", "beauty-skincare")
        self.assertEqual(other["name"], "Beauty & Skincare")

    def test_template_edit_rejects_empty_name_and_bad_format(self):
        with self.assertRaises(common.CosmosError):
            config.update_template("ads", "womens-apparel", {"name": "  "})
        with self.assertRaises(common.CosmosError):
            config.update_template("youtube", "educational-video", {"format": "podcast"})

    def test_active_template_selection_is_validated_and_persisted(self):
        result = config.set_active_template("ads", "home-living")
        self.assertEqual(result["active_ads_template_id"], "home-living")
        self.assertEqual(config.config_public()["active_ads_template_id"], "home-living")
        with self.assertRaises(common.CosmosError) as ctx:
            config.set_active_template("ads", "does-not-exist")
        self.assertEqual(ctx.exception.status, 404)

    def test_ads_snapshot_uses_active_template_and_freezes_on_edit(self):
        snapshot = config.effective("ads")
        self.assertEqual(snapshot["domain"], "ads")
        self.assertEqual(snapshot["template_id"], "womens-apparel")
        self.assertEqual(snapshot["template"]["name"], "Women's Apparel")
        frozen = copy.deepcopy(snapshot)

        config.update_template("ads", "womens-apparel", {"name": "Renamed Later"})
        self.assertEqual(frozen["template"]["name"], "Women's Apparel")
        refreshed = config.effective("ads")
        self.assertEqual(refreshed["template"]["name"], "Renamed Later")
        self.assertNotEqual(frozen["template"], refreshed["template"])

    def test_youtube_snapshot_includes_channel_and_template(self):
        snapshot = config.effective("youtube")
        self.assertEqual(snapshot["domain"], "youtube")
        self.assertEqual(snapshot["channel_id"], "ytch_test")
        self.assertEqual(snapshot["channel"]["content_niche"], "Small business finance")
        self.assertEqual(snapshot["template_id"], "educational-video")
        self.assertIn("captured_at", snapshot)

    def test_unknown_domain_rejected(self):
        with self.assertRaises(common.CosmosError) as ctx:
            config.effective("email")
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
