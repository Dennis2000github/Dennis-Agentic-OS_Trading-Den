"""Digest bridge route tests for the DENNIS Agentic OS server (stdlib only).

Run:  python3 -m unittest tests.test_digest_routes

The OpenJarvis service is never started here: the bridge module's network
functions are stubbed (same approach as tests/test_tts_routes.py stubbing
kokoro_speech). One unit test points the bridge at a dead port to prove the
graceful-degradation shape ({"available": False} instead of a raised error).
"""

import http.client
import json
import os
import sys
import unittest

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import digest as dennis_digest  # noqa: E402


class DigestBridgeUnitTests(unittest.TestCase):
    """The bridge module itself: offline shapes, no exceptions."""

    def setUp(self):
        self._orig_url = dennis_digest.OPENJARVIS_BASE_URL

    def tearDown(self):
        dennis_digest.OPENJARVIS_BASE_URL = self._orig_url

    def _dead_url(self):
        dennis_digest.OPENJARVIS_BASE_URL = "http://127.0.0.1:9"  # discard port

    def test_latest_offline_returns_available_false(self):
        self._dead_url()
        out = dennis_digest.latest()
        self.assertFalse(out["available"])
        self.assertIn("reason", out)
        self.assertEqual(out["service"], "openjarvis")

    def test_history_offline_returns_available_false(self):
        self._dead_url()
        out = dennis_digest.history()
        self.assertFalse(out["available"])
        self.assertNotIn("items", out)

    def test_generate_offline_returns_available_false(self):
        self._dead_url()
        out = dennis_digest.generate("daily")
        self.assertFalse(out["available"])

    def test_generate_rejects_unknown_kind_before_network(self):
        self._dead_url()
        with self.assertRaises(dennis_digest.DigestError):
            dennis_digest.generate("hourly")

    def test_weekly_offline_returns_available_false(self):
        self._dead_url()
        out = dennis_digest.weekly()
        self.assertFalse(out["available"])

    def test_weekly_schedule_info_shape(self):
        info = dennis_digest.weekly_schedule_info()
        self.assertIn("auto_enabled", info)
        self.assertIn("day", info)
        self.assertIn("hour", info)

    def test_audio_offline_raises_digest_error(self):
        self._dead_url()
        with self.assertRaises(dennis_digest.DigestError):
            dennis_digest.audio()

    def test_service_control_rejects_unknown_action(self):
        with self.assertRaises(dennis_digest.DigestError):
            dennis_digest.service_control("bogus")

    def test_save_schedule_always_sends_enabled_bool(self):
        captured = {}

        def fake_request(method, path, body=None, timeout=dennis_digest._DEFAULT_TIMEOUT):
            captured["method"], captured["path"], captured["body"] = method, path, body
            return 200, {"enabled": body["enabled"], "cron": body.get("cron", "0 7 * * *")}

        original = dennis_digest._request
        dennis_digest._request = fake_request
        try:
            out = dennis_digest.save_schedule(True)
            self.assertTrue(out["available"])
            self.assertEqual(captured["method"], "POST")
            self.assertEqual(captured["path"], "/api/digest/schedule")
            self.assertTrue(captured["body"]["enabled"])
            self.assertNotIn("cron", captured["body"])  # omitted when not provided
        finally:
            dennis_digest._request = original


