"""TTS Router route tests for the DENNIS Agentic OS server (stdlib unittest only).

Run:  python3 -m unittest tests.test_tts_routes

Covers the owner-token gate, config validation, preset/provider resolution
and the speech proxy wiring. Network generation is exercised against a
stubbed adapter so these tests never load the model; the real audio path is
verified separately in tests/e2e_tts_kokoro.py (manual end-to-end).
"""

import http.client
import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import tts as dennis_tts  # noqa: E402


class TtsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        dennis_tts.CONFIG_FILE = os.path.join(cls.tmp.name, "config.json")
        dennis_tts.SECRETS_FILE = os.path.join(cls.tmp.name, "secrets.json")
        dennis_tts.AUDIO_DIR = os.path.join(cls.tmp.name, "audio")

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
        cls.tmp.cleanup()

    def request(self, method, path, body=None, token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
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

    # -- config ------------------------------------------------------------
    def test_config_round_trip(self):
        status, body = self.request("GET", "/api/tts/config")
        self.assertEqual(status, 200)
        cfg = body["config"] if "config" in body else body
        self.assertEqual(cfg["global_default_provider"], "kokoro")
        self.assertIn("jarvis-british-male", cfg["presets"])
        self.assertEqual(cfg["presets"]["jarvis-british-male"]["voice_id"], "bm_george")
        self.assertEqual(cfg["presets"]["jarvis-british-male"]["lang"], "en-gb")

        status, body = self.request("POST", "/api/tts/config", {
            "studios": {"ads": {"voice_preset": "jarvis-british-male"}}
        })
        self.assertEqual(status, 200)
        saved = body["config"]
        self.assertEqual(saved["studios"]["ads"]["voice_preset"], "jarvis-british-male")

    def test_config_full_preset_patch_preserves_fields(self):
        """A speed-only UI patch must not reset the preset identity fields."""
        status, body = self.request("POST", "/api/tts/config", {
            "presets": {"jarvis-british-male": {
                "name": "JARVIS — BRITISH MALE",
                "provider": "kokoro",
                "voice_id": "bm_george",
                "fallback_voice_id": "bm_lewis",
                "lang": "en-gb",
                "speed": 1.1,
                "output": "pcm",
                "sample_rate": 24000,
            }}
        })
        self.assertEqual(status, 200)
        preset = body["config"]["presets"]["jarvis-british-male"]
        self.assertEqual(preset["speed"], 1.1)
        self.assertEqual(preset["voice_id"], "bm_george")
        self.assertEqual(preset["lang"], "en-gb")
        self.assertEqual(preset["fallback_voice_id"], "bm_lewis")

    def test_config_rejects_unknown_preset(self):
        status, body = self.request("POST", "/api/tts/config", {
            "studios": {"ads": {"voice_preset": "does-not-exist"}}
        })
        self.assertEqual(status, 400)

    def test_config_rejects_unknown_provider(self):
        status, body = self.request("POST", "/api/tts/config", {
            "studios": {"ads": {"provider": "skynet"}}
        })
        self.assertEqual(status, 400)

    def test_config_rejects_bad_speed(self):
        """Their validation clamps speed into [0.5, 2.0] rather than rejecting."""
        status, body = self.request("POST", "/api/tts/config", {
            "presets": {"jarvis-british-male": {
                "name": "JARVIS", "provider": "kokoro", "voice_id": "bm_george",
                "speed": 9.5,
            }}
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["config"]["presets"]["jarvis-british-male"]["speed"], 2.0)

    def test_config_requires_owner_token(self):
        status, body = self.request("POST", "/api/tts/config", {}, token=False)
        self.assertEqual(status, 401)

    def test_apollo_flags_round_trip(self):
        status, body = self.request("POST", "/api/tts/config", {
            "apollo": {"browser_fallback": False, "duck_mic": False}
        })
        self.assertEqual(status, 200)
        self.assertFalse(body["config"]["apollo"]["browser_fallback"])
        self.assertFalse(body["config"]["apollo"]["duck_mic"])
        # restore defaults
        self.request("POST", "/api/tts/config",
                     {"apollo": {"browser_fallback": True, "duck_mic": True}})

    # -- status & voices ----------------------------------------------------
    def test_status_shape(self):
        status, body = self.request("GET", "/api/tts/status")
        self.assertEqual(status, 200)
        self.assertIn("kokoro", body["providers"])
        self.assertIn("healthy", body["kokoro"])
        self.assertIn("global_default_provider", body)

    def test_voices_endpoint(self):
        status, body = self.request("GET", "/api/tts/voices")
        self.assertEqual(status, 200)
        self.assertIn("voices", body)

    # -- speech validation (no inference needed) -----------------------------
    def test_speech_rejects_missing_text(self):
        status, body = self.request("POST", "/api/tts/speech", {"text": "  "})
        self.assertEqual(status, 400)

    def test_speech_rejects_unknown_preset(self):
        status, body = self.request("POST", "/api/tts/speech", {
            "text": "hello", "preset": "nope"
        })
        self.assertEqual(status, 400)

    # -- speech proxy with stubbed generation --------------------------------
    def test_speech_proxy_passes_audio_through(self):
        original = dennis_tts.kokoro_speech

        def fake_speech(text, voice_id="bm_george", speed=1.0, lang=None,
                        response_format="wav", timeout=600.0):
            self.assertEqual(voice_id, "bm_george")
            import io as _io
            import wave as _wave
            import struct as _struct
            frames = b"".join(_struct.pack("<h", int(100 * (i % 7))) for i in range(240))
            buf = _io.BytesIO()
            with _wave.open(buf, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24000)
                wav.writeframes(frames)
            ctype = "audio/wav" if response_format == "wav" else "audio/pcm; rate=24000"
            return buf.getvalue(), ctype, 24000

        dennis_tts.kokoro_speech = fake_speech
        try:
            status, data = self.request("POST", "/api/tts/speech", {"text": "Router test"})
            self.assertEqual(status, 200, msg=data if isinstance(data, (str, bytes)) else data)
            self.assertGreater(len(data), 44)  # WAV header plus frames
        finally:
            dennis_tts.kokoro_speech = original

    def test_speech_save_returns_descriptor(self):
        original = dennis_tts.kokoro_speech
        dennis_tts.kokoro_speech = lambda *a, **k: (b"RIFFfake", "audio/wav", 24000)
        try:
            status, body = self.request("POST", "/api/tts/speech", {
                "text": "Save me", "save": True, "name": "unit-test"
            })
            self.assertEqual(status, 200)
            self.assertTrue(body["success"])
            self.assertTrue(body["audio"]["url"].startswith("/api/tts/audio/"))
            self.assertTrue(os.path.isfile(os.path.join(dennis_tts.AUDIO_DIR,
                                                        body["audio"]["file"])))
        finally:
            dennis_tts.kokoro_speech = original

    # -- secrets -------------------------------------------------------------
    def test_secret_patch_never_echoes_key(self):
        status, body = self.request("POST", "/api/tts/config", {
            "provider_secrets": {"elevenlabs": {"api_key": "sk-test-secret"}}
        })
        self.assertEqual(status, 200)
        self.assertNotIn("sk-test-secret", json.dumps(body))
        el = body["config"].get("elevenlabs") or {}
        self.assertNotIn("sk-test-secret", json.dumps(el))
        status, body = self.request("GET", "/api/tts/status")
        self.assertNotIn("sk-test-secret", json.dumps(body))

    # -- provider resolution --------------------------------------------------
    def test_provider_resolution_order(self):
        """Resolution: preset provider (pinned) -> studio preference -> global.

        The JARVIS preset pins provider 'kokoro', so an Ads Studio preference
        can never change what voice engine the Jarvis preset uses.
        """
        provider, preset, cfg = dennis_tts.effective_provider("apollo", "jarvis-british-male")
        self.assertEqual(provider, "kokoro")
        self.assertEqual(preset["voice_id"], "bm_george")

        # Persist an Ads Studio preference, then confirm it only affects ads.
        status, body = self.request("POST", "/api/tts/config", {
            "studios": {"ads": {"provider": "elevenlabs"}}
        })
        self.assertEqual(status, 200)
        provider, _, _ = dennis_tts.effective_provider("ads")
        self.assertEqual(provider, "elevenlabs")
        provider, _, _ = dennis_tts.effective_provider("apollo")
        self.assertEqual(provider, "kokoro")
        # the pinned Jarvis preset beats any studio preference
        provider, _, _ = dennis_tts.effective_provider("ads", "jarvis-british-male")
        self.assertEqual(provider, "kokoro")
        # unknown preset is an explicit error, never a silent fallback
        with self.assertRaises(dennis_tts.TTSError):
            dennis_tts.effective_provider("ads", "no-such-preset")
        # restore
        self.request("POST", "/api/tts/config", {"studios": {"ads": {"provider": "kokoro"}}})


if __name__ == "__main__":
    unittest.main()
