"""Unit tests for the Studio workspace registry and project store.

Run:  python3 -m unittest tests.test_studio_workspaces

Storage is redirected into a temporary directory; no server is started.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_workspaces as workspaces  # noqa: E402


class WorkspaceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = self.tmp.name
        self.patches = [
            unittest.mock.patch.object(workspaces, "STUDIO_DIR", self.dir),
            unittest.mock.patch.object(
                workspaces, "WORKSPACES_FILE", os.path.join(self.dir, "workspaces.json")
            ),
            unittest.mock.patch.object(
                workspaces, "WORKSPACES_DIR", os.path.join(self.dir, "workspaces")
            ),
        ]
        for patch in self.patches:
            patch.start()
        self.addCleanup(self.tmp.cleanup)
        for patch in self.patches:
            self.addCleanup(patch.stop)

    # -- registry ----------------------------------------------------------
    def test_overview_lists_both_workspaces(self):
        data = workspaces.overview()
        ids = [w["id"] for w in data["workspaces"]]
        self.assertEqual(ids, ["ads", "youtube"])
        self.assertEqual(data["active"], "ads")

    def test_active_workspace_persists(self):
        self.assertEqual(workspaces.set_active("youtube"), "youtube")
        self.assertEqual(workspaces.get_active(), "youtube")

    def test_unknown_workspace_rejected(self):
        with self.assertRaises(workspaces.WorkspaceError) as ctx:
            workspaces.descriptor("nope")
        self.assertEqual(ctx.exception.status, 404)

    def test_youtube_descriptor_has_stage_sections(self):
        descriptor = workspaces.descriptor("youtube")
        section_ids = [s["id"] for s in descriptor["sections"]]
        self.assertIn("overview", section_ids)
        self.assertIn("channel-manager", section_ids)
        self.assertIn("publishing", section_ids)
        ads = workspaces.descriptor("ads")
        self.assertEqual(ads["sections"], [])

    # -- isolation ---------------------------------------------------------
    def test_projects_are_isolated_per_workspace(self):
        workspaces.create_project("youtube", {"title": "Tax video"})
        self.assertEqual(workspaces.list_projects("youtube")["total"], 1)
        self.assertEqual(workspaces.list_projects("ads")["total"], 0)
        self.assertEqual(workspaces.descriptor("ads")["counts"]["total"], 0)

    def test_project_ids_are_prefixed_per_workspace(self):
        youtube = workspaces.create_project("youtube", {"title": "YT"})
        ads = workspaces.create_project("ads", {"title": "Ads"})
        self.assertTrue(youtube["id"].startswith("ytp_"))
        self.assertTrue(ads["id"].startswith("sp_"))

    # -- validation --------------------------------------------------------
    def test_title_is_required(self):
        with self.assertRaises(workspaces.WorkspaceError):
            workspaces.create_project("youtube", {"title": "   "})

    def test_title_length_is_bounded(self):
        with self.assertRaises(workspaces.WorkspaceError):
            workspaces.create_project("youtube", {"title": "x" * 200})

    def test_unknown_status_rejected(self):
        with self.assertRaises(workspaces.WorkspaceError) as ctx:
            workspaces.create_project("youtube", {"title": "T", "status": "bogus"})
        self.assertEqual(ctx.exception.code, "invalid_status")

    def test_unknown_stage_rejected(self):
        with self.assertRaises(workspaces.WorkspaceError):
            workspaces.create_project("youtube", {"title": "T", "stage": "bogus"})

    def test_tags_are_sanitized(self):
        project = workspaces.create_project("youtube", {"title": "T", "tags": ["Tax", "", "Tax"]})
        self.assertEqual(project["tags"], ["Tax"])

    # -- lifecycle ---------------------------------------------------------
    def test_create_update_and_counts(self):
        project = workspaces.create_project("youtube", {"title": "RPM explained", "content_pillar": "Tax"})
        updated = workspaces.update_project("youtube", project["id"], {"status": "in_progress", "stage": "script"})
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["stage"], "script")
        counts = workspaces.descriptor("youtube")["counts"]
        self.assertEqual(counts["total"], 1)
        self.assertEqual(counts["scripts_in_progress"], 1)
        self.assertEqual(counts["by_stage"]["script"], 1)

    def test_update_missing_project_404(self):
        with self.assertRaises(workspaces.WorkspaceError) as ctx:
            workspaces.update_project("youtube", "ytp_missing", {"title": "X"})
        self.assertEqual(ctx.exception.status, 404)

    def test_archive_and_filtering(self):
        project = workspaces.create_project("youtube", {"title": "Old"})
        workspaces.update_project("youtube", project["id"], {"status": "archived"})
        self.assertEqual(workspaces.list_projects("youtube")["total"], 0)
        archived = workspaces.list_projects("youtube", status="archived")
        self.assertEqual(archived["total"], 1)
        included = workspaces.list_projects("youtube", include_archived=True)
        self.assertEqual(included["total"], 1)

    def test_delete_is_soft_and_reversible_by_read(self):
        project = workspaces.create_project("youtube", {"title": "Gone"})
        workspaces.delete_project("youtube", project["id"])
        self.assertEqual(workspaces.list_projects("youtube")["total"], 0)
        with self.assertRaises(workspaces.WorkspaceError):
            workspaces.get_project("youtube", project["id"])
        restored = workspaces.get_project("youtube", project["id"], include_deleted=True)
        self.assertIsNotNone(restored["deleted_at"])

    def test_search_matches_title_and_pillar(self):
        workspaces.create_project("youtube", {"title": "Deductions", "content_pillar": "Tax Education"})
        workspaces.create_project("youtube", {"title": "Cash flow", "content_pillar": "Finance"})
        self.assertEqual(workspaces.list_projects("youtube", query="deduct")["total"], 1)
        self.assertEqual(workspaces.list_projects("youtube", query="finance")["total"], 1)
        self.assertEqual(workspaces.list_projects("youtube", query="nothing")["total"], 0)

    # -- settings ------------------------------------------------------------
    def test_settings_defaults_and_merge(self):
        settings = workspaces.get_settings("youtube")
        self.assertEqual(settings["render"]["width"], 1920)
        self.assertEqual(settings["publishing_schedule"], ["mon", "wed", "fri"])
        updated = workspaces.update_settings("youtube", {"render": {"fps": 24}})
        self.assertEqual(updated["render"]["fps"], 24)
        self.assertEqual(updated["render"]["width"], 1920)

    def test_ads_settings_do_not_leak_into_youtube(self):
        workspaces.update_settings("ads", {"default_provider": "higgsfield"})
        youtube = workspaces.get_settings("youtube")
        self.assertNotIn("default_provider", youtube)
        ads = workspaces.get_settings("ads")
        self.assertEqual(ads["default_provider"], "higgsfield")

    def test_invalid_settings_payload(self):
        with self.assertRaises(workspaces.WorkspaceError):
            workspaces.update_settings("youtube", ["not", "an", "object"])

    # -- activity ------------------------------------------------------------
    def test_activity_is_recorded_per_workspace(self):
        project = workspaces.create_project("youtube", {"title": "Activity"})
        workspaces.activity("youtube")
        ads_activity = workspaces.activity("ads")["activity"]
        youtube_activity = workspaces.activity("youtube")["activity"]
        self.assertEqual(ads_activity, [])
        events = [item["event"] for item in youtube_activity]
        self.assertIn("project.created", events)
        self.assertTrue(youtube_activity[0]["created_at"] >= youtube_activity[-1]["created_at"])
        self.assertEqual(youtube_activity[0]["project_id"], project["id"])

    def test_files_are_owner_only(self):
        workspaces.create_project("youtube", {"title": "Perms"})
        projects_file = os.path.join(self.dir, "workspaces", "youtube", "projects.json")
        self.assertTrue(os.path.isfile(projects_file))
        mode = os.stat(projects_file).st_mode & 0o777
        self.assertEqual(mode, 0o600)


import unittest.mock  # noqa: E402  (kept last so patch context reads cleanly)

if __name__ == "__main__":
    unittest.main()
