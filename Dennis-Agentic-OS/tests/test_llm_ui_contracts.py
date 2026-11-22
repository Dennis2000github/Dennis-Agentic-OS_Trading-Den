"""Static contract tests for the Workspace-pattern Agent/Model dropdowns.

No browser: these guard the wiring between index.html, the Studio frontend
and the COSMOS frontend so the dropdowns cannot silently regress to the
removed hard-coded provider profiles.

Run:  python3 -m unittest tests.test_llm_ui_contracts
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


class StudioDropdownContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read("index.html")
        cls.js = read("js", "studioYoutube.js")

    def test_both_studio_rows_have_the_full_dropdown_markup(self):
        for prefix in ("yt-llm", "yt-settings-llm"):
            for suffix in (
                "agent-btn", "agent-menu", "agent-options",
                "model-btn", "model-menu", "model-options",
                "model-search", "model-refresh",
            ):
                self.assertIn('id="%s-%s"' % (prefix, suffix), self.html, "%s-%s" % (prefix, suffix))

    def test_legacy_profile_ui_is_gone(self):
        self.assertNotIn('id="yt-llm-provider"', self.html)
        self.assertNotIn('id="yt-llm-save"', self.html)
        self.assertNotIn("yt-llm-save", self.js)
        self.assertNotIn("saveLLM", self.js)

    def test_frontend_uses_the_workspace_registry(self):
        self.assertIn("/api/agents/availability", self.js)
        self.assertIn("/models", self.js)
        self.assertNotIn("DENNIS_YT_LLM", self.js)
        # Selections persist as {agent_id, model}, never a provider profile.
        self.assertIn("agent_id:", self.js)
        self.assertNotIn('provider: "', self.js)

    def test_status_payload_keys_match_the_new_backend(self):
        self.assertIn("agent_name", self.js)
        self.assertIn("hint_setup", self.js)


class CosmosDropdownContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = read("js", "cosmosState.js")
        cls.dashboard = read("js", "cosmosDashboard.js")
        cls.css = read("css", "cosmos.css")

    def test_state_tracks_llm_selections(self):
        self.assertIn("llmSelections", self.state)
        self.assertIn("llm_selections", self.state)
        self.assertIn("llmSelection: llmSelection", self.state)
        self.assertIn("setLLMSelection: setLLMSelection", self.state)

    def test_dashboard_renders_and_wires_per_division_rows(self):
        for needle in (
            "data-cosmos-llm-row",
            "data-cosmos-llm-toggle",
            "data-cosmos-llm-agent",
            "data-cosmos-llm-model",
            "data-cosmos-llm-search",
            "data-cosmos-llm-refresh",
            "data-cosmos-llm-inherit",
        ):
            self.assertIn(needle, self.dashboard, needle)
        self.assertIn("/api/agents/availability", self.dashboard)
        self.assertIn("/api/cosmos/llm/", self.dashboard)
        self.assertNotIn("DENNIS_YT_LLM", self.dashboard)

    def test_dashboard_css_styles_the_rows(self):
        for selector in (
            ".cosmos-llm-row",
            ".cosmos-llm-btn",
            ".cosmos-llm-menu",
            ".cosmos-llm-inherit",
        ):
            self.assertIn(selector, self.css, selector)


if __name__ == "__main__":
    unittest.main()
