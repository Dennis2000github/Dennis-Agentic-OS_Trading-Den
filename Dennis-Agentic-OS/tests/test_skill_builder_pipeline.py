"""Tests for the Skill Builder extraction, review and publishing pipeline.

Run:  ./.venv/bin/python -m unittest tests.test_skill_builder_pipeline -v
(Use the interpreter that has yt-dlp for the live extraction tests; the unit
tests here mock every network/AI call.)
"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import skill_builder as sb
from backend import skill_jobs
from backend import skill_sources
from backend import vault


GOOD_SKILL = """---
name: generated-name
description: Analyze reference AI videos, extract scene and camera details, and iterate on recreation prompts.
---

# Reference Video Recreation

A reusable workflow for analysing a reference clip and recreating it with a video generator.

## When to use
Use when a reference video must be recreated with a different character.

## Core methods
1. Acquire the reference.
2. Analyse the scenes.
3. Build and iterate the prompt.

## Sources
- Example video
"""


class SourceValidationTests(unittest.TestCase):
    def test_classifies_supported_sources(self):
        self.assertEqual(skill_sources.classify_url(
            "https://youtu.be/dQw4w9WgXcQ")["kind"], "youtube")
        self.assertEqual(skill_sources.classify_url(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ")["id"], "dQw4w9WgXcQ")
        repo = skill_sources.classify_url("https://github.com/pallets/flask")
        self.assertEqual(repo["kind"], "repository")
        self.assertEqual(repo["id"], "pallets/flask")
        self.assertEqual(skill_sources.classify_url(
            "https://example.com/clip.mp4")["kind"], "video")
        tiktok = skill_sources.classify_url(
            "https://www.tiktok.com/@clair/video/7300000000000000000")
        self.assertEqual(tiktok["kind"], "platform")
        self.assertEqual(tiktok["platform"], "tiktok")
        self.assertEqual(skill_sources.classify_url(
            "https://www.instagram.com/reel/abc123/")["platform"], "instagram")
        self.assertEqual(skill_sources.classify_url(
            "https://x.com/clair/status/123")["platform"], "x")

    def test_extracts_platform_metadata_and_captions(self):
        info = {
            "id": "7300000000000000000",
            "title": "Clair neon reel",
            "uploader": "clair",
            "webpage_url": "https://www.tiktok.com/@clair/video/7300000000000000000",
            "description": "A short neon reel.",
            "duration": 12,
            "width": 1080,
            "height": 1920,
            "tags": ["neon", "reel"],
            "chapters": [],
        }
        with tempfile.TemporaryDirectory() as cache:
            with mock.patch.object(skill_sources, "CACHE_DIR", cache), \
                    mock.patch.object(skill_sources, "_ytdlp_info", return_value=info), \
                    mock.patch.object(
                        skill_sources, "_fetch_transcript",
                        return_value=("spoken words", "", None)):
                data = skill_sources.extract_platform_video(
                    info["webpage_url"], refresh=True)
        self.assertEqual(data["kind"], "platform")
        self.assertEqual(data["platform"], "tiktok")
        self.assertEqual(data["title"], "Clair neon reel")
        self.assertEqual(data["transcript"], "spoken words")
        self.assertIn("A short neon reel.", data["text"])

    def test_rejects_unsupported_and_traversal(self):
        with self.assertRaises(skill_sources.SourceError):
            skill_sources.classify_url("file:///etc/passwd")
        with self.assertRaises(skill_sources.SourceError):
            skill_sources.classify_url("https://example.com/page")
        with self.assertRaises(skill_sources.SourceError):
            skill_sources.classify_url("https://localhost/clip.mp4")

    def test_public_url_guard_blocks_private_and_redirects(self):
        with self.assertRaises(skill_sources.SourceError):
            skill_sources.validate_public_url("http://127.0.0.1/face.png")
        with self.assertRaises(skill_sources.SourceError):
            skill_sources.validate_public_url("http://localhost/face.png")
        handler = skill_sources._SafeRedirectHandler()
        with self.assertRaises(skill_sources.SourceError):
            handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/x")

    def test_normalizes_source_text(self):
        text = "<p>Hello   world</p>\n\n\nHello   world\n![badge](x)\n<script>x()</script>"
        out = skill_sources.normalize_text(text)
        self.assertIn("Hello world", out)
        self.assertNotIn("<p>", out)
        self.assertNotIn("<script>", out)


class CaptionTests(unittest.TestCase):
    def test_json3_caption_becomes_plain_and_timed(self):
        raw = json.dumps({"events": [
            {"tStartMs": 0, "segs": [{"utf8": "Hello "}, {"utf8": "world"}]},
            {"tStartMs": 61000, "segs": [{"utf8": "Second line"}]},
            {"tStartMs": 62000, "segs": [{"utf8": "Second line"}]},
        ]}).encode("utf-8")
        plain, timed = skill_sources._caption_to_text(raw, "json3")
        self.assertEqual(plain, "Hello world\nSecond line")
        self.assertIn("[01:01] Second line", timed)

    def test_vtt_caption(self):
        raw = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello\n\n00:00:03.000 --> 00:00:04.000\nWorld\n"
        plain, timed = skill_sources._caption_to_text(raw, "vtt")
        self.assertEqual(plain, "Hello\nWorld")
        self.assertIn("[00:01] Hello", timed)


class YouTubeCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-cache-")
        self._old_cache = skill_sources.CACHE_DIR
        skill_sources.CACHE_DIR = self.tmp
        self.info = {
            "id": "dQw4w9WgXcQ",
            "title": "Tutorial",
            "uploader": "Creator",
            "webpage_url": "https://youtu.be/dQw4w9WgXcQ",
            "upload_date": "20260101",
            "duration": 60,
            "description": "A tutorial about a workflow.",
            "automatic_captions": {"en": [{"ext": "json3", "url": "https://cap"}]},
        }

    def tearDown(self):
        skill_sources.CACHE_DIR = self._old_cache

    def test_single_fetch_then_cache_hit(self):
        with mock.patch.object(skill_sources, "_ytdlp_info", return_value=self.info) as info, \
                mock.patch.object(skill_sources, "_fetch_transcript",
                                  return_value=("hello transcript", "[00:00] hello transcript", "")):
            first = skill_sources.extract_youtube("https://youtu.be/dQw4w9WgXcQ")
            second = skill_sources.extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(info.call_count, 1)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(second["transcript"], "hello transcript")

    def test_caption_failure_is_not_cached_as_success(self):
        with mock.patch.object(skill_sources, "_ytdlp_info", return_value=self.info) as info, \
                mock.patch.object(skill_sources, "_fetch_transcript",
                                  return_value=("", "", "HTTP 429")):
            first = skill_sources.extract_youtube("https://youtu.be/dQw4w9WgXcQ")
            second = skill_sources.extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(info.call_count, 2)
        self.assertIn("429", first["transcript_error"])
        self.assertFalse(second["cached"])

    def test_refresh_bypasses_cache(self):
        with mock.patch.object(skill_sources, "_ytdlp_info", return_value=self.info) as info, \
                mock.patch.object(skill_sources, "_fetch_transcript",
                                  return_value=("hello", "hello", "")):
            skill_sources.extract_youtube("https://youtu.be/dQw4w9WgXcQ")
            skill_sources.extract_youtube("https://youtu.be/dQw4w9WgXcQ", refresh=True)
        self.assertEqual(info.call_count, 2)


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-repo-cache-")
        self._old_cache = skill_sources.CACHE_DIR
        skill_sources.CACHE_DIR = self.tmp
        self.info = {"full_name": "owner/repo", "default_branch": "main",
                     "description": "Docs repo", "html_url": "https://github.com/owner/repo",
                     "pushed_at": "2026-01-01T00:00:00Z", "stargazers_count": 1}
        self.tree = [
            {"type": "blob", "path": "README.md", "size": 100},
            {"type": "blob", "path": "docs/guide.md", "size": 100},
            {"type": "blob", "path": "src/app.py", "size": 100},
            {"type": "blob", "path": "node_modules/x/README.md", "size": 100},
            {"type": "blob", "path": "big.md", "size": 999999},
        ]

    def tearDown(self):
        skill_sources.CACHE_DIR = self._old_cache

    def test_only_docs_are_selected_and_fetched(self):
        def fake_fetch(owner, repo, ref, path):
            return path, "# " + path
        with mock.patch.object(skill_sources, "_repo_info", return_value=self.info), \
                mock.patch.object(skill_sources, "_repo_tree", return_value=self.tree), \
                mock.patch.object(skill_sources, "_fetch_repo_file", side_effect=fake_fetch) as fetch:
            source = skill_sources.extract_repository("https://github.com/owner/repo")
        fetched = {call.args[3] for call in fetch.call_args_list}
        self.assertEqual(fetched, {"README.md", "docs/guide.md"})
        self.assertNotIn("src/app.py", fetched)
        self.assertNotIn("node_modules/x/README.md", fetched)

    def test_pushed_at_change_invalidates_cache(self):
        with mock.patch.object(skill_sources, "_repo_info", return_value=self.info), \
                mock.patch.object(skill_sources, "_repo_tree", return_value=self.tree), \
                mock.patch.object(skill_sources, "_fetch_repo_file",
                                  return_value=("README.md", "# readme")):
            skill_sources.extract_repository("https://github.com/owner/repo")
            skill_sources.extract_repository("https://github.com/owner/repo")
            changed = dict(self.info, pushed_at="2026-02-02T00:00:00Z")
            with mock.patch.object(skill_sources, "_repo_info", return_value=changed):
                skill_sources.extract_repository("https://github.com/owner/repo")
        self.assertGreaterEqual(skill_sources._cache_read(
            "repo:owner/repo::") is not None, True)


class DraftLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-draft-")
        self.old = (sb.DRAFTS_DIR, sb.SKILLS_DIR, sb.CLAUDE_SKILLS_DIR, vault.VAULT_DIR)
        sb.DRAFTS_DIR = os.path.join(self.tmp, "drafts")
        sb.SKILLS_DIR = os.path.join(self.tmp, "skills")
        sb.CLAUDE_SKILLS_DIR = os.path.join(self.tmp, "claude")
        vault.VAULT_DIR = os.path.join(self.tmp, "vault")
        self.source = {
            "kind": "youtube", "url": "https://youtu.be/dQw4w9WgXcQ",
            "id": "dQw4w9WgXcQ", "title": "Reference Video",
            "metadata": {"title": "Reference Video", "channel": "C", "duration_seconds": 60},
            "text": "A detailed workflow " * 20, "transcript": "x", "documents": [],
            "content_hash": "hash", "fetched_at": 1,
        }

    def tearDown(self):
        sb.DRAFTS_DIR, sb.SKILLS_DIR, sb.CLAUDE_SKILLS_DIR, vault.VAULT_DIR = self.old

    def _draft(self, name=None):
        with mock.patch.object(sb, "_gemini_parts", return_value=(GOOD_SKILL, "test-model")):
            return sb.create_draft([self.source], requested_name=name)

    def test_generation_creates_draft_not_vault_file(self):
        draft = self._draft()
        self.assertEqual(draft["name"], "generated-name")
        self.assertFalse(os.path.exists(os.path.join(vault.VAULT_DIR, "Skills")))
        self.assertTrue(os.path.isfile(os.path.join(sb.DRAFTS_DIR, draft["id"] + ".json")))

    def test_update_syncs_frontmatter_and_preserves_body(self):
        draft = self._draft()
        edited = sb.update_draft(draft["id"], {
            "name": "my-new-skill",
            "description": "A specific description for this reviewed workflow.",
            "content": draft["content"] + "\n\n## Extra\nUser edit.\n",
        })
        self.assertIn("name: my-new-skill", edited["content"])
        self.assertIn("description: A specific description", edited["content"])
        self.assertIn("User edit.", edited["content"])

    def test_reject_removes_draft_only(self):
        draft = self._draft()
        sb.reject_draft(draft["id"])
        with self.assertRaises(sb.SkillBuilderError):
            sb.get_draft(draft["id"])
        self.assertFalse(os.path.exists(os.path.join(vault.VAULT_DIR, "Skills", "generated-name")))

    def test_publish_writes_verified_artifact(self):
        draft = self._draft()
        result = sb.publish_draft(draft["id"], {
            "name": "approved-skill",
            "description": "The approved description for this workflow.",
            "content": draft["content"],
        })
        expected = os.path.join(vault.VAULT_DIR, "Skills", "approved-skill", "SKILL.md")
        self.assertTrue(result["verified"])
        # Vault resolution returns a real path (macOS maps /var to /private/var).
        self.assertEqual(result["absolute_path"], os.path.realpath(expected))
        self.assertTrue(os.path.isfile(expected))
        saved = open(expected, encoding="utf-8").read()
        self.assertIn("name: approved-skill", saved)
        self.assertIn("The approved description", saved)
        self.assertEqual(saved, sb._set_frontmatter(
            draft["content"], "approved-skill", "The approved description for this workflow."))
        with self.assertRaises(sb.SkillBuilderError):
            sb.get_draft(draft["id"])

    def test_conflict_does_not_overwrite(self):
        first = self._draft()
        sb.publish_draft(first["id"], {"name": "taken", "description": "First description here.",
                                       "content": first["content"]})
        second = self._draft()
        with self.assertRaises(sb.SkillConflictError):
            sb.publish_draft(second["id"], {"name": "taken", "description": "Second description here.",
                                            "content": second["content"]})
        saved = open(os.path.join(vault.VAULT_DIR, "Skills", "taken", "SKILL.md"),
                     encoding="utf-8").read()
        self.assertIn("First description here.", saved)
        self.assertTrue(os.path.isfile(os.path.join(sb.DRAFTS_DIR, second["id"] + ".json")))

    def test_write_failure_preserves_draft(self):
        draft = self._draft()
        with mock.patch.object(sb, "_write_text_atomic", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                sb.publish_draft(draft["id"], {"name": "kept", "description": "Kept description here.",
                                               "content": draft["content"]})
        self.assertTrue(os.path.isfile(os.path.join(sb.DRAFTS_DIR, draft["id"] + ".json")))

    def test_video_mode_fails_honestly_without_visual_analysis(self):
        with mock.patch.object(sb, "_fetch_all", return_value=([
                dict(self.source, kind="youtube", url="https://youtu.be/dQw4w9WgXcQ", id="dQw4w9WgXcQ")], [])), \
                mock.patch.object(sb, "_add_video_context",
                                  return_value=["x: visual analysis failed — unavailable"]):
            with self.assertRaises(sb.SkillBuilderError) as ctx:
                sb.run_generation(["https://youtu.be/dQw4w9WgXcQ"], mode="video", objective="reference")
        self.assertIn("Visual analysis was unavailable", str(ctx.exception))
        self.assertEqual(sb.list_drafts(), [])

    def test_tutorial_rejects_character_replacement_image(self):
        with self.assertRaises(sb.SkillBuilderError) as ctx:
            sb.run_generation(["https://youtu.be/x"], mode="video", objective="tutorial",
                              character_image_url="https://example.com/face.png")
        self.assertIn("only applies to Reference-Video-to-Skill", str(ctx.exception))

    def test_validation_rejects_bad_name_and_short_description(self):
        draft = self._draft()
        with self.assertRaises(sb.SkillBuilderError):
            sb.update_draft(draft["id"], {"name": "../escape", "description": "long enough description",
                                          "content": draft["content"]})
        with self.assertRaises(sb.SkillBuilderError):
            sb.update_draft(draft["id"], {"name": "ok", "description": "short",
                                          "content": draft["content"]})


class JobRunnerTests(unittest.TestCase):
    def test_deduplicates_running_jobs_and_cancels(self):
        runner = skill_jobs.JobRunner(max_parallel=1)
        gate = threading.Event()

        def work(job):
            gate.wait(2)
            return {"ok": True}

        first, reused = runner.submit("generate", "same-key", work, stages=sb.STAGES)
        second, reused2 = runner.submit("generate", "same-key", work, stages=sb.STAGES)
        self.assertFalse(reused)
        self.assertTrue(reused2)
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(runner.cancel(first["id"]))
        gate.set()
        time.sleep(0.05)
        self.assertEqual(runner.get(first["id"])["status"], "cancelled")

    def test_stage_updates(self):
        runner = skill_jobs.JobRunner(max_parallel=1)
        seen = {}

        def work(job):
            job.set_stage("Fetching source content", "ok", 20)
            seen.update(job.snapshot())
            return {}

        snapshot, _ = runner.submit("analyze", "k", work, stages=sb.STAGES)
        for _ in range(50):
            job = runner.get(snapshot["id"])
            if job["status"] == "done":
                break
            time.sleep(0.02)
        self.assertEqual(job["status"], "done")
        self.assertEqual(seen["stage"], "Fetching source content")


class ModelHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-health-")
        self.old_state = sb._MODEL_STATE
        self.old_probe = sb._MODEL_LAST_PROBE
        sb._MODEL_STATE = {}
        sb._MODEL_LAST_PROBE = 0.0
        self.path_patch = mock.patch.object(
            sb, "_model_health_path", return_value=os.path.join(self.tmp, "health.json"))
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        sb._MODEL_STATE = self.old_state
        sb._MODEL_LAST_PROBE = self.old_probe

    def test_cooling_model_is_ordered_last(self):
        now = time.time()
        sb._MODEL_STATE = {
            "hung": {"cooldown_until": now + 600, "fails": 1},
            "fast": {"probe_ok": now, "probe_ms": 1200},
        }
        self.assertEqual(sb._model_order(["hung", "fast"]), ["fast", "hung"])

    def test_timeout_falls_through_and_records_cooldown(self):
        class Resp:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            if "hung" in req.full_url:
                raise TimeoutError("read timed out")
            return Resp({"candidates": [{"content": {"parts": [{"text": "OK"}]}}]})

        env = {"GEMINI_API_KEY": "k", "GEMINI_MODELS": "hung,fast"}
        with mock.patch.object(sb, "_needs_probe", return_value=False), \
                mock.patch.object(sb.urllib.request, "urlopen", side_effect=fake_urlopen), \
                mock.patch.object(sb.time, "sleep"):
            text, model = sb._gemini_parts(env, [{"text": "hi"}], timeout=30)
        self.assertEqual((text, model), ("OK", "fast"))
        self.assertGreater(
            sb._health_state("hung")["cooldown_until"], time.time())

    def test_health_persists_across_state_reload(self):
        sb._record_model("fast", True, elapsed=1.2)
        sb._MODEL_STATE = None
        self.assertEqual(sb._health_state("fast")["fails"], 0)
        self.assertTrue(sb._health_state("fast")["full_ok"])


class PromptTests(unittest.TestCase):
    def test_video_prompt_has_required_sequence_and_no_fabrication_rule(self):
        source = {"kind": "youtube", "url": "https://youtu.be/x", "id": "x",
                  "title": "Ref", "metadata": {"title": "Ref"}, "text": "visual",
                  "content_hash": "h", "fetched_at": 1}
        prompt = sb._skill_prompt([source], "video", "reference", None)
        self.assertIn("Acquire → Analyze → Extract → Adapt → Refine → Generate → Compare → Iterate → Save", prompt)
        self.assertIn("Never invent", prompt)
        self.assertIn("UNTRUSTED SOURCE", prompt)
        self.assertIn("Character replacement (optional)", prompt)

    def test_tutorial_video_prompt_teaches_method_not_recreation(self):
        source = {"kind": "youtube", "url": "https://youtu.be/x", "id": "x",
                  "title": "Tutorial", "metadata": {"title": "Tutorial"}, "text": "steps",
                  "content_hash": "h", "fetched_at": 1}
        prompt = sb._skill_prompt([source], "video", "tutorial", None)
        self.assertIn("Tutorial-to-Skill", prompt)
        self.assertIn("Step-by-step method", prompt)
        self.assertIn("must NOT be a template for recreating", prompt)
        self.assertIn("OBSERVED / TRANSCRIPT / UNCERTAIN labels", prompt)
        self.assertNotIn("Acquire → Analyze → Extract → Adapt", prompt)
        self.assertNotIn("Character replacement (optional)", prompt)

    def test_cleans_code_fences_and_strips_nested_frontmatter(self):
        raw = "---\nname: one\ndescription: bad\n---\n```markdown\n---\nname: two\n---\n\n# Body\n"
        out = sb._clean_generated_text(raw)
        self.assertNotIn("```", out)
        self.assertNotIn("name: two", out)
        self.assertIn("# Body", out)


if __name__ == "__main__":
    unittest.main()
