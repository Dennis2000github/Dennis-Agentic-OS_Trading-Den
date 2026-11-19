# OpenJarvis Voice — Audit & Setup

_Setup: 2026-11-12._

## 1. Audit of the existing Jarvis voice stack — verdict: KEEP

Audited per `JARVIS_V2_AUDIT.md`. Nothing needed deleting:

| Area | File | Status |
| --- | --- | --- |
| Wake word + recognition loop | `js/galaxy.js` (Hermes Apollo Voice Core) | Healthy — reuse |
| Kokoro TTS service | `tts_service/server.py` (+ `kokoro_server.py`) | Healthy — reuse |
| TTS router (3-tier: Kokoro → ElevenLabs → browser) | `backend/tts.py` | Healthy — reuse |
| Voice Provider Registry UI | `js/voiceRegistry.js` | Healthy — reuse |
| JARVIS dashboard | `js/jarvisDashboard.js` | Healthy — reuse |
| Repo-root audio test artifacts (`jarvis_test.wav`, `s1.pcm`, …) | repo root | Already cleaned up before this audit |

The ElevenLabs adapter in `backend/tts.py` reads its key from
`ELEVENLABS_API_KEY` **or** `data/tts/secrets.json` (`elevenlabs_api_key`,
0600). The key is now stored in `data/tts/secrets.json`, so the Voice Provider
Registry (section 6 of the workspace view) shows ElevenLabs as enabled and the
middle tier of the TTS fallback chain is live without any UI entry.

## 2. OpenJarvis (Stanford, Apache-2.0) — installed alongside

- **Repo:** https://github.com/open-jarvis/OpenJarvis (shallow clone, `main`)
- **Location:** `../OpenJarvis/` (sibling of this repo, kept out of this git history)
- **Python:** `uv` manages it (project requires `>=3.10,<3.14`; system 3.14.5 is
  too new, uv fetches a compatible interpreter into `../OpenJarvis/.venv`)
- **Its ElevenLabs backend:** `src/openjarvis/speech/elevenlabs_tts.py`
  (registered as `@TTSRegistry.register("elevenlabs")`), model
  `eleven_multilingual_v2`, default voice **George** (`JBFqnCBsd6RMkjVDRZzb`,
  warm British male — Jarvis-like)

### Where the ElevenLabs key lives (all 0600)

1. `../OpenJarvis/.env` — `ELEVENLABS_API_KEY` (sourced by `run-jarvis.sh`)
2. `~/.openjarvis/credentials.toml` — `[elevenlabs]` table; injected into the
   environment by `jarvis serve` / `jarvis gui` at startup
3. `data/tts/secrets.json` (this repo) — feeds `backend/tts.py`

### How to run

```bash
cd ../OpenJarvis
./run-jarvis.sh            # chat CLI (loads .env first)
./run-jarvis.sh gui        # local server + browser UI (Node 22+ present)
./run-jarvis.sh doctor     # component status
```

`jarvis gui` needs a front-end build (`frontend/`) and injects the key from
`~/.openjarvis/credentials.toml`; the wrapper covers plain CLI calls.

### Hardware note (Intel i5-7360U, 8 GB RAM)

OpenJarvis is local-first (Ollama engine). Ollama 0.40.2 is installed and the
service is started on demand (`ollama serve`, loopback 11434). The preset's
default `qwen3.5:4b` was too heavy for this machine, so `qwen3:0.6b`
(~522 MB, ~500 MB RAM at Q4) is the configured brain. On CPU expect roughly
4-8 tok/s — fine for chat and briefings, slow for long tool loops. A cloud
engine (NIM was reachable in `jarvis doctor`) is the speed upgrade if needed.

## 4. Caveman plugin — installed 2026-11-12

https://github.com/JuliusBrussee/caveman — token-reduction skill set
("answer first, fluff gone", cuts agent reply length 30-60%).

**Quota logic:** caveman shrinks agent *text*, not TTS directly — but Jarvis
speaks agent replies, so shorter replies = fewer ElevenLabs characters per
answer. The real TTS quota saver remains the Kokoro-first provider order.

- **Dennis OS:** installed via `backend/plugins.install_plugin` (the same code
  path as the Installed Plugins UI button) into `~/.agents/plugins/caveman`
  from the repo's `plugins/caveman/` bundle (v4.1.0, tier `public`). A
  `plugin.json` manifest was added so the card renders (title/icon/tag).
  Skills linked into: `claude-code`, `codex`, `gemini-cli`, `hermes`,
  `generic` (30 symlinks, 0 conflicts): `caveman`, `ultracave`, `megacave`,
  `cavecrew`, `caveman-compress`, `caveman-stats`.