class DigestRouteTests(unittest.TestCase):
    """Route wiring on the real HTTP server, with stubbed bridge calls."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
        import threading
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        from backend import auth as dennis_auth
        cls.token = dennis_auth.AUTH.token

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Dennis-Token"] = self.token
        payload = json.dumps(body or {}).encode("utf-8") if method == "POST" else None
        conn.request(method, path, body=payload, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        try:
            parsed = json.loads(data.decode("utf-8"))
        except Exception:
            parsed = data
        return res.status, parsed

    def test_digest_requires_owner_token(self):
        status, _ = self.request("GET", "/api/digest", token=False)
        self.assertEqual(status, 401)

    def test_latest_passes_through_stub(self):
        original = dennis_digest.latest
        dennis_digest.latest = lambda: {
            "available": True,
            "digest": {"text": "Good morning, sir.", "generated_at": "2026-11-13T08:00:00"},
        }
        try:
            status, body = self.request("GET", "/api/digest")
            self.assertEqual(status, 200)
            self.assertTrue(body["available"])
            self.assertEqual(body["digest"]["text"], "Good morning, sir.")
        finally:
            dennis_digest.latest = original

    def test_latest_when_no_digest_today(self):
        original = dennis_digest.latest
        dennis_digest.latest = lambda: {"available": True, "digest": None}
        try:
            status, body = self.request("GET", "/api/digest")
            self.assertEqual(status, 200)
            self.assertIsNone(body["digest"])
        finally:
            dennis_digest.latest = original

    def test_history_passes_through_stub(self):
        original = dennis_digest.history
        dennis_digest.history = lambda limit=10: {
            "available": True,
            "items": [{"text": "Yesterday, sir.", "generated_at": "2026-11-12T08:00:00"}],
        }
        try:
            status, body = self.request("GET", "/api/digest/history")
            self.assertEqual(status, 200)
            self.assertEqual(body["items"][0]["text"], "Yesterday, sir.")
        finally:
            dennis_digest.history = original

    def test_generate_passes_through_stub(self):
        original = dennis_digest.generate
        dennis_digest.generate = lambda kind="daily": {
            "available": True, "kind": kind, "text": "Fresh briefing.",
            "status": "ok"}
        try:
            status, body = self.request("POST", "/api/digest/generate", {})
            self.assertEqual(status, 200)
            self.assertTrue(body["available"])
            self.assertEqual(body["kind"], "daily")
            self.assertEqual(body["text"], "Fresh briefing.")

            status, body = self.request("POST", "/api/digest/generate",
                                        {"kind": "weekly"})
            self.assertEqual(status, 200)
            self.assertEqual(body["kind"], "weekly")
        finally:
            dennis_digest.generate = original

    def test_generate_rejects_unknown_kind(self):
        status, body = self.request("POST", "/api/digest/generate",
                                    {"kind": "hourly"})
        self.assertEqual(status, 400)

    def test_weekly_endpoint_offline_and_stubbed(self):
        original = dennis_digest.weekly
        dennis_digest.weekly = lambda: {
            "available": True,
            "weekly": {"text": "Your week in review, sir.",
                       "kind": "weekly", "generated_at": "2026-11-09T08:00:00"},
        }
        try:
            status, body = self.request("GET", "/api/digest/weekly")
            self.assertEqual(status, 200)
            self.assertEqual(body["weekly"]["kind"], "weekly")
        finally:
            dennis_digest.weekly = original

    def test_schedule_get_and_post_pass_through_stub(self):
        original_get = dennis_digest.schedule
        original_save = dennis_digest.save_schedule
        dennis_digest.schedule = lambda: {
            "available": True, "schedule": {"enabled": True, "cron": "0 7 * * *"}}
        dennis_digest.save_schedule = lambda enabled, cron=None: {
            "available": True, "schedule": {"enabled": enabled, "cron": cron or "0 7 * * *"}}
        try:
            status, body = self.request("GET", "/api/digest/schedule")
            self.assertEqual(status, 200)
            self.assertEqual(body["schedule"]["cron"], "0 7 * * *")

            status, body = self.request("POST", "/api/digest/schedule",
                                        {"enabled": False, "cron": "0 8 * * *"})
            self.assertEqual(status, 200)
            self.assertFalse(body["schedule"]["enabled"])
            self.assertEqual(body["schedule"]["cron"], "0 8 * * *")
        finally:
            dennis_digest.schedule = original_get
            dennis_digest.save_schedule = original_save

    def test_audio_404_when_no_briefing_audio(self):
        original = dennis_digest.audio
        dennis_digest.audio = lambda: (None, None)
        try:
            status, _ = self.request("GET", "/api/digest/audio")
            self.assertEqual(status, 404)
        finally:
            dennis_digest.audio = original

    def test_audio_streams_bytes(self):
        original = dennis_digest.audio
        dennis_digest.audio = lambda: (b"ID3fake-mp3", "audio/mpeg")
        try:
            status, raw = self.request("GET", "/api/digest/audio")
            self.assertEqual(status, 200)
            self.assertEqual(raw, b"ID3fake-mp3")
        finally:
            dennis_digest.audio = original

    def test_service_status_shape(self):
        """No OpenJarvis running in CI — the route must still answer 200."""
        status, body = self.request("GET", "/api/digest/service")
        self.assertEqual(status, 200)
        self.assertIn("available", body)
        self.assertIn("url", body)

    def test_service_rejects_unknown_action(self):
        status, body = self.request("POST", "/api/digest/service",
                                    {"action": "detonate"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_system_telemetry_shape(self):
        status, body = self.request("GET", "/api/system/telemetry")
        self.assertEqual(status, 200)
        self.assertIn("load_pct", body)
        self.assertIn("uptime_s", body)
        self.assertIn("cores", body)
        self.assertIsInstance(body["load_pct"], (int, float))
        self.assertGreaterEqual(body["uptime_s"], 0)


if __name__ == "__main__":
    unittest.main()
