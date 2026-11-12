"""HTTP tests for the Studio workspace routes.

Uses the real stdlib server with the workspace store redirected into a
temporary directory. No provider network call is made.

Run:  python3 -m unittest tests.test_studio_workspace_routes
"""

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
from backend import studio_workspaces  # noqa: E402


class WorkspaceRouteTests(unittest.TestCase):
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
            mock.patch.object(studio_workspaces, "STUDIO_DIR", self.dir),
            mock.patch.object(
                studio_workspaces, "WORKSPACES_FILE",
                os.path.join(self.dir, "workspaces.json"),
            ),
            mock.patch.object(
                studio_workspaces, "WORKSPACES_DIR",
                os.path.join(self.dir, "workspaces"),
            ),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(self.tmp.cleanup)
        for patch in self.patches:
            self.addCleanup(patch.stop)

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
            return resp.status, (json.loads(payload) if payload else {}), dict(resp.getheaders())
        finally:
            conn.close()

    # -- auth ----------------------------------------------------------------
    def test_workspace_routes_require_the_owner_token(self):
        for method, path, body in (
            ("GET", "/api/studio/workspaces", None),
            ("POST", "/api/studio/workspaces/youtube/projects", {"title": "X"}),
            ("PATCH", "/api/studio/workspaces/youtube/projects/x", {"title": "X"}),
            ("DELETE", "/api/studio/workspaces/youtube/projects/x", None),
        ):
            status, _, _ = self.request(method, path, body, token=False)
            self.assertEqual(status, 401, "%s %s" % (method, path))

    def test_cross_site_patch_is_blocked(self):
        status, _, _ = self.request(
            "PATCH", "/api/studio/workspaces/youtube/projects/x", {"title": "X"},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)

    # -- registry ------------------------------------------------------------
    def test_overview_lists_ads_and_youtube(self):
        status, data, _ = self.request("GET", "/api/studio/workspaces")
        self.assertEqual(status, 200)
        self.assertEqual([w["id"] for w in data["workspaces"]], ["ads", "youtube"])
        self.assertEqual(data["active"], "ads")
        youtube = next(w for w in data["workspaces"] if w["id"] == "youtube")
        self.assertEqual(youtube["subtitle"], "Research. Script. Visualize. Animate. Publish.")

    def test_active_workspace_can_be_set(self):
        status, data, _ = self.request(
            "POST", "/api/studio/workspaces/active", {"workspace": "youtube"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["active"], "youtube")
        status, data, _ = self.request("GET", "/api/studio/workspaces")
        self.assertEqual(data["active"], "youtube")

    def test_unknown_workspace_returns_404(self):
        status, data, _ = self.request("GET", "/api/studio/workspaces/nope/projects")
        self.assertEqual(status, 404)
        self.assertEqual(data["code"], "unknown_workspace")

    # -- projects --------------------------------------------------------------
    def test_project_crud_round_trip(self):
        status, data, _ = self.request(
            "POST", "/api/studio/workspaces/youtube/projects",
            {"title": "RPM explained", "content_pillar": "Tax", "tags": ["tax", ""]},
        )
        self.assertEqual(status, 200)
        project = data["project"]
        self.assertTrue(project["id"].startswith("ytp_"))
        self.assertEqual(project["status"], "draft")
        self.assertEqual(project["stage"], "research")
        self.assertEqual(project["tags"], ["tax"])

        status, data, _ = self.request(
            "PATCH", "/api/studio/workspaces/youtube/projects/" + project["id"],
            {"status": "in_progress", "stage": "script"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["project"]["status"], "in_progress")

        status, data, _ = self.request(
            "GET", "/api/studio/workspaces/youtube/projects"
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["counts"]["scripts_in_progress"], 1)

        status, data, _ = self.request(
            "GET", "/api/studio/workspaces/youtube/projects/" + project["id"]
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["project"]["title"], "RPM explained")

    def test_project_validation_errors(self):
        status, data, _ = self.request(
            "POST", "/api/studio/workspaces/youtube/projects", {"title": "  "}
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "invalid_project")

        status, data, _ = self.request(
            "POST", "/api/studio/workspaces/youtube/projects",
            {"title": "X", "status": "bogus"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["code"], "invalid_status")

    def test_soft_delete_hides_and_keeps_the_record(self):
        status, data, _ = self.request(
            "POST", "/api/studio/workspaces/youtube/projects", {"title": "Temp"}
        )
        project_id = data["project"]["id"]
        status, data, _ = self.request(
            "DELETE", "/api/studio/workspaces/youtube/projects/" + project_id
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["deleted"], project_id)

        status, data, _ = self.request("GET", "/api/studio/workspaces/youtube/projects")
        self.assertEqual(data["total"], 0)

        status, data, _ = self.request(
            "GET", "/api/studio/workspaces/youtube/projects/" + project_id
        )
        self.assertEqual(status, 404)

    def test_workspaces_are_isolated_over_http(self):
        self.request("POST", "/api/studio/workspaces/youtube/projects", {"title": "YT"})
        self.request("POST", "/api/studio/workspaces/ads/projects", {"title": "Ads"})

        _, youtube, _ = self.request("GET", "/api/studio/workspaces/youtube/projects")
        _, ads, _ = self.request("GET", "/api/studio/workspaces/ads/projects")
        self.assertEqual(youtube["total"], 1)
        self.assertEqual(ads["total"], 1)
        self.assertEqual(youtube["projects"][0]["title"], "YT")
        self.assertEqual(ads["projects"][0]["title"], "Ads")

        # A YouTube project id is invisible from the Ads workspace.
        youtube_id = youtube["projects"][0]["id"]
        status, _, _ = self.request(
            "GET", "/api/studio/workspaces/ads/projects/" + youtube_id
        )
        self.assertEqual(status, 404)

    # -- settings and activity ---------------------------------------------------
    def test_settings_round_trip(self):
        status, data, _ = self.request("GET", "/api/studio/workspaces/youtube/settings")
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["render"]["width"], 1920)

        status, data, _ = self.request(
            "PATCH", "/api/studio/workspaces/youtube/settings", {"render": {"fps": 24}}
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["settings"]["render"]["fps"], 24)
        self.assertEqual(data["settings"]["render"]["width"], 1920)

    def test_activity_lists_events_newest_first(self):
        self.request("POST", "/api/studio/workspaces/youtube/projects", {"title": "A"})
        self.request("POST", "/api/studio/workspaces/youtube/projects", {"title": "B"})
        status, data, _ = self.request("GET", "/api/studio/workspaces/youtube/activity")
        self.assertEqual(status, 200)
        events = [item["event"] for item in data["activity"]]
        self.assertEqual(events.count("project.created"), 2)
        stamps = [item["created_at"] for item in data["activity"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_storage_is_owner_only(self):
        self.request("POST", "/api/studio/workspaces/youtube/projects", {"title": "Perms"})
        path = os.path.join(self.dir, "workspaces", "youtube", "projects.json")
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
