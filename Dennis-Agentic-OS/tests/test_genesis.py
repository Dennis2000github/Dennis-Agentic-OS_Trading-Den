"""Unit tests for the GENESIS production workflow.

Run:  python3 -m unittest tests.test_genesis

Covers the production gates, provenance, the real FFmpeg render adapter
(when ffmpeg is installed), quality review, approval gates and the
publishing flow with mocked HTTP. No provider credits are spent and no
real upload happens: the Google endpoints are mocked, and the render
adapter uses locally generated fixture clips.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import ads_backlog  # noqa: E402
from backend import cosmos_common as common  # noqa: E402
from backend import genesis  # noqa: E402
from backend import genesis_api  # noqa: E402
from backend import genesis_publish as publish  # noqa: E402
from backend import genesis_render as render  # noqa: E402
from backend import genesis_store as store  # noqa: E402
from backend import studio_create  # noqa: E402
from backend import studio_media  # noqa: E402
from backend import studio_workspaces  # noqa: E402
from backend import youtube_common  # noqa: E402
from backend import youtube_topics  # noqa: E402


class GenesisTestBase(unittest.TestCase):
    """Every store path is redirected into a throwaway directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(common, "ADS_BACKLOG_FILE", os.path.join(self.dir, "ads-backlog.json")),
            mock.patch.object(studio_media, "MEDIA_DIR", os.path.join(self.dir, "media")),
            mock.patch.object(studio_media, "INDEX_FILE", os.path.join(self.dir, "media.json")),
            mock.patch.object(store, "GENESIS_DIR", os.path.join(self.dir, "genesis")),
            mock.patch.object(store, "PROJECTS_FILE", os.path.join(self.dir, "genesis", "projects.json")),
            mock.patch.object(store, "RENDERS_FILE", os.path.join(self.dir, "genesis", "renders.json")),
            mock.patch.object(store, "PUBLICATIONS_FILE", os.path.join(self.dir, "genesis", "publications.json")),
            mock.patch.object(store, "TOKENS_FILE", os.path.join(self.dir, "genesis", "oauth-tokens.json")),
            mock.patch.object(store, "TMP_DIR", os.path.join(self.dir, "genesis", "tmp")),
            mock.patch.object(studio_create, "BRIEFS_FILE", os.path.join(self.dir, "studio", "ads", "create-briefs.json")),
            mock.patch.object(studio_workspaces, "WORKSPACES_FILE", os.path.join(self.dir, "studio", "workspaces.json")),
            mock.patch.object(studio_workspaces, "WORKSPACES_DIR", os.path.join(self.dir, "studio", "workspaces")),
            mock.patch.object(youtube_common, "YOUTUBE_DIR", os.path.join(self.dir, "studio", "youtube")),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    # ------------------------------------------------------------------
    def _approved_product(self, name="Floral midi dress"):
        product = ads_backlog.create_product({
            "name": name,
            "merchant": "Shopee",
            "description": "A floral dress.",
            "category": "Dresses",
        })
        ads_backlog.set_status(product["id"], "approved")
        return ads_backlog.get_product(product["id"])

    def _approved_ads_brief(self, name="Floral dress ad"):
        """Create a brief, hand it off, and approve the Studio project."""
        created = studio_create.create_brief({
            "instruction": "Make a short product ad.",
            "product": {"name": name, "merchant": "Shopee", "product_url": "https://example.test/p/1"},
            "target_platforms": ["meta_ads"],
        })
        brief = created["brief"]
        handed = studio_create.handoff(brief["id"])
        studio_workspaces.update_project("ads", handed["project"]["id"], {"status": "approved"})
        return studio_create.get_brief(brief["id"])["brief"], handed["project"]

    def _approved_studio_project(self, workspace="youtube", **extra):
        payload = {
            "title": "Approved %s project" % workspace,
            "description": "A Studio project ready for production.",
            "status": "approved",
            "stage": "assets",
        }
        payload.update(extra)
        return studio_workspaces.create_project(workspace, payload)

    def _clip_media(self, duration=1.0, with_audio=True):
        """Generate a real tiny video clip with the installed ffmpeg."""
        engine = render.availability()
        if not engine["available"]:
            self.skipTest("ffmpeg/ffprobe is not installed")
        path = os.path.join(self.dir, "clip-%s.mp4" % common.new_id(""))
        audio = ["-f", "lavfi", "-i", "sine=frequency=440:duration=%.1f" % duration] if with_audio else []
        cmd = [engine["ffmpeg"], "-hide_banner", "-y",
               "-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=%.1f" % duration]
        cmd += audio
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if with_audio:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd.append(path)
        subprocess.run(cmd, check=True, capture_output=True)
        with open(path, "rb") as handle:
            return studio_media.add_bytes(handle.read(), "video/mp4", kind="video",
                                          filename=os.path.basename(path), meta={"role": "input"})

    def _project_with_media(self, with_audio=True):
        product = self._approved_product()
        media = self._clip_media(with_audio=with_audio)
        payload = {
            "source_kind": "ads_product",
            "source_id": product["id"],
            "title": "Genesis Test Production",
            "scenes": [{"title": "Scene 1", "media_id": media["id"]}],
            "settings": {"aspect_ratio": "16:9", "target_platforms": ["meta_ads"]},
            "publishing_metadata": {
                "title": "Genesis Test Ad",
                "description": "A test ad.",
                "sponsored": True,
                "disclosure": "Includes an affiliate link.",
            },
        }
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, payload)
        assert status == 201, body
        return body["project"], media, product


