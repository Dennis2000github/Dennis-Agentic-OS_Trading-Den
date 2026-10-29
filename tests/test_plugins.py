"""Unit tests for the agent-agnostic plugin manager (stdlib unittest only).

Run:  python3 -m unittest tests.test_plugins -v
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import plugins


class PluginManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dennis-plugins-test-")
        self._orig = (plugins.PLUGINS_ROOT, plugins.STATE_FILE, plugins.AGENT_SKILL_DIRS)
        plugins.PLUGINS_ROOT = os.path.join(self.tmp, "plugins")
        plugins.STATE_FILE = os.path.join(self.tmp, "state.json")
        plugins.AGENT_SKILL_DIRS = {
            "generic": os.path.join(self.tmp, "skills", "generic"),
            "claude-code": os.path.join(self.tmp, "skills", "claude"),
        }

    def tearDown(self):
        (plugins.PLUGINS_ROOT, plugins.STATE_FILE, plugins.AGENT_SKILL_DIRS) = self._orig

    def _make_source(self, name="demo-pack"):
        src = os.path.join(self.tmp, "src", name)
        skill = os.path.join(src, "skills", "demo-skill")
        os.makedirs(skill, exist_ok=True)
        with open(os.path.join(src, "plugin.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": name, "version": "2.0.0", "description": "demo",
                       "skills": ["skills/demo-skill"], "mcp": ["./mcp/x.json"]}, fh)
        with open(os.path.join(skill, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nname: demo-skill\ndescription: Use when demoing.\n---\n# Demo\n")
        return src

    def test_safe_name_rejects_traversal(self):
        for bad in ("../evil", "a/b", "", "A B", "."):
            with self.assertRaises(plugins.PluginError):
                plugins._safe_name(bad)

    def test_install_local_then_link_toggle_convert_uninstall(self):
        src = self._make_source()
        summary = plugins.install_plugin(src)
        self.assertEqual(summary["name"], "demo-pack")
        self.assertEqual(summary["version"], "2.0.0")
        self.assertEqual(len(summary["skills"]), 1)
        self.assertTrue(summary["enabled"])

        # install a second time without force -> rejected
        with self.assertRaises(plugins.PluginError):
            plugins.install_plugin(src)

        listing = plugins.list_plugins()
        self.assertEqual([p["name"] for p in listing["plugins"]], ["demo-pack"])

        res = plugins.install_skills("demo-pack", selected_agents=["generic"])
        self.assertTrue(all(r["ok"] for r in res["results"]))
        link = os.path.join(plugins.AGENT_SKILL_DIRS["generic"], "demo-skill")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.path.realpath(link),
                         os.path.realpath(os.path.join(plugins.PLUGINS_ROOT, "demo-pack", "skills", "demo-skill")))

        # re-linking is idempotent
        res2 = plugins.install_skills("demo-pack", selected_agents=["generic"])
        self.assertTrue(res2["results"][0]["ok"])
        self.assertEqual(res2["results"][0]["reason"], "already linked")

        toggled = plugins.set_enabled("demo-pack", False)
        self.assertFalse(toggled["enabled"])
        self.assertFalse(plugins.read_plugin("demo-pack")["enabled"])

        conv = plugins.convert_to_claude("demo-pack")
        self.assertTrue(os.path.isfile(conv["path"]))

        out = plugins.uninstall_plugin("demo-pack")
        self.assertTrue(out["removed"])
        self.assertFalse(os.path.exists(os.path.join(plugins.PLUGINS_ROOT, "demo-pack")))
        self.assertFalse(os.path.islink(link))

    def test_scaffold_creates_skeleton(self):
        p = plugins.scaffold("my-pack", description="hi")
        self.assertEqual(p["name"], "my-pack")
        self.assertEqual(p["tier"], "personal")
        self.assertTrue(os.path.isfile(os.path.join(p["path"], "plugin.json")))
        self.assertEqual(len(p["skills"]), 1)
        skill_md = os.path.join(p["path"], p["skills"][0]["rel"], "SKILL.md")
        self.assertTrue(os.path.isfile(skill_md))
        with self.assertRaises(plugins.PluginError):
            plugins.scaffold("my-pack")

    def test_install_rejects_missing_source(self):
        with self.assertRaises(plugins.PluginError):
            plugins.install_plugin("")

    def test_status_reports_agents(self):
        st = plugins.status()
        self.assertIn("root", st)
        self.assertEqual(sorted(a["id"] for a in st["agents"]), ["claude-code", "generic"])

    def test_catalog_lists_composio_public_pack(self):
        cat = plugins.catalog()
        self.assertEqual([item["id"] for item in cat["items"]], ["composio"])
        self.assertEqual(cat["counts"], {"personal": 0, "public": 1})
        self.assertEqual(cat["items"][0]["tier"], "public")

    def test_catalog_install_rejects_removed_api_mega_list_entry(self):
        with self.assertRaises(plugins.PluginError):
            plugins.install_catalog("api-mega-list")

    def test_local_install_defaults_to_personal_and_accepts_tier(self):
        personal = plugins.install_plugin(self._make_source("mine"), name="mine")
        self.assertEqual(personal["tier"], "personal")
        public = plugins.install_plugin(self._make_source("theirs"), name="theirs", tier="public")
        self.assertEqual(public["tier"], "public")

    def test_list_skills_reads_agent_dirs(self):
        src = self._make_source()
        plugins.install_plugin(src)
        plugins.install_skills("demo-pack", ["generic"])

        out = plugins.list_skills()
        self.assertEqual(out["count"], 1)
        skill = out["skills"][0]
        self.assertEqual(skill["name"], "demo-skill")
        self.assertEqual(skill["agents"], ["generic"])
        self.assertEqual(skill["title"], "demo-skill")
        self.assertIn("demoing", skill["description"])


if __name__ == "__main__":
    unittest.main()
