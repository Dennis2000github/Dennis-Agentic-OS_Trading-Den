"""DENNIS central TTS service — official Kokoro v1.0 (82M) via ONNX Runtime CPU.

Serving layer for the DENNIS Agent OS centralized TTS Router. Implements the
OpenAI-compatible speech API surface (the same shape as remsky/Kokoro-FastAPI)
so both the OS frontend and the installed Hermes Agent (`tts.provider: openai`)
can use one shared local service:

    GET  /health                service + model status
    POST /warmup                force model load + a tiny synthesis
    GET  /v1/models             OpenAI-style model list ("kokoro")
    GET  /v1/audio/voices       Kokoro voice catalog
    POST /v1/audio/speech       OpenAI-compatible synthesis
                                response_format: pcm | wav | mp3

Design constraints for the target machine (Intel x86_64, 8 GB RAM):
- stdlib http.server (matches app.py conventions; no extra web framework),
- exactly ONE model instance, inference serialized behind a lock,
- disk cache for identical requests (greetings/acks replay instantly),
- bound to loopback only — never exposed publicly,
- PCM output is 24 kHz mono s16le (Kokoro native), X-Audio-Sample-Rate header
  set for consumers like the Hermes OpenAI streaming TTS provider.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOST = os.environ.get("KOKORO_HOST", "127.0.0.1")
PORT = int(os.environ.get("KOKORO_PORT", "8880"))
MODEL_PATH = os.environ.get(
    "KOKORO_MODEL_PATH", os.path.join(BASE_DIR, "models", "kokoro-v1.0.onnx"))
VOICES_PATH = os.environ.get(
    "KOKORO_VOICES_PATH", os.path.join(BASE_DIR, "models", "voices-v1.0.bin"))
THREADS = int(os.environ.get("KOKORO_THREADS", "4"))
# Where generated-audio caches live (inside the OS repo data dir, git-ignored).
CACHE_DIR = os.environ.get(
    "KOKORO_CACHE_DIR",
    os.path.join(os.path.dirname(BASE_DIR), "data", "tts", "cache"))
CACHE_MAX_BYTES = int(os.environ.get("KOKORO_CACHE_MAX_BYTES", str(256 * 1024 * 1024)))
QUEUE_WAIT_S = float(os.environ.get("KOKORO_QUEUE_WAIT_S", "180"))
MAX_INPUT_CHARS = int(os.environ.get("KOKORO_MAX_CHARS", "6000"))

STARTED_AT = time.time()

# ---------------------------------------------------------------------------
# Official Kokoro v1.0 voice catalog (id prefix -> language/gender metadata).
# First letter: language (a American, b British, e Spanish, f French, h Hindi,
# i Italian, j Japanese, p Polish, z Chinese). Second letter: m/f gender.
# Names below follow the official VOICES.md where a display name is published.
# ---------------------------------------------------------------------------
LANG_NAMES = {
    "a": "American English", "b": "British English", "e": "Spanish",
    "f": "French", "h": "Hindi", "i": "Italian", "j": "Japanese",
    "p": "Polish", "z": "Chinese",
}
VOICE_NAMES = {
    "af_heart": "Heart", "af_alloy": "Alloy", "af_aoede": "Aoede",
    "af_bella": "Bella", "af_jessica": "Jessica", "af_kore": "Kore",
    "af_nicole": "Nicole", "af_nova": "Nova", "af_river": "River",
    "af_sarah": "Sarah", "af_sky": "Sky", "am_adam": "Adam",
    "am_echo": "Echo", "am_eric": "Eric", "am_fenrir": "Fenrir",
    "am_liam": "Liam", "am_michael": "Michael", "am_onyx": "Onyx",
    "am_puck": "Puck", "am_santa": "Santa", "bf_alice": "Alice",
    "bf_emma": "Emma", "bf_isabella": "Isabella", "bf_lily": "Lily",
    "bm_daniel": "Daniel", "bm_fable": "Fable", "bm_george": "George",
    "bm_lewis": "Lewis", "ef_dora": "Dora", "em_alex": "Alex",
    "em_santa": "Santa", "ff_siwis": "Siwis", "hf_alpha": "Alpha",
    "hf_beta": "Beta", "hm_omega": "Omega", "hm_psi": "Psi",
    "if_sara": "Sara", "im_nicola": "Nicola", "jf_alpha": "Alpha",
    "jf_gongitsune": "Gongitsune", "jf_nezumi": "Nezumi",
    "jf_tebukuro": "Tebukuro", "jm_kumo": "Kumo", "pf_dora": "Dora",
    "pm_alex": "Alex", "pm_santa": "Santa", "zf_xiaobei": "Xiaobei",
    "zf_xiaoni": "Xiaoni", "zf_xiaoxiao": "Xiaoxiao", "zf_xiaoyi": "Xiaoyi",
    "zm_yunjian": "Yunjian", "zm_yunxi": "Yunxi", "zm_yunxia": "Yunxia",
    "zm_yunyang": "Yunyang",
}


def _voice_meta(voice_id):
    lang = LANG_NAMES.get(voice_id[:1], "Unknown")
    gender = "Male" if voice_id[1:2] == "m" else ("Female" if voice_id[1:2] == "f" else "Unknown")
    return {
        "id": voice_id,
        "name": VOICE_NAMES.get(voice_id, voice_id),
        "language": lang,
        "lang_code": "en-gb" if voice_id.startswith("b") else "en-us",
        "gender": gender,
        "labels": [lang.lower(), gender.lower()],
    }


class Engine:
    """Single Kokoro instance + serialized inference + disk cache."""

    def __init__(self):
        self.lock = threading.Lock()
        self.kokoro = None
        self.voice_ids = []
        self.load_error = None
        self.generations = 0
        self.errors = 0
        self._cache_lock = threading.Lock()

    # -- model lifecycle ----------------------------------------------------
    def load(self):
        """Load the ONNX model once; safe to call repeatedly."""
        with self.lock:
            if self.kokoro is not None or self.load_error:
                return
            if not os.path.isfile(MODEL_PATH):
                self.load_error = (
                    f"Model weights not found at {MODEL_PATH}. Run "
                    f"tts_service/setup.sh to download the official Kokoro v1.0 export.")
                print(f"[kokoro] ERROR: {self.load_error}", flush=True)
                return
            t0 = time.time()
            import onnxruntime as ort
            from kokoro_onnx import Kokoro
            so = ort.SessionOptions()
            so.intra_op_num_threads = THREADS
            sess = ort.InferenceSession(MODEL_PATH, so, providers=["CPUExecutionProvider"])
            self.kokoro = Kokoro.from_session(sess, VOICES_PATH)
            self.voice_ids = sorted(self.kokoro.get_voices())
            print(f"[kokoro] model loaded in {time.time() - t0:.1f}s "
                  f"(threads={THREADS}, voices={len(self.voice_ids)})", flush=True)

    def warmup(self):
        self.load()
        if self.kokoro is None:
            return
        t0 = time.time()
        audio, rate = self.kokoro.create("Systems online.", voice="bm_george")
        print(f"[kokoro] warm-up synthesis {time.time() - t0:.1f}s "
              f"({len(audio) / rate:.1f}s audio)", flush=True)

    # -- cache --------------------------------------------------------------
    def _cache_path(self, key, fmt):
        return os.path.join(CACHE_DIR, f"{key}.{fmt}")

    def cache_get(self, key):
        """Return cached WAV bytes for the key, or None."""
        path = self._cache_path(key, "wav")
        try:
            with self._cache_lock:
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        return f.read()
        except OSError:
            pass
        return None

    def cache_put(self, key, wav_bytes):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            path = self._cache_path(key, "wav")
            tmp = path + ".tmp"
            with self._cache_lock:
                with open(tmp, "wb") as f:
                    f.write(wav_bytes)
                os.replace(tmp, path)
                self._prune_cache()
        except OSError as exc:
            print(f"[kokoro] cache write skipped: {exc}", flush=True)

    def _prune_cache(self):
        try:
            entries = []
            total = 0
            for name in os.listdir(CACHE_DIR):
                p = os.path.join(CACHE_DIR, name)
                if os.path.isfile(p):
                    st = os.stat(p)
                    entries.append((st.st_atime, st.st_size, p))
                    total += st.st_size
            if total <= CACHE_MAX_BYTES:
                return
            entries.sort()
            for _, size, p in entries:
                if total <= CACHE_MAX_BYTES * 0.85:
                    break
                os.unlink(p)
                total -= size
        except OSError:
            pass

    # -- synthesis ----------------------------------------------------------
    def synthesize(self, text, voice, speed, lang):
        """Return (samples float32, rate). Serialized; callers hold no lock."""
        self.load()
        if self.kokoro is None:
            raise RuntimeError(self.load_error or "Kokoro model is not loaded")
        with self.lock:
            return self.kokoro.create(text, voice=voice, speed=speed, lang=lang)

    def synthesize_stream(self, text, voice, speed, lang):
        """Yield (samples, rate) per internal batch as each finishes.

        The async ``kokoro.create_stream()`` generator runs on a worker thread
        and is bridged into a sync generator via a bounded queue. The engine
        lock is held for the whole request so inferences stay serialized.
        Abandoning this generator (client disconnect) stops synthesis.
        """
        self.load()
        if self.kokoro is None:
            raise RuntimeError(self.load_error or "Kokoro model is not loaded")
        return _StreamPump(self, text, voice, speed, lang)


class _StreamPump:
    """Sync iterator over the async kokoro stream (thread + queue bridge)."""

    _DONE = object()

    def __init__(self, engine, text, voice, speed, lang):
        self.engine = engine
        self.text = text
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.queue = None
        self.thread = None
        self.stopped = threading.Event()

    def __iter__(self):
        import asyncio
        import queue as queue_mod

        self.queue = queue_mod.Queue(maxsize=4)
        engine = self.engine
        stopped = self.stopped

        async def _consume():
            agen = engine.kokoro.create_stream(
                self.text, voice=self.voice, speed=self.speed, lang=self.lang)
            try:
                while True:
                    if stopped.is_set():
                        break
                    try:
                        samples, rate = await agen.__anext__()
                    except StopAsyncIteration:
                        break
                    if stopped.is_set():
                        break
                    # Bounded put with abandon: block briefly so a slow but
                    # live reader never loses audio, yet a disconnected reader
                    # cannot wedge the worker (it re-checks `stopped`).
                    payload = ("chunk", samples, rate)
                    while not stopped.is_set():
                        try:
                            self.queue.put(payload, timeout=0.5)
                            break
                        except queue_mod.Full:
                            continue
                    if stopped.is_set():
                        break
            except Exception as exc:  # surface synthesis errors to the reader
                self.queue.put(("error", exc, None))
            finally:
                self.queue.put(("done", None, None))

        def _run():
            engine.lock.acquire()
            try:
                asyncio.run(_consume())
            finally:
                engine.lock.release()

        self.thread = threading.Thread(target=_run, name="kokoro-stream", daemon=True)
        self.thread.start()
        try:
            while True:
                kind, payload, rate = self.queue.get()
                if kind == "chunk":
                    yield payload, rate
                elif kind == "error":
                    raise payload
                else:
                    break
        finally:
            # Producer thread exits on its own; mark stopped so a still-running
            # loop does not queue further chunks for nobody.
            stopped.set()


ENGINE = Engine()


# ---------------------------------------------------------------------------
# Audio encoding helpers
# ---------------------------------------------------------------------------

def samples_to_pcm16(samples):
    import numpy as np
    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


def pcm16_to_wav(pcm_bytes, rate=24000):
    buf = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm_bytes)
    buf.seek(0)
    return buf.read()


def wav_to_pcm16(wav_bytes):
    import io
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        return w.readframes(w.getnframes()), w.getframerate()


def find_ffmpeg():
    for cand in (
        os.path.join(os.path.dirname(BASE_DIR), "agent-skill-builder", "bin", "ffmpeg"),
        shutil.which("ffmpeg"),
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def convert_wav(wav_bytes, fmt):
    """Convert WAV bytes to `fmt` using ffmpeg. Returns (bytes, content_type)."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.wav")
        dst = os.path.join(td, f"out.{fmt}")
        with open(src, "wb") as f:
            f.write(wav_bytes)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
               "-i", src]
        if fmt == "mp3":
            cmd += ["-codec:a", "libmp3lame", "-q:a", "4", dst]
        else:
            return None
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            return None
        if not os.path.isfile(dst):
            return None
        with open(dst, "rb") as f:
            return f.read()


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DennisKokoro/1.0"

    # -- helpers ------------------------------------------------------------
    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > 2 * 1024 * 1024:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def log_message(self, fmt, *args):
        print(f"[kokoro] {self.address_string()} {fmt % args}", flush=True)

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/health"):
            loaded = ENGINE.kokoro is not None
            status = "ready" if loaded else ("error" if ENGINE.load_error else "loading")
            # Response carries both schema families so every local client
            # (DENNIS TTS Router, Hermes-style probes, ad-hoc scripts) can
            # read one shared service.
            self._json({
                # DENNIS TTS Router schema
                "ok": loaded,
                "status": status,
                "service": "dennis-kokoro-tts",
                "model": "kokoro",
                "model_version": "kokoro-v1.0",
                "loaded": loaded,
                "device": f"cpu ({THREADS} threads)",
                "sample_rate": 24000,
                "voices": len(ENGINE.voice_ids),
                "error": ENGINE.load_error,
                "uptime_s": round(time.time() - STARTED_AT, 1),
                # kokoro_server.py-compatible fields
                "model_loaded": loaded,
                "load_error": ENGINE.load_error,
                "voices_loaded": loaded,
                "voice_count": len(ENGINE.voice_ids),
                "generations": ENGINE.generations,
                "errors": ENGINE.errors,
            })
            return
        if path == "/v1/models":
            self._json({"object": "list", "data": [{
                "id": "kokoro", "object": "model", "created": int(STARTED_AT),
                "owned_by": "dennis-local",
            }]})
            return
        if path == "/v1/audio/voices":
            if not ENGINE.voice_ids:
                ENGINE.load()
            metas = [_voice_meta(v) for v in ENGINE.voice_ids]
            self._json({"object": "list", "count": len(metas), "voices": metas,
                        "voice_ids": ENGINE.voice_ids})
            return
        self._json({"error": {"message": f"Unknown path {path}", "type": "not_found"}}, 404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/warmup":
            try:
                ENGINE.warmup()
                self._json({"ok": True, "status": "ready"})
            except Exception as exc:  # pragma: no cover
                self._json({"ok": False, "error": str(exc)}, 500)
            return
        if path != "/v1/audio/speech":
            self._json({"error": {"message": f"Unknown path {path}", "type": "not_found"}}, 404)
            return
        try:
            self._speech()
        except BrokenPipeError:
            print("[kokoro] client disconnected mid-stream", flush=True)
            ENGINE.errors += 1
        except Exception as exc:
            import traceback
            traceback.print_exc()
            ENGINE.errors += 1
            try:
                self._json({"error": {"message": str(exc), "type": "server_error"}}, 500)
            except OSError:
                pass

    def _speech(self):
        body = self._read_json()
        if not body:
            self._json({"error": {"message": "JSON body required",
                                  "type": "invalid_request"}}, 400)
            return
        text = (body.get("input") or body.get("text") or "").strip()
        if not text:
            self._json({"error": {"message": "input text is required",
                                  "type": "invalid_request"}}, 400)
            return
        if len(text) > MAX_INPUT_CHARS:
            self._json({"error": {"message": f"input exceeds {MAX_INPUT_CHARS} chars",
                                  "type": "invalid_request"}}, 400)
            return
        voice = body.get("voice") or "bm_george"
        speed = float(body.get("speed") or 1.0)
        fmt = (body.get("response_format") or "wav").lower()
        if fmt not in ("pcm", "wav", "mp3"):
            self._json({"error": {"message": "response_format must be pcm, wav or mp3",
                                  "type": "invalid_request"}}, 400)
            return
        lang = body.get("lang") or ("en-gb" if voice.startswith("b") else "en-us")

        # Cache lookup (cache always stores WAV; pcm streams are derived).
        key = hashlib.sha1(
            f"{voice}|{speed}|{lang}|{fmt}|{text}".encode("utf-8")).hexdigest()
        if fmt != "mp3":
            cached = ENGINE.cache_get(key)
            if cached:
                pcm, rate = wav_to_pcm16(cached)
                if fmt == "wav":
                    self._send_audio(cached, "audio/wav")
                else:
                    self._send_audio(pcm, f"audio/pcm; rate={rate}",
                                     extra={"X-Audio-Sample-Rate": str(rate)})
                return

        t0 = time.time()
        if fmt == "pcm":
            samples_iter = ENGINE.synthesize_stream(text, voice, speed, lang)
            first = True
            for samples, rate in samples_iter:
                pcm = samples_to_pcm16(samples)
                if first:
                    self.send_response(200)
                    self.send_header("Content-Type", f"audio/pcm; rate={rate}")
                    self.send_header("X-Audio-Sample-Rate", str(rate))
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    first = False
                self._write_chunk(pcm)
            if first:  # no batches produced
                self._json({"error": {"message": "synthesis produced no audio",
                                      "type": "server_error"}}, 500)
                return
            self.wfile.write(b"0\r\n\r\n")
            ENGINE.generations += 1
            print(f"[kokoro] streamed {len(text)} chars in {time.time() - t0:.1f}s",
                  flush=True)
            return

        samples, rate = ENGINE.synthesize(text, voice, speed, lang)
        pcm = samples_to_pcm16(samples)
        wav = pcm16_to_wav(pcm, rate)
        ENGINE.cache_put(key, wav)
        ENGINE.generations += 1
        if fmt == "wav":
            self._send_audio(wav, "audio/wav")
        else:  # mp3
            mp3 = convert_wav(wav, "mp3")
            if mp3 is None:
                self._json({"error": {"message": (
                    "mp3 output requires an ffmpeg binary (checked "
                    "agent-skill-builder/bin/ffmpeg and PATH); use wav or pcm")},
                    "type": "server_error"}, 501)
                return
            self._send_audio(mp3, "audio/mpeg")
        print(f"[kokoro] synthesized {len(text)} chars -> {fmt} "
              f"in {time.time() - t0:.1f}s ({len(samples) / rate:.1f}s audio)", flush=True)

    def _send_audio(self, body, content_type, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _write_chunk(self, data):
        if not data:
            return
        self.wfile.write(f"{len(data):x}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    eager = os.environ.get("KOKORO_LAZY", "") not in ("1", "true", "yes")
    if eager:
        ENGINE.load()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"[kokoro] DENNIS Kokoro TTS service on http://{HOST}:{PORT} "
          f"(loopback only) pid={os.getpid()}", flush=True)
    if eager and ENGINE.kokoro is not None:
        try:
            ENGINE.warmup()
        except Exception as exc:
            print(f"[kokoro] warm-up failed: {exc}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
