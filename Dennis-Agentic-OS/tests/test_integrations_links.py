"""Unit tests for cross-platform campaign links (stdlib unittest only).

Run:  python3 -m unittest tests.test_integrations_links -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.integrations import links, store


class LinksTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-intx-links-")
        self._orig = links.LINKS_FILE
        links.LINKS_FILE = os.path.join(self.tmp, "links.json")

    def tearDown(self):
        links.LINKS_FILE = self._orig

    def _product(self):
        return {"item_id": 111, "shop_id": 222, "product_name": "Earbuds",
                "product_link": "https://shopee.ph/x", "offer_link": "https://aff/y"}

    def test_create_requires_product_provenance(self):
        with self.assertRaises(links.LinkError):
            links.create_link({})
        row = links.create_link(self._product(), campaign="launch")
        self.assertTrue(row["id"].startswith("link_"))
        self.assertEqual(row["shopee"]["item_id"], 111)
        self.assertEqual(row["shopee"]["offer_link"], "https://aff/y")
        self.assertIsNone(row["youtube"])

    def test_attach_video_and_status_flow(self):
        row = links.create_link(self._product())
        attached = links.attach_video(row["id"], {
            "video_id": "vid1", "video_url": "https://youtu.be/vid1",
            "channel_id": "UC1", "published_at": "2026-11-25"})
        self.assertEqual(attached["youtube"]["video_id"], "vid1")
        self.assertEqual(attached["status"], "in_production")  # private upload stage
        published = links.set_status(row["id"], "published")
        self.assertEqual(published["status"], "published")
        archived = links.set_status(row["id"], "archived")
        self.assertEqual(archived["status"], "archived")
        events = [h["event"] for h in archived["history"]]
        self.assertIn("video_attached", events)
        self.assertIn("status:archived", events)

    def test_attach_video_requires_reference(self):
        row = links.create_link(self._product())
        with self.assertRaises(links.LinkError):
            links.attach_video(row["id"], {"title": "no ids"})

    def test_filters(self):
        links.create_link(self._product(), campaign="a")
        second = links.create_link(self._product(), campaign="b")
        links.attach_video(second["id"], {"video_id": "v"}, status="published")
        self.assertEqual(len(links.list_links(campaign="a")), 1)
        self.assertEqual(len(links.list_links(status="published")), 1)
        self.assertEqual(len(links.list_links()), 2)

    def test_performance_snapshot_never_fabricates(self):
        row = links.create_link(self._product())
        # no video attached yet
        snapshot = links.build_performance_snapshot(row)
        self.assertFalse(snapshot["youtube"]["available"])
        self.assertIn("No video attached", snapshot["youtube"]["note"])
        self.assertFalse(snapshot["shopee"]["available"])
        self.assertEqual(snapshot["attribution"],
                         "none — platforms do not attribute views to conversions here")
        # shopee unconfigured: honest note, zero metrics invented
        self.assertIn("credentials", snapshot["shopee"]["note"].lower())

    def test_performance_snapshot_uses_shared_youtube_service(self):
        row = links.create_link(self._product())
        links.attach_video(row["id"], {"video_id": "vid1"})
        with mock.patch("backend.integrations.youtube.connection_state",
                        return_value={"execution_available": True, "blocked_reason": ""}), \
             mock.patch("backend.integrations.youtube.run",
                        return_value={"status": "ok", "data": {"items": [{"id": "vid1",
                                                                          "statistics": {"viewCount": "10"}}]}}):
            snapshot = links.build_performance_snapshot(links.get(row["id"]))
        self.assertTrue(snapshot["youtube"]["available"])
        self.assertEqual(snapshot["youtube"]["video_details"]["items"][0]["id"], "vid1")

    def test_recorded_performance_is_redacted_and_bounded(self):
        row = links.create_link(self._product())
        recorded = links.record_performance(row["id"], {
            "youtube": {"views": 100}, "api_key": "super-secret"})
        latest = recorded["performance"][0]
        self.assertNotIn("super-secret", json.dumps(latest))
        self.assertEqual(latest["youtube"]["views"], 100)

    def test_store_permissions(self):
        links.create_link(self._product())
        self.assertEqual(oct(os.stat(links.LINKS_FILE).st_mode & 0o777), "0o600")


if __name__ == "__main__":
    unittest.main()