class IntakeTests(GenesisTestBase):
    def test_approved_product_enters_production_and_advances_lifecycle(self):
        project, _, product = self._project_with_media()
        self.assertTrue(project["id"].startswith("gp_"))
        self.assertEqual(project["status"], "in_production")
        self.assertEqual(ads_backlog.get_product(product["id"])["status"], "in_production")

    def test_provenance_links_back_to_the_source_record(self):
        project, _, product = self._project_with_media()
        provenance = project["provenance"]
        self.assertEqual(provenance["backlog_kind"], "ads_product")
        self.assertEqual(provenance["backlog_item_id"], product["id"])
        self.assertEqual(provenance["product_id"], product["id"])
        self.assertEqual(provenance["division"], "ads")
        source = project["source"]
        self.assertEqual(source["status_at_intake"], "approved")

    def test_draft_product_cannot_bypass_the_production_gate(self):
        product = ads_backlog.create_product({"name": "Draft only", "merchant": "X"})
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_product", "source_id": product["id"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "not_eligible")

    def test_archived_product_cannot_enter_production(self):
        product = self._approved_product()
        ads_backlog.set_status(product["id"], "archived")
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_product", "source_id": product["id"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "not_eligible")

    def test_duplicate_active_project_is_blocked(self):
        project, _, _ = self._project_with_media()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_product", "source_id": project["source"]["id"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "duplicate_project")

    def test_unknown_source_kind_rejected(self):
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "tiktok_video", "source_id": "x",
        })
        self.assertEqual(status, 400)

    def test_handed_off_brief_with_approved_project_enters_production(self):
        brief, project = self._approved_ads_brief()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_brief", "source_id": brief["id"],
        })
        self.assertEqual(status, 201, body)
        production = body["project"]
        self.assertEqual(production["division"], "ads")
        self.assertEqual(production["source"]["kind"], "ads_brief")
        self.assertEqual(production["provenance"]["brief_id"], brief["id"])
        self.assertEqual(production["provenance"]["studio_project_id"], project["id"])
        self.assertEqual(studio_create.get_brief(brief["id"])["brief"]["status"], "in_production")
        self.assertEqual(studio_workspaces.get_project("ads", project["id"])["status"], "in_progress")

    def test_brief_without_handoff_cannot_enter_production(self):
        created = studio_create.create_brief({
            "instruction": "A draft brief.",
            "product": {"name": "Draft product", "merchant": "Shopee"},
        })
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_brief", "source_id": created["brief"]["id"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "not_eligible")

    def test_brief_with_unapproved_studio_project_cannot_enter_production(self):
        created = studio_create.create_brief({
            "instruction": "A handed-off brief.",
            "product": {"name": "Review product", "merchant": "Shopee"},
        })
        studio_create.handoff(created["brief"]["id"])
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_brief", "source_id": created["brief"]["id"],
        })
        self.assertEqual(status, 409)
        self.assertIn("Approve", body["error"])

    def test_approved_studio_projects_enter_production(self):
        youtube = self._approved_studio_project(
            "youtube", channel_id="ytch_test", topic_id="ytt_1",
        )
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "youtube_project", "source_id": youtube["id"],
        })
        self.assertEqual(status, 201, body)
        production = body["project"]
        self.assertEqual(production["division"], "youtube")
        self.assertEqual(production["source"]["kind"], "youtube_project")
        self.assertEqual(production["source"]["channel_id"], "ytch_test")
        self.assertEqual(
            studio_workspaces.get_project("youtube", youtube["id"])["status"], "in_progress"
        )

        ads = self._approved_studio_project("ads")
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_project", "source_id": ads["id"],
        })
        self.assertEqual(status, 201, body)
        self.assertEqual(body["project"]["division"], "ads")

    def test_unapproved_studio_project_is_rejected(self):
        project = studio_workspaces.create_project("youtube", {
            "title": "Not approved yet", "status": "review",
        })
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "youtube_project", "source_id": project["id"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "not_eligible")

    def test_brief_and_its_project_cannot_both_enter_production(self):
        brief, project = self._approved_ads_brief()
        status, _ = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_brief", "source_id": brief["id"],
        })
        self.assertEqual(status, 201)
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_project", "source_id": project["id"],
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "duplicate_project")


