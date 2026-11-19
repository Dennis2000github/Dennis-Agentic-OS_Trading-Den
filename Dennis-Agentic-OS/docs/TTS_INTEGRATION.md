# DENNIS Centralized TTS — Kokoro Integration

Status: **operational** (all automated tests pass; real audio generated and played).

One shared local Kokoro inference service serves every workspace — Hermes
Apollo (Jarvis voice), Ads Studio and YouTube Studio — through one provider
registry. ElevenLabs remains an optional cloud provider. No API credentials
exist in any frontend file.

## Architecture

```
HermesGalaxy.speak(text, onDone)          (js/galaxy.js — public signature preserved)
    ↓
window.DennisTTS                          (js/tts.js — DENNIS TTS client: queue,
    ↓                                      WebAudio PCM/WAV playback, cancel,
    ↓                                      browser fallback)
POST /api/tts/speech                      (app.py — owner-token gated, X-Dennis-Token)
    ↓
backend/tts.py  (Central TTS Router)      3-level provider resolution:
    ↓                                     preset override → studio preference → global
Kokoro Adapter (kokoro_speech / stream relay)
    ↓
tts_service/server.py                     ONE local inference process, loopback-only
    ↓                                     (managed by scripts/kokoro_service.py)
audio/pcm 24 kHz or audio/wav  →  Web Audio / afplay / Hermes voice mode
```

Hermes Agent's native TTS (voice mode) uses the same service through its
OpenAI-compatible provider: `tts.provider: openai` +
`tts.openai.base_url: http://127.0.0.1:8880/v1` (see below).

## Files

| File | Role |
|---|---|
| `tts_service/server.py` | Local Kokoro inference service (OpenAI-compatible subset: `/health`, `/v1/audio/voices`, `/v1/audio/speech` → pcm/wav/mp3, `/shutdown`). Chunked PCM streaming, sentence-batch granularity, response cache, single-instance guard, loopback bind. |
| `tts_service/kokoro_server.py` | Earlier equivalent implementation kept as an alternative; **not** started by default. |
| `tts_service/models/` | Official Kokoro v1.0 weights: `kokoro-v1.0.onnx` (325 MB fp32) + `voices-v1.0.bin` (27 MB, 54 voices incl. `bm_george`/`bm_lewis`). `kokoro-v1.0.int8.onnx` is a smaller/faster alternative (`KOKORO_MODEL` env). |
| `tts_service/.venv/` | Isolated Python 3.12 runtime: `kokoro-onnx 0.6.1`, `onnxruntime`, `espeakng_loader` (bundled espeak-ng — no system G2P deps). |
| `scripts/kokoro_service.py` | Service manager: `start | stop | restart | status | health`. PID file `data/tts/service.pid`, log `data/tts/service.log`. Never double-starts. |
| `backend/tts.py` | Central TTS Router: provider registry (kokoro default, elevenlabs optional, browser fallback-only), 3-level provider resolution, presets, secrets (0600), concurrency cap, saved voiceovers (`data/tts/audio`), service control. |
| `app.py` | Routes: `GET /api/tts/{status,config,voices,audio/<file>}`, `POST /api/tts/{config,service,speech}`. All owner-token gated (`X-Dennis-Token`); speech supports `stream: true` (chunked PCM relay) and `save: true` (persist + descriptor). |
| `js/tts.js` | `window.DennisTTS`: speak/queue/cancel, PCM streaming player (Web Audio), WAV fallback, browser-fallback engine, status/config APIs. |
| `js/galaxy.js` | `speak()` routes via DennisTTS (browser synthesis kept as explicit fallback only); mic suspension during speech; general voice commands submitted to the **selected agent** via `window.DennisAgentBridge` → `/api/chat`, real agent reply spoken through Kokoro. |
| `js/app.js` | `window.DennisAgentBridge`: reads `activeAgentState`/`activeModelState`, submits chat queries, returns the real reply. |
| `js/voiceRegistry.js` | Settings card (global default, per-studio presets, service toggle, ElevenLabs key) + per-studio voiceover panels with save/download. |
| `index.html` | Apollo widget voice controls + status line + provider panel; Voice Provider Registry card in settings; studio voiceover mounts; script tags for `tts.js` and `voiceRegistry.js`. |
| `tests/test_tts_routes.py` | 15 route/unit tests (stdlib unittest). |
| `tests/e2e_tts_kokoro.py` | End-to-end against the live service (status → voices → WAV → PCM stream). |

