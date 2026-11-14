"""Unit tests for the COSMOS agent registry and prompt contracts.

Run:  python3 -m unittest tests.test_cosmos_registry
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import cosmos_common as common  # noqa: E402
from backend import cosmos_registry as registry  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_five_agents_in_canonical_order(self):
        agents = registry.list_agents()
        self.assertEqual([a["id"] for a in agents], list(registry.AGENT_ORDER))
        self.assertEqual(len(agents), 5)
        for agent in agents:
            self.assertTrue(agent["name"])
            self.assertTrue(agent["role"])
            self.assertTrue(agent["tagline"])
            self.assertTrue(agent["description"])

    def test_every_agent_has_both_permanent_divisions(self):
        for agent in registry.list_agents():
            self.assertEqual(set(agent["divisions"]), {"ads", "youtube"})
            self.assertEqual(set(agent["capabilities"]), {"ads", "youtube"})
            self.assertEqual(agent["divisions"]["ads"]["title"], "Ads Intelligence")
            self.assertEqual(agent["divisions"]["youtube"]["title"], "YouTube Intelligence")
            self.assertGreaterEqual(len(agent["divisions"]["ads"]["responsibilities"]), 3)
            self.assertGreaterEqual(len(agent["divisions"]["youtube"]["responsibilities"]), 3)

    def test_expected_capability_kinds(self):
        expected = {
            "andromeda": "recommendations",
            "polaris": "analysis",
            "pleiades": "creative",
            "quasar": "research",
            "helios": "performance",
        }
        for agent_id, kind in expected.items():
            for division in ("ads", "youtube"):
                self.assertEqual(registry.capability(agent_id, division)["kind"], kind)

    def test_discovery_contract_is_division_specific(self):
        ads = registry.capability("andromeda", "ads")
        youtube = registry.capability("andromeda", "youtube")
        self.assertIn("name", ads["item_contract"])
        self.assertEqual(ads["item_contract"]["affiliate_status"], "unvalidated")
        self.assertIn("title", youtube["item_contract"])
        self.assertIn("pillar", youtube["item_contract"])
        self.assertEqual(ads["min_items"], 5)
        self.assertEqual(youtube["min_items"], 5)

    def test_system_prompt_carries_identity_division_and_honesty(self):
        prompt = registry.system_prompt("pleiades", "youtube")
        self.assertIn("PLEIADES", prompt)
        self.assertIn("YouTube Intelligence", prompt)
        self.assertIn("strict JSON", prompt)
        self.assertIn("Never invent", prompt)

    def test_unknown_agent_and_division_raise(self):
        with self.assertRaises(common.CosmosError) as ctx:
            registry.get_agent("nope")
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(common.CosmosError) as ctx:
            registry.capability("andromeda", "email")
        self.assertEqual(ctx.exception.code, "unknown_division")

    def test_public_agent_does_not_leak_internal_prompt_instructions(self):
        agent = registry.public_agent(registry.get_agent("andromeda"))
        self.assertNotIn("instructions", agent["capabilities"]["ads"])
        self.assertNotIn("item_contract", agent["capabilities"]["ads"])


if __name__ == "__main__":
    unittest.main()
