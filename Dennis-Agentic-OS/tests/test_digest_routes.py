"""Native briefing tests for the DENNIS Agentic OS server (stdlib only).

Run:  python3 -m unittest tests.test_digest_routes

The Step 5 briefing module reads local OS state only. Tests redirect all
artifact paths to a temporary directory and stub TTS so no audio or audit
entry is produced. The HTTP routes keep their ``/api/digest/*`` contract.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import briefing as dennis_briefing  # noqa: E402
from backend import jarvis_os  # noqa: E402

_PATH_NAMES = ("BRIEF_DIR", "DAILY_FILE", "WEEKLY_FILE", "HISTORY_FILE",
               "SCHEDULE_FILE", "AUDIO_FILE", "LOG_FILE", "_WEEKLY_STATE_FILE")


def _redirect_paths(module, root):
    saved = {name: getattr(module, name) for name in _PATH_NAMES}
    setattr(module, "BRIEF_DIR", root)
    for name in _PATH_NAMES:
        if name != "BRIEF_DIR":
            setattr(module, name, os.path.join(root, name + ".json"))
    return saved


def _restore_paths(module, saved):
    for name, value in saved.items():
        setattr(module, name, value)


class BriefingUnitTests(unittest.TestCase):
    """Native module: local sources, no invention, graceful shapes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = _redirect_paths(dennis_briefing, self.tmp.name)
        self.real_generate = dennis_briefing.tts.generate
        self.real_audit = jarvis_os.audit

        def no_audio(*a, **k):
            raise RuntimeError("TTS disabled in tests")

        dennis_briefing.tts.generate = no_audio
        jarvis_os.audit = lambda *a, **k: {}

    def tearDown(self):
        dennis_briefing.tts.generate = self.real_generate
        jarvis_os.audit = self.real_audit
        _restore_paths(dennis_briefing, self.saved)
        self.tmp.cleanup()

    def test_latest_empty(self):
        out = dennis_briefing.latest()
        self.assertTrue(out["available"])
        self.assertIsNone(out["digest"])

    def test_generate_rejects_unknown_kind(self):
        with self.assertRaises(dennis_briefing.DigestError):
            dennis_briefing.generate("hourly")

    def test_generate_uses_only_local_sources(self):
        out = dennis_briefing.generate("daily")
        self.assertTrue(out["available"])
        self.assertIn("local:", dennis_briefing.latest()["digest"]["source"])
        self.assertNotIn("http://", dennis_briefing.latest()["digest"]["source"])

    def test_sections_carry_source_evidence(self):
        dennis_briefing.generate("daily")
        sections = dennis_briefing.latest()["digest"]["sections"]
        for name in ("connections", "cosmos", "genesis", "trading_den", "audit", "voice"):
            self.assertIn(name, sections)
            self.assertTrue(sections[name].get("source"), name)

    def test_spoken_summary_is_bounded_and_local(self):
        sections = dennis_briefing.collect_sections()
        spoken = dennis_briefing._spoken_summary(sections, "daily")
        self.assertLessEqual(len(spoken), 420)
        self.assertNotIn("http://", spoken)
        self.assertNotIn("8000", spoken)

    def test_audio_absent_when_not_generated(self):
        self.assertEqual(dennis_briefing.audio(), (None, None))

    def test_service_control_rejects_unknown_action(self):
        with self.assertRaises(dennis_briefing.DigestError):
            dennis_briefing.service_control("bogus")

    def test_service_status_is_native(self):
        self.assertTrue(dennis_briefing.status()["native"])
        self.assertEqual(dennis_briefing.status()["service"], "local-briefing")

    def test_weekly_schedule_info_shape(self):
        info = dennis_briefing.weekly_schedule_info()
        for key in ("auto_enabled", "day", "hour"):
            self.assertIn(key, info)

    def test_save_schedule_roundtrip(self):
        out = dennis_briefing.save_schedule(True, "0 7 * * *")
        self.assertTrue(out["available"])
        self.assertEqual(dennis_briefing.schedule()["schedule"]["cron"], "0 7 * * *")


class BriefingRouteTests(unittest.TestCase):
    """Route wiring on the real HTTP server."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
        cls.port = cls.httpd.server_address[1]
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

    def test_writes_require_confirmation(self):
        for path in ("/api/digest/generate", "/api/digest/schedule", "/api/digest/service"):
            status, body = self.request("POST", path, {"kind": "daily"})
            self.assertEqual(status, 400, path)
            self.assertEqual(body.get("error"), "confirmation required")

    def test_latest_passes_through_stub(self):
        original = dennis_briefing.latest
        dennis_briefing.latest = lambda: {
            "available": True,
            "digest": {"text": "Good morning, sir.", "generated_at": "2026-11-21T08:00:00"},
        }
        try:
            status, body = self.request("GET", "/api/digest")
            self.assertEqual(status, 200)
            self.assertEqual(body["digest"]["text"], "Good morning, sir.")
        finally:
            dennis_briefing.latest = original

    def test_generate_passes_through_stub(self):
        original = dennis_briefing.generate
        dennis_briefing.generate = lambda kind="daily": {
            "available": True, "kind": kind, "text": "Fresh briefing.", "status": "ok"}
        try:
            status, body = self.request("POST", "/api/digest/generate",
                                        {"kind": "weekly", "confirmed": True})
            self.assertEqual(status, 200)
            self.assertEqual(body["kind"], "weekly")
        finally:
            dennis_briefing.generate = original

    def test_generate_rejects_unknown_kind(self):
        status, _ = self.request("POST", "/api/digest/generate",
                                 {"kind": "hourly", "confirmed": True})
        self.assertEqual(status, 400)

    def test_audio_404_when_no_audio(self):
        original = dennis_briefing.audio
        dennis_briefing.audio = lambda: (None, None)
        try:
            status, _ = self.request("GET", "/api/digest/audio")
            self.assertEqual(status, 404)
        finally:
            dennis_briefing.audio = original

    def test_audio_streams_bytes(self):
        original = dennis_briefing.audio
        dennis_briefing.audio = lambda: (b"RIFFfake-wav", "audio/wav")
        try:
            status, raw = self.request("GET", "/api/digest/audio")
            self.assertEqual(status, 200)
            self.assertEqual(raw, b"RIFFfake-wav")
        finally:
            dennis_briefing.audio = original

    def test_service_status_shape(self):
        status, body = self.request("GET", "/api/digest/service")
        self.assertEqual(status, 200)
        self.assertTrue(body["available"])
        self.assertTrue(body["native"])


if __name__ == "__main__":
    unittest.main()
