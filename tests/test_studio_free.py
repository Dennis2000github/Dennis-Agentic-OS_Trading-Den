"""Unit tests for the Ads Studio free engines (Pollinations + Google AI).

No network calls: urllib is patched. Run:

    python3 -m unittest tests.test_studio_free
"""

import base64
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DENNIS_NO_DENMA", "1")
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_free as free  # noqa: E402
from backend import studio_media as media  # noqa: E402
from backend import studio_secrets as secrets  # noqa: E402


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self):
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def fake_json_response(payload):
    """A (raw_bytes, content_type) pair like studio_free._open returns."""
    return json.dumps(payload).encode("utf-8"), "application/json"


class PollinationsUrlTests(unittest.TestCase):
    def test_url_encodes_prompt_and_aspect(self):
        url = free.pollinations_url("a model in a red coat", "9:16", seed=7)
        self.assertIn("https://image.pollinations.ai/prompt/", url)
        self.assertIn("a%20model%20in%20a%20red%20coat", url)
        self.assertIn("width=768", url)
        self.assertIn("height=1344", url)
        self.assertIn("model=flux", url)
        self.assertIn("seed=7", url)
        self.assertNotIn("image=", url)

    def test_url_landscape_and_edit_refs(self):
        url = free.pollinations_url("dress the model", "16:9", seed=1,
                                    ref_urls=["https://pub.example/m.jpg"])
        self.assertIn("width=1344", url)
        self.assertIn("height=768", url)
        self.assertIn("image=https%3A%2F%2Fpub.example%2Fm.jpg", url)


class GoogleKeyTests(unittest.TestCase):
    def setUp(self):
        self.dir = os.environ.get("FREE_TEST_DIR") or "/tmp"

    def test_capabilities_report_key_absence(self):
        with mock.patch.object(free, "google_api_key", return_value=""):
            caps = free.capabilities()
        self.assertFalse(caps["google"])
        self.assertEqual([e["id"] for e in caps["video_engines"]],
                         ["veo-3.0-fast-generate-001", "veo-3.0-generate-001",
                          "veo-2.0-generate-001"])

    def test_missing_key_blocks_gemini(self):
        with mock.patch.object(free, "google_api_key", return_value=""):
            with self.assertRaises(free.FreeError) as ctx:
                free.gemini_image("a shoe")
        self.assertEqual(ctx.exception.code, "missing_key")

    def test_gemini_image_sends_inline_references(self):
        captured = {}

        def fake_open(url, data=None, headers=None, method="GET", timeout=0):
            captured["url"] = url
            captured["body"] = json.loads(data.decode("utf-8"))
            inline = captured["body"]["contents"][0]["parts"][1]["inlineData"]
            self.assertEqual(inline["data"], base64.b64encode(b"img-bytes").decode("ascii"))
            return fake_json_response({"candidates": [{"content": {"parts": [
                {"inlineData": {"mimeType": "image/png",
                                "data": base64.b64encode(b"out-bytes").decode("ascii")}}
            ]}}]})

        with mock.patch.object(free, "google_api_key", return_value="key-1"), \
             mock.patch.object(free, "_open", side_effect=fake_open), \
             mock.patch.object(free, "_read_reference", return_value=(b"img-bytes", "image/png")):
            data, mime = free.gemini_image("edit this", ["/api/studio/media/med_x"])
        self.assertEqual(data, b"out-bytes")
        self.assertIn(":generateContent", captured["url"])
        self.assertIn("gemini-2.5-flash-image", captured["url"])