## Installation (already complete on this machine)

No Docker, no Homebrew. Requirements: the `tts_service/.venv` (uv-managed
Python 3.12) and the two model files. If models are missing:

```bash
cd tts_service/models
curl -L -o kokoro-v1.0.onnx  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o voices-v1.0.bin   https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

## Service startup / management

```bash
python3 scripts/kokoro_service.py status    # health probe
python3 scripts/kokoro_service.py start     # starts tts_service/server.py on 127.0.0.1:8880 (single instance)
python3 scripts/kokoro_service.py restart
python3 scripts/kokoro_service.py stop
```

Or from the UI: Apollo widget → ⚙ voice panel → "Start/Stop TTS service", or
the Voice Provider Registry card in Settings. The service binds **loopback
only** and is never exposed to the network. Exactly one inference process
exists; concurrent requests are serialized (browser 429s are surfaced as
"busy" errors, studio requests queue via the router semaphore).

## Hermes Agent configuration (`~/.hermes/config.yaml`)

Backup: `~/.hermes/config.yaml.pre-kokoro-20261110-183910.bak` (and older
`config.yaml.dennis-backup-*`). Verified against installed Hermes v0.22.2
(`tts_tool_openai.py` honors `base_url`/`voice`/`speed`/`pcm_sample_rate`;
`tts_streaming.py` streams `response_format: pcm` and honors
`X-Audio-Sample-Rate`).

```yaml
tts:
  provider: openai
  openai:
    base_url: http://127.0.0.1:8880/v1
    model: kokoro
    voice: bm_george        # JARVIS — BRITISH MALE
    api_key: local-kokoro   # placeholder; the local service does not validate keys
    pcm_sample_rate: 24000
    speed: 1.03
