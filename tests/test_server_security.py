"""HTTP security tests for the DENNIS Agentic OS server (stdlib unittest only).

Run:  python3 -m unittest tests.test_server_security

Covers the owner-token gate on mutating endpoints, the cross-site (CSRF)
guard, the installer allowlist and the diagnostic-argument allowlist. It is
read-only against ``data/`` and never runs a real installer.
"""

import http.client
import gzip
import json
import os
import sys
import threading
import unittest

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import auth as dennis_auth  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ServerSecurityTests(unittest.TestCase):
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

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = json.dumps(body) if body is not None else None
        hdrs = dict(headers or {})
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            payload = resp.read()
            return resp.status, payload, dict(resp.getheaders())
        finally:
            conn.close()

    def test_session_token_endpoint_is_public(self):
        status, body, _ = self.request("GET", "/api/auth/session")
        self.assertEqual(status, 200)
        self.assertIn("token", json.loads(body))

    def test_no_wildcard_cors_header(self):
        _, _, headers = self.request("GET", "/api/auth/session")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_reading_api_requires_token(self):
        status, _, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 401)

    def test_mutating_endpoint_requires_token(self):
        status, _, _ = self.request("POST", "/api/chat", {"message": "hi"})
        self.assertEqual(status, 401)

    def test_delete_endpoint_requires_token(self):
        status, _, _ = self.request("DELETE", "/api/sessions/nope")
        self.assertEqual(status, 401)

    def test_token_allows_request_to_reach_handler(self):
        # /bin/echo --version proves the authenticated request reached the
        # diagnostic handler and ran (exit 0).
        status, body, _ = self.request(
            "POST", "/api/agents/test",
            {"binary_path": "/bin/echo", "args": ["--version"]},
            {"X-Dennis-Token": self.token})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["success"])

    def test_query_token_is_accepted(self):
        status, _, _ = self.request("GET", "/api/status?token=" + self.token)
        self.assertEqual(status, 200)

    def test_cross_site_request_blocked_even_with_token(self):
        status, _, _ = self.request(
            "POST", "/api/chat", {"message": "hi"},
            {"Sec-Fetch-Site": "cross-site", "X-Dennis-Token": self.token})
        self.assertEqual(status, 403)

    def test_mismatched_origin_blocked(self):
        status, _, _ = self.request(
            "POST", "/api/chat", {"message": "hi"},
            {"Origin": "http://evil.example", "X-Dennis-Token": self.token})
        self.assertEqual(status, 403)

    def test_installer_rejects_unknown_command(self):
        status, body, _ = self.request(
            "POST", "/api/agents/install",
            {"command": "rm -rf / #", "binary_name": "x"},
            {"X-Dennis-Token": self.token})
        self.assertEqual(status, 400)
        self.assertIn("approved", json.loads(body)["error"].lower())

    def test_diagnostic_rejects_arbitrary_args(self):
        status, body, _ = self.request(
            "POST", "/api/agents/test",
            {"binary_path": "/bin/sh", "args": ["-c", "echo pwned"]},
            {"X-Dennis-Token": self.token})
        self.assertEqual(status, 400)
        self.assertIn("unsupported", json.loads(body)["error"].lower())

class StaticAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, headers=dict(headers or {}))
            resp = conn.getresponse()
            return resp.status, resp.read(), dict(resp.getheaders())
        finally:
            conn.close()

    def test_large_asset_is_gzipped_and_matches_source(self):
        status, body, headers = self.request(
            "GET", "/js/app.js", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        with open(os.path.join(ROOT, "js", "app.js"), "rb") as fh:
            self.assertEqual(gzip.decompress(body), fh.read())

    def test_root_document_is_gzipped(self):
        status, body, headers = self.request(
            "GET", "/", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        with open(os.path.join(ROOT, "index.html"), "rb") as fh:
            self.assertEqual(gzip.decompress(body), fh.read())

    def test_identity_client_gets_uncompressed_body(self):
        status, body, headers = self.request(
            "GET", "/js/app.js", {"Accept-Encoding": "identity"})
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)

    def test_non_text_asset_is_not_compressed(self):
        status, _, headers = self.request(
            "GET", "/dennis_agentic_os_v5.png", {"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)

    def test_static_asset_supports_conditional_304(self):
        _, _, headers = self.request("GET", "/js/app.js")
        last_modified = headers["Last-Modified"]
        status, body, _ = self.request(
            "GET", "/js/app.js",
            {"Accept-Encoding": "gzip", "If-Modified-Since": last_modified})
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")

    def test_api_responses_are_no_store(self):
        _, _, headers = self.request("GET", "/api/auth/session")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_static_responses_are_revalidated(self):
        _, _, headers = self.request("GET", "/js/app.js")
        self.assertEqual(headers.get("Cache-Control"), "no-cache, must-revalidate")


if __name__ == "__main__":
    unittest.main()
