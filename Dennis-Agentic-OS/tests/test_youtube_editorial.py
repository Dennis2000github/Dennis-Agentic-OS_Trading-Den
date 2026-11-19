"""Unit tests for the YouTube Studio editorial backend and LLM adapter.

Run:  python3 -m unittest tests.test_youtube_editorial

Storage is redirected into a temporary directory; no server is started and no
network call is made (the OpenCode CLI adapter is exercised with stubs).
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_llm  # noqa: E402
from backend import studio_workspaces as workspaces  # noqa: E402
from backend import youtube_api  # noqa: E402
from backend import youtube_channels as channels  # noqa: E402
from backend import youtube_common as common  # noqa: E402
from backend import youtube_research as research  # noqa: E402
from backend import youtube_scripts as scripts  # noqa: E402
from backend import youtube_topics as topics  # noqa: E402


class EditorialTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "YOUTUBE_DIR", os.path.join(self.dir, "youtube")),
            mock.patch.object(workspaces, "STUDIO_DIR", os.path.join(self.dir, "studio")),
            mock.patch.object(
                workspaces, "WORKSPACES_FILE", os.path.join(self.dir, "studio", "workspaces.json")
            ),
            mock.patch.object(
                workspaces, "WORKSPACES_DIR", os.path.join(self.dir, "studio", "workspaces")
            ),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def active_channel_id(self):
        return channels.active_channel()["id"]


class ChannelTests(EditorialTestBase):
    def test_seed_creates_blueprint_channel_with_master_prompt(self):
        channel = channels.active_channel()
        self.assertEqual(channel["name"], "The Business Money Blueprint")
        self.assertTrue(channel.get("master_prompt_version_id"))
        current = channels.current_prompt(channel["id"])["prompt"]
        self.assertTrue(current.get("body"))
        roles = channels.list_agent_prompts(channel["id"])["prompts"]
        self.assertTrue(any(role.get("role") == "scriptwriter" for role in roles))

    def test_create_and_duplicate_name(self):
        created = channels.create_channel({"name": "Second Channel"})
        self.assertEqual(created["name"], "Second Channel")
        with self.assertRaises(channels.ChannelError) as ctx:
            channels.create_channel({"name": "second channel"})
        self.assertEqual(ctx.exception.status, 409)

    def test_activate_persists_in_workspace_settings(self):
        second = channels.create_channel({"name": "Other"})
        channels.activate_channel(second["id"])
        settings = workspaces.get_settings("youtube")
        self.assertEqual(settings.get("active_channel_id"), second["id"])

    def test_last_channel_cannot_be_deleted(self):
        only = channels.active_channel()
        with self.assertRaises(channels.ChannelError) as ctx:
            channels.delete_channel(only["id"])
        self.assertEqual(ctx.exception.status, 409)

    def test_prompt_versions_are_immutable_and_restorable(self):
        channel = channels.active_channel()
        version_two = channels.add_prompt_version(channel["id"], {"body": "v2 body", "note": "second"})["version"]
        self.assertEqual(version_two["version"], 2)
        channels.set_default_prompt_version(channel["id"], version_two["id"])
        restored = channels.restore_prompt_version(channel["id"], version_two["id"], "back to v1")["version"]
        self.assertEqual(restored["version"], 3)
        self.assertEqual(restored["body"], "v2 body")
        versions = channels.list_prompt_versions(channel["id"])["versions"]
        self.assertEqual(len(versions), 3)


class ResearchTests(EditorialTestBase):
    def test_perfect_scores_pass(self):
        criteria = {key: {"score": 5} for key in (
            "rpm_potential", "proven_demand", "geographic_audience",
            "content_sustainability", "content_gap",
        )}
        validation = research.create_validation(
            self.active_channel_id(), {"subject": "S-corp salaries", "criteria": criteria}
        )
        self.assertEqual(validation["verdict"], "pass")
        self.assertEqual(validation["total_score"], 25)

    def test_low_score_fails(self):
        criteria = {key: {"score": 2} for key in (
            "rpm_potential", "proven_demand", "geographic_audience",
            "content_sustainability", "content_gap",
        )}
        validation = research.create_validation(
            self.active_channel_id(), {"subject": "Weak niche", "criteria": criteria}
        )
        self.assertEqual(validation["verdict"], "fail")

    def test_uncertainty_flag_forces_review(self):
        criteria = {key: {"score": 5} for key in (
            "rpm_potential", "proven_demand", "geographic_audience",
            "content_sustainability", "content_gap",
        )}
        validation = research.create_validation(
            self.active_channel_id(),
            {"subject": "Legal edge", "criteria": criteria, "flags": ["legal_uncertainty"]},
        )
        self.assertEqual(validation["verdict"], "review")

    def test_source_url_must_be_http(self):
        with self.assertRaises(research.ResearchError):
            research.create_validation(
                self.active_channel_id(),
                {"subject": "Bad source", "sources": [{"label": "x", "url": "ftp://nope"}]},
            )

    def test_competitor_observation_basis_rules(self):
        competitor = research.create_competitor({"name": "Competitor One"})
        # missing basis defaults to observed
        updated = research.add_observation(competitor["id"], {"note": "something"})
        self.assertEqual(updated["observations"][0]["basis"], "observed")
        # an invalid basis is rejected
        with self.assertRaises(research.ResearchError):
            research.add_observation(competitor["id"], {"note": "x", "basis": "vibes"})
        updated = research.add_observation(
            competitor["id"], {"note": "posts weekly", "basis": "observed"}
        )
        self.assertEqual(updated["observations"][1]["basis"], "observed")


class TopicTests(EditorialTestBase):
    def test_create_with_validation_becomes_validated(self):
        criteria = {key: {"score": 5} for key in (
            "rpm_potential", "proven_demand", "geographic_audience",
            "content_sustainability", "content_gap",
        )}
        validation = research.create_validation(
            self.active_channel_id(), {"subject": "Home office deduction", "criteria": criteria}
        )
        topic = topics.create_topic(
            self.active_channel_id(),
            {"title": "Home office deduction 2026", "validation_id": validation["id"]},
        )
        self.assertEqual(topic["status"], "validated")
        self.assertEqual(topic["score"], 25)

    def test_duplicates_are_blocked_unless_forced(self):
        topics.create_topic(self.active_channel_id(), {"title": "Quarterly Taxes Explained"})
        with self.assertRaises(topics.TopicError) as ctx:
            topics.create_topic(self.active_channel_id(), {"title": "quarterly taxes explained"})
        self.assertEqual(ctx.exception.status, 409)
        forced = topics.create_topic(
            self.active_channel_id(), {"title": "quarterly taxes explained"}, force=True
        )
        self.assertEqual(forced["status"], "idea")
        duplicates = topics.find_duplicates(self.active_channel_id(), "quarterly taxes explained")
        self.assertTrue(any(match["blocks"] for match in duplicates))

    def test_status_transitions_are_forward_only(self):
        topic = topics.create_topic(self.active_channel_id(), {"title": "Idea stage topic"})
        moved = topics.set_status(self.active_channel_id(), topic["id"], "researching")
        self.assertEqual(moved["status"], "researching")
        with self.assertRaises(topics.TopicError):
            topics.set_status(self.active_channel_id(), topic["id"], "approved")


class ScriptTests(EditorialTestBase):
    def _approved_topic(self):
        criteria = {key: {"score": 5} for key in (
            "rpm_potential", "proven_demand", "geographic_audience",
            "content_sustainability", "content_gap",
        )}
        validation = research.create_validation(
            self.active_channel_id(), {"subject": "Approved niche", "criteria": criteria}
        )
        topic = topics.create_topic(
            self.active_channel_id(), {"title": "Approved topic", "validation_id": validation["id"]}
        )
        # Validation alone yields "validated"; approval is an explicit transition.
        return topics.set_status(self.active_channel_id(), topic["id"], "approved")

    def test_script_requires_approved_topic(self):
        topic = topics.create_topic(self.active_channel_id(), {"title": "Unapproved"})
        with self.assertRaises(scripts.ScriptError) as ctx:
            scripts.create_script(self.active_channel_id(), {"topic_id": topic["id"], "title": "X"})
        self.assertEqual(ctx.exception.code, "topic_not_approved")

    def test_create_updates_version_and_word_count(self):
        topic = self._approved_topic()
        script = scripts.create_script(
            self.active_channel_id(),
            {
                "topic_id": topic["id"],
                "title": "My script",
                "sections": [{"heading": "Hook", "narration": "four words only here"}],
            },
        )
        self.assertEqual(script["status"], "draft")
        self.assertEqual(script["word_count"], 4)
        self.assertEqual(script["version"], 1)
        self.assertEqual(topics.get_topic(self.active_channel_id(), topic["id"])["status"], "scripted")
        updated = scripts.update_script(
            self.active_channel_id(),
            script["id"],
            {"sections": [{"heading": "Hook", "narration": "now six whole words here"}]},
            note="edit",
        )
        self.assertEqual(updated["version"], 2)
        versions = scripts.list_versions(self.active_channel_id(), script["id"])["versions"]
        self.assertEqual(len(versions), 1)

    def test_approval_requires_fact_check(self):
        topic = self._approved_topic()
        script = scripts.create_script(
            self.active_channel_id(),
            {
                "topic_id": topic["id"],
                "title": "Gated script",
                "sections": [{"heading": "Hook", "narration": "words"}],
                "citations": [{"label": "IRS Pub 334", "url": "https://irs.gov"}],
                "fact_check": {"items": [{"claim": "rates", "verified": False}]},
            },
        )
        scripts.set_status(self.active_channel_id(), script["id"], "in_review")
        with self.assertRaises(scripts.ScriptError):
            scripts.set_status(self.active_channel_id(), script["id"], "approved")


class DispatcherTests(EditorialTestBase):
    def test_overview_route(self):
        status, payload = youtube_api.handle("GET", "/api/studio/youtube/overview")
        self.assertEqual(status, 200)
        self.assertIn("channel", payload)
        self.assertIn("llm", payload)

    def test_features_shape(self):
        status, payload = youtube_api.handle("GET", "/api/studio/youtube/features")
        self.assertEqual(status, 200)
        for key in ("publishing", "analytics", "oauth", "templates", "packaging", "production", "intelligence"):
            self.assertIn(key, payload)
        self.assertFalse(payload["oauth"]["configured"])

    def test_unknown_endpoint_404(self):
        status, payload = youtube_api.handle("GET", "/api/studio/youtube/definitely-not-here")
        self.assertEqual(status, 404)


class LLMProfileTests(EditorialTestBase):
    def setUp(self):
        super().setUp()
        for name in (
            "DENNIS_YT_LLM_PROVIDER", "DENNIS_YT_LLM_BASE_URL",
            "DENNIS_YT_LLM_API_KEY", "DENNIS_YT_LLM_MODEL",
            "DENNIS_YT_LLM_OPENCODE_MODEL", "DENNIS_OPENCODE_BIN",
        ):
            self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
            self.env_patch.start()
            self.addCleanup(self.env_patch.stop)
            os.environ.pop(name, None)

    def test_default_profile_is_opencode_glm(self):
        self.assertEqual(studio_llm.profile(), "opencode")
        self.assertEqual(studio_llm.model(), studio_llm.DEFAULT_OPENCODE_MODEL)

    def test_configure_persists_profile_and_model(self):
        status = studio_llm.configure({"provider": "openai_compatible", "model": "deepseek-chat"})
        self.assertEqual(status["provider"], "openai_compatible")
        self.assertEqual(status["model"], "deepseek-chat")
        self.assertEqual(studio_llm.profile(), "openai_compatible")
        self.assertEqual(studio_llm.model(), "deepseek-chat")
        # switch back with no model: profile default applies again
        status = studio_llm.configure({"provider": "opencode"})
        self.assertEqual(studio_llm.model(), studio_llm.DEFAULT_OPENCODE_MODEL)

    def test_configure_rejects_unknown_provider(self):
        with self.assertRaises(studio_llm.LLMError) as ctx:
            studio_llm.configure({"provider": "nope"})
        self.assertEqual(ctx.exception.status, 400)

    def test_env_used_when_settings_empty(self):
        with mock.patch.dict(os.environ, {"DENNIS_YT_LLM_PROVIDER": "openai_compatible"}):
            self.assertEqual(studio_llm.profile(), "openai_compatible")

    def test_status_reports_unconfigured_openai_backend(self):
        with mock.patch.object(studio_llm.deepseek_harness, "api_key", return_value=("", "none")):
            status = studio_llm.configure({"provider": "openai_compatible"})
            self.assertFalse(status["configured"])
            self.assertTrue(status.get("hint_setup"))

    def test_complete_opencode_runs_cli(self):
        calls = []

        def fake_run(argv, capture_output, text, timeout):
            calls.append(argv)
            result = mock.Mock()
            result.returncode = 0
            result.stdout = "MODEL ANSWER"
            result.stderr = ""
            return result

        with mock.patch.object(studio_llm, "_opencode_binary", return_value="/usr/bin/opencode"), \
                mock.patch.object(studio_llm.subprocess, "run", side_effect=fake_run):
            result = studio_llm.complete(
                [{"role": "system", "content": "SYS"}, {"role": "user", "content": "QUESTION"}],
                temperature=0.5,
            )
        self.assertEqual(result["content"], "MODEL ANSWER")
        argv = calls[0]
        self.assertEqual(argv[0], "/usr/bin/opencode")
        self.assertIn("run", argv)
        self.assertIn(studio_llm.DEFAULT_OPENCODE_MODEL, argv)
        self.assertIn("SYS", argv[-1])
        self.assertIn("QUESTION", argv[-1])

    def test_complete_opencode_missing_binary_is_honest(self):
        with mock.patch.object(studio_llm, "_opencode_binary", return_value=None):
            with self.assertRaises(studio_llm.LLMError) as ctx:
                studio_llm.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, "llm_not_configured")

    def test_complete_opencode_empty_output_is_error(self):
        result = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(studio_llm, "_opencode_binary", return_value="/usr/bin/true"), \
                mock.patch.object(studio_llm.subprocess, "run", return_value=result):
            with self.assertRaises(studio_llm.LLMError) as ctx:
                studio_llm.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.code, "llm_empty_response")

    def test_complete_openai_requires_key(self):
        studio_llm.configure({"provider": "openai_compatible"})
        with mock.patch.object(studio_llm.deepseek_harness, "api_key", return_value=("", "none")):
            with self.assertRaises(studio_llm.LLMError) as ctx:
                studio_llm.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
