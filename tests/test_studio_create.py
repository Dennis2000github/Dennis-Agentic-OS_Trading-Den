"""Unit tests for Ads Studio Create briefs and handoff.

The existing five-step Generate workflow is not touched by these tests; they
verify that Create only composes reviewable briefs and creates a draft Ads
project, never a render.

Run:  python3 -m unittest tests.test_studio_create
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ads_backlog  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import studio_create  # noqa: E402


TEMPLATE = {
    "id": "womens-apparel",
    "name": "Women's Apparel",
    "product_category": "Women's fashion and apparel",
    "target_audience": "Women aged 18-35",
    "creative_style": "Warm natural daylight; mirror try-on.",
    "video_format": "9:16 short-form try-on",
    "target_platforms": ["TikTok", "Instagram Reels"],
    "price_range": {"min": 200, "max": 2500, "currency": "PHP"},
    "product_restrictions": ["No counterfeit goods"],
}


class StudioCreateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(studio_create, "BRIEFS_FILE", os.path.join(self.dir, "create-briefs.json")),
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json")),
            mock.patch.object(studio_create, "_active_template", return_value=dict(TEMPLATE)),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def _brief(self, **overrides):
        payload = {
            "instruction": "Create a 15-second try-on video for TikTok.",
            "product": {
                "name": "Floral midi dress",
                "description": "A breathable floral dress.",
                "merchant": "Shopee",
                "price": 899,
                "target_audience": "Women 18-35",
                "product_url": "https://shopee.ph/item/1",
            },
            "duration_seconds": 15,
        }
        payload.update(overrides)
        return studio_create.create_brief(payload)["brief"]

    def test_brief_composes_structured_prompt_from_active_template(self):
        brief = self._brief()
        self.assertTrue(brief["id"].startswith("crb_"))
        self.assertEqual(brief["template_id"], "womens-apparel")
        self.assertEqual(brief["target_platforms"], ["TikTok", "Instagram Reels"])
        self.assertEqual(brief["duration_seconds"], 15)
        prompt = brief["prompt"]
        self.assertIn("Floral midi dress", prompt)
        self.assertIn("Women's Apparel", prompt)
        self.assertIn("9:16 short-form try-on", prompt)
        self.assertIn("899", prompt)
        self.assertIn("unverified", prompt)
        self.assertIn("do not publish automatically", prompt)
        self.assertIn("Affiliate link: none validated", prompt)
        self.assertEqual(brief["affiliate_destination"]["status"], "none")
        self.assertEqual(brief["status"], "draft")
        self.assertIsNone(brief["handoff_project_id"])

    def test_manual_price_accepts_number_and_amount_object(self):
        number_brief = self._brief(product={"name": "Number price dress", "price": 899})
        self.assertEqual(number_brief["product"]["price"]["amount"], 899)
        object_brief = self._brief(product={
            "name": "Object price dress",
            "price": {"amount": 1299, "currency": "PHP"},
        })
        self.assertEqual(object_brief["product"]["price"]["amount"], 1299)
        self.assertEqual(object_brief["product"]["price"]["currency"], "PHP")
        self.assertIn("1299", object_brief["prompt"])

    def test_product_can_come_from_ads_backlog(self):
        product = ads_backlog.create_product({
            "name": "Linen blazer",
            "merchant": "Lazada",
            "category": "Outerwear",
            "price": {"amount": 1299, "currency": "PHP"},
            "status": "approved",
        })
        brief = self._brief(product={"product_id": product["id"]})
        self.assertEqual(brief["product"]["source"], "backlog")
        self.assertEqual(brief["product"]["id"], product["id"])
        self.assertIn("Linen blazer", brief["prompt"])

    def test_validated_affiliate_adds_disclosure_and_prompt_line(self):
        brief = self._brief(affiliate_destination={
            "url": "https://shopee.ph/affiliate/123",
            "program": "Shopee Affiliate",
            "validated": True,
        })
        self.assertEqual(brief["affiliate_destination"]["status"], "validated")
        self.assertIn("affiliate links", brief["prompt"].lower())
        self.assertTrue(any("affiliate" in item.lower() for item in brief["disclosures"]))

    def test_unvalidated_affiliate_is_excluded_from_prompt(self):
        brief = self._brief(affiliate_destination={
            "url": "https://shopee.ph/affiliate/123",
            "program": "Shopee Affiliate",
            "validated": False,
        })
        self.assertEqual(brief["affiliate_destination"]["status"], "unvalidated")
        self.assertNotIn("include the owner-supplied tracking link", brief["prompt"])
        self.assertFalse(any("affiliate links" in item.lower() for item in brief["disclosures"]))

    def test_sponsored_disclosure_is_required(self):
        brief = self._brief(sponsored=True)
        self.assertTrue(any("Sponsored" in item for item in brief["disclosures"]))

    def test_missing_product_and_instruction_is_rejected(self):
        with self.assertRaises(common.CosmosError) as ctx:
            studio_create.create_brief({"instruction": "", "product": {}})
        self.assertEqual(ctx.exception.code, "product_required")

    def test_archived_backlog_product_cannot_be_used(self):
        product = ads_backlog.create_product({"name": "Old dress", "merchant": "Shopee"})
        ads_backlog.archive(product["id"])
        with self.assertRaises(common.CosmosError) as ctx:
            studio_create.create_brief({"instruction": "Make an ad", "product": {"product_id": product["id"]}})
        self.assertEqual(ctx.exception.code, "product_archived")

    def test_handoff_creates_draft_ads_project_and_is_idempotent(self):
        brief = self._brief()
        project = {
            "id": "sp_test123",
            "workspace": "ads",
            "title": "Ad creative — Floral midi dress",
            "status": "draft",
            "stage": "script",
        }
        with mock.patch.object(studio_create.studio_workspaces, "create_project", return_value=project) as create:
            result = studio_create.handoff(brief["id"])
        self.assertFalse(result["already_handed_off"])
        self.assertEqual(result["project"]["id"], "sp_test123")
        self.assertEqual(result["prefill"]["prompt"], brief["prompt"])
        args, kwargs = create.call_args
        self.assertEqual(args[0], "ads")
        payload = args[1]
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(payload["metadata"]["brief_id"], brief["id"])
        self.assertEqual(payload["metadata"]["source"], "cosmos_ads_create")
        self.assertEqual(payload["metadata"]["affiliate_status"], "none")
        self.assertNotIn("render", payload)
        stored = studio_create.get_brief(brief["id"])["brief"]
        self.assertEqual(stored["status"], "handed_off")
        self.assertEqual(stored["handoff_project_id"], "sp_test123")

        with mock.patch.object(studio_create.studio_workspaces, "get_project", return_value=project) as get:
            again = studio_create.handoff(brief["id"])
        self.assertTrue(again["already_handed_off"])
        get.assert_called_once_with("ads", "sp_test123")

    def test_list_and_soft_delete(self):
        one = self._brief()
        two = self._brief(instruction="Create a comparison video.")
        listed = studio_create.list_briefs()
        self.assertEqual(listed["total"], 2)
        studio_create.delete_brief(one["id"])
        remaining = studio_create.list_briefs()
        self.assertEqual([item["id"] for item in remaining["briefs"]], [two["id"]])
        with self.assertRaises(common.CosmosError) as ctx:
            studio_create.get_brief(one["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_context_exposes_template_products_and_defaults(self):
        ads_backlog.create_product({"name": "Dress A", "merchant": "Shopee"})
        data = studio_create.context()
        self.assertEqual(data["template"]["id"], "womens-apparel")
        self.assertEqual([p["name"] for p in data["products"]], ["Dress A"])
        self.assertEqual(data["duration_default"], 15)


if __name__ == "__main__":
    unittest.main()
