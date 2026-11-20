# AUDIT.md — JARVIS app, Phase 1 (audit only)

Audited: 2026-11-20 (Asia/Manila date context). No app code was rewritten in this phase.
Only `.gitignore` was extended and the git branch `upgrade-cosmos` was created.

## 0. Scope / which folder is "JARVIS" — evidence, not a guess

Raw evidence:

- Running process: `Python .../DENNIS AGENTIC OS_Trading Den/Dennis-Agentic-OS/app.py`,
  listening on `*:8080` (PID 48225, seen via `ps aux` + `lsof -nP -iTCP -sTCP:LISTEN`).
- `Dennis-Agentic-OS/index.html` title: `DENNIS Agentic OS - Your AI Brain. Your Tools. Your Universe.`;
  it contains `#os-sidebar`, `#sidebar-nav-jarvis`, and `js/jarvisDashboard.js`
  ("HERMES–JARVIS NEURAL COMMAND CENTER").
- `Dennis-Agentic-OS/docs/JARVIS_V2_AUDIT.md` calls this stack "the Jarvis OS" and
  describes Hermes Apollo voice core + `/api/chat` agent bridge.
- Sibling `OpenJarvis/` is an upstream clone (`origin https://github.com/open-jarvis/OpenJarvis.git`,
  HEAD `6201505`) that is **git-ignored** by the parent repo. It contains the Claude Agent
  SDK bridge (`src/openjarvis/agents/claude_code.py`, `claude_code_runner/package.json`)
  and cloud TTS backends (`speech/elevenlabs_tts.py`, `speech/kokoro_tts.py`).
- Other similarly named folders exist (`~/PycharmProjects/Dennis-Agentic-OS`,
  `~/PycharmProjects/DENNIS Agentic OS`, `~/Desktop/dennis-data-backup/openjarvis`,
  `~/Downloads/Chrome Downloads/JARVIS`) but are older copies / data / images; the
  running one is the `Dennis-Agentic-OS` folder above.

**Scope confirmed by user (2026-11-20 checkpoint):** the app to upgrade is the running
`Dennis-Agentic-OS/` app only. `OpenJarvis/` is adjacent context, not the upgrade target.
**User decision:** the "follower / subscriber / GoHighLevel pipeline / Your skills"
cards requirement is **discarded** — do not add, keep, or rebuild those cards in
Phases 3–5. The phrases "Settings > Voice" and "Settings > Connections" were also
not found in either codebase (see §7); those are to be created as new surface, not
treated as existing features.

## 1. Framework and versions

| Item | Value | Evidence |
| --- | --- | --- |
| App server | Python 3.14.5, stdlib `http.server` + `socketserver` (no Flask/FastAPI) | `app.py` imports, `python3 --version` |
| Server entry | `app.py` → `run(port=8080)`; `PORT = 8080` | `app.py:121`, `app.py:5877` |
| Frontend | Vanilla JS (ES5-style `var`/IIFE modules), static `index.html` (305 KB) | `index.html`, `js/*.js` |
| CSS | Prebuilt Tailwind 3.4.17 output (`css/tailwind.css`) + custom CSS | `index.html:8-9` |
| Desktop shell | Electron 45.1.0 | `Dennis-Agentic-OS/package.json` |
| 3D | three.js ^0.149.0 | `package.json` |
| Local TTS | Kokoro service expected at `http://127.0.0.1:8880` | `backend/tts.py:42` |
| Adjacent OpenJarvis frontend | React 19.3, Vite 8.3.4, TypeScript ~7.0.2, Tailwind 4.3.3 | `OpenJarvis/frontend/package.json` |
| Adjacent OpenJarvis backend | Python 3.12 (`.venv`), `uv` runner | `OpenJarvis/.venv`, `run-jarvis.sh` |
| Node / npm installed | Node **v24.21.0**, npm **12.2.0** | `node -v`, `npm -v` |
| Git | 2.39.2 (Apple Git-143) | `git --version` |
| Chrome | `/Applications/Google Chrome.app` present | `ls -d` |
| Claude Code | `/Users/macbookpro/.local/bin/claude`, 2.1.329 | `which`, `claude --version` |

Tool check result: **Node 20+ PASS, Git PASS, Chrome PASS, Claude Code PASS. Nothing missing.**

## 2. Folder structure (top level of the working repo)