class QueueTests(GenesisTestBase):
    def test_queue_lists_only_eligible_records(self):
        approved = self._approved_product()
        ads_backlog.create_product({"name": "Draft only", "merchant": "X"})
        result = genesis.queue()
        ids = [row["source_id"] for row in result["queue"]]
        self.assertIn(approved["id"], ids)
        self.assertNotIn("draft-only", ids)

    def test_queue_includes_briefs_and_studio_projects_with_context(self):
        brief, _ = self._approved_ads_brief()
        youtube = self._approved_studio_project("youtube", channel_id="ytch_test")
        result = genesis.queue()
        by_kind = {(row["source_kind"], row["source_id"]): row for row in result["queue"]}
        self.assertIn(("ads_brief", brief["id"]), by_kind)
        self.assertIn(("youtube_project", youtube["id"]), by_kind)
        brief_row = by_kind[("ads_brief", brief["id"])]
        self.assertEqual(brief_row["origin"], "Ads Studio Create brief")
        self.assertIn("Ads Studio project", brief_row["destination"])
        youtube_row = by_kind[("youtube_project", youtube["id"])]
        self.assertEqual(youtube_row["origin"], "YouTube Studio project")
        self.assertIn("ytch_test", youtube_row["destination"])

    def test_queue_hides_sources_already_in_active_production(self):
        product = self._approved_product()
        status, _ = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_product", "source_id": product["id"],
        })
        self.assertEqual(status, 201)
        ids = [row["source_id"] for row in genesis.queue()["queue"]]
        self.assertNotIn(product["id"], ids)

        brief, project = self._approved_ads_brief(name="Second product")
        status, _ = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_brief", "source_id": brief["id"],
        })
        self.assertEqual(status, 201)
        queue_ids = [row["source_id"] for row in genesis.queue()["queue"]]
        self.assertNotIn(brief["id"], queue_ids)
        self.assertNotIn(project["id"], queue_ids)

    def test_active_source_index_and_source_projects_are_cross_store(self):
        brief, project = self._approved_ads_brief()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_brief", "source_id": brief["id"],
        })
        self.assertEqual(status, 201)
        production = body["project"]
        index = genesis.active_source_index("ads_brief")
        self.assertEqual(index[brief["id"]]["id"], production["id"])
        projects = genesis.source_projects("ads_brief", brief["id"])
        self.assertEqual([item["id"] for item in projects], [production["id"]])
        self.assertEqual(projects[0]["provenance"]["studio_project_id"], project["id"])


