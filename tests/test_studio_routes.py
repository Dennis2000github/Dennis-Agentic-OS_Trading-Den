"""HTTP tests for the Studio routes.

Uses the real stdlib server and the real catalog, but redirects credentials,
jobs, approvals and media into a temporary directory. No provider network call
is made.

Run:  python3 -m unittest tests.test_studio_routes
"""

import base64
import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")
os.environ["HIGGSFIELD_API_KEY"] = ""
os.environ["HIGGSFIELD_SECRET"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import auth as dennis_auth  # noqa: E402
from backend import studio_collections  # noqa: E402
from backend import studio_jobs  # noqa: E402
from backend import studio_mcp  # noqa: E402
from backend import studio_media  # noqa: E402
from backend import studio_secrets  # noqa: E402
from backend import studio_tools  # noqa: E402
from backend import vault as dennis_vault  # noqa: E402


class StudioRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.token = dennis_auth.AUTH.token

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            mock.patch.object(studio_secrets, "STUDIO_DIR", self.dir),
            mock.patch.object(
                studio_secrets, "CREDENTIALS_FILE",
                os.path.join(self.dir, "credentials.json"),
            ),
            mock.patch.object(studio_media, "STUDIO_DIR", self.dir),
            mock.patch.object(studio_media, "MEDIA_DIR", os.path.join(self.dir, "media")),
            mock.patch.object(
                studio_media, "INDEX_FILE", os.path.join(self.dir, "media.json")
            ),
            mock.patch.object(studio_mcp, "STUDIO_DIR", self.dir),
            mock.patch.object(
                studio_mcp, "CONFIG_FILE", os.path.join(self.dir, "mcp.json")
            ),
            mock.patch.object(studio_collections, "STUDIO_DIR", self.dir),
            mock.patch.object(
                studio_collections, "PLAYLISTS_FILE",
                os.path.join(self.dir, "playlists.json"),
            ),
            mock.patch.object(
                studio_collections, "TEMPLATES_FILE",
                os.path.join(self.dir, "templates.json"),
            ),
        ]
        for patch in self.patches:
            patch.start()
        self.old_store = server.STUDIO_RUNNER.store
        self.old_approvals = server.STUDIO_TOOLS.approvals
        server.STUDIO_RUNNER.store = studio_jobs.JobStore(
            os.path.join(self.dir, "jobs.json")
        )
        server.STUDIO_TOOLS.approvals = studio_tools.ApprovalStore(
            os.path.join(self.dir, "approvals.json")
        )

    def tearDown(self):
        server.STUDIO_RUNNER.store = self.old_store
        server.STUDIO_TOOLS.approvals = self.old_approvals
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=True, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        hdrs = dict(headers or {})
        if token:
            hdrs.setdefault("X-Dennis-Token", self.token)
        data = json.dumps(body) if body is not None else None
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, payload, dict(resp.getheaders())
        finally:
            conn.close()

    def json(self, method, path, body=None, **kwargs):
        status, payload, headers = self.request(method, path, body, **kwargs)
        return status, (json.loads(payload) if payload else {}), headers

    # -- auth --------------------------------------------------------------
    def test_studio_routes_require_the_owner_token(self):
        for method, path, body in (
            ("GET", "/api/studio/providers", None),
            ("GET", "/api/studio/jobs", None),
            ("GET", "/api/studio/library", None),
            ("GET", "/api/studio/tools", None),
            ("GET", "/api/studio/video-kit/templates", None),
            ("POST", "/api/studio/jobs", {"model_id": "x", "params": {}}),
            ("DELETE", "/api/studio/library/med_x", None),
        ):
            status, _, _ = self.request(method, path, body, token=False)
            self.assertEqual(status, 401, "%s %s" % (method, path))

    def test_cross_site_write_is_blocked_even_with_token(self):
        status, _, _ = self.request(
            "POST", "/api/studio/jobs", {"model_id": "x", "params": {}},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)

    # -- providers and catalog --------------------------------------------
    def test_providers_are_described_without_secrets(self):
        status, data, _ = self.json("GET", "/api/studio/providers")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["providers"]), 2)
        official = next(p for p in data["providers"] if p["id"] == "higgsfield")
        self.assertEqual(official["model_count"], 84)
        self.assertFalse(official["configured"])
        self.assertFalse(official["capabilities"]["estimate"])
        self.assertFalse(official["capabilities"]["progress"])
        self.assertEqual(
            set(official["credentials"]),
            {"configured", "source", "hint", "updated_at", "missing", "env_override"},
        )
        self.assertNotIn("super-secret", json.dumps(data))

    def test_model_list_and_detail(self):
        status, data, _ = self.json("GET", "/api/studio/models?kind=image")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 17)
        self.assertTrue(all(m["kind"] == "image" for m in data["models"]))
        status, detail, _ = self.json(
            "GET", "/api/studio/models?id=bytedance/seedance-2.5/text-to-video"
        )
        self.assertEqual(status, 200)
        self.assertTrue(detail["fields"])
        self.assertNotIn("api_key", json.dumps(detail).lower())
        status, error, _ = self.json("GET", "/api/studio/models?id=nope/nope")
        self.assertEqual(status, 404)
        self.assertEqual(error["code"], "catalog_error")

    # -- credentials -------------------------------------------------------
    def test_credentials_are_stored_privately_and_never_echoed(self):
        status, data, _ = self.json(
            "POST", "/api/studio/providers/higgsfield/credentials",
            {"api_key": "key-id-1234", "secret": "super-secret-value"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["credentials"]["configured"])
        self.assertEqual(data["credentials"]["source"], "stored")
        self.assertNotIn("super-secret-value", json.dumps(data))

        path = os.path.join(self.dir, "credentials.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, "r", encoding="utf-8") as fh:
            self.assertIn("super-secret-value", fh.read())  # stored, not exposed

        status, data, _ = self.json("GET", "/api/studio/providers")
        self.assertNotIn("super-secret-value", json.dumps(data))

        status, data, _ = self.json(
            "DELETE", "/api/studio/providers/higgsfield/credentials"
        )
        self.assertEqual(status, 200)
        self.assertFalse(data["credentials"]["configured"])

    def test_credentials_validation(self):
        status, error, _ = self.json(
            "POST", "/api/studio/providers/higgsfield/credentials",
            {"api_key": "only-key"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["code"], "credentials_error")
        status, error, _ = self.json(
            "POST", "/api/studio/providers/other/credentials",
            {"api_key": "a", "secret": "b"},
        )
        self.assertEqual(status, 400)

    # -- jobs --------------------------------------------------------------
    def test_job_submission_validates_before_calling_a_provider(self):
        status, error, _ = self.json(
            "POST", "/api/studio/jobs", {"model_id": "nope/nope", "params": {"prompt": "x"}}
        )
        self.assertEqual(status, 404)
        status, error, _ = self.json(
            "POST", "/api/studio/jobs",
            {"model_id": "bytedance/seedance-2.5/text-to-video",
             "params": {"prompt": "x", "unknown": 1}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["code"], "unknown_field")
        status, error, _ = self.json(
            "POST", "/api/studio/jobs",
            {"model_id": "bytedance/seedance-2.5/text-to-video",
             "params": {"prompt": "x", "duration": 99}},
        )
        self.assertEqual(status, 400)
        status, error, _ = self.json("POST", "/api/studio/jobs", {"params": {}})
        self.assertEqual(status, 400)

    def test_job_lifecycle_endpoints(self):
        job = server.STUDIO_RUNNER.store.create(
            provider="higgsfield", model_id="m", model_label="M", kind="video",
            state="failed", finished_at=1.0,
            error={"code": "x", "message": "y", "retryable": False},
        )
        status, data, _ = self.json("GET", "/api/studio/jobs")
        self.assertEqual(status, 200)
        self.assertEqual(data["jobs"][0]["id"], job["id"])
        first = server.STUDIO_RUNNER.store.create(
            provider="higgsfield", model_id="m", state="queued"
        )
        status, error, _ = self.json("DELETE", "/api/studio/jobs/" + first["id"])
        self.assertEqual(status, 409)
        status, data, _ = self.json("DELETE", "/api/studio/jobs/" + job["id"])
        self.assertEqual(status, 200)
        status, _, _ = self.json("GET", "/api/studio/jobs/" + job["id"])
        self.assertEqual(status, 404)

    # -- uploads and library ----------------------------------------------
    def test_upload_rejects_unsupported_types(self):
        payload = base64.b64encode(b"hello").decode("ascii")
        status, error, _ = self.json(
            "POST", "/api/studio/uploads",
            {"filename": "x.exe", "content_type": "application/x-msdownload",
             "data_base64": payload},
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["code"], "unsupported_media_type")
        status, error, _ = self.json(
            "POST", "/api/studio/uploads",
            {"filename": "x.png", "content_type": "image/png", "data_base64": "!!"},
        )
        self.assertEqual(status, 400)

    def test_media_library_serves_ranges_and_deletes(self):
        record = studio_media.add_bytes(
            b"0123456789", "image/png", kind="image", filename="frame.png",
            meta={"title": "Frame"},
        )
        status, data, _ = self.json("GET", "/api/studio/library")
        self.assertEqual(status, 200)
        self.assertEqual(data["items"][0]["id"], record["id"])

        status, body, headers = self.request(
            "GET", "/api/studio/media/" + record["id"]
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"0123456789")
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")

        status, body, headers = self.request(
            "GET", "/api/studio/media/" + record["id"],
            headers={"Range": "bytes=2-5"},
        )
        self.assertEqual(status, 206)
        self.assertEqual(body, b"2345")
        self.assertEqual(headers.get("Content-Range"), "bytes 2-5/10")

        status, _, _ = self.request(
            "GET", "/api/studio/media/" + record["id"],
            headers={"Range": "bytes=99-120"},
        )
        self.assertEqual(status, 416)

        status, data, _ = self.json("DELETE", "/api/studio/library/" + record["id"])
        self.assertEqual(status, 200)
        status, _, _ = self.json("GET", "/api/studio/library")
        self.assertEqual(status, 200)

    def test_media_urls_require_a_token(self):
        record = studio_media.add_bytes(b"x", "image/png", filename="x.png")
        status, _, _ = self.request(
            "GET", "/api/studio/media/" + record["id"], token=False
        )
        self.assertEqual(status, 401)

    # -- playlists and templates ------------------------------------------
    def test_playlist_routes_round_trip_and_media_delete_cleanup(self):
        record = studio_media.add_bytes(
            b"playlist-bytes", "image/png", kind="image", filename="shot.png",
            meta={"title": "Shot"},
        )
        status, data, _ = self.json("POST", "/api/studio/playlists", {"name": "My shots"})
        self.assertEqual(status, 200)
        playlist = data["playlist"]
        self.assertEqual(playlist["name"], "My shots")

        status, data, _ = self.json(
            "POST", "/api/studio/playlists/%s/items" % playlist["id"],
            {"media_id": record["id"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["playlist"]["item_count"], 1)

        status, data, _ = self.json("GET", "/api/studio/playlists")
        self.assertEqual(data["playlists"][0]["item_count"], 1)

        status, data, _ = self.json(
            "POST", "/api/studio/playlists/%s" % playlist["id"],
            {"name": "Renamed"},
        )
        self.assertEqual(data["playlist"]["name"], "Renamed")

        status, data, _ = self.json(
            "DELETE", "/api/studio/playlists/%s/items/%s" % (playlist["id"], record["id"])
        )
        self.assertEqual(data["playlist"]["item_count"], 0)

        self.json(
            "POST", "/api/studio/playlists/%s/items" % playlist["id"],
            {"media_id": record["id"]},
        )
        self.json("DELETE", "/api/studio/library/" + record["id"])
        status, data, _ = self.json("GET", "/api/studio/playlists")
        self.assertEqual(data["playlists"][0]["item_count"], 0)

        status, data, _ = self.json("DELETE", "/api/studio/playlists/" + playlist["id"])
        self.assertEqual(data["deleted"], playlist["id"])
        self.assertEqual(self.json("GET", "/api/studio/playlists")[1]["playlists"], [])

    def test_template_routes_validate_and_persist(self):
        status, error, _ = self.json(
            "POST", "/api/studio/templates",
            {"name": "Bad", "model_id": "nope/nope", "params": {"prompt": "x"}},
        )
        self.assertEqual(status, 404)

        status, error, _ = self.json(
            "POST", "/api/studio/templates",
            {"name": "Bad", "model_id": "bytedance/seedance-2.5/text-to-video",
             "params": {"prompt": "x", "unknown": 1}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["code"], "unknown_field")

        status, data, _ = self.json(
            "POST", "/api/studio/templates",
            {"name": "Neon", "model_id": "bytedance/seedance-2.5/text-to-video",
             "params": {"prompt": "neon city", "duration": 5}},
        )
        self.assertEqual(status, 200)
        template = data["template"]
        self.assertTrue(template["id"].startswith("tpl_"))
        self.assertEqual(template["params"]["prompt"], "neon city")
        self.assertTrue(os.path.isfile(os.path.join(self.dir, "templates.json")))

        status, data, _ = self.json("GET", "/api/studio/templates")
        self.assertEqual(status, 200)
        self.assertEqual(data["templates"][0]["id"], template["id"])

        status, data, _ = self.json("DELETE", "/api/studio/templates/" + template["id"])
        self.assertEqual(data["deleted"], template["id"])
        status, data, _ = self.json("GET", "/api/studio/templates")
        self.assertEqual(data["templates"], [])

    def test_library_import_route_uses_the_guarded_downloader(self):
        record = studio_media.add_bytes(b"import", "image/png", filename="imported.png")
        with mock.patch.object(
            studio_media, "add_from_url", return_value=record
        ) as add_from_url:
            status, data, _ = self.json(
                "POST", "/api/studio/library/import",
                {"url": "https://example.test/out.png", "kind": "image",
                 "title": "Imported", "model_id": "m"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(data["item"]["id"], record["id"])
        add_from_url.assert_called_once()
        args, kwargs = add_from_url.call_args
        self.assertEqual(args[0], "https://example.test/out.png")
        self.assertEqual(kwargs["kind"], "image")
        self.assertEqual(kwargs["meta"]["title"], "Imported")

        status, error, _ = self.json(
            "POST", "/api/studio/library/import", {"url": ""}
        )
        self.assertEqual(status, 400)
        self.assertEqual(error["code"], "missing_url")

    # -- agent tools and approvals ----------------------------------------
    def test_tool_surface_and_approval_flow(self):
        status, data, _ = self.json("GET", "/api/studio/tools")
        self.assertEqual(status, 200)
        names = {tool["name"] for tool in data["tools"]}
        self.assertIn("studio_create_job", names)
        mutating = {t["name"]: t["mutating"] for t in data["tools"]}
        self.assertTrue(mutating["studio_create_job"])
        self.assertFalse(mutating["studio_list_models"])

        status, data, _ = self.json(
            "POST", "/api/studio/tools/call",
            {"name": "studio_list_models", "arguments": {"kind": "image"}},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(len(data["result"]["models"]), 17)

        status, data, _ = self.json(
            "POST", "/api/studio/tools/call",
            {"name": "studio_create_job",
             "arguments": {
                 "model_id": "bytedance/seedance-2.5/text-to-video",
                 "params": {"prompt": "a cat"},
             }},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "approval_required")
        approval = data["approval"]
        self.assertEqual(approval["status"], "pending")
        self.assertEqual(approval["preview"]["action"], "create_job")

        status, data, _ = self.json(
            "POST", "/api/studio/approvals/" + approval["id"],
            {"decision": "reject"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "rejected")
        status, error, _ = self.json(
            "POST", "/api/studio/approvals/" + approval["id"],
            {"decision": "approve"},
        )
        self.assertEqual(status, 409)

    def test_approved_tool_executes_with_the_registered_runner(self):
        fake_job = {"id": "stj_fake", "state": "queued"}
        status, data, _ = self.json(
            "POST", "/api/studio/tools/call",
            {"name": "studio_create_job",
             "arguments": {
                 "model_id": "bytedance/seedance-2.5/text-to-video",
                 "params": {"prompt": "approved"},
             }},
        )
        approval_id = data["approval"]["id"]
        with mock.patch.object(
            server.STUDIO_TOOLS.runner, "submit",
            return_value=(fake_job, False),
        ) as submit:
            status, data, _ = self.json(
                "POST", "/api/studio/approvals/" + approval_id,
                {"decision": "approve"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "approved")
        self.assertEqual(data["result"]["job"]["id"], "stj_fake")
        submit.assert_called_once()

    def test_tool_calls_reject_unknown_names_and_arguments(self):
        status, error, _ = self.json(
            "POST", "/api/studio/tools/call", {"name": "studio_nope", "arguments": {}}
        )
        self.assertEqual(status, 404)
        status, error, _ = self.json(
            "POST", "/api/studio/tools/call",
            {"name": "studio_list_models", "arguments": {"bogus": 1}},
        )
        self.assertEqual(status, 400)

    # -- Video Kit chat templates -----------------------------------------
    def test_video_kit_templates_come_from_the_vault(self):
        vault_dir = os.path.join(self.dir, "vault")
        skill_dir = os.path.join(vault_dir, "Skills", "reel-maker")
        os.makedirs(skill_dir)
        with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "---\n"
                "name: reel-maker\n"
                "description: Builds a short vertical video from a reference reel.\n"
                "---\n\n"
                "# Reel Maker\n\n"
                "## Initial generation prompt\n\n"
                "```text\nA cinematic vertical reel of the hero product.\n```\n"
            )
        not_video_dir = os.path.join(vault_dir, "Skills", "tax-helper")
        os.makedirs(not_video_dir)
        with open(os.path.join(not_video_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
            fh.write(
                "---\n"
                "name: tax-helper\n"
                "description: Reconciles quarterly ledgers and receipts.\n"
                "---\n\n# Tax Helper\n\n## Method\n\nAdd the columns.\n"
            )
        with mock.patch.object(dennis_vault, "VAULT_DIR", vault_dir):
            status, data, _ = self.json("GET", "/api/studio/video-kit/templates")
        self.assertEqual(status, 200)
        self.assertEqual(data["count"], 1)
        template = data["templates"][0]
        self.assertEqual(template["id"], "reel-maker")
        self.assertEqual(template["prompt"], "A cinematic vertical reel of the hero product.")

    # -- MCP configuration -------------------------------------------------
    def test_mcp_config_is_saved_without_echoing_env_values(self):
        status, data, _ = self.json(
            "POST", "/api/studio/mcp/config",
            {"command": "uvx",
             "args": ["--from", "higgsfield-mcp-unified", "higgsfield-mcp"],
             "env": {"HIGGSFIELD_SECRET": "top-secret"}},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["config"]["configured"])
        self.assertNotIn("top-secret", json.dumps(data))
        status, data, _ = self.json("GET", "/api/studio/mcp")
        self.assertNotIn("top-secret", json.dumps(data))
        status, data, _ = self.json("DELETE", "/api/studio/mcp/config")
        self.assertFalse(data["config"]["configured"])

    # -- Video Kit: Vault avatars and save-to-Vault ------------------------
    def test_video_kit_resolves_vault_avatars_and_serves_assets(self):
        vault_dir = os.path.join(self.dir, "vault")
        os.makedirs(os.path.join(vault_dir, "Avatars"))
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAFgwJ/lw5G6wAAAABJRU5ErkJggg=="
        )
        with open(os.path.join(vault_dir, "Avatars", "Clair.png"), "wb") as fh:
            fh.write(png)
        os.makedirs(os.path.join(vault_dir, "Characters"))
        with open(os.path.join(vault_dir, "Characters", "Mia.md"), "w", encoding="utf-8") as fh:
            fh.write("# Mia\n\n![Mia avatar](../Avatars/Clair.png)\n")
        with open(os.path.join(vault_dir, "Ideas.md"), "w", encoding="utf-8") as fh:
            fh.write("# Ideas\n\nJust some notes.\n")

        with mock.patch.object(dennis_vault, "VAULT_DIR", vault_dir):
            status, data, _ = self.json("GET", "/api/studio/video-kit/avatars")
            self.assertEqual(status, 200)
            names = {item["name"]: item for item in data["avatars"]}
            self.assertIn("Clair", names)
            self.assertIn("Mia", names)
            self.assertNotIn("Ideas", names)
            self.assertTrue(names["Clair"]["url"].startswith("/api/vault/asset?path="))
            self.assertTrue(names["Mia"]["url"].startswith("/api/vault/asset?path="))

            asset_path = names["Clair"]["url"]
            status, payload, headers = self.request("GET", asset_path)
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Content-Type"), "image/png")
            self.assertEqual(payload, png)

        status, data, _ = self.json(
            "GET", "/api/vault/asset?path=../outside.png")
        self.assertEqual(status, 404)

    def test_video_kit_analyzes_attached_base64_without_provider_url(self):
        png = base64.b64encode(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
            "z8BQDwAFgwJ/lw5G6wAAAABJRU5ErkJggg=="
        )).decode("ascii")
        with mock.patch.object(server.dennis_skills, "_load_env", return_value={}):
            status, data, _ = self.json(
                "POST", "/api/studio/video-kit/analyze",
                {
                    "reference": {
                        "name": "kit.png",
                        "url": "",
                        "data_base64": png,
                        "content_type": "image/png",
                    },
                    "prompt": "Neon close-up",
                },
            )
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["platform"], "direct_image")
        self.assertTrue(data["prompt"])
        self.assertIn("Neon close-up", data["prompt"])

    def test_video_kit_routes_images_and_platform_videos_to_the_right_eyes(self):
        with mock.patch.object(
                server.dennis_skills, "_load_env",
                return_value={"GEMINI_API_KEY": "test"}), \
                mock.patch.object(
                    server.dennis_skills, "_image_analysis",
                    return_value="IMAGE ANALYSIS") as image_pass, \
                mock.patch.object(
                    server.dennis_skills, "_video_analysis",
                    return_value="VIDEO ANALYSIS") as video_pass:
            status, data, _ = self.json(
                "POST", "/api/studio/video-kit/analyze",
                {
                    "reference": {
                        "url": "https://cdn.example.com/avatar",
                        "name": "avatar.png",
                        "content_type": "image/png",
                    }
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(data["analysis"], "IMAGE ANALYSIS")
            image_pass.assert_called_once()

            status, data, _ = self.json(
                "POST", "/api/studio/video-kit/analyze",
                {
                    "reference": {
                        "url": "https://www.tiktok.com/@clair/video/1",
                        "name": "tiktok reel",
                        "content_type": "video/mp4",
                    }
                },
            )
            self.assertEqual(status, 200)
            self.assertEqual(data["platform"], "tiktok")
            passed = video_pass.call_args[0][0]
            self.assertEqual(passed["kind"], "platform")
            self.assertEqual(passed["platform"], "tiktok")
            self.assertEqual(data["analysis"], "VIDEO ANALYSIS")

    def test_video_kit_saves_analysis_prompt_as_vault_skill(self):
        vault_dir = os.path.join(self.dir, "vault")
        os.makedirs(vault_dir)
        prompt = "A cinematic vertical reel of Clair walking through neon Tokyo."
        with mock.patch.object(dennis_vault, "VAULT_DIR", vault_dir):
            status, data, _ = self.json(
                "POST", "/api/studio/video-kit/save-skill",
                {
                    "name": "Clair Neon Reel",
                    "prompt": prompt,
                    "analysis": "Shot 1: close-up. Shot 2: tracking shot.",
                    "source": "https://www.tiktok.com/@clair/video/1",
                    "platform": "tiktok",
                    "model": "higgsfield/video",
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(data["success"])
            skill = data["skill"]
            target = os.path.join(vault_dir, skill["path"])
            self.assertTrue(os.path.isfile(target))
            with open(target, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn(prompt, body)
            self.assertIn("Shot 2: tracking shot.", body)
            self.assertIn("tiktok", body)

            status, data, _ = self.json("GET", "/api/studio/video-kit/templates")
            self.assertEqual(status, 200)
            ids = [item["id"] for item in data["templates"]]
            self.assertIn(skill["name"], ids)

        status, data, _ = self.json(
            "POST", "/api/studio/video-kit/save-skill", {"name": "empty"})
        self.assertEqual(status, 400)


    def test_fashion_config_lists_reference_models_without_credentials(self):
        status, data, _ = self.json("GET", "/api/studio/fashion/config")
        self.assertEqual(status, 200)
        self.assertEqual(data["workflow"], "fashion_video")
        self.assertTrue(data["models"])
        ids = [item["id"] for item in data["models"]]
        self.assertIn("bytedance/seedance-2.0/reference-to-video", ids)
        self.assertTrue(data["defaults"]["model_id"])
        self.assertEqual(data["limits"]["outfits"], 5)
        self.assertEqual(data["limits"]["outfit_images"], 5)
        self.assertFalse(data["configured"])
        self.assertTrue(data["presets"])
        self.assertTrue(data["model_presets"])
        self.assertTrue(data["outfit_presets"])
        self.assertIn("type", data["preset_filters"])
        self.assertIn("style", data["preset_filters"])
        self.assertIn("model", data["preset_filters"])
        self.assertIn("engine", data["preset_filters"])

    def test_fashion_job_validates_the_draft_before_submitting(self):
        status, data, _ = self.json(
            "POST", "/api/studio/fashion/jobs",
            {"draft": {
                "outfits": [{"images": ["https://example.com/top.jpg"]}],
                "reference_video": "https://example.com/ref.mp4",
            }},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "missing_asset")

    def test_fashion_job_accepts_a_skipped_reference_video(self):
        # Step 4 is skippable: without a reference clip the plan moves to an
        # image-to-video model and still submits.
        fake_job = {"id": "job-i2v", "state": "queued"}
        body = {
            "provider": "higgsfield",
            "draft": {
                "model": "https://example.com/model.jpg",
                "prompt": "slow turn toward camera",
            },
        }
        with mock.patch.object(
                server.STUDIO_RUNNER, "submit", return_value=(fake_job, False)
        ) as submit:
            status, data, _ = self.json("POST", "/api/studio/fashion/jobs", body)
        self.assertEqual(status, 200)
        self.assertEqual(data["job"]["id"], "job-i2v")
        provider_id, model_id, params = submit.call_args[0][:3]
        self.assertEqual(provider_id, "higgsfield")
        self.assertTrue(model_id.endswith("/image-to-video"))
        self.assertNotIn("video_urls", params)
        self.assertNotIn("video_url", params)

    def test_fashion_job_maps_the_three_step_draft_to_provider_params(self):
        fake_job = {"id": "job-fashion", "state": "queued"}
        body = {
            "provider": "higgsfield",
            "model_id": "bytedance/seedance-2.0/reference-to-video",
            "draft": {
                "model": "https://example.com/model.jpg",
                "scene_image": "https://example.com/scene.jpg",
                "outfits": [
                    {"name": "Day", "images": ["https://example.com/top.jpg"]},
                    {"name": "Night", "images": ["https://example.com/dress.jpg"]},
                ],
                "reference_video": "https://example.com/ref.mp4",
                "template": "try_on",
                "prompt": "end with a slow turn",
                "resolution": "720p",
                "duration": 6,
                "aspect_ratio": "9:16",
                "generate_audio": False,
            },
        }
        with mock.patch.object(
                server.STUDIO_RUNNER, "submit", return_value=(fake_job, False)
        ) as submit:
            status, data, _ = self.json("POST", "/api/studio/fashion/jobs", body)
        self.assertEqual(status, 200)
        self.assertEqual(data["job"]["id"], "job-fashion")
        self.assertEqual(data["plan"]["model_id"], body["model_id"])
        self.assertEqual(data["plan"]["image_count"], 4)
        provider_id, model_id, params = submit.call_args[0][:3]
        self.assertEqual(provider_id, "higgsfield")
        self.assertEqual(model_id, body["model_id"])
        self.assertEqual(params["image_urls"], [
            "https://example.com/model.jpg",
            "https://example.com/scene.jpg",
            "https://example.com/top.jpg",
            "https://example.com/dress.jpg",
        ])
        self.assertEqual(params["video_urls"], ["https://example.com/ref.mp4"])
        self.assertEqual(params["resolution"], "720p")
        self.assertEqual(params["duration"], 6)
        self.assertFalse(params["generate_audio"])
        self.assertIn("@Image2 is a SCENE REFERENCE ONLY", params["prompt"])
        self.assertIn("@Image3 is outfit 1", params["prompt"])
        self.assertIn("@Image4 is outfit 2", params["prompt"])
        self.assertIn("end with a slow turn", params["prompt"])

    def test_fashion_job_resolves_preset_backed_drafts(self):
        config_status, config, _ = self.json("GET", "/api/studio/fashion/config")
        self.assertEqual(config_status, 200)
        preset = next(item for item in config["presets"]
                      if item["outfits"] == 1 and item.get("suggested_model_id"))
        garment = config["outfit_presets"][0]
        fake_job = {"id": "job-preset", "state": "queued"}
        with mock.patch.object(
                server.STUDIO_RUNNER, "submit", return_value=(fake_job, False)
        ) as submit:
            status, data, _ = self.json(
                "POST", "/api/studio/fashion/jobs",
                {
                    "provider": "higgsfield",
                    "draft": {
                        "preset": preset["id"],
                        "model_preset": config["model_presets"][0]["id"],
                        "outfits": [{"preset": garment["id"]}],
                    },
                },
            )
        self.assertEqual(status, 200)
        self.assertEqual(data["job"]["id"], "job-preset")
        provider_id, model_id, params = submit.call_args[0][:3]
        self.assertEqual(model_id, preset["suggested_model_id"])
        self.assertEqual(params["video_urls"], [preset["video"]])
        self.assertEqual(params["image_urls"][0], config["model_presets"][0]["cover"])
        self.assertEqual(params["image_urls"][1], garment["cover"])

    def test_fashion_job_rejects_models_outside_the_workflow(self):
        status, data, _ = self.json(
            "POST", "/api/studio/fashion/jobs",
            {
                "model_id": "bytedance/seedance-2.0/text-to-video",
                "draft": {
                    "model": "https://example.com/model.jpg",
                    "outfits": [{"images": ["https://example.com/top.jpg"]}],
                    "reference_video": "https://example.com/ref.mp4",
                },
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "unknown_model")


if __name__ == "__main__":
    unittest.main()
