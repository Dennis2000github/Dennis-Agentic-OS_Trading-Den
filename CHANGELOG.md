# Changelog — DENNIS Agentic OS upgrade (`upgrade-cosmos`)

All dates Asia/Manila. Scope: **`Dennis-Agentic-OS/` only** (OpenJarvis is adjacent context, untouched).
Nothing was installed. Existing bridge, voice setup and dashboards were patched, not rebuilt.

## 2026-11-20 — Fish Audio key configured from local .env

- Created a repo-root `.env` with `FISH_API_KEY` at mode **0600**, ignored by `.gitignore` (`.env`,
  `.env.*`) and **never staged or committed**. The key value is never logged or returned to the browser.
- Added a stdlib-only `_load_dotenv()` in `app.py` (app dir, then repo root; existing environment wins),
  called before `_ensure_denma_runtime()` so the value survives the DenMa re-exec. No new dependency.
- Live `/api/tts/status` now reports `fish.configured: true`, correct `/v1/tts` endpoint, `s2.1-pro`
  model header, and **no key fields in the payload** (doctor: 13 pass/info, 0 FAIL).
- Note: the earlier standing rule was "secrets only in keychain / `~/.jarvis` (0600)". This `.env` was
  created at the user's explicit request; it is chmod 600 and gitignored, but it is a deviation from
  that rule and is deliberately kept out of version control. No live paid TTS call was placed.

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
