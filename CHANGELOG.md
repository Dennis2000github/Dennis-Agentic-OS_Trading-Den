# Changelog — DENNIS Agentic OS upgrade (`upgrade-cosmos`)

All dates Asia/Manila. Scope: **`Dennis-Agentic-OS/` only** (the sibling OpenJarvis checkout itself
was never modified; the OS's dependency on it was removed on 2026-11-21).
Nothing was installed. Existing bridge, voice setup and dashboards were patched, not rebuilt.

## 2026-11-22 — Voice Provider → Model → Voice picker (free-first, local-first)

- New shared picker `js/voicePicker.js` + `css/voice-picker.css` mounted on the JARVIS
  dashboard bottom bar (`#jarvis-bar-voice`, scopes Apollo + Global default, plus a
  registry section with the Kokoro service toggle and the ElevenLabs key field) and
  auto-mounted in the Ads (`#studio-ads-voice`) and YouTube (`#studio-youtube-voice`)
  studio headers. Replaces the Workspace "Voice Provider Registry" card (removed);
  the registry capabilities remain available in the picker's registry section and
  Settings → Voice. `js/voiceRegistry.js` deleted.
- New read-only `GET /api/tts/catalog`: providers sorted local-first then
  free → free-tier → paid → deprecated, with a **verified** model catalog — Fish
  (`s2.1-pro-free` free; `s2.1-pro`, `s2-pro` paid; `s1` deprecated) from the Fish
  developer docs; ElevenLabs (`eleven_flash_v2`, `eleven_flash_v2_5`,
  `eleven_multilingual_v2` free-tier; v3/v4/v4-turbo paid) from the ElevenLabs docs;
  Kokoro `kokoro-v1.0` (free, local) with its **54 voices discovered live** from the
  local service. No authenticated list calls are made; cloud voice IDs are owner-entered.
- `backend/tts.py`: per-scope `model_id`/`voice_id` + global defaults
  (`global_default_model_id`/`global_default_voice_id`), catalog validation
  (`_validate_model_id`), model passthrough in `generate()` for Fish/ElevenLabs/Kokoro,
  `status()` now reports live `available`/`default` per provider and exposes
  `models`. A preset's voice only applies when the preset targets the same provider,
  so switching a studio to a cloud provider never leaks a Kokoro voice ID upstream.
- Step 1 write rules completed for voice: `POST /api/tts/config`, `/api/tts/secret`,
  `/api/tts/service`, `/api/tts/service/start`, `/api/tts/service/stop` all require
  `confirmed:true` (400 otherwise) and append an audit entry (`who/what/when/result`).
  `DennisTTS` write helpers auto-attach `confirmed:true` after the caller confirms;
  the picker and the legacy Apollo voice panel both ask via `window.JarvisOs.confirm`
  first (paid models say so in the dialog).
- UI fixes found by browser testing: the Jarvis confirm overlay used `z-[9999]` /
  `bg-black/70`, which the pre-compiled Tailwind build does not contain — without a
  real z-index the neural-core canvas (under a `z-index:10` ancestor) painted over the
  dialog and swallowed Confirm/Cancel clicks; now inline `z-index:9999` + backdrop.
  The picker panel is portaled to `<body>` with fixed positioning so `.hud-panel`
  clip-path and studio `overflow:hidden` can no longer clip the dropdown.
- Global default verified/restored via the audited API: `kokoro` / `kokoro-v1.0` /
  `bm_george`; JARVIS + Narrator presets normalized (no drift). Unconfirmed POST
  returns 400; confirmed saves audited.
- `scripts/doctor.py` extended: JS syntax list now covers `tts.js`, `voicePicker.js`,
  `jarvisDashboard.js`; the write-confirmation check now verifies all 6 gated write
  routes → **13 pass/info, 0 fail**.
- Verification: `tests.test_tts` + `tests.test_tts_routes` **32/32**, JS suites
  (`node --test tests/*.test.js`) **6/6**, `tests/e2e_tts_kokoro.py` PASS (54 voices,
  valid WAV + PCM stream), read-only Playwright browser smoke **22/22** (dashboard +
  both studio headers render unclipped, live voices listed, confirm dialog wired,
  Settings badges correct, registry card gone), UI save → audited entry written, live
  `/api/tts/speech` 200 (129,068-byte WAV). App bound to `127.0.0.1:8080` only;
  Kokoro on `127.0.0.1:8880` only. Pre-existing unrelated test failures (freellmapi
  imports, UPIF, plugins, stale skill-builder cache-bust expectation) are documented
  and were left untouched.

## 2026-11-21 — OpenJarvis dependency removed; native Step 5 briefings

- Replaced the sibling-repo digest bridge with `backend/briefing.py`: local-only, no `:8000`
  service and no sibling checkout. The `/api/digest/*` contract is preserved so the JARVIS
  briefing panel and voice command keep working. Every section carries its source path and
  missing values are reported as `unknown` — nothing is invented.
- `POST /api/digest/generate|schedule|service` now require `confirmed:true` (Step 1 write rule);
  the dashboard asks through `window.JarvisOs.confirm` before posting. GET routes stay read-only
  and `/api/digest/audio` never generates on a read.
- Artifacts live under `~/.jarvis/briefings` (directory 0700, files 0600): `daily.json`,
  `weekly.json`, `history.json`, `schedule.json`, `latest.wav`, `briefing.log`. Generation writes
  an append-only audit entry.
- The full evidence-heavy text stays on screen; local CPU TTS speaks a bounded (~390 char)
  real-data summary. The first full-text attempt made Kokoro hang past 180 s on CPU, so the
  spoken audio was deliberately condensed without dropping the on-screen evidence.
- Repointed the bridge connection card from `../OpenJarvis/.../claude_code.py` to the OS's own
  `backend/execution_router.py` + `backend/runner.py`; `OPENJARVIS_DIR` removed from `jarvis_os.py`.
- Removed the now-unused `backend/digest.py`, `scripts/openjarvis_service.py` and
  `docs/OPENJARVIS_VOICE_SETUP.md`. Older audit docs still mention OpenJarvis as history.
- `tests/test_digest_routes.py` rewritten: **18 tests pass**. Syntax checks pass. Live smoke:
  digest GETs 200, unconfirmed POST 400, one confirmed generation produced a 1,230,892-byte WAV
  in 86.6 s, audio GET 200, audit entry written. `npm run doctor`: **13 pass/info, 0 FAIL**.
- Kokoro was restarted to clear the stuck full-text synthesis; the app was restarted and remains
  bound to `127.0.0.1:8080` only.

## 2026-11-21 — Fish Audio key rotated (owner-supplied replacement)

- Owner supplied a genuinely new key (verified different from the original by SHA-256 prefix:
  old `7b8c7b051b7ad77c…`, new `d94afe1e669dcccd…`). The first "replacement" they pasted was
  byte-identical to the original and was **rejected** — no change was made until a distinct key arrived.
- Replaced the `FISH_API_KEY` line in `.env` (still 0600, gitignored, untracked). No key value appears
  in logs, git, or browser payloads.
- Restarted the app (PID 60507, `127.0.0.1:8080` only) and placed a live `POST /v1/tts` call with the
  new key: HTTP 200, `audio/mpeg`, **55,169 bytes**, valid MP3 magic `fffb90c4`, saved to
  `data/tts/fish_rotation_test.mp3` (gitignored test artifact, not committed).
- **Only after** the new key was verified, permanently revoked the exposed original key via
  `POST https://api.fish.audio/apikey/revoke` → **HTTP 202 accepted** (no auth required per official docs).
  The old key was held in a 0600 file under `~/.jarvis` solely for this step and that file was deleted.
- Two append-only audit entries recorded at 2026-11-21T00:05:56+08:00 (key installed / old key revoked).

## 2026-11-20 — Fish Audio key configured from local .env

- Created a repo-root `.env` with `FISH_API_KEY` at mode **0600**, ignored by `.gitignore` (`.env`,
  `.env.*`) and **never staged or committed**. The key value is never logged or returned to the browser.
- Added a stdlib-only `_load_dotenv()` in `app.py` (app dir, then repo root; existing environment wins),
  called before `_ensure_denma_runtime()` so the value survives the DenMa re-exec. No new dependency.
- Live `/api/tts/status` now reports `fish.configured: true`, correct `/v1/tts` endpoint, `s2.1-pro`
  model header, and **no key fields in the payload** (doctor: 13 pass/info, 0 FAIL).
- Note: the earlier standing rule was "secrets only in keychain / `~/.jarvis` (0600)". This `.env` was
  created at the user's explicit request; it is chmod 600 and gitignored, but it is a deviation from
  that rule and is deliberately kept out of version control.
- **Live call test (owner-approved):** `fish_audio_speech()` → `POST /v1/tts` returned HTTP 200,
  `audio/mpeg`, **58,095 bytes**, valid MP3 magic `fffb90c4`, saved to `data/tts/fish_test.mp3`
  (test artifact, not committed). Logged to the append-only audit at 2026-11-20T23:52:18+08:00.
  This confirms the key, endpoint, `model: s2.1-pro` header and JSON body all work end to end.

## 2026-11-20 — Follow-ups after the upgrade

- **Pushed** local `main` (`6f90f25`) to `origin/full-project`.
- **Kokoro local TTS started** — `python3 scripts/kokoro_service.py start` → PID 57233,
  `127.0.0.1:8880`, healthy, model `kokoro-v1.0` loaded, 54 voices. No install needed
  (venv, packages and weights were already provisioned). Settings > Voice now reports it online.
- **Fish Audio endpoint corrected** using official docs (docs.fish.audio, verified 2026-11-20):
  the initial `/v1/audio/speech` guess was wrong. Real contract is
  `POST https://api.fish.audio/v1/tts`, `Authorization: Bearer`, a `model` header
  (`s2.1-pro` default), and JSON `{text, format, reference_id?}`. Key is read from
  `FISH_API_KEY` (or `FISH_AUDIO_API_KEY`, or `~/.jarvis/secrets.json`). Status now reports
  `endpoint_verified_on_this_machine: true`. **No live call was made — no key exists on this
  machine.** To use it: set `FISH_API_KEY`, or POST the key once to `/api/tts/secret`
  (stored 0600, never echoed).
- **COSMOS honesty fix**: `executions.json` genuinely contains only 9 ANDROMEDA records;
  POLARIS/PLEIADES/QUASAR/HELIOS have zero. Instead of a bare `unknown`, those intelligences now
  report `status: "no executions recorded"`, `record_count: 0`, and `task: "no execution recorded"`
  with the source path. **No execution data was invented.** To produce real records, the COSMOS
  engine (`backend/cosmos_engine.py:run`) must actually be run for those agents — that is a separate,
  explicitly-confirmed action because it invokes providers and writes data.

## 2026-11-20 — Phase 5: lock-down, boot, doctor

- Added `npm run boot`, `npm run stop`, `npm run doctor` to `Dennis-Agentic-OS/package.json`.
  - `scripts/boot.sh` — idempotent start, localhost-only, owner-token health probe, PID/log under `~/.jarvis`.
  - `scripts/stop.sh` — graceful stop, handles legacy PID-less process, verifies port 8080 freed.
  - `scripts/doctor.py` — 13 checks: syntax, localhost bind (source + runtime), private-home/audit/secrets
    permissions, confirmation gating, Trading Den read-only, no key in browser payload, backend readiness.
- Restarted the live server: old PID 48225 (`*:8080`) → new PID 56177 (`127.0.0.1:8080` only).
- `npm run doctor` result: **13 pass/info, 0 FAIL**.
- Live smoke test: all `/api/jarvis/*` routes 200 with token, 401 without; `js/jarvisOs.js` served 200;
  all new DOM hooks present in `index.html`.
- Docker is not ready; local agent fallback is active (reported as INFO, not a failure).
- Kokoro local TTS at `127.0.0.1:8880` unreachable (honestly reported in the Settings > Voice panel).
- `~/.jarvis` is 0700; `audit.log` is 0600; `secrets.json` created 0600 on first key write.

## 2026-11-20 — Phase 4: dashboards + voice routing

- `js/jarvisOs.js` (new) — Settings Voice/Connections/Audit panels, confirmation dialog for all Genesis
  writes, audit client, tool-router client, mission strip renderer.
- `index.html` — Settings sidebar item + `#settings-view`; JARVIS-bar **Push to talk** + **LISTENING**
  indicator; collapsible **Mission** strip with Genesis + Trading Den panels; script include.
- `js/galaxy.js` — push-to-talk (dedicated one-shot recognition, wake loop suspended during PTT),
  visible browser-voice fallback notice, tool routing in `executeVoiceCommand` (unknown → normal brain).
- `js/app.js` — Settings wired into `switchMainView` / nav highlight / refresh.
- Genesis panel shows **working / needs approval / stopped** with real stop reasons (e.g.
  `ffmpeg_failed: ffmpeg exited with code 8: Error opening input files: Option not found`).
  Restart maps to the nearest existing endpoint — no native restart exists — and shows the reason first.
- Trading Den brief is **read-only**: hard-coded demo values (`+318.42`, `+271.15`, `38,432.77`) shown
  with an explicit **DEMO DATA** warning; DB rows are labelled seeded/all-history, not today's PNL.
  No bot/order/control action is exposed.
- Voice commands routed locally: `genesis status`, `trading den brief`, `cosmos status`.

## 2026-11-20 — Phase 3: backend + voice/connections groundwork

- `backend/jarvis_os.py` (new) — append-only audit (`who/what/when/result`, Asia/Manila), 6 connection
  cards (COSMOS, Genesis, Trading Den, Claude Agent SDK bridge, Kokoro, OpenClaw), COSMOS/Genesis/
  Trading Den read adapters, tool router, confirmation-gated Genesis action mapping. Unknown data renders
  as `"unknown"` with source/raw evidence — never invented.
- `backend/tts.py` — optional **Fish Audio** provider; key in `~/.jarvis/secrets.json` (0600), never
  echoed. Endpoint configurable via `FISH_AUDIO_TTS_URL`; flagged **unverified on this machine**
  (no local docs found; web check returned HTTP 402). Kokoro default and ElevenLabs unchanged.
- `app.py` — imported `jarvis_os`; added `/api/jarvis/*` GET routes and confirmation-gated POST routes;
  changed bind from `""` (all interfaces) to `"127.0.0.1"`.
- `.gitignore` — added `node_modules/`, `.jarvis/`, `**/.jarvis/` (`.env` was already excluded).
- Source decisions: browser receives connection **status only, never keys**; every write needs a
  confirmation dialog + audit entry; Trading Den strictly read-only.

## 2026-11-20 — Phases 1–2: audit + discovery

- `AUDIT.md` — Phase 1 audit, confirmed scope and security rules.
- `DISCOVERY.md` — Phase 2 read-only findings, data sources, confidence and gaps.
- No discovery write/control calls were made.

## Notes / honest unknowns

- Fish Audio endpoint could not be verified (HTTP 402 on web search, no local docs, no env var/package).
- Kokoro service unavailable at `127.0.0.1:8880` — reported offline, not hidden.
- COSMOS intelligences other than ANDROMEDA have no executions → `unknown` with source path.
- Never call `GET /api/history/spread-candles` (GET can write candles) — left untouched.
- Owner token comes from `/api/auth/session` via `X-Dennis-Token` / `?token=`; it is not a provider key.