class CaptionTests(GenesisTestBase):
    SRT = (
        "1\n00:00:00,000 --> 00:00:01,000\nHello there\n\n"
        "2\n00:00:01,200 --> 00:00:02,000\nSecond cue\n"
    )

    def _srt_media(self, text=None):
        return studio_media.add_bytes(
            (text or self.SRT).encode("utf-8"),
            "application/x-subrip",
            filename="captions.srt",
            meta={"role": "input", "title": "captions.srt"},
        )

    def test_caption_text_reads_attached_srt_media(self):
        media = self._srt_media()
        text = render.caption_text({"captions_media_id": media["id"]})
        self.assertIn("Hello there", text)
        self.assertEqual(media["kind"], "file")
        self.assertTrue(media["filename"].endswith(".srt"))

    def test_missing_or_invalid_caption_media_is_a_real_error(self):
        with self.assertRaises(render.RenderError) as ctx:
            render.caption_text({"captions_media_id": "med_missing"})
        self.assertEqual(ctx.exception.code, "captions_missing")

        invalid = studio_media.add_bytes(
            b"not an srt file", "text/plain", filename="captions.txt",
        )
        with self.assertRaises(render.RenderError) as ctx:
            render.caption_text({"captions_media_id": invalid["id"]})
        self.assertEqual(ctx.exception.code, "captions_invalid")

    def test_caption_sync_checks_real_cue_timings(self):
        self.assertEqual(render.caption_sync(self.SRT, 10.0)[0], "pass")
        status, detail = render.caption_sync(self.SRT, 0.5)
        self.assertEqual(status, "warn")
        self.assertIn("late cues", detail)
        self.assertEqual(render.caption_sync("no cues", 10.0)[0], "fail")

    def test_quality_review_includes_caption_sync_when_captions_burn(self):
        project, _, _ = self._project_with_media()
        media = self._srt_media()
        status, body = genesis_api.handle("PATCH", "/api/cosmos/genesis/projects/" + project["id"], {}, {
            "audio": {"captions_media_id": media["id"]},
        })
        self.assertEqual(status, 200, body)
        status, body = genesis_api.handle(
            "POST", "/api/cosmos/genesis/projects/%s/render" % project["id"], {}, {}
        )
        self.assertEqual(status, 201)
        record_id = body["render"]["id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            record = store.get_render(record_id)
            if record["status"] in ("complete", "failed"):
                break
            time.sleep(0.5)
        if record["status"] == "failed" and record.get("error", {}).get("code") == "captions_missing":
            self.skipTest("this ffmpeg build cannot read the captions sidecar")
        self.assertEqual(record["status"], "complete", record.get("error"))
        if not record["captions_burned"]:
            self.skipTest("this ffmpeg build has no subtitles filter")
        status, body = genesis_api.handle(
            "POST", "/api/cosmos/genesis/projects/%s/quality-review" % project["id"], {}, {}
        )
        self.assertEqual(status, 200)
        checks = {check["id"]: check for check in body["review"]["checks"]}
        self.assertIn("caption_sync", checks)
        self.assertEqual(checks["caption_sync"]["status"], "pass")


class RenderTests(GenesisTestBase):
    def test_scene_without_media_fails_validation_precisely(self):
        product = self._approved_product()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_product",
            "source_id": product["id"],
            "scenes": [{"title": "Empty scene"}],
        })
        self.assertEqual(status, 201)
        project = body["project"]
        with self.assertRaises(render.RenderError) as ctx:
            render.build_manifest(project)
        self.assertEqual(ctx.exception.code, "scene_without_media")

    def test_render_produces_a_real_file_in_the_media_library(self):
        project, _, _ = self._project_with_media()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/render" % project["id"], {}, {})
        self.assertEqual(status, 201)
        record = body["render"]
        self.assertEqual(record["status"], "queued")
        deadline = time.time() + 120
        while time.time() < deadline:
            record = store.get_render(record["id"])
            if record["status"] in ("complete", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(record["status"], "complete", record.get("error"))
        self.assertTrue(record["media_id"])
        media = studio_media.get(record["media_id"])
        self.assertEqual(media["kind"], "video")
        self.assertTrue(record["duration_seconds"] > 0.5)
        # The project links the finished render.
        updated = store.get_project(project["id"])
        self.assertIn(record["id"], updated["render_ids"])
        self.assertEqual(updated["media_id"], record["media_id"])
        self.assertEqual(updated["status"], "quality_review")

    def test_render_without_ffmpeg_reports_unavailable(self):
        project, _, _ = self._project_with_media()
        with mock.patch.object(render, "availability", return_value={
            "engine": "ffmpeg", "available": False, "ffmpeg": None, "ffprobe": None,
            "version": None, "runner": False,
        }):
            status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/render" % project["id"], {}, {})
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "renderer_unavailable")

    def test_render_failure_is_reported_as_a_failure(self):
        project, _, _ = self._project_with_media()
        # Corrupt the manifest path: point the clip at a missing file.
        broken = studio_media.add_bytes(b"not a video", "video/mp4", kind="video",
                                        filename="broken.mp4", meta={"role": "input"})
        scenes = [{"title": "Broken", "media_id": broken["id"]}]
        genesis_api.handle("PATCH", "/api/cosmos/genesis/projects/" + project["id"], {}, {"scenes": scenes})
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/render" % project["id"], {}, {})
        self.assertEqual(status, 201)
        record_id = body["render"]["id"]
        deadline = time.time() + 60
        while time.time() < deadline:
            record = store.get_render(record_id)
            if record["status"] in ("complete", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["error"]["message"], "a failed render must carry the real error")


