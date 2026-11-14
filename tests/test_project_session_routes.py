"""HTTP tests for renaming a project session (the project name).

Run:  python3 -m unittest tests.test_project_session_routes
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import auth as dennis_auth  # noqa: E402
from backend import config, session_store  # noqa: E402


class ProjectSessionRenameRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.token = dennis_auth.AUTH.token

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        fd, self.path = tempfile.mkstemp(prefix="dennis-sessions-", suffix=".json")
        os.write(fd, b"{}")
        os.close(fd)
        self.patch = mock.patch.object(config, "SESSION_STORE_FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        try:
            os.remove(self.path)
        except OSError:
            pass

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body) if body is not None else None
        headers = {"X-Dennis-Token": self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        finally:
            conn.close()

    def _session(self, title="Old name"):
        return session_store.create_session(
            config.DEFAULT_OWNER, "opencode", "proj", "/tmp/proj", title=title)

    def test_rename_updates_the_title(self):
        s = self._session()
        status, data = self.request(
            "PATCH", "/api/project-sessions/" + s["id"], {"title": "Trading Den"})
        self.assertEqual(status, 200)
        self.assertEqual(data["session"]["title"], "Trading Den")
        self.assertEqual(
            session_store.get_session(config.DEFAULT_OWNER, s["id"])["title"],
            "Trading Den")
        # The project folder is never renamed.
        self.assertEqual(data["session"]["project_path"], "/tmp/proj")

    def test_blank_title_is_rejected(self):
        s = self._session()
        status, data = self.request("PATCH", "/api/project-sessions/" + s["id"], {"title": "  "})
        self.assertEqual(status, 400)
        self.assertIn("required", data["error"].lower())

    def test_unknown_session_is_404(self):
        status, data = self.request(
            "PATCH", "/api/project-sessions/ps-missing", {"title": "X"})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
