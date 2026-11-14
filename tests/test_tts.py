"""Tests for the centralized TTS Router (backend/tts.py).

Covers the acceptance criteria that are unit-testable without the inference
service: persistent JARVIS preset, 3-level provider selection, per-studio
isolation (an Ads preference can never change Apollo's voice), config
validation, and ElevenLabs secret handling (backend-side only, never echoed).
Kokoro HTTP calls are exercised against a local mock server.
"""

import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import tts as dennis_tts  # noqa: E402
from backend import config as dennis_config  # noqa: E402


class _MockKokoroHandler(BaseHTTPRequestHandler):
    """Mock of the Kokoro service surface used by the Router."""

    def log_message(self, *args):
        pass

    def _send(self, body, ctype="application/json", status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if ctype.startswith("audio/pcm"):
            self.send_header("X-Audio-Sample-Rate", "24000")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(json.dumps({
                "ok": True, "status": "ready", "model_version": "kokoro-v1.0",
                "voice_count": 54, "loaded": True,
            }).encode())
        elif self.path == "/v1/audio/voices":
            self._send(json.dumps({"voices": [
                {"id": "bm_george", "name": "George"},
                {"id": "bm_lewis", "name": "Lewis"},
            ]}).encode())
        else:
            self._send(b"{}", status=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v1/audio/speech":
            # Deterministic non-silence payload tagged with the request voice.
            marker = body.get("voice", "?").encode()
            pcm = marker + b"\x01\x02" * 16
            self._send(pcm, ctype="audio/pcm; rate=24000")
        else:
            self._send(b"{}", status=404)


class TTSRouterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._server = ThreadingHTTPServer(("127.0.0.1", 0), _MockKokoroHandler)
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        cls._orig_url = dennis_tts.KOKORO_BASE_URL
        dennis_tts.KOKORO_BASE_URL = f"http://127.0.0.1:{cls._server.server_port}"

        # Isolated data dir so tests never touch the real data/tts/config.json.
        cls._tmp = tempfile.mkdtemp(prefix="dennis-tts-test-")
        cls._orig_data = dennis_config.DATA_DIR
        dennis_config.DATA_DIR = cls._tmp
        dennis_tts.TTS_DIR = os.path.join(cls._tmp, "tts")
        dennis_tts.CONFIG_FILE = os.path.join(dennis_tts.TTS_DIR, "config.json")
        dennis_tts.AUDIO_DIR = os.path.join(dennis_tts.TTS_DIR, "audio")
        dennis_tts.SECRETS_FILE = os.path.join(dennis_tts.TTS_DIR, "secrets.json")

    @classmethod
    def tearDownClass(cls):
        dennis_tts.KOKORO_BASE_URL = cls._orig_url
        dennis_config.DATA_DIR = cls._orig_data
        cls._server.shutdown()
        import shutil
        shutil.rmtree(cls._tmp, ignore_errors=True)

    # -- preset & selection -------------------------------------------------
    def test_jarvis_preset_is_persistent_default(self):
        cfg = dennis_tts.load_config()
        jarvis = cfg["presets"]["jarvis-british-male"]
        self.assertEqual(jarvis["name"], "JARVIS — BRITISH MALE")
        self.assertEqual(jarvis["voice_id"], "bm_george")
        self.assertEqual(jarvis["fallback_voice_id"], "bm_lewis")
        self.assertEqual(jarvis["lang"], "en-gb")
        self.assertAlmostEqual(jarvis["speed"], 1.03)
        self.assertEqual(jarvis["output"], "pcm")
        self.assertEqual(cfg["studios"]["apollo"]["preset"], "jarvis-british-male")
        self.assertEqual(cfg["global_default_provider"], "kokoro")

    def test_studio_preferences_are_independent(self):
        # Changing Ads must never change Apollo or YouTube.
        dennis_tts.save_config({"studios": {"ads": {"preset": "narrator-en-us"}}})
        cfg = dennis_tts.load_config()
        self.assertEqual(cfg["studios"]["ads"]["preset"], "narrator-en-us")
        self.assertEqual(cfg["studios"]["apollo"]["preset"], "jarvis-british-male")
        self.assertEqual(cfg["studios"]["youtube"]["preset"], "narrator-en-us")

    def test_preset_override_pins_provider(self):
        dennis_tts.save_config({"presets": {"jarvis-british-male": {"provider": "elevenlabs"}}})
        provider, preset, _ = dennis_tts.effective_provider("apollo", "jarvis-british-male")
        self.assertEqual(provider, "elevenlabs")
        dennis_tts.save_config({"presets": {"jarvis-british-male": {"provider": "kokoro"}}})

    def test_global_default_used_when_studio_unspecified(self):
        provider, _, _ = dennis_tts.effective_provider("nonexistent-studio")
        self.assertEqual(provider, dennis_tts.load_config()["global_default_provider"])

    # -- validation ---------------------------------------------------------
    def test_unknown_provider_rejected(self):
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.save_config({"global_default_provider": "bogus"})
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.save_config({"studios": {"ads": {"provider": "bogus"}}})

    def test_unknown_preset_rejected(self):
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.save_config({"studios": {"ads": {"preset": "no-such-preset"}}})

    def test_speed_clamped(self):
        dennis_tts.save_config({"presets": {"jarvis-british-male": {"speed": 99.0}}})
        cfg = dennis_tts.load_config()
        self.assertLessEqual(cfg["presets"]["jarvis-british-male"]["speed"], 2.0)
        dennis_tts.save_config({"presets": {"jarvis-british-male": {"speed": 1.03}}})

    # -- kokoro adapter -----------------------------------------------------
    def test_generate_uses_studio_preset_and_returns_audio(self):
        audio, ctype, rate, meta = dennis_tts.generate(
            "Systems online.", studio="apollo")
        self.assertTrue(audio.startswith(b"bm_george"))  # mock tags the voice
        self.assertIn("pcm", ctype)
        self.assertEqual(rate, 24000)
        self.assertEqual(meta["voice_id"], "bm_george")
        self.assertEqual(meta["provider"], "kokoro")

    def test_generate_explicit_voice_overrides_preset(self):
        audio, _, _, meta = dennis_tts.generate(
            "Preview.", studio="apollo", voice_id="bm_lewis")
        self.assertTrue(audio.startswith(b"bm_lewis"))
        self.assertEqual(meta["voice_id"], "bm_lewis")

    def test_kokoro_speech_rejects_long_text(self):
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.kokoro_speech("x" * (dennis_tts.MAX_TEXT_CHARS + 1))

    def test_kokoro_speech_rejects_empty(self):
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.kokoro_speech("   ")

    # -- status -------------------------------------------------------------
    def test_status_shape_for_frontend(self):
        status = dennis_tts.status()
        self.assertTrue(status["kokoro"]["reachable"])
        self.assertTrue(status["kokoro"]["health"].get("model_loaded"))
        self.assertTrue(status["providers"]["kokoro"]["enabled"])
        self.assertIn("preset", status["studios"]["apollo"])
        self.assertIn("id", dennis_tts.load_config()["presets"]["jarvis-british-male"])

    def test_config_exposes_providers_view(self):
        cfg = dennis_tts.load_config()
        self.assertIn("kokoro", cfg["providers"])
        self.assertTrue(cfg["providers"]["kokoro"]["enabled"])

    # -- secrets ------------------------------------------------------------
    def test_elevenlabs_secret_stored_backend_side(self):
        dennis_tts.set_provider_secret("elevenlabs", "sk-test-123")
        self.assertTrue(os.path.isfile(dennis_tts.SECRETS_FILE))
        self.assertEqual(dennis_tts._elevenlabs_key(), "sk-test-123")
        status = dennis_tts.status()
        self.assertTrue(status["elevenlabs"]["configured"])
        # The key must never appear anywhere in the public status payload.
        self.assertNotIn("sk-test-123", json.dumps(status))
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.set_provider_secret("kokoro", "nope")
        # elevenlabs generation without a real key fails with a clear error
        dennis_tts.set_provider_secret("elevenlabs", "sk-invalid")
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.elevenlabs_speech("hello", "voice-id")
        os.unlink(dennis_tts.SECRETS_FILE)

    def test_elevenlabs_unconfigured_error_is_actionable(self):
        if os.path.exists(dennis_tts.SECRETS_FILE):
            os.unlink(dennis_tts.SECRETS_FILE)
        with self.assertRaises(dennis_tts.TTSError) as ctx:
            dennis_tts.elevenlabs_speech("hello", "voice-id")
        self.assertIn("not configured", str(ctx.exception))

    # -- service control ----------------------------------------------------
    def test_service_control_rejects_unknown_action(self):
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.service_control("self-destruct")

    # -- audio persistence --------------------------------------------------
    def test_save_audio_creates_file_and_record(self):
        record = dennis_tts.save_audio(
            b"RIFFxxxx", name="Ads Voiceover Demo!",
            meta={"provider": "kokoro", "voice_id": "am_michael", "sample_rate": 24000},
            content_type="audio/wav")
        self.assertTrue(record["file"].startswith("ads-voiceover-demo-"))
        self.assertTrue(record["file"].endswith(".wav"))
        self.assertTrue(record["url"].startswith("/api/tts/audio/"))
        path = os.path.join(dennis_tts.AUDIO_DIR, record["file"])
        self.assertTrue(os.path.isfile(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"RIFFxxxx")


if __name__ == "__main__":
    unittest.main(verbosity=2)
