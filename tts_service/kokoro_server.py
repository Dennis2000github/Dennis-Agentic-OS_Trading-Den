"""Local Kokoro TTS inference service (single shared instance, loopback only).

Serves the official Kokoro v1.0 ONNX weights through an OpenAI-compatible
subset so that DENNIS (and Hermes Agent's ``tts.provider: openai`` route) can
use one persistent local speech service without Docker:

    GET  /health              -> service + model status
    GET  /v1/models           -> OpenAI-style model list ("kokoro")
    GET  /v1/audio/voices     -> {"voices": [...]} (Kokoro-FastAPI shape)
    POST /v1/audio/speech     -> OpenAI TTS API shape
        body: {model, input, voice, speed, response_format, lang}
        response_format: "pcm" (chunked streaming, 24 kHz s16le mono)
                         "wav" (complete file)  "mp3" (via ffmpeg)
    POST /shutdown            -> graceful exit (loopback callers only)

Design notes for the 8 GB Intel Mac target:
- One process, one model load; concurrent speech requests are rejected with
  429 while a generation is running (no duplicate inference engines).
- The model loads lazily in a background thread; /health responds immediately
  and reports model_loaded so callers can wait for warm-up.
- "pcm" streams sentence-level via kokoro_onnx.create_stream, so
  time-to-first-audio is roughly one sentence, not the whole text.
"""

import argparse
import asyncio
import io
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.environ.get("KOKORO_MODEL", os.path.join(BASE_DIR, "models", "kokoro-v1.0.onnx"))
VOICES_PATH = os.environ.get("KOKORO_VOICES", os.path.join(BASE_DIR, "models", "voices-v1.0.bin"))
SAMPLE_RATE = 24000

STARTED_AT = time.time()
_STATS = {"generations": 0, "errors": 0}


class Engine:
    """Lazy, single-instance Kokoro engine with a one-generation semaphore."""

    def __init__(self):
        self._kokoro = None
        self._lock = threading.Lock()
        self._load_error = None
        self._slot = threading.Semaphore(1)

    @property
    def state(self):
        return {
            "model_loaded": self._kokoro is not None,
            "load_error": self._load_error,
            "voices": self.voice_names(),
        }

    def load(self):
        with self._lock:
            if self._kokoro is not None or self._load_error:
                return
            try:
                from kokoro_onnx import Kokoro

                started = time.time()
                self._kokoro = Kokoro(MODEL_PATH, VOICES_PATH)
                print(f"[kokoro] model loaded in {time.time() - started:.1f}s", flush=True)
            except Exception as exc:  # pragma: no cover - depends on runtime files
                self._load_error = str(exc)
                print(f"[kokoro] model load FAILED: {exc}", flush=True)

    def ensure_loaded(self):
        if self._kokoro is None:
            self.load()
        if self._kokoro is None:
            raise RuntimeError(self._load_error or "model not loaded")

    def voice_names(self):
        if self._kokoro is None:
            return []
        try:
            return sorted(self._kokoro.get_voices())
        except Exception:
            return []

    def _acquire(self):
        if not self._slot.acquire(blocking=False):
            raise Busy("another generation is already running")

    def synth_full(self, text, voice, speed, lang):
        """Whole-text synthesis -> (float32 audio, sample_rate)."""
        self.ensure_loaded()
        self._acquire()
        try:
            _STATS["generations"] += 1
            audio, sample_rate = self._kokoro.create(
                text, voice=voice, speed=speed, lang=lang
            )
            return np.asarray(audio, dtype=np.float32), int(sample_rate)
        finally:
            self._slot.release()

    def synth_stream(self, text, voice, speed, lang):
        """Sentence-level streaming -> iterator of (float32 chunk, sample_rate).

        The generation slot is acquired on the first iteration and released
        when the iterator finishes or is abandoned, so a request that never
        consumes the stream cannot pin the engine.
        """
        self.ensure_loaded()

        out_q = queue.Queue(maxsize=8)
        kokoro = self._kokoro
        acquired = threading.Event()

        async def produce():
            try:
                async for audio, sample_rate in kokoro.create_stream(
                    text, voice=voice, speed=speed, lang=lang
                ):
                    await asyncio.get_running_loop().run_in_executor(
                        None, out_q.put, (np.asarray(audio, dtype=np.float32), int(sample_rate))
                    )
            except Exception as exc:  # surface generation failures to the consumer
                out_q.put(exc)
            finally:
                out_q.put(None)

        def runner():
            try:
                asyncio.run(produce())
            except Exception as exc:
                out_q.put(exc)

        def iterator():
            if not acquired.is_set():
                self._acquire()
                acquired.set()
                _STATS["generations"] += 1
                threading.Thread(target=runner, daemon=True).start()
            try:
                while True:
                    item = out_q.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        raise item
                    yield item
            finally:
                if acquired.is_set():
                    self._slot.release()
                    acquired.clear()

        return iterator()


class Busy(RuntimeError):
    pass


ENGINE = Engine()


def float_to_pcm16(audio):
    audio = np.clip(audio, -1.0, 1.0)
    return (audio * 32767.0).astype("<i2").tobytes()


def wav_bytes(pcm, sample_rate):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buf.getvalue()


