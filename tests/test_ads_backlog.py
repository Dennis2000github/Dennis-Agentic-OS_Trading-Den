"""Unit tests for the Ads Product Backlog lifecycle.

Run:  python3 -m unittest tests.test_ads_backlog
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ads_backlog  # noqa: E402
from backend import cosmos_common as common  # noqa: E402


class AdsBacklogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patch = mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "backlog.json"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def _product(self, name="Floral midi dress", merchant="Shopee", status="drafted"):
        return ads_backlog.create_product({
            "name": name,
            "merchant": merchant,
            "description": "A floral dress.",
            "category": "Dresses",
            "price": {"amount": 899, "currency": "PHP"},
            "target_audience": "Women 18-35",
            "template_id": "womens-apparel",
            "product_url": "https://shopee.ph/item/1",
            "status": status,
        })

    def test_create_product_fields_and_history(self):
        item = self._product()
        self.assertTrue(item["id"].startswith("adp_"))
        self.assertEqual(item["status"], "drafted")
        self.assertEqual(item["price"]["amount"], 899)
        self.assertFalse(item["price_verified"])
        self.assertEqual(item["history"][0]["status"], "drafted")
        self.assertEqual(item["provenance"], {})

    def test_duplicate_blocked_but_archived_duplicate_allowed(self):
        first = self._product()
        with self.assertRaises(ads_backlog.BacklogError) as ctx:
            self._product("  floral   MIDI dress ")
        self.assertEqual(ctx.exception.status, 409)
        ads_backlog.set_status(first["id"], "archived")
        second = self._product("Floral midi dress")
        self.assertNotEqual(first["id"], second["id"])

    def test_forward_only_status_lifecycle(self):
        item = self._product()
        ads_backlog.set_status(item["id"], "reviewing", note="looks good")
        moved = ads_backlog.set_status(item["id"], "approved")
        self.assertEqual(moved["status"], "approved")
        self.assertEqual(len(moved["history"]), 3)
        with self.assertRaises(ads_backlog.BacklogError) as ctx:
            ads_backlog.set_status(item["id"], "discovered")
        self.assertEqual(ctx.exception.code, "invalid_transition")
        with self.assertRaises(ads_backlog.BacklogError):
            ads_backlog.set_status(item["id"], "not-a-status")

    def test_archived_is_terminal(self):
        item = self._product()
        ads_backlog.archive(item["id"])
        with self.assertRaises(ads_backlog.BacklogError):
            ads_backlog.set_status(item["id"], "approved")

    def test_counts_and_listing_filters(self):
        a = self._product("Dress A")
        self._product("Dress B")
        ads_backlog.set_status(a["id"], "approved")
        counts = ads_backlog.counts()
        self.assertEqual(counts["approved"], 1)
        self.assertEqual(counts["drafted"], 1)
        self.assertEqual(counts["total"], 2)
        approved = ads_backlog.list_products(status="approved")
        self.assertEqual([row["id"] for row in approved["products"]], [a["id"]])
        search = ads_backlog.list_products(query="dress b")
        self.assertEqual([row["name"] for row in search["products"]], ["Dress B"])

    def test_update_product_and_soft_delete(self):
        item = self._product()
        updated = ads_backlog.update_product(item["id"], {
            "name": "Floral maxi dress",
            "price": {"amount": 1099, "currency": "PHP", "verified": True},
            "affiliate_status": "potential",
        })
        self.assertEqual(updated["name"], "Floral maxi dress")
        self.assertTrue(updated["price_verified"])
        self.assertEqual(updated["affiliate_status"], "potential")
        ads_backlog.delete_product(item["id"])
        with self.assertRaises(ads_backlog.BacklogError) as ctx:
            ads_backlog.get_product(item["id"])
        self.assertEqual(ctx.exception.status, 404)

    def test_owner_origin_provenance_is_retained(self):
        item = ads_backlog.create_product(
            {"name": "Provenance dress", "merchant": "Lazada"},
            origin={"agent_id": "andromeda", "execution_id": "exe_1", "recommendation_id": "rec_1",
                    "division": "ads", "template_id": "womens-apparel"},
        )
        self.assertEqual(item["provenance"]["agent_id"], "andromeda")
        self.assertEqual(item["provenance"]["execution_id"], "exe_1")


if __name__ == "__main__":
    unittest.main()
