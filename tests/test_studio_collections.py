"""Unit tests for Studio playlists and templates.

Run:  python3 -m unittest tests.test_studio_collections
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_collections as collections  # noqa: E402
from backend import studio_media  # noqa: E402


class StudioCollectionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(collections, "STUDIO_DIR", self.dir),
            mock.patch.object(collections, "PLAYLISTS_FILE", os.path.join(self.dir, "playlists.json")),
            mock.patch.object(collections, "TEMPLATES_FILE", os.path.join(self.dir, "templates.json")),
            mock.patch.object(studio_media, "STUDIO_DIR", self.dir),
            mock.patch.object(studio_media, "MEDIA_DIR", os.path.join(self.dir, "media")),
            mock.patch.object(studio_media, "INDEX_FILE", os.path.join(self.dir, "media.json")),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def media(self, name="frame.png"):
        return studio_media.add_bytes(b"png-bytes", "image/png", kind="image", filename=name)

    def test_playlist_round_trip_hydrates_media(self):
        item = self.media()
        playlist = collections.create_playlist("  My   shots  ")
        self.assertEqual(playlist["name"], "My shots")
        self.assertEqual(playlist["item_count"], 0)

        playlist = collections.add_to_playlist(playlist["id"], item["id"])
        self.assertEqual(playlist["item_count"], 1)
        self.assertTrue(playlist["items"][0]["media"]["title"].endswith("frame.png"))
        self.assertNotIn("source_url", playlist["items"][0]["media"])

        playlist = collections.remove_from_playlist(playlist["id"], item["id"])
        self.assertEqual(playlist["item_count"], 0)
        self.assertEqual(collections.delete_playlist(playlist["id"])["deleted"], playlist["id"])
        self.assertEqual(collections.list_playlists(), [])

    def test_playlist_duplicate_add_is_idempotent_and_missing_media_pruned(self):
        item = self.media()
        playlist = collections.create_playlist("Cut")
        collections.add_to_playlist(playlist["id"], item["id"])
        playlist = collections.add_to_playlist(playlist["id"], item["id"])
        self.assertEqual(playlist["item_count"], 1)

        studio_media.delete(item["id"])
        self.assertEqual(collections.list_playlists()[0]["item_count"], 0)

    def test_forget_media_cleans_all_playlists(self):
        item = self.media()
        a = collections.create_playlist("A")
        b = collections.create_playlist("B")
        collections.add_to_playlist(a["id"], item["id"])
        collections.add_to_playlist(b["id"], item["id"])
        collections.forget_media(item["id"])
        self.assertTrue(all(p["item_count"] == 0 for p in collections.list_playlists()))

    def test_playlist_validation(self):
        with self.assertRaises(collections.CollectionError):
            collections.create_playlist("x" * (collections.NAME_LIMIT + 1))
        with self.assertRaises(collections.CollectionError):
            collections.add_to_playlist("pl_missing", "med_missing")
        playlist = collections.create_playlist("A")
        with self.assertRaises(collections.CollectionError):
            collections.add_to_playlist(playlist["id"], "med_missing")

    def test_templates_are_private_and_round_trip(self):
        template = collections.save_template(
            "Neon city",
            "higgsfield",
            "bytedance/seedance-2.5/text-to-video",
            "Seedance 2.5",
            {"prompt": "neon city", "duration": 5},
            {"durations": [5, 10]},
        )
        self.assertTrue(template["id"].startswith("tpl_"))
        items = collections.list_templates()
        self.assertEqual(items[0]["name"], "Neon city")
        self.assertEqual(items[0]["params"]["duration"], 5)
        path = os.path.join(self.dir, "templates.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

        # Returned records are copies; mutating them cannot corrupt storage.
        items[0]["params"]["duration"] = 99
        self.assertEqual(collections.list_templates()[0]["params"]["duration"], 5)

        self.assertEqual(collections.delete_template(template["id"])["deleted"], template["id"])
        self.assertEqual(collections.list_templates(), [])

    def test_template_validation_and_limits(self):
        with self.assertRaises(collections.CollectionError):
            collections.save_template("", "", "m", "M", {})
        with self.assertRaises(collections.CollectionError):
            collections.save_template("T", "higgsfield", "", "M", {})
        with self.assertRaises(collections.CollectionError):
            collections.save_template("T", "higgsfield", "m", "M", [])
        with self.assertRaises(collections.CollectionError):
            collections.delete_template("tpl_missing")

    def test_corrupt_collection_file_is_reported(self):
        path = os.path.join(self.dir, "playlists.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self.assertRaises(collections.CollectionError):
            collections.list_playlists()


if __name__ == "__main__":
    unittest.main()