- **OpenJarvis:** the three voice skills (`caveman`, `ultracave`, `megacave`)
  copied to `~/.openjarvis/skills/` (its skill catalog auto-discovers
  `SKILL.md` dirs there). The stats/crew skills were skipped — they target
  coding CLIs, not OpenJarvis agents.
- Say "stop caveman" / `/caveman off` to revert an agent to normal prose.

## 5. Relationship between the two systems

They are deliberately **parallel, not merged**: the Dennis OS keeps its
integrated Hermes Apollo voice core (wake word, galaxy visual, dashboard);
OpenJarvis provides its own speech stack (`speech/` backends: Kokoro, ElevenLabs,
Cartesia, Deepgram, OpenAI + Whisper STT) for its agents (morning digest,
deep research, …). Both now share the same ElevenLabs key.

**The bridge exists since 2026-11-13** (steps 1-2 of the JARVIS build order):

- The OS owns the mic, wake word and voice (Apollo in `js/galaxy.js`);
  OpenJarvis runs headless as the **briefing service**.
- `backend/digest.py` proxies OpenJarvis's `/api/digest*` REST API with hard
  timeouts and graceful degradation; `scripts/openjarvis_service.py` manages
  exactly one `jarvis serve` process (PID/log in `data/openjarvis/`) and
  starts **Ollama on demand** (engine discovery otherwise falls back to NIM
  without a key — observed live).
- **Engine: GLM 5.3 Flash via NVIDIA NIM** (2026-11-13). The NIM key was
  copied from the OpenCode provider auth into
  `~/.openjarvis/credentials.toml` (`[nim] NIM_API_KEY`, 0600) and
  `~/.openjarvis/config.toml` now sets `engine.default = "nim"` with
  `default_model = "z-ai/glm-5.3-flash"` (confirmed present on NIM's model
  list). Ollama `qwen3:0.6b` remains the local fallback. This replaced the
  incoherent 0.6b digest ramble with a proper Jarvis-persona briefing.
- **Connectors to the OS (2026-11-13)**: the OS vault is wired in as a
  digest source — `scripts/openjarvis_service.py` exports
  `OBSIDIAN_VAULT_PATH=<repo>/vault`, the Obsidian connector gained the env
  fallback + a naive/aware `since` fix, and `digest_collect.py` gained a
  MEMORY section (`obsidian`, `apple_notes`, `granola`). `apple_calendar`
  joined CALENDAR and `hackernews` covers WORLD. Notes/iMessage/Calendar
  sqlite reads are TCC-blocked until Full Disk Access is granted to the
  host process — the connectors then light up with zero code changes.
- OS endpoints (owner-token gated): `GET /api/digest`,
  `GET /api/digest/weekly`, `GET|POST /api/digest/schedule`,
  `GET /api/digest/history`, `GET /api/digest/audio`,
  `GET /api/digest/service`, `POST /api/digest/generate` (body
  `{"kind": "daily"|"weekly"}`), `POST /api/digest/service`.
- Dashboard: **Briefing history** panel (DAILY/WEEKLY badges, tap to reopen,
  latest speaks through DennisTTS with the JARVIS preset) with separate
  daily/weekly generate buttons, plus the telemetry strip and command bar.
- **Voice command**: "Hey Apollo … daily briefing / brief me" speaks the
  latest briefing through the JARVIS voice
  (`HermesGalaxy.handleBriefingCommand`).
- **Weekly briefings**: digest artifacts carry a `kind` (`daily`/`weekly`,
  migrated column); the digest agent uses a 168 h window and a weekly hint
  when asked for a weekly digest. The OS runs a weekly auto-scheduler daemon
  (`dennis_digest.start_weekly_scheduler()`, default Monday 08:00 local,
  `DENNIS_WEEKLY_BRIEFING=0` to disable) and the panel can generate one on
  demand.
- Local OpenJarvis patches (kept minimal, upstream Apache-2.0):
  `connectors/obsidian.py` (env vault path + tz fix), `tools/digest_collect.py`
  (MEMORY section + obsidian formatter), `agents/morning_digest.py`
  (memory sources, weekly window/kind), `agents/digest_store.py` (kind column
  via the existing `_migrate` pattern), `server/digest_routes.py`
  (kind params + `/api/digest/latest`).
- Config knobs: `OPENJARVIS_URL` (default `http://127.0.0.1:8000`),
  `OPENJARVIS_HOME` (default `../OpenJarvis`), `OPENJARVIS_CMD`,
  `OPENJARVIS_API_KEY` (only when the service runs with auth),
  `DENNIS_WEEKLY_BRIEFING[_DAY|_HOUR]`.
