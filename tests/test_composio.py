"""Unit tests for the Composio integration (stdlib unittest only).

Run:  python3 -m unittest tests.test_composio -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import composio, plugins


class ComposioTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-composio-test-")
        self._env = {k: os.environ.get(k) for k in (composio.CONSUMER_ENV, composio.API_ENV)}
        os.environ.pop(composio.CONSUMER_ENV, None)
        os.environ.pop(composio.API_ENV, None)
        self._orig = (
            composio.STORE_FILE,
            composio.CACHE_FILE,
            composio._CLIENTS,
            plugins.PLUGINS_ROOT,
            plugins.STATE_FILE,
            plugins.AGENT_SKILL_DIRS,
        )
        composio.STORE_FILE = os.path.join(self.tmp, "composio.json")
        composio.CACHE_FILE = os.path.join(self.tmp, "cache.json")
        composio._CLIENTS = {
            "opencode": {
                "label": "OpenCode",
                "path": os.path.join(self.tmp, "opencode", "opencode.jsonc"),
                "format": "jsonc",
                "nested": ["mcp"],
            },
            "claude-code": {
                "label": "Claude Code",
                "path": os.path.join(self.tmp, "claude", ".claude.json"),
                "format": "json",
                "nested": ["mcpServers"],
            },
        }
        plugins.PLUGINS_ROOT = os.path.join(self.tmp, "plugins")
        plugins.STATE_FILE = os.path.join(self.tmp, "plugins-state.json")
        plugins.AGENT_SKILL_DIRS = {
            "generic": os.path.join(self.tmp, "skills", "generic"),
            "claude-code": os.path.join(self.tmp, "skills", "claude"),
        }

    def tearDown(self):
        (composio.STORE_FILE,
         composio.CACHE_FILE,
         composio._CLIENTS,
         plugins.PLUGINS_ROOT,
         plugins.STATE_FILE,
         plugins.AGENT_SKILL_DIRS) = self._orig
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # -- keys ---------------------------------------------------------
    def test_save_and_clear_keys(self):
        state = composio.save_keys("ck_test123", "sk_test456")
        self.assertTrue(state["consumer_key_set"])
        self.assertTrue(state["api_key_set"])
        self.assertEqual(oct(os.stat(composio.STORE_FILE).st_mode & 0o777), "0o600")
        self.assertNotIn("ck_test123", json.dumps(composio.public_keys()))
        with self.assertRaises(composio.ComposioError):
            composio.save_keys("bad key with spaces", None)
        composio.clear_keys()
        self.assertFalse(composio.public_keys()["consumer_key_set"])

    def test_environment_wins_and_is_not_written(self):
        composio.save_keys("ck_stored", None)
        os.environ[composio.CONSUMER_ENV] = "ck_env"
        state = composio.public_keys()
        self.assertTrue(state["consumer_key_set"])
        self.assertEqual(state["consumer_key_source"], composio.CONSUMER_ENV)
        with open(composio.STORE_FILE, encoding="utf-8") as handle:
            store = json.load(handle)
        self.assertEqual(store["consumer_key"], "ck_stored")

    # -- jsonc --------------------------------------------------------
    def test_strip_jsonc(self):
        text = """{
            // line comment
            "a": 1, /* block */
            "b": [1, 2,],
            "c": "http://not-a-comment",
        }"""
        parsed = json.loads(composio._strip_jsonc(text))
        self.assertEqual(parsed, {"a": 1, "b": [1, 2], "c": "http://not-a-comment"})

    # -- mcp ----------------------------------------------------------
    def test_mcp_blocks_per_client(self):
        self.assertEqual(composio.mcp_block("opencode")["mcp"]["composio"]["type"], "remote")
        self.assertIn("httpUrl", composio.mcp_block("gemini-cli")["mcpServers"]["composio"])
        self.assertIn("serverUrl", composio.mcp_block("antigravity")["mcpServers"]["composio"])
        self.assertIn("url", composio.mcp_block("cursor")["mcpServers"]["composio"])
        placeholder = composio.mcp_block("opencode")["mcp"]["composio"]["headers"][composio.CONSUMER_HEADER]
        self.assertEqual(placeholder, "{env:COMPOSIO_CONSUMER_KEY}")
        composio.save_keys("ck_inline", None)
        inline = composio.mcp_block("opencode")["mcp"]["composio"]["headers"][composio.CONSUMER_HEADER]
        self.assertEqual(inline, "ck_inline")
        self.assertEqual(composio.mcp_snippet("codex")["language"], "toml")

    def test_wire_is_idempotent_and_reversible(self):
        opencode = composio._CLIENTS["opencode"]["path"]
        os.makedirs(os.path.dirname(opencode), exist_ok=True)
        with open(opencode, "w", encoding="utf-8") as handle:
            handle.write('{\n  // user config\n  "theme": "dark",\n}\n')

        result = composio.wire(["opencode", "claude-code"])
        self.assertTrue(result["ok"])
        actions = {item["client"]: item["action"] for item in result["results"]}
        self.assertEqual(actions["opencode"], "added")
        self.assertEqual(actions["claude-code"], "added")
        with open(opencode, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["theme"], "dark")
        self.assertTrue(data["mcp"]["composio"]["enabled"])
        backups = [n for n in os.listdir(os.path.dirname(opencode)) if "dennis-backup" in n]
        self.assertEqual(len(backups), 1)

        again = composio.wire(["opencode"], remove=False)
        self.assertEqual(again["results"][0]["action"], "unchanged")

        removed = composio.wire(["opencode", "claude-code"], remove=True)
        self.assertTrue(removed["ok"])
        with open(opencode, encoding="utf-8") as handle:
            self.assertNotIn("composio", json.load(handle).get("mcp", {}))

    def test_wire_reports_invalid_config(self):
        path = composio._CLIENTS["opencode"]["path"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        result = composio.wire(["opencode"])
        self.assertFalse(result["ok"])
        self.assertIn("not valid JSON", result["results"][0]["error"])

    # -- catalog ------------------------------------------------------
    def test_catalog_falls_back_to_curated_seed(self):
        data = composio.catalog()
        self.assertEqual(data["source"], "seed")
        self.assertGreaterEqual(data["count"], 30)
        self.assertIn("warning", data)
        slugs = {item["slug"] for item in data["toolkits"]}
        for slug in ("gmail", "slack", "github", "notion"):
            self.assertIn(slug, slugs)

    def test_toolkit_seed_detail(self):
        detail = composio.toolkit("gmail")
        self.assertEqual(detail["toolkit"]["slug"], "gmail")
        self.assertEqual(detail["source"], "seed")
        meta = composio.toolkit("composio")
        self.assertTrue(any(tool["slug"] == "COMPOSIO_SEARCH_TOOLS" for tool in meta["tools"]))

    # -- plugin pack --------------------------------------------------
    def test_pack_manifest_and_install(self):
        with open(composio.PACK_MANIFEST, encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertEqual(manifest["name"], "composio")
        for rel in manifest["skills"]:
            self.assertTrue(os.path.isfile(os.path.join(composio.PACK_DIR, rel, "SKILL.md")))
        for rel in manifest["mcp"]:
            self.assertTrue(os.path.isfile(os.path.join(composio.PACK_DIR, rel)))

        result = composio.install_plugin()
        self.assertTrue(result["status"]["installed"])
        self.assertEqual(result["status"]["tier"], "public")
        self.assertTrue(os.path.isdir(os.path.join(plugins.PLUGINS_ROOT, "composio")))
        link = os.path.join(plugins.AGENT_SKILL_DIRS["generic"], "composio")
        self.assertTrue(os.path.exists(link))

    def test_catalog_lists_composio(self):
        ids = {item["id"] for item in plugins.catalog()["items"]}
        self.assertIn("composio", ids)


if __name__ == "__main__":
    unittest.main()