```

STT (`stt.provider: local`, whisper base), voice (`ctrl+b`), model, agent,
Telegram/Discord/Slack sections are untouched. Verified end-to-end:
whole-file synthesis **and** the streaming voice-mode path both produced
24 kHz `bm_george` audio through the local service
(`/tmp/hermes_kokoro_test.wav`).

## TTS Router API (browser → OS server, always token-gated)

| Endpoint | Purpose |
|---|---|
| `GET /api/tts/status` | providers, global default, per-studio prefs, kokoro health, elevenlabs quota |
| `GET /api/tts/config` / `POST` | read / patch config (studios, presets, apollo flags, `provider_secrets`) |
| `GET /api/tts/voices` | voice catalog from the Kokoro service |
| `POST /api/tts/speech` | generate: `{text, studio, preset, voice_id, speed, lang, response_format, stream, save, name}` → audio bytes, chunked PCM (`stream:true`), or saved-audio descriptor (`save:true`) |
| `POST /api/tts/service` | `{action: start|stop|restart|status}` |
| `GET /api/tts/audio/<file>` | serve saved voiceovers |

Presets live in `data/tts/config.json`. `jarvis-british-male` = `bm_george`
(fallback `bm_lewis`), lang `en-gb`, speed 1.03, output pcm. Presets are
provider-independent: a preset without an explicit provider follows the
studio preference, which follows the global default. Changing the Ads Studio
preference can never alter Apollo's voice.

## Test results (2026-11-10)

- `tests.test_tts_routes` — **15/15 OK** (owner gate, config validation,
  preset merge semantics, provider resolution, proxy passthrough, secret
  non-echo, saved-audio serving).
- `tests.test_server_security` + `tests.test_backend` — **99/99 OK**.
- Full backend suite — 363 tests: all TTS tests pass; 2 pre-existing failures
  unrelated to TTS (`test_plugins` `rea` catalog pack,
  `test_studio_routes` provider credential descriptor) introduced by other
  in-progress work; 1 flaky timeout under heavy machine load (not
  reproducible on re-run).
- JS tests (`node tests/*.test.js`) — all pass (agentDashboards, kanbanState,
  panelState, studioState, studioWorkspaceState).
- E2E (`tests/e2e_tts_kokoro.py`): status OK, 54 voices incl. `bm_george`,
  5.33 s WAV via JARVIS preset, PCM streaming 200 OK, Ads voiceover saved
  (`data/tts/audio/ads-check-*.wav`) and served, studio preset independence
  verified.
- Playback: `afplay` played router-generated Kokoro audio and Hermes-generated
  Kokoro audio aloud on this machine.
- Service lifecycle: `stop` → port free → `start` → healthy (single process).
- Hermes Agent: native TTS config verified by invoking the installed agent's
  own TTS tool path with the real `~/.hermes/config.yaml`.

## Measured latency (Intel x86_64, 4 cores, 8 GB, macOS 13.7)

| Condition | Throughput | Example |
|---|---|---|
| Quiet machine, fp32, warm | **≈ 1.2× realtime** | 4.74 s speech in 4.0 s |
| Quiet machine, int8, warm | ≈ 0.5× realtime (slower — dequant overhead) | — |
| PyCharm indexing + Chrome running (load avg 125) | ≈ 0.15–0.3× realtime | 4.7 s speech in 20–69 s |

Model load: ~6–14 s at service start (lazy by default; the manager warms it).

## Limitations & honest notes

1. **Streaming granularity** is kokoro's internal phoneme batching
   (`MAX_PHONEME_LENGTH = 510` ≈ 35–40 s of speech). Texts shorter than that
   arrive as a single chunk, so time-to-first-audio ≈ total synthesis time
   for typical conversational replies. The chunked transport is real and
   benefits multi-paragraph replies.
2. **Kokoro voices are not the browser's previous voices** and are not a
   cloned JARVIS. `bm_george` is the closest standard British male voice.
   No pitch/emotion/cloning controls are exposed because the provider does
   not support them.
3. **ElevenLabs** is registered and selectable but unconfigured (no API key
   stored); quota display activates once a key is set. Non-kokoro providers
   do not support the streaming path (buffered MP3 only).
4. **CPU latency under load** can make long spoken replies slow (see table).
   This is a hardware/load limitation, reported per spec — the system does
   not silently switch providers. Closing heavy apps (PyCharm indexing,
   Chrome tabs) restores ≈1.2× realtime.
5. **Browser fallback** (`speechSynthesis`) is kept behind the
   "Browser voice fallback" checkbox (default on) and engages only when the
   router/service fails.
6. The earlier parallel implementation `tts_service/kokoro_server.py` remains
   in the repo as an alternative but is not started by the manager.

## Manual browser checklist (requires microphone)

1. Open the OS → Apollo widget shows "Kokoro connected … voices".
2. Click Wake → greeting spoken by Kokoro (`bm_george`).
3. Say "Hey Apollo" → wake chime + status "Apollo Awake".
4. "Open trading den" → navigates + spoken confirmation.
5. "Explain the current project architecture" → transcript shows
   `Asking <agent>…` → real agent reply → spoken via Kokoro.
6. Mic suspends during playback (Apollo does not transcribe itself) and
   resumes after.
7. "Go to sleep" → sleep chime, standby.
8. Mic toggle mutes/unmutes wake; ⚙ panel: preview/test/speed/provider save.
9. Settings → Voice Provider Registry: global default, per-studio presets,
   service toggle, ElevenLabs key field.
10. Ads Studio / YouTube Studio voiceover panels: generate + audio player +
    download from `data/tts/audio/`.
