"""Tests for the skill package compiler and the publication registry.

Run:  ../.venv/bin/python -m unittest tests.test_skill_compiler -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import skill_compiler
from backend import skill_registry
from backend import skill_tests


SKILL = """---
name: reference-short
description: Recreate a fast vertical explainer from a reference video using an evidence-grounded workflow.
---

# Reference Short

Executable workflow for producing a new vertical explainer in the same style and
format as the analysed reference, without reusing its content.

## Objective
Produce a 30-60 second vertical explainer on a new topic.

## When to use
Use when a new topic must be delivered in the analysed style.

## Inputs
- A new topic and audience.
- Optional footage or stills.

## Outputs
- One 1080x1920 MP4 at 30 fps.

## Required capabilities
- ffmpeg and python.

## Execution workflow
1. Write the hook from the extracted formula.
2. Map the narrative beats to the new topic.
3. Build the scene list and assets.
4. Render and check loudness.

## Production methodology
Deterministic FFmpeg composition with text overlays.

## Adaptation rules
Keep the style consistency rules in STYLE.md and the output configuration in
FORMAT.md; change only the topic, script and assets.

## Dependencies
See DEPENDENCIES.md; nothing is installed automatically.

## Validation
- Duration within 2% of plan.
- Captions match narration.

## Failure handling
If a scene asset is missing, stop and report the scene index.

## Style consistency
Follow STYLE.md.

## Format and output configurations
Follow FORMAT.md.

## Evidence and limitations
No causal performance claim is made.