def mp3_bytes(pcm, sample_rate):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error",
         "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
         "-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3", "pipe:1"],
        input=pcm, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError("ffmpeg mp3 encode failed: " + proc.stderr.decode("utf-8", "replace")[:200])
    return proc.stdout


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "KokoroLocal/1.0"

    def log_message(self, fmt, *args):
        print(f"[kokoro] {self.address_string()} {fmt % args}", flush=True)

    # -- helpers ------------------------------------------------------------
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_audio(self, content_type, data, sample_rate=None, chunked=False):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        if sample_rate is not None:
            self.send_header("X-Audio-Sample-Rate", str(sample_rate))
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if chunked:
            try:
                for chunk in data:
                    payload = float_to_pcm16(chunk)
                    self.wfile.write(f"{len(payload):x}\r\n".encode("ascii"))
                    self.wfile.write(payload)
                    self.wfile.write(b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # consumer cancelled; the generator closes and frees the slot
            except Exception as exc:
                # Headers are already sent mid-stream; abort the connection so
                # the consumer sees a truncated body instead of a fake success.
                print(f"[kokoro] stream error: {exc}", flush=True)
                self.close_connection = True
        else:
            self.wfile.write(data)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("invalid JSON body")
        if not isinstance(body, dict):
            raise ValueError("JSON body must be an object")
        return body

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        if self.path == "/health":
            state = ENGINE.state
            return self._send_json({
                "status": "ok",
                "model_loaded": state["model_loaded"],
                "load_error": state["load_error"],
                "device": "cpu",
                "sample_rate": SAMPLE_RATE,
                "voices_loaded": bool(state["voices"]),
                "voice_count": len(state["voices"]),
                "uptime_s": round(time.time() - STARTED_AT, 1),
                "generations": _STATS["generations"],
                "errors": _STATS["errors"],
            })
        if self.path == "/v1/models":
            return self._send_json({
                "object": "list",
                "data": [{"id": "kokoro", "object": "model", "owned_by": "local"}],
            })
        if self.path == "/v1/audio/voices":
            state = ENGINE.state
            return self._send_json({"voices": state["voices"]})
        self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/shutdown":
            self._send_json({"status": "shutting_down"})
            threading.Thread(target=self._shutdown, daemon=True).start()
            return
        if self.path != "/v1/audio/speech":
            return self._send_json({"error": "not found"}, 404)

        try:
            body = self._read_json_body()
        except ValueError as exc:
            return self._send_json({"error": str(exc)}, 400)

        text = str(body.get("input") or body.get("text") or "").strip()
        if not text:
            return self._send_json({"error": "input text is required"}, 400)
        if len(text) > 10000:
            return self._send_json({"error": "input text too long (10000 char max)"}, 413)

        voice = str(body.get("voice") or "af_heart")
        speed = body.get("speed", 1.0)
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            return self._send_json({"error": "speed must be a number"}, 400)
        speed = max(0.5, min(2.0, speed))
        lang = str(body.get("lang") or body.get("language") or "en-us")
        response_format = str(body.get("response_format") or "wav").lower()
        if response_format not in ("pcm", "wav", "mp3"):
            return self._send_json({"error": "response_format must be pcm, wav, or mp3"}, 400)

        state = ENGINE.state
        if state["voices"] and voice not in state["voices"]:
            return self._send_json(
                {"error": f"unknown voice {voice!r}", "available": state["voices"]}, 400)

        try:
            if response_format == "pcm":
                self._send_audio(
                    "audio/pcm;rate=%d;channels=1;bits=16" % SAMPLE_RATE,
                    ENGINE.synth_stream(text, voice, speed, lang),
                    sample_rate=SAMPLE_RATE, chunked=True)
            else:
                audio, sample_rate = ENGINE.synth_full(text, voice, speed, lang)
                pcm = float_to_pcm16(audio)
                if response_format == "wav":
                    self._send_audio("audio/wav", wav_bytes(pcm, sample_rate))
                else:
                    data = mp3_bytes(pcm, sample_rate)
                    if data is None:
                        self._send_audio("audio/wav", wav_bytes(pcm, sample_rate))
                    else:
                        self._send_audio("audio/mpeg", data)
        except Busy:
            _STATS["errors"] += 1
            return self._send_json(
                {"error": "busy: one generation at a time on this host"}, 429)
        except Exception as exc:
            _STATS["errors"] += 1
            print(f"[kokoro] generation error: {exc}", flush=True)
            try:
                self._send_json({"error": str(exc)}, 500)
            except Exception:
                pass

    def _shutdown(self):
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description="Local Kokoro TTS service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8880)
    parser.add_argument("--warm", action="store_true", help="load model at startup")
    args = parser.parse_args()

    # Single-instance guard: refuse to start a second inference process.
    if os.environ.get("KOKORO_ALLOW_SECOND") != "1":
        import socket
        probe = socket.socket()
        try:
            probe.connect((args.host if args.host != "0.0.0.0" else "127.0.0.1", args.port))
            probe.close()
            print(f"[kokoro] service already answering on {args.host}:{args.port}; not starting another", flush=True)
            return 0
        except OSError:
            pass
        finally:
            probe.close()

    if not os.path.isfile(MODEL_PATH) or os.path.getsize(MODEL_PATH) < 1024 * 1024:
        print(f"[kokoro] FATAL: model file missing/incomplete: {MODEL_PATH}", flush=True)
        return 1
    if not os.path.isfile(VOICES_PATH) or os.path.getsize(VOICES_PATH) < 1024 * 1024:
        print(f"[kokoro] FATAL: voices file missing/incomplete: {VOICES_PATH}", flush=True)
        return 1

    if args.warm:
        threading.Thread(target=ENGINE.load, daemon=True).start()
    else:
        print("[kokoro] lazy mode: model loads on first speech request "
              "(start with --warm to pre-load)", flush=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"[kokoro] listening on http://{args.host}:{args.port} (loopback only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
