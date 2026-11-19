"""End-to-end acceptance test: DENNIS TTS Router -> Kokoro inference -> audio.

Starts the OS server in-process (ephemeral port) and exercises the real
/api/tts/* routes against the live local Kokoro service on 127.0.0.1:8880.
Writes a WAV to /tmp suitable for `afplay`.

Run:  DENNIS_NO_DENMA=1 python3 tests/e2e_tts_kokoro.py
"""

import http.client
import json
import os
import sys
import threading
import time
import wave

os.environ.setdefault("DENNIS_NO_DENMA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as server  # noqa: E402
from backend import auth as dennis_auth  # noqa: E402

OUT_WAV = "/tmp/dennis_kokoro_e2e.wav"


def request(port, method, path, body=None, token=None, raw=False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Dennis-Token"] = token
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    conn.request(method, path, body=payload, headers=headers)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    if raw:
        return res.status, dict(res.getheaders()), data
    try:
        return res.status, json.loads(data.decode("utf-8"))
    except Exception:
        return res.status, data


def main():
    httpd = server.DennisHTTPServer(("127.0.0.1", 0), server.DennisOSHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = dennis_auth.AUTH.token
    print(f"[e2e] OS server on :{port}, Kokoro service on :8880")

    # 1. Router status
    status, body = request(port, "GET", "/api/tts/status", token=token)
    kokoro = body.get("kokoro", {})
    print(f"[e2e] status: HTTP {status}, kokoro healthy={kokoro.get('healthy')}, "
          f"loaded={kokoro.get('loaded')}, model={kokoro.get('model')}")
    assert status == 200 and kokoro.get("healthy"), "Kokoro service must be healthy"

    # 2. Voice catalog
    status, body = request(port, "GET", "/api/tts/voices", token=token)
    voices = body.get("voices", [])
    ids = [v["id"] if isinstance(v, dict) else v for v in voices]
    print(f"[e2e] voices: HTTP {status}, count={len(ids)}, "
          f"bm_george present={'bm_george' in ids}")
    assert "bm_george" in ids, "Jarvis preset voice must exist"

    # 3. Non-streaming WAV generation through the Jarvis preset
    t0 = time.time()
    status, headers, data = request(port, "POST", "/api/tts/speech", {
        "text": "Integration check complete. The centralized voice router is operational, Dennis.",
        "preset": "jarvis-british-male",
        "response_format": "wav",
    }, token=token, raw=True)
    dt = time.time() - t0
    ctype = headers.get("Content-Type", "")
    print(f"[e2e] speech (wav): HTTP {status}, {len(data)} bytes in {dt:.1f}s, type={ctype}")
    assert status == 200 and ctype.startswith("audio/wav"), "expected WAV audio"
    with open(OUT_WAV, "wb") as fh:
        fh.write(data)
    with wave.open(OUT_WAV, "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
        print(f"[e2e] WAV valid: {duration:.2f}s @ {wav.getframerate()} Hz, {wav.getnchannels()} ch")

    # 4. Streaming PCM generation (chunked relay through the OS router)
    t0 = time.time()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    conn.request("POST", "/api/tts/speech",
                 body=json.dumps({
                     "text": "Streaming check. Each sentence arrives as it is synthesized.",
                     "preset": "jarvis-british-male", "stream": True,
                 }).encode("utf-8"),
                 headers={"Content-Type": "application/json", "X-Dennis-Token": token})
    res = conn.getresponse()
    first_byte = None
    total = 0
    while True:
        chunk = res.read(4096)
        if not chunk:
            break
        if first_byte is None:
            first_byte = time.time() - t0
        total += len(chunk)
    conn.close()
    print(f"[e2e] speech (pcm stream): HTTP {res.status}, {total} bytes, "
          f"first byte after {first_byte:.1f}s, total {time.time() - t0:.1f}s")
    assert res.status == 200 and total > 10000, "expected streamed PCM audio"

    httpd.shutdown()
    print(f"[e2e] PASS — WAV written to {OUT_WAV}")
    print(f"[e2e] play it with: afplay {OUT_WAV}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