```
DENNIS AGENTIC OS_Trading Den/            <- git repo, branch main -> upgrade-cosmos
├── AUDIT.md                              <- this file (new)
├── .gitignore                            <- extended: .env, node_modules/, .jarvis/
├── Dennis-Agentic-OS/                    <- RUNNING JARVIS app
│   ├── app.py                            <- HTTP server + API routes (271 KB)
│   ├── index.html                        <- single-page shell + sidebar (305 KB)
│   ├── js/                               <- 36 frontend modules
│   │   ├── app.js                        <- agent bridge, view switching, boot
│   │   ├── galaxy.js                     <- HERMES APOLLO voice core + galaxy
│   │   ├── jarvisDashboard.js            <- JARVIS home dashboard
│   │   ├── voiceRegistry.js              <- Voice Provider Registry settings card
│   │   ├── tts.js                        <- browser-side TTS client
│   │   ├── cosmos*.js                    <- COSMOS / Genesis views
│   │   └── workspace.js                  <- agent/workspace UI
│   ├── backend/                          <- 110+ Python modules (tts.py, denma.py, …)
│   ├── data/                             <- runtime state, mostly chmod 600
│   │   ├── tts/                          <- tts config.json, secrets.json (0600), logs, audio
│   │   ├── cosmos/                       <- COSMOS state (Phase 2)
│   │   └── *.json                        <- agents, sessions, skills, connections, …
│   ├── trading_den/                      <- embedded Trading Den backend (Phase 2)
│   ├── tts_service/                      <- local Kokoro server + its venv
│   ├── css/, electron/, vault/, docs/, tests/, scripts/, public-plugins/
│   └── package.json                      <- start / desktop scripts only
└── OpenJarvis/                           <- git-ignored upstream clone; Claude Agent SDK
    ├── src/openjarvis/agents/claude_code.py
    ├── src/openjarvis/speech/{kokoro,elevenlabs}_tts.py
    ├── frontend/                         <- React UI (Settings > Speech > Voice)
    ├── .env                              <- chmod 600 (ELEVENLABS_API_KEY)
    └── run-jarvis.sh
```

## 3. Sidebar pages

From `index.html:113-170` (`#os-sidebar`), in order:

1. **JARVIS** (`#sidebar-nav-jarvis`) — home command center (`js/jarvisDashboard.js`)
2. **Workspace** (`#sidebar-nav-workspace`) — default active page, agent/model toolbar
3. **Studio** (`#sidebar-nav-studio`)
4. **COSMOS** (`#sidebar-nav-cosmos`)
5. **Trading Den** (`#sidebar-nav-trading`)
6. **Vault** (`#sidebar-nav-vault`)
7. **Memory Galaxy** (`#sidebar-nav-galaxy`)
8. **Hermes** (`#sidebar-nav-hermes`)
9. **Agent Skill Builder** (`#sidebar-nav-skillbuilder`)
10. **Plugins** (`#sidebar-nav-plugins`)
11. **Settings** (button with no id found in the sidebar block)

Views are switched by `switchMainView()` in `js/app.js`; `DOMContentLoaded` selects
`jarvis` as the landing view (per `docs/JARVIS_V2_AUDIT.md`).

## 4. How the bridge works

- Frontend: `js/app.js:431` defines `window.DennisAgentBridge`. `ask()` POSTs JSON to
  `/api/chat` (line ~449) and includes the **live** Workspace agent + model selection
  via `window.Workspace.voiceContext()` (this was a deliberate 2026-11-12 fix, per
  `docs/JARVIS_V2_AUDIT.md`). History: GET/DELETE `/api/chat/history`.
- Backend: `app.py:4756` handles `POST /api/chat`; it runs the selected agent CLI
  through the local execution router (`backend/execution_router.py`, `backend/runner.py`)
  and streams/returns the result. There is also a direct CLI path at `js/app.js:835`.
- **Claude Agent SDK bridge** lives in the adjacent ignored repo, not in the running
  app: `OpenJarvis/src/openjarvis/agents/claude_code.py` +
  `OpenJarvis/src/openjarvis/agents/claude_code_runner/package.json`.
- Network: server binds `bind_host = "" if DENNIS_DESKTOP != "1"` (`app.py:5896`),
  i.e. **all interfaces**; `lsof` confirms `*:8080`. This is NOT localhost-only.

## 5. How voice works

- Core: `js/galaxy.js` `class HermesGalaxy`, exported as `window.hermesApollo`.
  State machine: `standby | listening | awake | speaking | muted`.
