"""Registry integration for host-detected agent CLIs (stdlib unittest only).

Run:  ./.venv/bin/python -m unittest tests.test_agent_integration -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


def _detected(agent_id, name, binary):
    return {
        "id": agent_id,
        "name": name,
        "provider": "test",
        "version": "Installed",
        "icon_char": name[0],
        "color_theme": "blue",
        "binary_path": "/usr/local/bin/" + binary,
        "type": "Coding CLI",
        "detected": True,
        "registered": False,
    }


class AgentIntegrationTests(unittest.TestCase):
    def _integrate(self, registered, detected, ids=None):
        saved = {}

        def fake_save(agents):
            saved["agents"] = agents

        with mock.patch.object(app, "load_agents",
                               lambda: [dict(a) for a in registered]), \
                mock.patch.object(app, "save_agents", fake_save), \
                mock.patch.object(app.dennis_agents, "detect_installed",
                                  lambda **kw: list(detected)):
            result = app.integrate_detected_agents(ids)
        return result, saved

    def test_adds_detected_cli_to_registry(self):
        result, saved = self._integrate(
            [{"id": "antigravity", "name": "Antigravity", "binary_path": "/usr/local/bin/agy"}],
            [_detected("claude-code", "Claude Code", "claude")],
        )
        self.assertEqual(result["added"], ["claude-code"])
        self.assertEqual(result["missing"], [])
        entry = saved["agents"][-1]
        self.assertEqual(entry["id"], "claude-code")
        self.assertEqual(entry["binary_path"], "/usr/local/bin/claude")
        self.assertEqual(entry["status"], "Connected")
        self.assertNotIn("detected", entry)

    def test_dedupes_registered_binary_alias(self):
        result, _ = self._integrate(
            [{"id": "codex-cli", "name": "Codex CLI", "binary_path": "/usr/local/bin/codex"}],
            [_detected("codex", "Codex CLI", "codex"),
             _detected("claude-code", "Claude Code", "claude")],
        )
        self.assertEqual(result["added"], ["claude-code"])

    def test_already_integrated_is_idempotent(self):
        result, saved = self._integrate(
            [{"id": "claude-code", "name": "Claude Code",
              "binary_path": "/usr/local/bin/claude"}],
            [_detected("claude-code", "Claude Code", "claude")],
        )
        self.assertEqual(result["added"], [])
        self.assertEqual(saved, {})

    def test_missing_cli_is_reported(self):
        result, saved = self._integrate(
            [{"id": "antigravity", "name": "Antigravity",
              "binary_path": "/usr/local/bin/agy"}],
            [],
            ids=["not-installed"],
        )
        self.assertEqual(result["added"], [])
        self.assertEqual(result["missing"], ["not-installed"])
        self.assertEqual(saved, {})


if __name__ == "__main__":
    unittest.main()
