"""Static contract tests for the Skill Builder UI wiring.

These guard the review screen and the video-mode composer without needing a
browser: every element id the frontend looks up must exist in index.html, every
review tab must have a panel, and the progress stages must match the backend.

Run:  ../.venv/bin/python -m unittest tests.test_skill_builder_ui -v
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend import skill_builder as sb


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


class SkillBuilderUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read("index.html")
        cls.js = read("js", "skill-builder.js")
        start = cls.html.index('id="skill-builder-view"')
        end = cls.html.index("<!-- PLUGINS", start)
        cls.view = cls.html[start:end]

    def test_three_modes_are_offered(self):
        for mode in ("source", "video", "templates"):
            self.assertIn('data-sb-mode="%s"' % mode, self.view)
        self.assertIn("Video Reverse Engineering", self.view)

    def test_video_controls_are_present_and_enabled(self):
        for element in ("sb-character-url", "sb-frames", "sb-replicate",
                        "sb-replication-topic", "sb-jobs", "sb-jobs-refresh"):
            self.assertIn('id="%s"' % element, self.view, element)
        eyes = re.search(r'<input id="sb-eyes"[^>]*>', self.view)
        self.assertIsNotNone(eyes)
        self.assertNotIn("disabled", eyes.group(0))
        for value in ("tutorial", "reference"):
            self.assertIn('name="sb-objective" value="%s"' % value, self.view)

    def test_review_tabs_each_have_a_panel(self):
        tabs = set(re.findall(r'data-sb-rtab="([^"]+)"', self.view))
        panels = set(re.findall(r'data-sb-rpanel="([^"]+)"', self.view))
        self.assertEqual(tabs, panels)
        self.assertEqual(tabs, {"skill", "analysis", "style", "format",
                                "production", "dependencies", "tests",
                                "package"})
        for element in ("sb-review-analysis", "sb-review-style",
                        "sb-review-format", "sb-review-production",
                        "sb-review-dependencies", "sb-review-tests",
                        "sb-package-list", "sb-package-preview"):
            self.assertIn('id="%s"' % element, self.view, element)

    def test_every_referenced_id_exists_or_is_injected(self):
        referenced = set(re.findall(r'\$\("([^"]+)"\)', self.js))
        # The FreeLLMAPI setup form is rendered from JS, not static HTML.
        injected = {name for name in referenced if name.startswith("sb-free-")}
        missing = sorted(referenced - injected
                         - set(re.findall(r'id="([^"]+)"', self.html)))
        self.assertEqual(missing, [])

    def test_frontend_progress_stages_match_the_backend(self):
        block = re.search(r"var STATE_STAGES = \[(.*?)\];", self.js, re.S)
        self.assertIsNotNone(block)
        stages = re.findall(r'"([^"]+)"', block.group(1))
        self.assertEqual(stages, sb.STAGES)

    def test_job_requests_carry_the_video_options(self):
        for key in ("extract_frames", "replicate", "replication_topic",
                    "character_image_url"):
            self.assertIn(key + ":", self.js, key)
        for path in ("/api/skill-builder/jobs",
                     "/api/skill-builder/package?draft="):
            self.assertIn(path, self.js)

    def test_cache_busting_covers_the_new_script(self):
        self.assertIn('js/skill-builder.js?v=20261112-video-reverse', self.html)


if __name__ == "__main__":
    unittest.main()