- Mic: `webkitSpeechRecognition` loop with wake words ("Hey Apollo / Apollo /
  Hermes / Jarvis / wake up"); mic is ducked while speaking (`apollo.duck_mic = true`).
  **Always-on wake-word, no push-to-talk** found (`grep -rniE "push.to.talk|ptt" js/*.js` → none).
- Listening indicator: the reactive galaxy + `#jarvis-bar-wake` / `#jarvis-core-state`
  elements reflect state (`js/jarvisDashboard.js:148-188`); there is no explicit
  push-to-talk indicator.
- TTS router: `backend/tts.py` with provider registry `kokoro` (default, local),
  `elevenlabs` (cloud, needs key), `browser` (fallback only). JARVIS preset
  `jarvis-british-male` uses Kokoro `bm_george` → `bm_lewis`; `browser_fallback: true`.
  Endpoint `/api/tts/status` reports only `configured: bool(key)` to the browser —
  no key material (`backend/tts.py:444`, `app.py:1346`).
- UI: "Voice Provider Registry" is **section 6 of the Workspace view**
  (`index.html:787`, `js/voiceRegistry.js`), not a `Settings > Voice` page.
  No Preview button found.
- Local Kokoro service on `127.0.0.1:8880` did **not** answer (`curl` → `000`) at audit time.

## 6. Secrets storage

| Location | Mode | Content (names only, values never read into this doc) |
| --- | --- | --- |
| `OpenJarvis/.env` | 0600 | `ELEVENLABS_API_KEY` (plus commented optional overrides) |
| `~/.openjarvis/credentials.toml` | 0600 | `[elevenlabs] ELEVENLABS_API_KEY`, `[nim] NIM_API_KEY` |
| `Dennis-Agentic-OS/data/tts/secrets.json` | 0600 | provider API keys (backend-side only) |
| `Dennis-Agentic-OS/data/*.json` | mostly 0600 | agents, sessions, connections, skill registry, Composio cache |
| `Dennis-Agentic-OS/data/os_config.json`, `data/providers_config.json`, `data/tts/config.json` | 0644 | config only in the samples checked; **no key values observed**, but mode is looser than 600 |
| macOS Keychain | — | `security find-generic-password -s elevenlabs` → not found |

Browser exposure: `/api/tts/status` sends booleans, not keys. **No secret values
were printed to logs or written to git in this phase.**

## 7. Broken / gaps found (evidence-based)

1. **Not localhost-only** — `*:8080` (see §4). Violates global rule 1.
2. **No push-to-talk** — always-on wake word (§5). Violates Phase 3 requirement.
3. **Voice providers incomplete** — only Kokoro / ElevenLabs / browser; no Fish Audio (§5).
4. **No `Settings > Voice` page and no Preview button** — registry lives in Workspace (§5).
5. **"follower / subscriber / GoHighLevel pipeline / Your skills" cards: NOT FOUND.**
   Raw evidence: case-insensitive grep for `follow|subscrib|gohighlevel|your skill`
   across `Dennis-Agentic-OS/js/*.js`, `index.html`, `OpenJarvis/frontend/src` returned
   only unrelated `pipeline` strings (e.g. `js/cosmosDashboard.js:499`) and "Your skills"
   appearing in `index.html`/`js/skill-builder.js`/`js/studio.js` via Spotlight but not
   as a dashboard card. **Status: unknown — needs your confirmation.**
6. **Local Kokoro TTS endpoint unreachable** at audit time (`curl :8880` → `000`).
7. **Huge log files** — `data/tts/dennis-server.log` 133,519,884 bytes,
   `service.log` 425 KB; risk of accidental secret leakage / repo bloat.
8. **No `npm run boot|stop|doctor` scripts** in `package.json` (only `start`, `desktop`).
9. **`~/.jarvis` does not exist** — no secrets there yet.
10. **Adjacent OpenJarvis is git-ignored** by the parent repo and has its own `.git`;
    changes there will not appear in this repo's `upgrade-cosmos` branch.
11. 401 responses on unauthenticated `/api/status`, `/api/tts/config`, `/api/skills`
    are expected (auth middleware), not necessarily a defect.

## 8. Git phase-1 actions completed

- Repo already existed at the workspace root (`main → origin/full-project`); no `git init` needed.
- `.gitignore` extended with `node_modules/`, `.jarvis/`, `**/.jarvis/`
  (`.env` / `.env.*` and `Dennis-Agentic-OS/.gitignore` rules were already present).
  `~/.jarvis` is outside the repo; the ignore entries cover any in-repo `.jarvis` copies.
- Branch created and checked out: **`upgrade-cosmos`**. Working tree: only `.gitignore` modified.
