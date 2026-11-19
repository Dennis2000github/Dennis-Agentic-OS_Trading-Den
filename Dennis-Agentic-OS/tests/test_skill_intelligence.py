"""Tests for reverse-engineering intelligence, classification and production.

Run:  ../.venv/bin/python -m unittest tests.test_skill_intelligence -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import skill_analysis
from backend import skill_classify
from backend import skill_evidence
from backend import skill_production


SOURCE = {
    "kind": "youtube", "id": "vid-1", "url": "https://youtu.be/vid-1",
    "title": "Reference", "metadata": {"duration_seconds": 60, "width": 1080,
                                       "height": 1920, "view_count": 900},
    "transcript": "hook line one two three", "timed_transcript": "[00:00] hook",
}


def evidence():
    return skill_evidence.normalize(
        SOURCE,
        technical={"duration_seconds": 60, "width": 1080, "height": 1920,
                   "fps": 30, "aspect": "9:16", "video_codec": "h264",
                   "audio_codec": "aac", "channels": 2, "sample_rate": 44100,
                   "container": "mp4"},
        scenes=[{"start": 0, "end": 3}, {"start": 3, "end": 30},
                {"start": 30, "end": 60}],
        frames=[{"index": 0, "t": 1.5, "ref": "frames/0000.jpg"}])


class ParseTests(unittest.TestCase):
    def test_parse_json_accepts_fences_and_prose(self):
        self.assertEqual(skill_analysis.parse_json('{"a": 1}'), {"a": 1})
        self.assertEqual(skill_analysis.parse_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(
            skill_analysis.parse_json('Here you go:\n{"a": {"b": 2}}\nThanks'),
            {"a": {"b": 2}})
        self.assertIsNone(skill_analysis.parse_json("not json at all"))
        self.assertIsNone(skill_analysis.parse_json(""))

    def test_confidence_and_source_coercion(self):
        self.assertEqual(skill_analysis._confidence(0.9), "high")
        self.assertEqual(skill_analysis._confidence(0.5), "medium")
        self.assertEqual(skill_analysis._confidence("nonsense"), "low")
        self.assertEqual(skill_analysis._source("OBSERVED"), "observed")
        self.assertEqual(skill_analysis._source("made up"), "hypothesis")


class ReverseNormalisationTests(unittest.TestCase):
    def test_labels_are_forced_onto_every_claim(self):
        data = {
            "strategy": {"audience": "founders", "claims": [
                {"claim": "fast pacing", "source": "observed", "confidence": "high"},
                {"claim": "it went viral", "source": "made-up", "confidence": 0.1},
            ]},
            "hook": {"formula": "question then reveal"},
            "story": {"structure": [
                {"beat": "introduction", "start": 0, "end": 3,
                 "what": "cold open", "source": "observed", "confidence": "high"},
                "not-an-object",
            ]},
            "retention": {"pacing": "very fast", "curiosity_loops": ["loop one"]},
            "psychology": [{"trigger": "curiosity", "how": "open loop"}],
            "production_method": {"editing": "hard cuts"},
            "performance": {
                "observed": {"views": "not-a-number"},
                "hypotheses": [{"claim": "short hook helped", "confidence": "low"}],
            },
            "limitations": ["no audio analysis"],
        }
        result = skill_analysis.normalize_reverse(data, evidence())
        self.assertEqual(len(result["strategy"]["claims"]), 2)
        self.assertEqual(result["strategy"]["claims"][1]["source"], "hypothesis")
        self.assertEqual(result["strategy"]["claims"][1]["confidence"], "low")
        self.assertEqual(len(result["story"]["structure"]), 1)
        self.assertEqual(result["story"]["structure"][0]["source"], "observed")
        self.assertEqual(result["psychology"][0]["trigger"], "curiosity")
        self.assertEqual(result["performance"]["observed"]["views"], 900)
        self.assertEqual(result["limitations"], ["no audio analysis"])

    def test_empty_input_is_still_a_valid_shape(self):
        result = skill_analysis.normalize_reverse(None)
        for key in ("strategy", "hook", "story", "retention", "psychology",
                    "production_method", "performance", "limitations"):
            self.assertIn(key, result)
        self.assertEqual(result["story"]["structure"], [])

    def test_timeline_consistency_flags_overrun(self):
        reverse = {"story": {"structure": [
            {"beat": "climax", "start": 10, "end": 400}]}}
        notes = skill_analysis.timeline_consistency(reverse, evidence())
        self.assertTrue(any("after the video" in note for note in notes))

    def test_reverse_prompt_forbids_causality(self):
        prompt = skill_analysis.reverse_prompt("EVIDENCE", title="t")
        self.assertIn("Never claim a video is viral", prompt)
        self.assertIn('"source"', prompt)


class StyleFormatTests(unittest.TestCase):
    def test_style_and_format_are_separate_objects(self):
        style = skill_classify.normalize_style(
            {"style_id": "2D Animation", "confidence": "high",
             "color_palette": ["#000000"], "consistency_rules": ["flat fills"]},
            evidence())
        fmt = skill_classify.normalize_format(
            {"format_id": "explainer", "placement": "shorts",
             "output_configurations": [{"name": "vertical", "fps": 30}]},
            evidence(), platform_hint="youtube")
        self.assertEqual(style["style_id"], "2d_animation")
        self.assertEqual(fmt["format_id"], "explainer")
        self.assertEqual(fmt["placement"], "shorts")
        self.assertEqual(fmt["platform"], "youtube")
        self.assertEqual(skill_classify.validate_profiles(style, fmt), [])
        self.assertNotIn("format_id", style)
        self.assertNotIn("style_id", fmt)

    def test_technical_values_override_model_guesses(self):
        fmt = skill_classify.normalize_format(
            {"format_id": "tutorial", "aspect_ratio": "1:1", "fps": 999,
             "resolution": "1x1", "duration_seconds": 1}, evidence())
        self.assertEqual(fmt["aspect_ratio"], "9:16")
        self.assertEqual(fmt["resolution"], "1080x1920")
        self.assertEqual(fmt["fps"], 30.0)
        self.assertEqual(fmt["duration_seconds"], 60)
        self.assertEqual(fmt["video_codec"], "h264")

    def test_unknown_ids_fall_back_without_crashing(self):
        style = skill_classify.normalize_style({"style_id": "not-a-style"})
        fmt = skill_classify.normalize_format({"format_id": "not-a-format",
                                               "placement": "not-a-placement"})
        self.assertEqual(style["style_id"], "hybrid")
        self.assertEqual(style["confidence"], "low")
        self.assertEqual(fmt["format_id"], "explainer")
        self.assertEqual(fmt["placement"], "unknown")

    def test_output_configurations_are_always_multiple(self):
        configs = skill_classify.default_outputs(1080, 1920, 30)
        self.assertGreaterEqual(len(configs), 2)
        self.assertEqual(configs[0]["aspect_ratio"], "9:16")
        self.assertTrue(any(item["aspect_ratio"] == "16:9" for item in configs))

    def test_profiles_must_not_collide(self):
        style = {"style_id": "explainer"}
        fmt = {"format_id": "cartoon"}
        errors = skill_classify.validate_profiles(style, fmt)
        self.assertTrue(errors)

    def test_markdown_renderers(self):
        style = skill_classify.normalize_style(
            {"style_id": "cartoon", "style_name": "Cartoon",
             "consistency_rules": ["flat shading"]})
        fmt = skill_classify.normalize_format({"format_id": "explainer"})
        style_md = skill_classify.style_text(style)
        format_md = skill_classify.format_text(fmt)
        self.assertIn("# Video Style", style_md)
        self.assertIn("flat shading", style_md)
        self.assertIn("# Video Format", format_md)
        self.assertIn("Output configurations", format_md)


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "os": {"name": "Darwin", "version": "25", "arch": "x86_64"},
            "cpu": {"cores": 8}, "ram_gb": 16.0,
            "storage": {"free_gb": 50.0},
            "gpu": {"available": False, "name": ""},
            "python": {"path": "/usr/bin/python3", "version": "3.14.5"},
            "node": {"path": "", "version": ""},
            "ffmpeg": {"path": "/usr/bin/ffmpeg", "version": "ffmpeg 6"},
            "ffprobe": {"path": "/usr/bin/ffprobe", "version": "ffprobe 6"},
            "chrome": {"path": "", "version": ""},
            "package_managers": ["pip"], "venvs": [], "models": [],
            "network": {"online": None},
            "providers": {},
        }

    def test_smallest_stack_is_selected_and_detected(self):
        style = {"style_id": "cartoon"}
        fmt = {"format_id": "explainer"}
        stack = skill_production.resolve_stack(style, fmt, self.env)
        selected = [item["id"] for item in stack if item["selected"]]
        self.assertIn("ffmpeg", selected)
        self.assertIn("python", selected)
        self.assertIn("canvas_2d", selected)
        self.assertNotIn("blender", selected)
        for item in stack:
            if item["id"] == "ffmpeg":
                self.assertTrue(item["detected"])
            if item["id"] == "remotion":
                self.assertFalse(item["detected"])

    def test_tutorial_adds_headless_chrome(self):
        stack = skill_production.resolve_stack({"style_id": "live_action"},
                                               {"format_id": "tutorial"}, self.env)
        selected = [item["id"] for item in stack if item["selected"]]
        self.assertIn("headless_chrome", selected)

    def test_manifest_buckets_and_policy(self):
        production = skill_production.build_production(
            {"style_id": "2d_animation"}, {"format_id": "explainer"}, self.env)
        manifest = skill_production.dependency_manifest(production, self.env)
        buckets = manifest["dependencies"]
        self.assertTrue(buckets["mandatory"])
        self.assertTrue(any(item["id"] == "canvas_2d"
                            for item in buckets["conditional"]))
        self.assertTrue(any(item["id"] == "video_provider"
                            for item in buckets["external"]))
        self.assertFalse(manifest["policy"]["auto_install"])
        self.assertFalse(manifest["policy"]["system_wide_changes"])
        for step in manifest["install_plan"]:
            self.assertFalse(step["automatic"])
            if step["estimated_size_mb"] > 100:
                self.assertTrue(step["requires_approval"])

    def test_build_production_documents_pipeline(self):
        production = skill_production.build_production(
            {"style_id": "3d_animation"}, {"format_id": "advertisement"}, self.env)
        self.assertGreaterEqual(len(production["rendering_pipeline"]), 5)
        self.assertTrue(production["quality_control"])
        text = skill_production.production_text(production)
        self.assertIn("# Production Technology", text)
        self.assertIn("Rendering pipeline", text)
        deps_text = skill_production.dependencies_text(
            skill_production.dependency_manifest(production, self.env))
        self.assertIn("Installation plan", deps_text)
        self.assertIn("Nothing is installed automatically", deps_text)

    def test_detect_environment_shape_with_mocked_probes(self):
        with mock.patch.object(skill_production, "_gpu",
                               return_value={"available": False, "name": ""}), \
                mock.patch.object(skill_production, "_ram_gb", return_value=8.0), \
                mock.patch.object(skill_production, "_version", return_value="v1"), \
                mock.patch.object(skill_production, "_read_env_keys",
                                  return_value={"GEMINI_API_KEY": True}):
            env = skill_production.detect_environment(force=True)
        for key in ("os", "cpu", "ram_gb", "storage", "gpu", "python", "node",
                    "ffmpeg", "ffprobe", "package_managers", "providers"):
            self.assertIn(key, env)
        self.assertEqual(env["ram_gb"], 8.0)
        self.assertEqual(env["python"]["version"],
                         "%d.%d.%d" % sys.version_info[:3])


if __name__ == "__main__":
    unittest.main()