class VeoTests(unittest.TestCase):
    def test_submit_sends_image_and_no_music_negative(self):
        captured = {}

        def fake_open(url, data=None, headers=None, method="GET", timeout=0):
            captured["url"] = url
            captured["body"] = json.loads(data.decode("utf-8"))
            return fake_json_response({"name": "models/veo-3.0-fast-generate-001/operations/op-1"})

        with mock.patch.object(free, "google_api_key", return_value="key-1"), \
             mock.patch.object(free, "_open", side_effect=fake_open), \
             mock.patch.object(free, "_read_reference", return_value=(b"frame", "image/png")):
            name = free.veo_submit("veo-3.0-fast-generate-001", "walk", image="/api/studio/media/m1",
                                   aspect="9:16", duration=8)
        self.assertEqual(name, "models/veo-3.0-fast-generate-001/operations/op-1")
        self.assertIn("predictLongRunning", captured["url"])
        instance = captured["body"]["instances"][0]
        self.assertEqual(instance["image"]["bytesBase64Encoded"],
                         base64.b64encode(b"frame").decode("ascii"))
        params = captured["body"]["parameters"]
        self.assertEqual(params["aspectRatio"], "9:16")
        self.assertEqual(params["durationSeconds"], 8)
        self.assertIn("background music", params["negativePrompt"])

    def test_poll_done_without_video_reports_error(self):
        with mock.patch.object(free, "google_api_key", return_value="k"), \
             mock.patch.object(free, "_open", return_value=fake_json_response({"done": True})):
            done, uri, error = free.veo_poll("models/x/operations/1")
        self.assertTrue(done)
        self.assertIsNone(uri)
        self.assertTrue(error)

    def test_poll_running_keeps_waiting(self):
        with mock.patch.object(free, "google_api_key", return_value="k"), \
             mock.patch.object(free, "_open", return_value=fake_json_response({"done": False})):
            done, uri, error = free.veo_poll("models/x/operations/1")
        self.assertFalse(done)
        self.assertIsNone(uri)
        self.assertIsNone(error)


