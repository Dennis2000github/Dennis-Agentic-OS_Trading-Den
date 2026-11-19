"""Tests for the API Mega List repository workspace (stdlib unittest only).

Run:  python3 -m unittest tests.test_megalist -v
"""

import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import megalist


README = """# API Mega List

**The most comprehensive collection of APIs on GitHub.**

| Metric | Count |
|--------|-------|
| **Core directory APIs** | **2** |
| **Sponsored partner listings** | **1** |
| **Categories** | **2** |
| **Last Updated** | 2026-07-23 |
"""


def _write_repo(root):
    os.makedirs(os.path.join(root, "ai-apis-2"), exist_ok=True)
    os.makedirs(os.path.join(root, "00-featured-apis", "demo"), exist_ok=True)
    os.makedirs(os.path.join(root, "settings"), exist_ok=True)
    os.makedirs(os.path.join(root, "assets"), exist_ok=True)
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as fh:
        fh.write(README)
    with open(os.path.join(root, "ai-apis-2", "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# AI\n\n| API Name | Description |\n|---|---|\n| [Demo](https://example.com) | Demo API |\n")
    with open(os.path.join(root, "00-featured-apis", "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# Featured APIs\n")
    with open(os.path.join(root, "00-featured-apis", "demo", "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# Demo\n")
    with open(os.path.join(root, "settings", "fetch_apify_actors.js"), "w", encoding="utf-8") as fh:
        fh.write("console.log('fetch');\n")
    with open(os.path.join(root, "assets", "demo.png"), "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")


class MegaListWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-megalist-")
        self.repo = os.path.join(self.tmp, "repo")
        _write_repo(self.repo)
        self._orig = (
            megalist.REPO_DIR,
            megalist.PACK_DIR,
            megalist.SNAPSHOT_FILE,
            dict(megalist._stats_cache),
        )
        megalist.REPO_DIR = self.repo
        megalist.PACK_DIR = self.tmp
        megalist.SNAPSHOT_FILE = os.path.join(self.tmp, "SNAPSHOT.json")
        megalist._stats_cache.update({"mtime": None, "data": None})

    def tearDown(self):
        (megalist.REPO_DIR, megalist.PACK_DIR, megalist.SNAPSHOT_FILE, cache) = self._orig
        megalist._stats_cache.clear()
        megalist._stats_cache.update(cache)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_reads_the_repository_pipeline_output(self):
        status = megalist.status()
        self.assertEqual(status["api_count"], 2)
        self.assertEqual(status["sponsored_count"], 1)
        self.assertEqual(status["category_count"], 2)
        self.assertEqual(status["core_category_count"], 1)
        self.assertEqual(status["featured_count"], 1)
        self.assertEqual(status["last_updated"], "2026-07-23")
        self.assertTrue(status["repo_ready"])
        self.assertEqual(status["categories"][0]["name"], "AI")

    def test_tree_exposes_repository_sections_and_counts(self):
        tree = megalist.tree()
        by_id = {section["id"]: section for section in tree["sections"]}
        self.assertEqual(by_id["repository"]["items"][0]["path"], "README.md")
        self.assertEqual(by_id["categories"]["items"][0]["label"], "AI")
        self.assertEqual(by_id["categories"]["items"][0]["count"], 2)
        self.assertEqual(by_id["featured"]["items"][0]["readme"], "00-featured-apis/README.md")
        self.assertEqual(by_id["pipeline"]["items"][0]["path"], "settings/fetch_apify_actors.js")

    def test_read_root_and_category_directory(self):
        root = megalist.read("README.md")
        self.assertEqual(root["kind"], "markdown")
        self.assertIn("Core directory APIs", root["text"])
        category = megalist.read("ai-apis-2")
        self.assertEqual(category["path"], "ai-apis-2/README.md")
        self.assertIn("Demo API", category["text"])
        self.assertTrue(category["github_url"].endswith("/ai-apis-2/README.md"))

    def test_path_traversal_is_rejected(self):
        for path in ("../README.md", "/etc/passwd", "ai-apis-2/../../README.md"):
            with self.assertRaises(megalist.MegalistError) as ctx:
                megalist.read(path)
            self.assertEqual(ctx.exception.code, "path_invalid")

    def test_non_text_and_unsupported_assets_are_rejected(self):
        with self.assertRaises(megalist.MegalistError) as ctx:
            megalist.read("assets/demo.png")
        self.assertEqual(ctx.exception.code, "not_text")
        with self.assertRaises(megalist.MegalistError) as ctx:
            megalist.asset("settings/fetch_apify_actors.js")
        self.assertEqual(ctx.exception.code, "asset_invalid")

    def test_asset_returns_bytes_and_content_type(self):
        body, ctype, path = megalist.asset("assets/demo.png")
        self.assertTrue(body.startswith(b"\x89PNG"))
        self.assertEqual(ctype, "image/png")
        self.assertEqual(path, "assets/demo.png")

    def _archive(self, extra_name="README.md", extra_body="# Synced\n"):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            def add(name, body):
                raw = body.encode("utf-8")
                info = tarfile.TarInfo("API-mega-list-main/" + name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
            add(extra_name, extra_body)
            add("ai-apis-7/README.md", "# AI\n\n7 APIs\n")
            add("settings/fetch_apify_actors.js", "console.log('fetch');\n")
        return buffer.getvalue()

    def test_sync_replaces_the_snapshot_atomically(self):
        body = self._archive()
        with mock.patch.object(megalist, "_download", return_value=body), \
                mock.patch.object(megalist, "_remote_commit", return_value="a" * 40):
            out = megalist.sync()
        self.assertTrue(out["synced"])
        self.assertEqual(out["snapshot"]["commit"], "a" * 40)
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "ai-apis-7", "README.md")))
        self.assertFalse(os.path.exists(os.path.join(self.repo, "ai-apis-2")))
        with open(megalist.SNAPSHOT_FILE, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["commit"], "a" * 40)

    def test_sync_rejects_tar_path_traversal(self):
        body = self._archive(extra_name="../escape.txt", extra_body="nope")
        with mock.patch.object(megalist, "_download", return_value=body):
            with self.assertRaises(megalist.MegalistError) as ctx:
                megalist.sync()
        self.assertEqual(ctx.exception.code, "path_invalid")
        self.assertTrue(os.path.isfile(os.path.join(self.repo, "ai-apis-2", "README.md")))


if __name__ == "__main__":
    unittest.main()