## Sources
- Reference video.
"""


def draft(**overrides):
    base = {
        "name": "reference-short",
        "description": "Recreate a fast vertical explainer from a reference video.",
        "content": SKILL,
        "mode": "video",
        "objective": "reference",
        "sources": [{"kind": "youtube", "id": "vid-1",
                     "url": "https://youtu.be/vid-1", "version": "1"}],
        "created_at": "2026-11-12T00:00:00",
        "analysis": {
            "strategy": {"audience": "founders", "objective": "explain"},
            "hook": {"formula": "question then reveal"},
            "story": {"structure": [
                {"beat": "introduction", "start": 0, "end": 3,
                 "what": "cold open", "source": "observed", "confidence": "high"}]},
            "retention": {"pacing": "fast"},
            "psychology": [{"trigger": "curiosity", "how": "open loop",
                            "source": "hypothesis", "confidence": "low"}],
            "production_method": {"editing": "hard cuts"},
            "performance": {"observed": {"views": 10}, "hypotheses": [],
                            "limitations": []},
            "limitations": [],
        },
        "style_profile": {"style_id": "motion_graphics",
                          "style_name": "Motion graphics",
                          "confidence": "high", "color_palette": ["#101010"],
                          "consistency_rules": ["flat fills"]},
        "format_profile": {"format_id": "explainer", "format_name": "Explainer",
                           "confidence": "high", "platform": "youtube",
                           "placement": "shorts", "aspect_ratio": "9:16",
                           "resolution": "1080x1920", "fps": 30,
                           "duration_seconds": 45,
                           "output_configurations": [
                               {"name": "vertical", "aspect_ratio": "9:16",
                                "resolution": "1080x1920", "fps": 30,
                                "notes": "primary"}]},
        "production": {"stack": [
            {"id": "ffmpeg", "name": "FFmpeg", "kind": "mandatory",
             "why": "encode", "selected": True, "detected": True,
             "version": "6"},
        ]},
        "dependencies": {"version": 1, "generated_at": "2026-11-12T00:00:00",
                         "environment": {}, "dependencies": {},
                         "install_plan": [], "policy": {"auto_install": False}},
    }
    base.update(overrides)
    return base


class CompileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-package-")
        self.root = os.path.join(self.tmp, "package")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_compile_writes_expected_files(self):
        manifest, errors = skill_compiler.compile_package(draft(), self.root)
        self.assertEqual(errors, [])
        for rel in ("SKILL.md", "CONFIG.yaml", "STRATEGY.md", "STYLE.md",
                    "FORMAT.md", "PRODUCTION.md", "DEPENDENCIES.md",
                    "ANALYSIS.md", "examples/example-brief.md",
                    "tests/validation.md", "tests/replication.md",
                    "assets/README.md", "scripts/render.md",
                    "manifests/package.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.root, rel)), rel)
        self.assertEqual(manifest["entrypoint"], "SKILL.md")
        self.assertTrue(manifest["profiles"]["style"])
        self.assertTrue(manifest["profiles"]["format"])
        self.assertTrue(manifest["files"])

    def test_compiled_package_validates(self):
        skill_compiler.compile_package(draft(), self.root)
        self.assertEqual(skill_compiler.validate_package(self.root), [])

    def test_conditional_files_are_omitted(self):
        minimal = draft(analysis={}, style_profile={}, format_profile={},
                        production={}, dependencies={})
        manifest, errors = skill_compiler.compile_package(minimal, self.root)
        self.assertEqual(errors, [])
        self.assertFalse(os.path.exists(os.path.join(self.root, "STRATEGY.md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "STYLE.md")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "DEPENDENCIES.md")))
        self.assertEqual(manifest["profiles"]["style"], "")

    def test_config_is_parseable_yaml_like(self):
        skill_compiler.compile_package(draft(), self.root)
        with open(os.path.join(self.root, "CONFIG.yaml"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("schema: 1", text)
        self.assertIn("entrypoint: scripts/render.md", text)
        self.assertIn('name: reference-short', text)
        self.assertIn("auto_install: false", text)

    def test_validate_detects_a_tampered_file(self):
        skill_compiler.compile_package(draft(), self.root)
        path = os.path.join(self.root, "STYLE.md")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\ntampered\n")
        errors = skill_compiler.validate_package(self.root)
        self.assertTrue(any("hash mismatch" in error for error in errors))

    def test_validate_detects_missing_required_sections(self):
        weak = draft(content="---\nname: weak\ndescription: weak skill\n---\n\n"
                             "# Weak\n\nSTYLE.md FORMAT.md but no workflow.\n")
        skill_compiler.compile_package(weak, os.path.join(self.tmp, "weak"))
        errors = skill_compiler.validate_package(os.path.join(self.tmp, "weak"))
        self.assertTrue(any("missing section" in error for error in errors))

    def test_validate_detects_missing_config_and_manifest(self):
        weak = os.path.join(self.tmp, "broken")
        os.makedirs(weak)
        with open(os.path.join(weak, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(SKILL)
        errors = skill_compiler.validate_package(weak)
        self.assertTrue(any("CONFIG.yaml is missing" in error for error in errors))

    def test_validate_rejects_unsafe_manifest_paths(self):
        skill_compiler.compile_package(draft(), self.root)
        manifest_path = os.path.join(self.root, "manifests", "package.json")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        manifest["files"].append({"path": "../escape.md", "sha256": "0" * 64})
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        errors = skill_compiler.validate_package(self.root)
        self.assertTrue(any("unsafe path" in error for error in errors))

    def test_secret_pattern_blocks_credentials(self):
        leaky = draft(content=SKILL + "\napi_key = abcdef1234567890abcdef\n")
        skill_compiler.compile_package(leaky, os.path.join(self.tmp, "leaky"))
        errors = skill_compiler.validate_package(os.path.join(self.tmp, "leaky"))
        self.assertTrue(any("credential" in error for error in errors))

    def test_preview_lists_files_without_binaries(self):
        skill_compiler.compile_package(draft(), self.root)
        preview = skill_compiler.preview(self.root)
        paths = [item["path"] for item in preview["files"]]
        self.assertIn("SKILL.md", paths)
        self.assertIn("manifests/package.json", paths)
        self.assertGreater(preview["total_bytes"], 0)
        skill_file = next(item for item in preview["files"]
                          if item["path"] == "SKILL.md")
        self.assertIn("## Objective", skill_file["preview"])

    def test_iter_package_files_ignores_symlinks_and_hidden(self):
        skill_compiler.compile_package(draft(), self.root)
        hidden = os.path.join(self.root, ".hidden")
        with open(hidden, "w", encoding="utf-8") as fh:
            fh.write("x")
        outside = os.path.join(self.tmp, "outside.txt")
        with open(outside, "w", encoding="utf-8") as fh:
            fh.write("x")
        try:
            os.symlink(outside, os.path.join(self.root, "link.md"))
        except OSError:
            pass
        rels = [rel for rel, _path in skill_compiler.iter_package_files(self.root)]
        self.assertNotIn(".hidden", rels)
        self.assertNotIn("link.md", rels)


class StaticCheckTests(unittest.TestCase):
    def test_required_sections_gate_passes_clean_draft(self):
        result = skill_tests.static_checks(draft())
        self.assertTrue(result["passed"], result["errors"])
        self.assertTrue(any(check["name"] == "References STYLE.md"
                            for check in result["checks"]))

    def test_missing_section_fails(self):
        result = skill_tests.static_checks(draft(content="# Nothing\n"))
        self.assertFalse(result["passed"])
        self.assertTrue(result["errors"])

    def test_viral_claim_is_rejected(self):
        result = skill_tests.static_checks(
            draft(content=SKILL + "\nThis technique caused the success.\n"))
        self.assertFalse(result["passed"])

    def test_recommended_sections_do_not_gate(self):
        content = SKILL.replace("## Production methodology", "## Production notes")
        result = skill_tests.static_checks(draft(content=content))
        self.assertTrue(result["passed"], result["errors"])
        self.assertTrue(any(check["name"] == "Section: Production methodology"
                            and not check["passed"] and not check["required"]
                            for check in result["checks"]))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-registry-")
        self.path = os.path.join(self.tmp, "skill_registry.json")
        self.patch = mock.patch.object(skill_registry, "REGISTRY_FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_tracks_versions_and_provenance(self):
        first = skill_registry.register(
            "demo", "Skills/demo/SKILL.md", draft_id="sbd_1",
            sources=[{"id": "vid-1", "version": "1"}], files=9)
        self.assertEqual(first["current_version"], "1.0.0")
        second = skill_registry.register("demo", "Skills/demo/SKILL.md",
                                         draft_id="sbd_2", files=10,
                                         replaced=True)
        self.assertEqual(second["current_version"], "1.1.0")
        listing = skill_registry.list_skills()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["skills"][0]["versions"], 2)
        self.assertTrue(listing["skills"][0]["sources"])

    def test_next_version_without_history(self):
        self.assertEqual(skill_registry.next_version("missing"), "1.0.0")

    def test_corrupt_registry_is_treated_as_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("not json")
        self.assertEqual(skill_registry.list_skills()["count"], 0)
        record = skill_registry.register("recovered", "Skills/recovered/SKILL.md")
        self.assertEqual(record["current_version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
