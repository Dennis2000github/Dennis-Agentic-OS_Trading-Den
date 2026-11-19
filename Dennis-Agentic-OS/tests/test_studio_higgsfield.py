"""Tests for the official Higgsfield adapter against a local mock API.

No network access and no credentials: the client is pointed at a local HTTP
server that records exactly what was sent.

Run:  python3 -m unittest tests.test_studio_higgsfield
"""

import http.server
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_higgsfield as hf

REQUESTS = []


class MockHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return {"_raw": raw.decode("utf-8", "replace")}

    def _send(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _record(self):
        REQUESTS.append({
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers),
            "body": self._body(),
        })

    def do_POST(self):
        self._record()
        host = "http://%s" % self.headers.get("Host")
        if self.path == "/files/generate-upload-url":
            return self._send(200, {
                "public_url": "https://cdn.example.com/input/example.jpeg",
                "upload_url": host + "/storage/put",
                "content_type": "image/jpeg",
                "upload_headers": {
                    "Content-Type": "image/jpeg",
                    "x-amz-tagging": "retention=temporary",
                },
            })
        if self.path == "/bytedance/seedance-2.5/text-to-video":
            return self._send(200, {
                "status": "queued",
                "request_id": "req-123",
                "status_url": host + "/requests/req-123/status",
                "cancel_url": host + "/requests/req-123/cancel",
            })
        if self.path == "/requests/req-123/cancel":
            return self._send(202)
        if self.path == "/unauthorized":
            return self._send(401, {"detail": "Invalid credentials"})
        if self.path == "/credits":
            return self._send(403, {"detail": "Insufficient credits"})
        return self._send(404, {"detail": "Not found"})

    def do_PUT(self):
        self._record()
        return self._send(200, {})

    def do_GET(self):
        self._record()
        if self.path == "/requests/req-123/status":
            return self._send(200, {
                "status": "completed",
                "request_id": "req-123",
                "video": {"url": "https://cdn.example.com/out/video.mp4"},
                "images": [{"url": "https://cdn.example.com/out/frame.jpg"}],
            })
        return self._send(404, {"detail": "Not found"})


class HiggsfieldAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        cls.port = cls.httpd.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        REQUESTS.clear()
        self.client = hf.HiggsfieldClient(
            {"api_key": "key-id", "secret": "key-secret"}, base_url=self.base
        )

    def test_submit_sends_documented_auth_and_idempotency(self):
        result = self.client.submit(
            "bytedance/seedance-2.5/text-to-video",
            {"prompt": "a neon city", "duration": 6},
            "idem-1",
        )
        self.assertEqual(result["request_id"], "req-123")
        request = REQUESTS[-1]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/bytedance/seedance-2.5/text-to-video")
        self.assertEqual(request["headers"].get("Authorization"), "Key key-id:key-secret")
        self.assertEqual(request["headers"].get("Idempotency-Key"), "idem-1")
        self.assertEqual(request["body"]["prompt"], "a neon city")

    def test_status_and_cancel_use_returned_urls(self):
        payload = self.client.status("http://127.0.0.1:%d/requests/req-123/status" % self.port)
        self.assertEqual(payload["status"], "completed")
        self.client.cancel("http://127.0.0.1:%d/requests/req-123/cancel" % self.port)
        self.assertEqual(REQUESTS[-1]["path"], "/requests/req-123/cancel")

    def test_upload_never_sends_credentials_to_storage(self):
        result = self.client.upload(b"jpeg-bytes", "image/jpeg", "input.jpg")
        self.assertEqual(result["public_url"], "https://cdn.example.com/input/example.jpeg")
        ticket, put = REQUESTS[0], REQUESTS[1]
        self.assertEqual(ticket["path"], "/files/generate-upload-url")
        self.assertEqual(ticket["body"], {"content_type": "image/jpeg"})
        self.assertEqual(put["method"], "PUT")
        self.assertEqual(put["path"], "/storage/put")
        put_headers = {key.lower(): value for key, value in put["headers"].items()}
        self.assertNotIn("authorization", put_headers)
        self.assertEqual(put_headers.get("x-amz-tagging"), "retention=temporary")

    def test_http_errors_become_normalized_provider_errors(self):
        with self.assertRaises(hf.ProviderRequestError) as ctx:
            self.client._request("POST", "unauthorized")
        error = ctx.exception.error
        self.assertEqual(error["code"], "authentication_failed")
        self.assertFalse(error["retryable"])
        self.assertNotIn("key-secret", error["message"])

        with self.assertRaises(hf.ProviderRequestError) as ctx:
            self.client._request("POST", "credits")
        self.assertEqual(ctx.exception.error["code"], "insufficient_credits")

    def test_rejects_endpoints_that_could_escape_the_api_base(self):
        for endpoint in ("../admin", "http://evil.example/x", "a//b", "a?x=1"):
            with self.assertRaises(Exception):
                self.client.submit(endpoint, {"prompt": "x"}, "idem")

    def test_status_and_cancel_paths_quote_request_ids(self):
        self.assertEqual(hf.status_path("a b"), "requests/a%20b/status")
        self.assertEqual(hf.cancel_path("a/b"), "requests/a%2Fb/cancel")
        with self.assertRaises(hf.ProviderRequestError):
            hf.status_path("")

    def test_credentials_are_required(self):
        with self.assertRaises(ValueError):
            hf.HiggsfieldClient({"api_key": "", "secret": ""}, base_url=self.base)

    def test_test_credentials_uses_the_upload_ticket_endpoint(self):
        result = self.client.test_credentials()
        self.assertTrue(result["ok"])
        self.assertEqual(REQUESTS[0]["path"], "/files/generate-upload-url")


if __name__ == "__main__":
    unittest.main()