class QualityReviewTests(GenesisTestBase):
    def test_quality_review_passes_a_good_render_and_blocks_approval_after_failure(self):
        project, _, _ = self._project_with_media()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/render" % project["id"], {}, {})
        record_id = body["render"]["id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            record = store.get_render(record_id)
            if record["status"] in ("complete", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(record["status"], "complete")

        status, body = genesis_api.handle(
            "POST", "/api/cosmos/genesis/projects/%s/quality-review" % project["id"], {}, {})
        self.assertEqual(status, 200)
        review = body["review"]
        self.assertNotEqual(review["verdict"], "fail", review["checks"])
        ids = [check["id"] for check in review["checks"]]
        for expected in ("render_exists", "file_exists", "video_stream", "audio_stream",
                         "resolution", "aspect_ratio", "missing_assets", "disclosure"):
            self.assertIn(expected, ids)
        # The failing review on a broken project cannot be approved.
        empty_project = None
        product = ads_backlog.create_product({"name": "No media", "merchant": "X"})
        ads_backlog.set_status(product["id"], "approved")
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects", {}, {
            "source_kind": "ads_product", "source_id": product["id"],
            "title": "No media production",
        })
        empty_project = body["project"]
        status, body = genesis_api.handle(
            "POST", "/api/cosmos/genesis/projects/%s/quality-review" % empty_project["id"], {}, {})
        self.assertEqual(body["review"]["verdict"], "fail")
        status, body = genesis_api.handle(
            "POST", "/api/cosmos/genesis/projects/%s/approval" % empty_project["id"], {}, {"approved": True})
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "quality_review_required")


