"""Tests for replication testing and the anti-copy guard.

Run:  ../.venv/bin/python -m unittest tests.test_skill_replication -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import skill_tests


SOURCE_TEXT = (
    "The hook opened on a dark room and asked why every founder wastes the "
    "first ninety seconds of their launch video explaining the product "
    "instead of the problem the viewer already feels every single morning."
)

INDEPENDENT = {
    "topic": "why your portfolio review is too slow",
    "hook": "Open on a frozen spreadsheet and ask why the review takes a week.",
    "structure": ["cold open", "problem", "method", "payoff"],
    "visual_identity": ["flat fills", "one accent colour"],
    "editing": ["hard cuts every two seconds"],
    "dependencies": ["ffmpeg", "python"],
    "production_plan": ["write script", "render", "check loudness"],
    "output_spec": "1080x1920 30fps",
    "quality_checks": ["captions synced", "duration within 2%"],
    "criteria": {
        "hook_formula": True, "narrative_structure": True,
        "visual_identity": True, "editing_methodology": True,
        "dependency_selection": True, "production_plan": True,
        "output_format": True, "quality_requirements": True,
    },
    "evidence": ["methodology applied to a new topic"],
}


class OverlapTests(unittest.TestCase):
    def test_verbatim_copy_is_detected(self):
        self.assertGreater(skill_tests.content_overlap(SOURCE_TEXT, SOURCE_TEXT),
                           0.9)

    def test_independent_answer_has_no_overlap(self):
        answer = " ".join(str(INDEPENDENT[key]) for key in
                          ("hook", "structure", "visual_identity", "editing"))
        self.assertLess(skill_tests.content_overlap(answer, SOURCE_TEXT), 0.15)

    def test_short_text_is_safe(self):
        self.assertEqual(skill_tests.content_overlap("tiny answer", SOURCE_TEXT), 0.0)
        self.assertEqual(skill_tests.content_overlap(SOURCE_TEXT, ""), 0.0)


class EvaluateTests(unittest.TestCase):
    def test_complete_independent_answer_passes(self):
        result = skill_tests.evaluate_replication(
            INDEPENDENT, source_text=SOURCE_TEXT,
            topic="why your portfolio review is too slow")
        self.assertTrue(result["passed"], result)
        self.assertLess(result["content_overlap"], 0.15)
        self.assertEqual(len(result["criteria"]),
                         len(skill_tests.REPLICATION_CRITERIA))

    def test_missing_criteria_fail(self):
        broken = dict(INDEPENDENT)
        broken["criteria"] = {"hook_formula": True}
        result = skill_tests.evaluate_replication(broken, source_text=SOURCE_TEXT)
        self.assertFalse(result["passed"])
        failed = [item["id"] for item in result["criteria"] if not item["passed"]]
        self.assertIn("narrative_structure", failed)
        self.assertIn("production_plan", failed)

    def test_copying_the_reference_fails_independence(self):
        copied = dict(INDEPENDENT)
        copied["hook"] = SOURCE_TEXT
        copied["structure"] = SOURCE_TEXT
        result = skill_tests.evaluate_replication(copied, source_text=SOURCE_TEXT)
        self.assertFalse(result["passed"])
        independence = next(item for item in result["criteria"]
                            if item["id"] == "content_independence")
        self.assertFalse(independence["passed"])
        self.assertGreater(result["content_overlap"], 0.15)

    def test_garbage_input_never_passes(self):
        self.assertFalse(skill_tests.evaluate_replication(None)["passed"])
        self.assertFalse(skill_tests.evaluate_replication({})["passed"])


class PromptTests(unittest.TestCase):
    def test_prompt_states_the_new_topic_and_forbids_reuse(self):
        draft = {
            "analysis": {
                "strategy": {"audience": "founders"},
                "hook": {"formula": "question then reveal"},
                "story": {"structure": [
                    {"beat": "introduction", "start": 0, "end": 3}]},
            },
            "style_profile": {"style_name": "Motion graphics",
                              "visual_language": "flat shapes"},
            "format_profile": {"format_name": "Explainer",
                               "output_configurations": [{"name": "vertical"}]},
            "production": {"stack": [{"name": "FFmpeg", "selected": True}]},
        }
        prompt = skill_tests.replication_prompt(draft, "a new topic")
        self.assertIn("a new topic", prompt)
        self.assertIn("Do not reuse any scene", prompt)
        self.assertIn("question then reveal", prompt)
        self.assertIn("Motion graphics", prompt)
        self.assertIn("FFmpeg", prompt)
        self.assertIn("criteria", prompt)


if __name__ == "__main__":
    unittest.main()