class JobRegistryTests(unittest.TestCase):
    def setUp(self):
        free._JOBS.clear()
        free._WORKERS.clear()

    def test_submit_validates_kind_and_prompt(self):
        with self.assertRaises(free.FreeError):
            free.submit_job({"kind": "audio", "prompt": "x"})
        with self.assertRaises(free.FreeError):
            free.submit_job({"kind": "image", "prompt": "  "})

    @staticmethod
    def _await_terminal(job_id, attempts=200):
        import time as _time
        status = None
        for _ in range(attempts):
            status = free.job_status(job_id)
            if status["terminal"]:
                return status
            _time.sleep(0.02)
        return status

    def test_image_job_lifecycle_success(self):
        with mock.patch.object(free, "_run_image",
                               return_value=[{"media_id": "med_1", "url": "/api/studio/media/med_1"}]):
            job = free.submit_job({"kind": "image", "prompt": "a shoe", "engine": "pollinations"})
        self.assertIn(job["state"], ("queued", "running", "succeeded"))
        status = self._await_terminal(job["id"])
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["outputs"][0]["media_id"], "med_1")
        self.assertEqual(status["outputs"][0]["url"], "/api/studio/media/med_1")
        self.assertEqual(status["engine_label"], "Pollinations Flux")

    def test_video_job_requires_google_key(self):
        with mock.patch.object(free, "google_api_key", return_value=""):
            job = free.submit_job({"kind": "video", "prompt": "walk", "engine": "veo-3.0-fast-generate-001"})
            status = self._await_terminal(job["id"])
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"]["code"], "missing_key")

    def test_video_job_success_downloads_output(self):
        def fake_run_video(plan, job_id):
            time_sleep = 0  # noqa: F841
            return [{"media_id": "med_v", "url": "/api/studio/media/med_v", "kind": "video"}]

        with mock.patch.object(free, "google_api_key", return_value="k"), \
             mock.patch.object(free, "_run_video", side_effect=fake_run_video):
            job = free.submit_job({"kind": "video", "prompt": "walk",
                                   "engine": "veo-3.0-fast-generate-001", "duration": 8})
            for _ in range(100):
                status = free.job_status(job["id"])
                if status["terminal"]:
                    break
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["kind"], "video")
        self.assertEqual(status["outputs"][0]["media_id"], "med_v")

    def test_explicit_pollinations_turbo_uses_turbo_model(self):
        captured = {}
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for target in ("STUDIO_DIR", "MEDIA_DIR"):
            patch = mock.patch.object(
                media, target,
                tmp.name if target == "STUDIO_DIR" else os.path.join(tmp.name, "media"),
            )
            patch.start()
            self.addCleanup(patch.stop)

        def fake_fetch(url, max_bytes):
            captured["url"] = url
            return b"jpg-bytes", "image/jpeg"

        with mock.patch.object(free, "_fetch_bytes", side_effect=fake_fetch):
            job = free.submit_job({"kind": "image", "prompt": "a shoe",
                                   "engine": "pollinations-turbo"})
            status = self._await_terminal(job["id"])
        self.assertEqual(status["state"], "succeeded")
        self.assertIn("model=turbo", captured["url"])
        self.assertEqual(status["engine_label"], "Pollinations Turbo (free, fast, no key)")

    def test_explicit_gemini_without_key_fails_with_hint(self):
        with mock.patch.object(free, "google_api_key", return_value=""):
            job = free.submit_job({"kind": "image", "prompt": "a shoe",
                                   "engine": "google-gemini-image"})
            for _ in range(100):
                status = free.job_status(job["id"])
                if status["terminal"]:
                    break
        self.assertEqual(status["state"], "failed")
        self.assertEqual(status["error"]["code"], "missing_key")
        self.assertIn("Pollinations", status["error"]["message"])

    def test_auto_without_key_uses_pollinations_flux(self):
        captured = {}
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for target in ("STUDIO_DIR", "MEDIA_DIR"):
            patch = mock.patch.object(
                media, target,
                tmp.name if target == "STUDIO_DIR" else os.path.join(tmp.name, "media"),
            )
            patch.start()
            self.addCleanup(patch.stop)

        def fake_fetch(url, max_bytes):
            captured["url"] = url
            return b"jpg-bytes", "image/jpeg"

        with mock.patch.object(free, "google_api_key", return_value=""), \
             mock.patch.object(free, "_fetch_bytes", side_effect=fake_fetch):
            job = free.submit_job({"kind": "image", "prompt": "a shoe", "engine": "auto"})
            status = self._await_terminal(job["id"])
        self.assertEqual(status["state"], "succeeded")
        self.assertIn("model=flux", captured["url"])

    def test_upload_once_dedupes_identical_bytes(self):
        import os
        import tempfile
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for target in ("STUDIO_DIR", "MEDIA_DIR"):
            patch = mock.patch.object(
                media, target,
                tmp.name if target == "STUDIO_DIR" else os.path.join(tmp.name, "media"),
            )
            patch.start()
            self.addCleanup(patch.stop)
        free._UPLOAD_HASH_CACHE.clear()
        first = free.upload_once(b"same-bytes", "image/png", "one.png")
        second = free.upload_once(b"same-bytes", "image/png", "two.png")
        self.assertEqual(first["id"], second["id"])
        different = free.upload_once(b"other-bytes", "image/png", "three.png")
        self.assertNotEqual(first["id"], different["id"])

    def test_unknown_job_id_is_404(self):
        with self.assertRaises(free.FreeError) as ctx:
            free.job_status("free_nope")
        self.assertEqual(ctx.exception.status, 404)


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        for target in ("STUDIO_DIR", "MEDIA_DIR"):
            patch = mock.patch.object(
                media, target,
                self.tmp.name if target == "STUDIO_DIR" else os.path.join(self.tmp.name, "media"),
            )
            patch.start()
            self.addCleanup(patch.stop)

    def test_read_reference_from_local_media(self):
        record = media.add_bytes(b"png-bytes", "image/png", kind="image", filename="t.png")
        data, content_type = free._read_reference("/api/studio/media/" + record["id"])
        self.assertEqual(data, b"png-bytes")
        self.assertEqual(content_type, "image/png")

    def test_public_url_detection(self):
        self.assertTrue(free._public_url("https://pub.example/a.jpg"))
        self.assertFalse(free._public_url("/api/studio/media/med_x"))
        self.assertFalse(free._public_url("ftp://x"))


if __name__ == "__main__":
    unittest.main()