class PublishingTests(GenesisTestBase):
    def _approved_project(self):
        project, _, _ = self._project_with_media()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/render" % project["id"], {}, {})
        record_id = body["render"]["id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            record = store.get_render(record_id)
            if record["status"] in ("complete", "failed"):
                break
            time.sleep(0.5)
        self.assertEqual(record["status"], "complete")
        genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/quality-review" % project["id"], {}, {})
        genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/approval" % project["id"], {}, {"approved": True})
        return project

    def test_publish_without_explicit_approval_flag_is_rejected(self):
        project = self._approved_project()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/publish" % project["id"], {}, {
            "destination": "youtube",
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "approval_required")

    def test_publish_before_approval_is_rejected(self):
        project, _, _ = self._project_with_media()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/publish" % project["id"], {}, {
            "destination": "youtube", "approved": True,
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "project_not_approved")

    def test_unimplemented_destination_never_publishes(self):
        project = self._approved_project()
        status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/publish" % project["id"], {}, {
            "destination": "tiktok", "approved": True,
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "destination_unavailable")

    def test_successful_upload_stores_remote_id_and_blocks_duplicates(self):
        project = self._approved_project()
        calls = []

        def fake_http(method, url, headers=None, payload=None, data=None, timeout=30):
            calls.append(method)
            if method == "POST" and "oauth2.googleapis.com/token" in url:
                return 200, {"access_token": "at", "expires_in": 3600}, {}
            if method == "POST" and "upload/youtube/v3/videos" in url:
                return 200, {"id": "zzz"}, {"Location": "https://upload.example/session"}
            if method == "PUT":
                return 200, {"id": "test_video_id", "status": {"privacyStatus": "private"}}, {}
            raise AssertionError("unexpected %s %s" % (method, url))

        tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": time.time() + 9999}
        with mock.patch.object(publish, "_http_json", side_effect=fake_http), \
             mock.patch.object(publish, "_tokens", return_value=tokens):
            status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/publish" % project["id"], {}, {
                "destination": "youtube", "approved": True,
                "metadata": {"title": "Genesis Test Ad"},
            })
            self.assertEqual(status, 201)
            publication = body["publication"]
            deadline = time.time() + 20
            while time.time() < deadline:
                publication = store.get_publication(publication["id"])
                if publication["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.25)
            self.assertEqual(publication["status"], "succeeded", publication.get("error"))
            self.assertEqual(publication["remote_id"], "test_video_id")
            self.assertEqual(publication["remote_url"], "https://youtu.be/test_video_id")
            # The project and the source record advanced to published. The
            # worker thread links the confirmation back after the publication
            # record flips, so poll for the final source transition instead of
            # assuming instantaneous ordering.
            deadline = time.time() + 20
            while time.time() < deadline:
                if ads_backlog.get_product(project["source"]["id"])["status"] == "published":
                    break
                time.sleep(0.25)
            self.assertEqual(store.get_project(project["id"])["status"], "published")
            product = ads_backlog.get_product(project["source"]["id"])
            self.assertEqual(product["status"], "published")
            # Duplicate requests are rejected.
            status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/publish" % project["id"], {}, {
                "destination": "youtube", "approved": True,
            })
            self.assertEqual(status, 409)
            self.assertEqual(body["code"], "already_published")
        self.assertIn("PUT", calls, "the upload must actually PUT the video bytes")

    def test_upload_failure_is_recorded_and_retryable(self):
        project = self._approved_project()

        def fake_http(method, url, headers=None, payload=None, data=None, timeout=30):
            if method == "POST" and "upload/youtube/v3/videos" in url:
                return 403, {"error": {"message": "quotaExceeded"}}, {}
            raise AssertionError("unexpected %s %s" % (method, url))

        tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": time.time() + 9999}
        with mock.patch.object(publish, "_http_json", side_effect=fake_http), \
             mock.patch.object(publish, "_tokens", return_value=tokens):
            status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/publish" % project["id"], {}, {
                "destination": "youtube", "approved": True,
            })
            publication = body["publication"]
            deadline = time.time() + 20
            while time.time() < deadline:
                publication = store.get_publication(publication["id"])
                if publication["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.25)
            self.assertEqual(publication["status"], "failed")
            self.assertEqual(publication["error"]["code"], "upload_init_failed")
            # The source record must NOT claim published.
            self.assertEqual(ads_backlog.get_product(project["source"]["id"])["status"], "in_production")

        def fixed_http(method, url, headers=None, payload=None, data=None, timeout=30):
            if method == "POST" and "oauth2.googleapis.com/token" in url:
                return 200, {"access_token": "at", "expires_in": 3600}, {}
            if method == "POST" and "upload/youtube/v3/videos" in url:
                return 200, {"id": "zzz"}, {"Location": "https://upload.example/session"}
            if method == "PUT":
                return 200, {"id": "retried_video_id"}, {}
            raise AssertionError("unexpected %s %s" % (method, url))

        with mock.patch.object(publish, "_http_json", side_effect=fixed_http), \
             mock.patch.object(publish, "_tokens", return_value=tokens):
            status, body = genesis_api.handle(
                "POST", "/api/cosmos/genesis/publications/%s/retry" % publication["id"], {}, {})
            self.assertEqual(status, 200)
            retried = body["publication"]
            deadline = time.time() + 20
            while time.time() < deadline:
                retried = store.get_publication(publication["id"])
                if retried["status"] in ("succeeded", "failed"):
                    break
                time.sleep(0.25)
            self.assertEqual(retried["status"], "succeeded", retried.get("error"))

    def test_scheduled_publication_is_not_published_immediately(self):
        project = self._approved_project()
        future = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        tokens = {"access_token": "old", "refresh_token": "rt", "expires_at": time.time() + 9999}
        with mock.patch.object(publish, "_tokens", return_value=tokens):
            status, body = genesis_api.handle("POST", "/api/cosmos/genesis/projects/%s/publish" % project["id"], {}, {
                "destination": "youtube", "approved": True, "schedule_at": future,
            })
        self.assertEqual(status, 201)
        self.assertEqual(body["publication"]["status"], "scheduled")
        self.assertTrue(body["publication"]["remote_id"] is None)
        self.assertEqual(ads_backlog.get_product(project["source"]["id"])["status"], "in_production")

    def test_oauth_status_is_honest_without_configuration(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GOOGLE_OAUTH_CLIENT_ID", None)
            os.environ.pop("GOOGLE_OAUTH_CLIENT_SECRET", None)
            with mock.patch.object(publish, "OAUTH_CLIENT_FILE", os.path.join(self.dir, "missing-oauth.json")):
                data = publish.destinations()
        youtube = data["destinations"][0]
        self.assertEqual(youtube["id"], "youtube")
        self.assertFalse(youtube["configured"])
        self.assertFalse(youtube["authorized"])
        self.assertTrue(youtube["reason"])
        tiktok = [d for d in data["destinations"] if d["id"] == "tiktok"][0]
        self.assertFalse(tiktok["available"])

    def test_tokens_are_never_exposed_through_the_api(self):
        data = genesis_api.handle("GET", "/api/cosmos/genesis/publishing/destinations", {}, {})[1]
        raw = str(data)
        self.assertNotIn("access_token", raw)
        self.assertNotIn("refresh_token", raw)


class ApiSurfaceTests(GenesisTestBase):
    def test_overview_reports_real_counts(self):
        project, _, _ = self._project_with_media()
        status, body = genesis_api.handle("GET", "/api/cosmos/genesis/overview", {}, {})
        self.assertEqual(status, 200)
        self.assertEqual(body["counts"]["projects"]["in_production"], 1)
        self.assertTrue(body["engine"])

    def test_project_crud_and_archive(self):
        project, _, _ = self._project_with_media()
        status, body = genesis_api.handle("PATCH", "/api/cosmos/genesis/projects/" + project["id"], {}, {
            "title": "Renamed production",
        })
        self.assertEqual(body["project"]["title"], "Renamed production")
        status, body = genesis_api.handle(
            "POST", "/api/cosmos/genesis/projects/%s/archive" % project["id"], {}, {})
        self.assertEqual(body["project"]["archived"], True)
        status, body = genesis_api.handle("GET", "/api/cosmos/genesis/projects", {}, {})
        self.assertEqual(body["projects"], [])

    def test_awaiting_approval_stays_editable_for_iteration(self):
        project, _, _ = self._project_with_media()
        store.update_project(project["id"], {"status": "awaiting_approval"})
        status, body = genesis_api.handle("PATCH", "/api/cosmos/genesis/projects/" + project["id"], {}, {
            "audio": {"captions_srt": "1\n00:00:00,000 --> 00:00:01,000\nHello\n"},
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["project"]["audio"]["captions_srt"])

    def test_approved_project_locks_edits(self):
        project, _, _ = self._project_with_media()
        store.update_project(project["id"], {"status": "approved"})
        status, body = genesis_api.handle("PATCH", "/api/cosmos/genesis/projects/" + project["id"], {}, {
            "title": "Should fail",
        })
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "project_locked")

    def test_cosmos_dispatch_routes_genesis_subtree(self):
        from backend import cosmos_api
        status, body = cosmos_api.handle("GET", "/api/cosmos/genesis/overview", {}, None)
        self.assertEqual(status, 200)
        self.assertIn("counts", body)


if __name__ == "__main__":
    unittest.main()
