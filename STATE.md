# STATE

_Last updated: 2026-11-12_

## Root causes of the reported problems (Phase 1, verified 2026-10-16 before edits)

1. **CLI loads slowly** - nothing warm-starts on agent selection: the previous
   frontend only started a process when a sidebar session was clicked, models for
   Claude launch a second CLI (its `/model` picker), and no container/PTY open or
   first-output timing was measured or logged anywhere. Prebuilt images exist as
   Dockerfiles but are not built by anything.
2. **Switching agents does not switch the CLI** - verified in the old
   `js/workspace.js`: it keeps ONE shared `state.term` / `state.ws` /
   `state.sessionId`. `selectAgent()` reloads models/sessions but never touches the
   terminal, so the old agent's process stays on screen. There is no per-session
   dashboard registry; hiding a dashboard also had no concept (single host).
3. **Prompts cannot be typed** - no `term.focus()` on session show/switch; xterm
   is created only after `/start` resolves; PTY output emitted before mount is
   dropped (no queue); `fitTerm()` can run while the host has zero size; the old
   flow never re-fits on panel resize.
4. **CLI dashboard renders messy** - xterm was loaded with the fit addon only:
   no Unicode 11, no WebGL with canvas/DOM fallback, `termSize()` guessed 17px
   rows instead of letting the fit addon measure, and replay could start
   mid-escape-sequence.
5. **"Docker is not installed"** - `backend/docker_runtime.py` already separates
   CLI-missing from daemon-down and honours `DOCKER_BIN`/`DOCKER_HOST`, but the
   UI never sees it: there is no `/api/system/readiness` handler in `app.py`
   (only `backend/preflight.py`), `DENNIS_SANDBOX=docker` silently falls back to
   the local runner with a `print()`, and start responses never carry an
   actionable backend message.
6. **No handoff auto-expand** - `backend/filewatch.py` exists (bursts + preview
   URL scan) but is imported nowhere; no session owns a `ProjectWatcher` and no
   handoff event reaches the WebSocket. File-tree browsing also has no endpoint
   (only single-file reads).
7. **Panel state model did not match the spec** - the old `panelState.js` used
   S0=collapsed / S1=empty / S2=code / S3=preview / S4=split / S5=maximized /
   S6=fullscreen. The spec requires S0=no agent, S1=collapsed, S2=split,
   S3=code-maximized, S4=preview-maximized, S5/S6=OS fullscreen per pane, with
   exact restore, minimizable strips, X reset and Escape ordering. Nothing
   supported handoff auto-expansion or per-pane minimize.
8. **Technical UI leaked** - the toolbar carried Restart/Stop buttons and the
   panel had no slim handle, no per-pane Minimize/Maximize/Expand controls and no
   X reset, so the required S0-S6 interactions were impossible.

## Smallest safe change set (Phase 1, before editing)

- `backend/session_manager.py` - per-session watcher/preview scanner, handoff
  events, start/first-output timing, live model read-back, start dimensions.
- `app.py` - `/api/system/readiness`, file-tree endpoint, session `current_model`
  + backend message on start, handoff messages over the existing terminal WS.
- `backend/preflight.py` - report the effective session backend and why.
- `js/panelState.js` (rewrite), `js/agentDashboards.js` (new), `js/workspace.js`
  (rewrite), `index.html` (workspace + sidebar), `css/style.css`.
- `tests/panelState.test.js` (rewrite), `tests/agentDashboards.test.js` (new),
  `tests/test_backend.py` (extend). No changes to registries/modals/nav.

## Objective

Rebuild the **Workspace** view of DENNIS Agentic OS into a real agent screen:

- Run the host agent CLIs (OpenCode, Antigravity, Codex CLI, Claude Code) in a PTY,
  streamed to an in-page xterm.js terminal.
- Native **Agent** + **Model** dropdowns, where the model list comes from each CLI
  itself (never the Model Registry, never a fabricated list).
- Project sessions listed in the left sidebar; one session = one agent + one project
  folder, kept alive across UI navigation.
- A collapsible **Code & Preview** panel driven by an **S0-S6** state machine plus
  OS-maximize and browser fullscreen.
- Keep the rest of the OS (hero banner, registries, modals, footer) intact.

## Status: complete and verified end to end (local PTY backend)

Note: this repo has no `AGENTS.md`, `LOOP.md`, architecture spec or separate
step spec; `STATE.md` is the working spec. There is no build step (static
HTML/CSS/JS, no bundler); the verification gate is the test suites plus the
syntax checks below.

## What exists now

### Backend (`backend/`, wired into `app.py`)

| Module | Responsibility |
| --- | --- |
| `backend/config.py` | paths, atomic JSON IO, defaults (model cache TTL 300 s) |
| `backend/auth.py` | single local owner + session token in `data/auth.json` |
| `backend/projects.py` | allowed roots, path validation (realpath), project discovery |
| `backend/session_store.py` | per-owner project-session metadata + panel state |
| `backend/agent_registry.py` | CLI profiles (model/resume/continue flags), augmented env |
| `backend/model_sources/` | per-CLI model listing (`opencode`, `antigravity`, `codex`, `claude`) + TTL cache |
| `backend/sandbox.py` | `PtyProcess` (TERM/COLORTERM/UTF-8 locale), `LocalPtyRunner`, `DockerRunner`, `ExecContext` |
| `backend/session_manager.py` | live `AgentSession` lifecycle, fan-out, status, handoff bursts, timing logs, idle reaper |
| `backend/filewatch.py` | project file-change bursts + localhost preview-URL scanner (real handoff signals) |
| `backend/docker_runtime.py` | Docker CLI/daemon detection, `DOCKER_BIN`/`DOCKER_HOST`, per-agent image helpers |
| `backend/preflight.py` | readiness report: Docker, Node, build tools, agent binaries, images |
| `backend/ws.py` | minimal RFC6455 WebSocket (stdlib only) |

### HTTP / WS endpoints (all require `X-Dennis-Token`/`?token=` except `/api/auth/session`)

```
GET    /api/auth/session
GET    /api/system/readiness                        # Docker/Node/tools/images + actionable message
GET    /api/projects
GET    /api/agents/availability                     # + session_count, backend, backend_note
GET    /api/agents/<id>/models?refresh=&project=&session_id=
                                                    # + current_model, supports_live_model_switch
GET    /api/project-sessions?agent_id=
GET    /api/project-sessions/<id>                   # + live status/current_model/timings/backend
GET    /api/project-sessions/<id>/file?path=REL     # safe text read inside project
GET    /api/project-sessions/<id>/tree?path=REL     # safe folder listing for the Code file tree
GET    /api/ws/terminal?session_id=&token=          # WebSocket
POST   /api/project-sessions                        {agent_id, project, model_id}
POST   /api/project-sessions/<id>/start             {rows, cols, model_id?}
POST   /api/project-sessions/<id>/stop
POST   /api/project-sessions/<id>/model             {model_id, confirm?}
POST   /api/project-sessions/<id>/panel             {panel}
POST   /api/project-sessions/<id>/reset-panel
POST   /api/project-sessions/<id>/cli-session       {cli_session_id}
DELETE /api/project-sessions/<id>
GET    /api/deepseek/status                         # configured/source/default_model
GET    /api/deepseek/conversations                  # owner-scoped history index
GET    /api/deepseek/conversations/<id>
POST   /api/deepseek/credentials                    {api_key}
DELETE /api/deepseek/credentials
POST   /api/deepseek/conversations                  {model_id?}
POST   /api/deepseek/conversations/<id>/messages    {content} -> text/event-stream
DELETE /api/deepseek/conversations/<id>
```

Terminal WS frames: `data`, `replay`, `status`, `exit`, `handoff`
(`{reason:'file_change'|'preview', burst, path|preview_url}`).

### Frontend

- `js/panelState.js` - pure, node-testable **spec** S0-S6 state machine with
  per-pane minimize/maximize, exact fullscreen restore, X reset and Escape order.
- `js/agentDashboards.js` - per-session dashboard registry: one view/socket/process
  per project session, show/hide without dispose, input routed only to the owning
  session, output queued until the terminal is mounted.
- `js/workspace.js` - agent screen controller: token, agents, CLI-native models,
  project sessions, xterm (fit + Unicode11 + WebGL with fallback), handoff
  auto-expand, Code & Preview panel, OS fullscreen overlay, focus trap.
- `index.html` - `#agent-workspace`: toolbar Agent -> Model -> status, one screen
  per session, slim Code & Preview handle, S2-S4 panel, fullscreen bar. Sidebar
  Sessions section (under Settings) filtered to the selected agent. Registry
  panels in `section.registry-scroll-section`; body is `h-screen overflow-hidden`
  so `main` is the single scroll container.
- `css/style.css` - workspace row (`clamp(420px, 100vh-300px, 780px)`), handle,
  pane strips, fullscreen overlay, file tree, disabled dropdown, reduced motion.
- `js/app.js` - unchanged except it no longer calls removed chat/code/terminal
  initializers (the undefined `initSessions()` crash was already fixed).

### Scripts

- `scripts/preflight.py` - CLI readiness report (exit 0/1).
- `scripts/build_agent_images.sh` - builds one prebuilt image per agent CLI.

### Tests (committed)

- `tests/test_backend.py` - 37 stdlib unittest cases (Docker states, filewatch
  bursts, real-PTY typing, session isolation, handoff events, timings, readiness).
- `tests/panelState.test.js` - 23 checks for the spec S0-S6 machine.
- `tests/agentDashboards.test.js` - 8 checks for per-session isolation/routing.
- `tests/e2e_workspace.py` + `tests/ws_client.py` - headless-Chrome CDP E2E
  (65 checks across two phases + optional Docker note checks).

## Panel state mapping (spec)

| State | Meaning |
| --- | --- |
| S0 | no agent selected: panel hidden, handle hidden |
| S1 | collapsed (default once an agent is selected): full-width agent screen + slim right-edge handle |
| S2 | split (expanded): Code and Preview side by side |
| S3 | Code maximized in the panel; Preview is a restorable slim strip |
| S4 | Preview maximized in the panel; Code is a restorable slim strip |
| S5 | Code covers the whole app window (OS fullscreen overlay) |
| S6 | Preview covers the whole app window |

Transitions: S1->S2 handle click or auto-expand on a real handoff; S2->S3/S4
pane Maximize; S3/S4->S2 Minimize or restore strip; S2/S3/S4->S5/S6 Expand to
full screen and exit returns to the exact previous state; any state -> S1
Collapse (content kept); X resets to S1 and clears file/editor/URL/pane state
without touching the session; Escape exits fullscreen first, then panel maximize.
Code and Preview are never both hidden while the panel is expanded.

## CLI facts (source of truth for model listing)

- **opencode**: `opencode models` -> `provider/model` lines; select `-m`.
- **agy**: `agy models` -> `<id>\t<Name>`; select `--model`.
- **codex**: `codex debug models` -> JSON catalog; select `-m`.
- **claude**: **no** non-interactive list; only the interactive `/model` picker.
  We start a short-lived, *separate* PTY in the same project folder, wait for the
  TUI to be ready ("auto mode on"), send `/model`, and parse the rows inside the
  `Select model` section. Parser notes: the renderer moves the cursor by column
  instead of printing spaces, so words collapse (`OpusOpus5.5`,
  `9routerfallbackcombo✔`); we take the leading `[A-Za-z0-9._-]` run, strip the
  glued suffix, and collapse a doubled name prefix -> alias `opus`, name
  `Opus 5.5`. If the folder is untrusted, the user isn't logged in, or no rows
  parse, we raise an actionable error (never a fabricated list). Result: 6 models
  (default, opus, fable, sonnet, haiku, 9routerfallbackcombo) after the folder was
  trusted.

## Files changed in this rebuild

- Backend: `backend/session_manager.py` (per-session watcher, handoff bursts,
  timings, resume-on-restart, current-model read-back), `backend/filewatch.py`
  (ignore paths), `backend/sandbox.py` (UTF-8 locale defaults), `app.py`
  (readiness, tree, session read-back, start dims), `backend/config.py` (TTL).
- Frontend: `js/panelState.js` (rewritten), `js/agentDashboards.js` (new),
  `js/workspace.js` (rewritten), `index.html`, `css/style.css`.
- Tests/scripts: `tests/test_backend.py`, `tests/panelState.test.js`,
  `tests/agentDashboards.test.js`, `tests/e2e_workspace.py`, `tests/ws_client.py`,
  `scripts/preflight.py` (existing), `scripts/build_agent_images.sh` (new).

## Measured timings (local PTY backend)

| Measurement | Result |
| --- | --- |
| Agent process start (`start_ms`, warm host) | 2-3 ms |
| Cold OpenCode TUI first output | 2.5-4.2 s (latest 2.94 s) |
| Warm reconnect to a running session, first frame | 5-8 ms |
| `opencode models` (576 models, uncached) | ~12 s; cached 300 s |
| `agy models` / `codex debug models` / Claude picker | 6 / 11 / 6 models parsed |

## Evidence

- `./.venv/bin/python -m unittest tests.test_backend` -> **37 passed**.
- `node tests/panelState.test.js` -> **23 checks passed**.
- `node tests/agentDashboards.test.js` -> **8 checks passed**.
- `./.venv/bin/python tests/e2e_workspace.py --serve --phase all` -> **65/65
  checks passed** (headless Chrome, real CLIs): no JS exceptions, no tabs, no
  technical text, S0/S1 defaults, per-agent dashboard + focus without a click,
  real file-change handoff auto-expand once per burst, S2->S3->S5 Escape->S3
  Escape->S2, S4 fullscreen restore, X reset without touching the session,
  keystroke routing + CLI echo, single scroll container, two-agent isolation
  (OpenCode + Claude Code side by side, hidden dashboard keeps its live terminal
  and is never restarted, input never crosses sessions), panel resize on
  open/collapse, per-session panel restore, CLI-native model list/current/search,
  model apply + backend read-back, parse-failure Retry, new handoff burst, OS
  features intact, no provider credentials in the page.
- `./.venv/bin/python tests/e2e_workspace.py --phase none --docker-missing-url ...
  --docker-down-url ...` -> **2/2**: the UI shows different, actionable messages
  for "Docker CLI not found" and "daemon is not running" (servers started with
  `DENNIS_SANDBOX=docker`, the second with a fake `DOCKER_BIN`).
- Live PTY/WS probe: real `opencode` TUI streamed over the authenticated
  WebSocket; typed characters reached the process and were echoed; `/api/.../tree`
  listed the project; stop/delete worked.
- `scripts/preflight.py` -> ready, backend local, `docker_cli_missing`.
- Model lists are the CLIs' own: OpenCode **576** (identical to
  `opencode models | wc -l`), Antigravity 6, Codex 11, Claude 6 — nothing hardcoded.

## Trading Den (DenMa) backend - fix 2026-10-17

Root cause of "DenMa unreachable / REST+WS lost / empty data+charts" while
embedded: `trading_den/MMcloneLoopEngineering/templates/index.html` uses
same-origin relative URLs (`fetch('/api/...')`, `WebSocket('.../ws')`). The
Trading Den iframe served that page from the **OS** origin (`app.py`), which
has none of DenMa's routes - `/api/history/spread-candles`, `/api/live-snapshot`
and `/ws` all returned 404 while `/api/status` hit the OS's own endpoint.

Fix: the dashboard runs on its own origin instead of being embedded.

- `backend/denma.py` (new) - supervisor: finds a Python with `ccxt`/`aiohttp`/
  `aiodns` (`DENMA_PYTHON`, else `trading_den/.../.venv`, else
  `~/PycharmProjects/MarketMaking/.venv`, else `sys.executable`), spawns
  `app.py --host 127.0.0.1 --port 8000`, health-checks, restarts, stops on exit.
- `app.py` - auto-starts the backend on launch (`DENNIS_NO_DENMA=1` disables),
  `atexit` cleanup, new endpoints `GET /api/trading-den` and
  `POST /api/trading-den/restart|stop`, and a **same-origin reverse proxy**:
  DenMa's REST paths (`/api/markets|live-snapshot|screener|triangular|mt5-pricing`
  + `/api/history|arbitrage|bots/*`) and `/ws` are forwarded to the private
  loopback backend (`http.client` for HTTP; a raw socket byte-relay for the WS
  upgrade). `/api/status` routes to DenMa only when the Referer is the dashboard
  page, so the OS keeps its own `/api/status`. The browser therefore only ever
  talks to the OS port - one server, one origin.
- `index.html` - the `#td-iframe` loads the dashboard from the OS origin
  (`/trading_den/MMcloneLoopEngineering/templates/index.html`), so its relative
  `/api/*` + `/ws` resolve through the proxy. The tab auto-loads it once the
  backend is up; a status overlay (spinner/error + "Open in New Tab" + "Restart
  Backend") covers it while starting/stopped.
- `js/app.js` - polls `/api/trading-den` (5 s), sets `iframe.src` to the
  same-origin dashboard path when running, drives the overlay, wires restart.
- `trading_den/.../arbitrage/server.py` - fixed a DenMa bug that made
  `GET /api/markets` close the connection with no reply:
  `NormalizedMarketState` has no `is_stale()`; the catalog overlay now checks
  `norm_state.freshness == FreshnessStatus.STALE.value` (the convention used in
  `direction_engine.py`).
- `tests/e2e_workspace.py` - starts `app.py` with `DENNIS_NO_DENMA=1`.

Config: `DENMA_HOST`/`DENMA_PORT` (default `127.0.0.1:8000`), `DENMA_PYTHON`.
Verified: OS auto-started the backend in ~6 s; through the OS port `/api/status`,
`/api/markets`, `/api/live-snapshot`, `/api/history/spread-candles`,
`/api/history/pnl`, `/api/screener`, `/api/triangular` all 200 and `/ws`
handshakes 101 (stable over time). `tests/test_backend.py` 38 passed, JS 23 + 8
passed.

## Trading Den runs in-process: one server, one port - 2026-10-18

The OS and the embedded Trading Den are now a **single process on a single
user-facing port**. Previously DenMa ran as a separate `app.py` child on
`127.0.0.1:8000` (loopback) with hardcoded `http://127.0.0.1:8000/` links in the
UI; those links could send the browser to a second server (and "connection
refused" when it was not up / not reachable).

- `backend/denma.py` - the DenMa `ArbitrageServer` is now started **in-process**
  on a private, **ephemeral** loopback port from a background asyncio thread
  (`_InProcessEngine`); `app.py` proxies `/api/*` + `/ws` to it as before.
  The child-process launch is kept as an automatic fallback when the running
  interpreter lacks `ccxt`/`aiohttp`/`aiodns`. `status()` now reports `mode`
  (`inproc` | `subprocess`).
- `app.py` - `_ensure_denma_runtime()` re-executes the OS under an
  interpreter that has the DenMa deps (found via `DENMA_PYTHON`, the project
  `.venv`, `~/PycharmProjects/MarketMaking/.venv`, ...) so the engine can be
  imported in-process. Guarded by `DENNIS_DENMA_REEXEC` (no loop) and skipped
  when `DENNIS_NO_DENMA=1`. Note: the venv `bin/python` is a symlink to the base
  interpreter, so the re-exec guard compares `os.path.abspath` (not `realpath`).
- `index.html` - the "Fullscreen Live UI" / "Open in New Tab" links and the
  backend URL label now point at the same-origin dashboard path
  (`/trading_den/MMcloneLoopEngineering/templates/index.html`) instead of
  `http://127.0.0.1:8000/`; overlay copy updated.

Verified (headless Chrome against `http://localhost:8080/`): exactly one OS
process; one public listener (`*:8080`) plus one private ephemeral loopback port;
embedded dashboard `td-iframe` loads same-origin, `__DENMA_BACKEND_CONNECTED ===
true`, `/ws` handshake **101**, and `/api/live-snapshot`, `/api/markets`,
`/api/screener`, `/api/history/spread-candles` all **200** through the OS port.
`tests/test_backend.py` 44 passed; JS 23 + 8 passed. Subprocess fallback
re-verified by forcing `DENNIS_DENMA_REEXEC=1` (reports `mode: subprocess`).

## Vault: Obsidian-compatible knowledge base - 2026-10-18

The left-nav **Vault** item and the *Obsidian* / *Graphify* quick tools were
stubs (no handler / toast only). They are now a working, self-contained
Obsidian-compatible notes app on the single OS origin.

- `backend/vault.py` (new) - plain-Markdown vault under `vault/`
  (`DENNIS_VAULT_DIR` overrides). `ensure_vault` seeds `Welcome.md`/`Ideas.md`/
  `Projects.md` on first run. API: `list_tree`, `read_note`, `write_note`,
  `create_note`, `rename_note`, `delete_note` (soft -> `.trash/`), `search`,
  `graph`, `backlinks`, plus `[[wikilink]]`/Markdown-link parsing
  (`extract_links`). Atomic writes; every path is realpath-confined to the
  vault. **Fix:** walk/relativise with a single resolved root (`_root()`) so a
  symlinked vault path (e.g. macOS `/var` -> `/private/var`) does not yield
  `../..` paths.
- `app.py` - `/api/vault/notes|note|search|graph|backlinks` (GET) and
  `/api/vault/note|rename|delete` (POST), all owner-token gated like the rest of
  the API.
- `index.html` - new `#vault-view`: search + tree | CodeMirror editor / live
  preview / graph canvas | backlinks. `css/style.css` gains vault + Markdown
  typography; `js/vault.js` (new) implements the tree, editor, preview renderer
  (headings, lists, quotes, code, `[[wikilinks]]` with create-on-click), search,
  backlinks and a force-directed, pan/zoom/click graph. `js/app.js`
  `switchMainView` now handles `vault`; sidebar routes Vault, quick tools call
  `Vault.open()` / `Vault.openGraph()`, and `galaxy.js` voice routes "vault/
  obsidian/note" to it.
- `.gitignore` - `vault/.trash/` ignored.

Verified: backend round-trip (create/write/rename/soft-delete, `../` escape
rejected) and headless-Chrome UI (tree = 3 notes, open note -> title/editor/
backlinks, preview renders H1 + wikilinks, graph visible). `tests/test_backend.py`
50 passed (6 new `VaultTests`); JS 23 + 8 passed.

## Hermes orchestrator + Kanban board - 2026-10-18

The OS had **no real sub-agent orchestration**: "Subagents" was a UI badge,
"Hermes" was only the frontend Apollo voice core, and work was dispatched one
prompt at a time to the single active CLI (`/api/chat`). Hermes is now a real,
first-class orchestrator with a Kanban board and the Vault as its second brain.

- `backend/hermes.py` (new) - `HermesOrchestrator` runs the pipeline
  `goal -> plan -> route -> delegate -> aggregate -> remember`. State lives in
  `data/hermes.json`. `decompose()` is a deterministic five-role pipeline
  (research, plan, implement, verify, document). `route()` assigns the
  least-loaded available sub-agent and keeps *verify* off the implementer.
  `delegate()` fans tasks out as streamed subprocesses, bounded by a
  concurrency limit (`DENNIS_HERMES_CONCURRENCY`, default 3); `SubprocessRunner`
  streams stdout and reports completion. Planner and runner are injectable for
  testing; planning brain is `vault` (default) or `cli` (sub-agent print mode).
  Plans/runs/memories are written to `vault/Hermes/{Plans,Runs}` and
  `vault/Hermes/*.md`.
- `app.py` - `GET /api/hermes/state|tasks|runs|agents|memory`, `POST
  /api/hermes/plan|run|delegate|tasks` and `.../tasks/<id>/assign|status|cancel`,
  `DELETE /api/hermes/tasks/<id>` and an authenticated per-task stream at
  `/api/hermes/ws?task=<id>`. `/api/status` now includes the `orchestrator`
  identity.
- Frontend - the Kanban board is embedded **full-width** in the OS
  `#hermes-view` (sidebar **Hermes** entry or Apollo voice). `js/kanbanState.js`
  (pure, node-tested board logic) + `js/kanban.js` (renderer + API client + live
  log, drag-and-drop, manual/Autonomous assignment) + `js/hermes.js` (the panel
  with goal composer, sub-agent strip and board). `js/app.js` `switchMainView`
  handles `hermes`; `js/galaxy.js` voice routes hermes/orchestrate/delegate and
  kanban/board -> the panel. A separate `/kanban` landing page was dropped in
  favour of the embedded board.

Verified: `test_backend.py` 64 passed (14 new `HermesTests`); `kanbanState.test.js`
9 passed; panelState 23 + agentDashboards 8. Live: plan spreads work across
sub-agents (verify avoids the implementer), task CRUD + board, `/api/hermes/ws`
streams the initial status frame, and headless Chrome confirms the full-width
board renders with no clipping (1024-1440px) with 3 sub-agents.

## Code & Preview IDE (built-in editor + Run) - 2026-10-17

The Workspace Code & Preview pane is now a lightweight, self-contained IDE: the
Code side is an editable CodeMirror buffer with Save, and a Run control starts
the open project's server and auto-loads it in Preview.

- `vendor/codemirror/` - CodeMirror 5.65.16 vendored locally (lib + modes
  python/javascript/xml/css/htmlmixed/markdown/shell/yaml + edit/matchbrackets,
  edit/closebrackets, selection/active-line). No CDN at runtime.
- `backend/runner.py` (new) - stdlib-only generic project runner.
  `detect_command()` picks `app.py` > `main.py` > `manage.py runserver` >
  `package.json` (`dev`/`start`) > a static `index.html` (served via the raw
  route, no process). `ProjectRun` supervises the child (own session), keeps a
  400-line output ring buffer, scrapes the first `http(s)://localhost|127.0.0.1|
  0.0.0.0:PORT` from its output as the preview URL, and reports
  `running/command/url/pid/uptime/error/output`.
- `app.py` - endpoints `GET /api/project-sessions/<id>/run` (status),
  `POST .../run/start`, `POST .../run/stop`, `POST .../file` (atomic save; 2 MB
  cap; realpath-confined; owner token) and `GET .../raw?path=` (raw bytes with a
  guessed MIME type, for static previews). For the DenMa project
  (`trading_den/MMcloneLoopEngineering`) the run endpoints reuse the existing
  `dennis_denma.BACKEND` supervisor and return the dashboard page as the URL, so
  one Run button covers both a normal project server and the embedded dashboard.
- `index.html` / `css/style.css` - Code pane: CodeMirror host, Save (dirty dot),
  Run/Stop, a run status pill and a collapsible run-log; a small dark editor
  theme.
- `js/workspace.js` - lazy CodeMirror mount (`modeForPath` by extension), dirty
  tracking + Save (Ctrl/Cmd+S), Run/Stop + status polling, and auto-preview of
  the running URL (the DenMa page path or the project's static raw route, with
  the owner token appended for same-origin `/api/` URLs). Editor scroll/cursor is
  persisted with the panel; `resetPanel` clears the editor and run state.

Verified live (spare port, `DENNIS_NO_DENMA=1`): session create, file save +
read-back, static project run → raw preview 200, `main.py` project run → URL
`...:8123` scraped, stop; path escape rejected; headless Chrome mounts the editor,
typing marks dirty. `tests/test_backend.py` 38, `panelState` 23,
`agentDashboards` 8 passed.

Known limitation: a multi-file static site renders its entry document but its
relative assets resolve under the raw route without the owner token (the DenMa
dashboard and dev-server URLs are unaffected).

## MCP Connector Hub (replaces the Connections panel) - 2026-10-17

The static **Connections** card (Google/GitHub/OpenAI/NVIDIA/Hugging Face)
in the registry grid is replaced by a live **MCP Connectors** panel plus an
**MCP Connector Hub** modal that covers discovery, custom connectors, the OS
policy gate and the OAuth 2.1 authorization flow.

- `backend/mcp.py` (new) - MCP subsystem, stdlib only:
  - **Discovery** - `discover()` searches the official MCP Registry REST API
    (`GET {registry}/v0.1/servers?search=&limit=`), simplifies the
    `app.py`/`_meta` payload (remotes -> http transport, packages ->
    stdio), and falls back to a small curated offline catalog when the
    registry is unreachable. `DENNIS_MCP_REGISTRY` / `DENNIS_MCP_TIMEOUT`
    override the base URL and timeout.
  - **Policy gate** - every connector stores a policy
    (`read-only`/`restricted`/`trusted` presets: scopes, tool allowlist,
    allow_write, allow_network, require_approval, risk tier, data class) and
    must have `accepted=true` before it can be used.
  - **OAuth 2.1** - `begin_oauth()` fetches Protected Resource Metadata
    (RFC 9728) to find the authorization server, then Authorization Server
    Metadata (RFC 8414), then registers a client by the spec priority
    (pre-registered id > Dynamic Client Registration RFC 7591), builds an
    authorization URL with PKCE (S256) and a `resource` indicator
    (RFC 8707). `complete_oauth()` exchanges the pasted code/redirect URL at
    the token endpoint; `refresh_token()` renews it. stdio/API-key
    connectors connect directly through the policy gate.
  - State lives in `data/mcp_servers.json` (chmod 0600). The browser view
    strips tokens: only `has_token`, a masked preview and the expiry cross
    the wire.
- `app.py` - endpoints (all owner-authenticated): `GET /api/mcp/servers`,
  `GET /api/mcp/discover`, `GET /api/mcp/oauth/callback` (loopback redirect
  page that `postMessage`s the code back), `POST /api/mcp/servers`,
  `POST /api/mcp/servers/<id>/{policy,client-id,authorize,callback,connect,refresh}`,
  `DELETE /api/mcp/servers/<id>`.
- `index.html` - the Connections card becomes the MCP Connectors card
  (registry search box, live connector list, Discover/Custom buttons); a new
  `#mcp-modal` holds the hub (Search Registry / Custom Connector / Connected)
  and the 3-step workflow (Policy Review -> Authorization -> Connect).
- `js/mcp.js` (new) - hub controller: token handling, registry search,
  add-from-registry, custom connector form, policy form, OAuth authorize +
  code exchange, direct connect, refresh and remove. `js/app.js` drops the
  old `btn-manage-connections` modal trigger.

New-session naming: the sidebar **+** (Sessions) now always opens a small
"New project session" popover with an optional **project name** field and
the allowed folder(s). Naming it loads the session with that title; leaving
it blank lets the agent name it (the backend defaults the title to
`<Agent> · <project>`). The sidebar row shows `title` (falling back to
`project`). `POST /api/project-sessions` accepts an optional `title`.

Verified live against the real registry (source `registry`) and against
Notion's MCP endpoint (PRM/ASM discovery + RFC 7591 dynamic registration
produced a valid PKCE authorization URL). `tests/test_backend.py` 44 passed
(6 new `McpConnectorTests`), `panelState` 23, `agentDashboards` 8.

## Agent Skill Builder - 2026-10-20

New sidebar view **Agent Skill Builder** (`#skill-builder-view`, sidebar
`#sidebar-nav-skillbuilder`) that turns YouTube videos into reusable Claude Code
skills, following the "ears → eyes → expert agents" pipeline.

- **Backend** `backend/skill_builder.py` (wired into `app.py`): wraps the
  standalone `agent-skill-builder/` workspace. Reads its `.env` for the Gemini
  key + model chain; runs the ears (`yt-transcript.sh`, `yt-metadata.sh`) and
  eyes (`gemini-video-analyze.sh`) scripts, then synthesises a `SKILL.md` with
  Gemini (primary→fallback model chain, `thinkingLevel` from `GEMINI_THINKING`)
  and installs it into `~/.claude/skills`.
- **Endpoints** (all `X-Dennis-Token`-gated):
  `GET /api/skill-builder/status`, `GET /api/skill-builder/skills`,
  `GET /api/skill-builder/skill?name=`, `POST /api/skill-builder/analyze`,
  `POST /api/skill-builder/build`, `POST /api/skill-builder/install`.
- **Frontend** `js/skill-builder.js` (+ `css/style.css` `sb-*` rules): URL input,
  Analyze/Build actions, Transcript/Visual/Skill/Log panes, a generated-skills
  rail with View/Install, and a live configuration panel. `switchMainView` and
  `initSidebar` in `js/app.js` route the new view.
- Verified live: status/skills/401 gate, `analyze` (real transcript) and `build`
  (real `SKILL.md` via Gemini, model fell through to `gemini-3.5-flash-lite`
  while `3.8/3.7/3.6` were overloaded) both succeeded. 60 Python tests run, the
  only error being the pre-existing `test_server_security` `No module named
  'server'`; `agentDashboards` 8, `kanbanState` 9, `panelState` 23 passed.
- **Author agent + model prompts**: the view now has Agent / Model dropdowns
  (mirroring Studio; populated from `/api/agents/availability` and
  `/api/agents/<id>/models`). The selected agent authors the `SKILL.md` in
  non-interactive print mode via `agent_registry.one_shot_argv` with the
  augmented PATH, so the agent chooses the skill **name and description**
  (`_auto_name()` is only a fallback when no frontmatter is returned). Manual
  name/description inputs were removed.
- **Graceful authoring fallback**: if the agent CLI fails (e.g. Antigravity
  returned 429 "Individual quota reached" during verification) the build falls
  back to the Gemini chain and returns `agent_error`, which the Log pane shows.
- **Setup action**: `POST /api/skill-builder/setup` runs the workspace
  `install.sh` (deps, ffmpeg fetch, skill links); the header Setup button calls
  it. Status errors are surfaced verbatim (404 → "restart the server") instead
  of defaulting to "workspace not installed".
- **Parallel agent dashboards** (`js/workspace.js`): a per-agent open-session map
  (`dennis.workspace.open`) keeps each agent's pane alive; switching agents
  re-attaches that agent's own session (or starts/stops only that agent), and
  `loadModels` no longer relaunches a CLI when switching back. `node --check`
  clean; live multi-agent switching not yet exercised in a browser.
- **Robust `yt-dlp` resolution** (fixed "yt-dlp is missing"): `yt-dlp` is a
  `pip --user` console script (`~/Library/Python/3.14/bin/yt-dlp`) that is not on
  a GUI-launched `PATH`. `_env.sh`/`check-deps.sh` now probe PATH → module →
  user-site script and the app exports `PYTHON_BIN` (its own interpreter);
  `agent_registry.get_augmented_env()` and a new `skill_builder._child_env()`
  add the user-scripts dir, and all spawned scripts/`_run` use it. Verified with
  a stripped `PATH=/usr/bin:/bin`: status reports `yt_dlp: true`, check-deps
  passes, `yt-metadata.sh` returns real data and `/api/skill-builder/analyze`
  returns a 2066-char transcript.
- **"Argument list too long" fixed**: `yt-metadata.sh` used to pass the whole
  `yt-dlp -J` dump as an argv (a test video alone was 665 KB; macOS `ARG_MAX` is
  ~1 MB), causing `E2BIG`. It now writes the dump to a temp file and passes the
  path. `gemini-video-analyze.sh` passes URL/prompt/thinking via env instead of
  argv, and agent authoring caps the prompt at `MAX_AGENT_ARG` (150 KB).
- **Agent dropdown selection** verified in headless Chrome via a temporary
  self-test: 3 agents render, clicking `opencode` updated the label to
  "Agent: OpenCode", and the menu was the topmost element at its coordinates.
  The composer originally got `relative z-20`, but that utility was missing
  from the compiled Tailwind bundle and the menu later fell behind the output
  panel — fixed via `.sb-composer` (see the latest bullet). No selection bug
  reproduced in a clean load — if it persists it is a stale client/server.
- **Installed-agent discovery in every dropdown** (2026-10-20 latest): the
  registry only held `antigravity`, `codex-cli` and `opencode`, so an installed
  CLI such as **Claude Code** never appeared. `backend/agent_registry.py` now
  owns one `HOST_AGENT_PROBES` table (`agy`, `claude`, `opencode`, `codex`,
  `gemini`, `aider`, `goose`, `hermes`) and `available_agents()` merges
  registered agents with every probe whose binary resolves. Detected entries
  carry `registered: false` / `detected: true`; `/api/agents/detect` uses the
  same table (no duplicated probe lists). `get_agent()` falls back to a
  detected CLI, so selecting Claude Code / Hermes Agent starts that agent's
  **own** CLI session while other agents keep theirs. Dedupe is by id and binary (registered `codex-cli` hides the `codex`
  probe). Availability now only requires a launchable binary — missing model
  listing falls back to the CLI default — and the binary scan is cached 15 s
  with `--version` probed only by `/api/agents/detect`, so the dropdown opens
  in ~8 ms instead of 5.5 s. `js/skill-builder.js` re-probes on view switch
  without resetting the chosen agent/model. Live check: availability returns
  5 agents including Claude Code and Hermes Agent.
- **One-click integration of detected CLIs** (2026-10-20 latest):
  `POST /api/agents/integrate` (`{agent_id}` or `{all: true}`) persists a
  detected CLI into `data/agents.json` through `integrate_detected_agents()`.
  It is idempotent, never overwrites existing entries and dedupes by id and
  binary (so `codex` does not duplicate `codex-cli`); an unknown/uninstalled
  id returns 404. Detected rows in the Skill Builder dropdown are amber and
  labelled `detected · setup`; selecting one switches to that agent and
  registers it in the same click. Claude Code is integrated on this host, so
  every dropdown shows it as a normal registered CLI.
  `tests/test_agent_integration.py` adds 4 tests (add, alias dedupe, idempotent,
  missing).
- **Unsupported agents removed + popover stacking fixed** (2026-10-20
  latest): Junie, Qoder and Devin had no CLI-native model source and were
  dropped from `HOST_AGENT_PROBES`, `CLI_PROFILES`, one-shot argv dispatch,
  the Skill Builder author table and `data/agents.json`; only supported CLIs
  remain (`antigravity`, `codex-cli`, `opencode`, `claude-code`,
  `hermes-cli`). The dropdown overlap was a stacking bug: the composer used
  Tailwind `z-20`, which was **not present in the compiled
  `css/tailwind.css`**, so the output pane's `backdrop-filter` stacking context
  painted over the open menu. The composer now uses `.sb-composer`
  (`position:relative; z-index:30`) defined in `css/style.css`; verified in
  headless Chrome that every menu row hit-tests above the pane. CSS cache token
  bumped to `20261020-plugins3`.
- **Hermes Agent CLI integrated** (2026-10-20): the installed `hermes` CLI
  (v0.22.1) is a first-class agent under the id **`hermes-cli`**; the bare
  `hermes` id stays reserved for the in-OS orchestrator so sub-agent routing
  does not filter it out. Added to `HOST_AGENT_PROBES` and `CLI_PROFILES`
  (`-m` / `--resume` / `--continue`); one-shot delegation uses
  `hermes [-m MODEL] -z PROMPT`. `backend/model_sources/hermes.py` reads
  `~/.hermes/config.yaml` + `provider_models_cache.json` (no TUI scraping, no
  YAML dependency) and raises an actionable "run `hermes model --refresh`"
  error when the cache is missing. Live at the time: availability returned 7
  agents (now 5 after the unsupported-agent cleanup) and
  `/api/agents/hermes-cli/models` returned the 30 cached `ollama-cloud` models
  with `deepseek-v4-flash` marked default (that revision later retired; the
  latest setup entry below changes the default to `gemma4:31b`). The universal
  chat handler now
  resolves detected CLIs through `get_agent()` instead of falling back to
  Antigravity, and no longer injects the fabricated `gemini-2.5-flash` model
  when the static model registry is empty (the CLI default is used instead).
  `tests.test_backend` 69 passed.
- **Searchable model dropdowns** (2026-10-20): the Workspace, Studio and Skill
  Builder model menus now filter by model name, id or description as you type.
  The Workspace box is always visible once models load (it previously appeared
  only above 12 models); Studio and Skill Builder gained the same box. The menu
  stays open while typing, the query resets after a selection or agent switch,
  and the Workspace menu focuses the search field when it opens.

## Limitations / unverified

- **Docker runtime unverified live**: Docker is not installed on this machine, so
  `DockerRunner` / `build_docker_argv` are covered by unit tests only. Detection,
  readiness and both UI messages are unit- and browser-verified with fake CLIs.
- `DENNIS_SANDBOX=docker` does not hard-fail when Docker is unavailable: it falls
  back to the local runner and surfaces the actionable readiness message instead.
- **Claude model listing** scrapes the interactive `/model` picker in a separate
  short-lived PTY (the folder is trusted via `~/.claude.json`). It is inherently
  brittle across Claude Code versions; parse failures raise a retryable error
  rather than a fabricated list.
- `current_model` is what the backend actually launched the CLI with (read back
  from the live session). A session started with no model flag reports `null`
  until a model is applied.
- None of the four CLIs expose a non-interactive live model switch. Applying a
   model to a running session relaunches the CLI with `-m`/`--model` **without**
   the resume flags (`-c`/`-s`/`--continue`), i.e. it starts a fresh conversation.
   This is required: OpenCode (verified live against its session DB) restores the
   session's recorded model on resume and ignores the model flag, so resuming
   would silently keep the old model (upstream issues #45204/#23369). A restart
   that does not change the model still resumes the conversation. The model
   dropdown stays usable while the agent is Busy (the CLI status flips often,
   e.g. OpenCode redraws its TUI); picking a model then goes through the backend
   409 confirmation, which warns that the session restarts. The control is only
   disabled with no agent selected or while the session starts. Clicks inside an
   open menu (search box, refresh) no longer dismiss it.
- Browser Fullscreen API is optional (toggle in the fullscreen bar, persisted in
  `localStorage`); the CSS overlay is always used and handles denial.
- The E2E harness needs Apple Google Chrome at the default path (override with
  `--chrome`); it is not part of the unit test run.

## Plugins - 2026-10-20

> **Superseded 2026-10-23:** the discovery/catalog UI was replaced by the API
> Mega List console (next section). The install/link backend below still powers
> the console's "Install skill" action.

Agent-agnostic plugin manager (new sidebar tab **Plugins**, `#plugins-view`).
A plugin is a portable folder: optional `plugin.json` + one or more
`skills/<name>/SKILL.md` (the open Agent Skills standard), optional `agents/`,
`mcp/`, `hooks/`, `README.md`. The same pack can be linked into any agent's
skill directory - no Claude lock-in.

- **Backend** `backend/plugins.py` (stdlib only):
  `list_plugins`, `read_plugin`, `install_plugin` (local folder or `git clone`),
  `set_enabled`, `install_skills` (symlink per target agent), `uninstall_plugin`
  (removes its links), `convert_to_claude` (adds `.claude-plugin/plugin.json`),
  `scaffold`. Plugins live in `DENNIS_PLUGINS_DIR` (default
  `~/.agents/plugins`); enable/agent state persists in `data/plugins_state.json`
  (git-ignored). Names are validated (`^[a-z0-9][a-z0-9._-]{0,63}$`) and manifest
  skill paths must stay inside the plugin folder.
- **Target agents** (`AGENT_SKILL_DIRS`): generic `~/.agents/skills`, Claude Code
  `~/.claude/skills`, Cursor, Codex, Gemini CLI, OpenCode, Hermes.
- **Endpoints** (token-gated): `GET /api/plugins`, `/api/plugins/status`,
  `/api/plugins/plugin?name=`; `POST /api/plugins/install|toggle|install-skills|uninstall|convert|scaffold`.
- **Frontend** `js/plugins.js` + `#plugins-view`: installed list with on/off,
  per-plugin detail (skills and their linked agents, MCP entries, README),
  target-agent checkboxes, Install skills / Add Claude metadata / Uninstall,
  install-from-folder-or-git form and a New skeleton action. `js/app.js` routes
  the `plugins` view and sidebar label.
- **Discovery pipeline (2026-10-20, second pass)** mirrors ChatGPT's Plugins
  structure: **Plugins | Skills** sub-tabs, search, **Public | Personal** filter,
  **Popular** / **New & Noteworthy** catalog sections, Add (+), Installed list,
  and a detail pane. Catalog is bundled and real: `youtube`, `yt-visual-analysis`,
  `yt2skill` (sources under `agent-skill-builder/`), installed with
  `POST /api/plugins/install {catalog_id}`. New `GET /api/plugins/catalog` and
  `GET /api/plugins/skills` (scans every agent skill dir, dedupes by name).
  `_plugin_dirs` now accepts a bare top-level `SKILL.md` so catalog installs show
  up in the installed list.
- **Why it "did not work":** the long-running server on :8080 predated the
  `/api/plugins*` routes, so the new HTML/JS loaded but every API call 404'd.
  Restart the server after backend changes; a stale process is the #1 cause here.
- Verified: 7 unit tests (`tests/test_plugins.py`) pass; live catalog install →
  list → link into `~/.agents/skills` → toggle → uninstall cleaned up; headless
  Chrome (CDP) confirms catalog renders 3 cards in Popular/New, skills tab lists
  linked skills, no runtime errors. Full suite: 67 tests, only the pre-existing
  `test_server_security` error.
- Also in this pass: **Analyze timeout raised** (`DENNIS_SB_TRANSCRIPT_TIMEOUT`
  default 600s, `DENNIS_SB_METADATA_TIMEOUT` 120s, `DENNIS_SB_VISUAL_TIMEOUT`
  600s), `_run` now kills the whole process group on timeout, and yt-dlp calls
  use `--socket-timeout 20 --retries 3 --fragment-retries 3`.

## Plugins = real API Mega List repository workspace - 2026-10-23

The custom API search/run console was removed. The Plugins tab now displays the
actual upstream repository and its generated README pipeline.

- **Snapshot:** `public-plugins/api-mega-list/repo/` is the upstream repo at
  commit `b7c914f5abd7d0f7562bd1f2036411d3c8f7c29e`. `SNAPSHOT.json` records the
  source, branch, commit, sync time, file count and byte size. No second index,
  actor catalog, token store, or Apify runner exists.
- **Workspace:** repository tree (main README, 23 core categories, featured,
  sponsored partners, pipeline scripts) + document viewer. Markdown is rendered
  with vendored `marked` 15.0.12; inline HTML is sanitised, relative links stay
  inside the workspace, and relative images are served by
  `/api/megalist/asset`. Large READMEs render a preview with a
  **Render full file** action.
- **Pipeline:** `settings/fetch_apify_actors.js` →
  `settings/generate_readme_clean.js` → `settings/validate_partner_collections.js`
  are visible as repository files. The tab reads their generated READMEs rather
  than reimplementing the pipeline.
- **Backend `backend/megalist.py`** (stdlib only): `status()`, `tree()`,
  `read(path)`, `asset(path)`, `sync()`. Paths are resolved inside the snapshot;
  traversal, absolute paths, non-text reads, unsupported assets and oversized
  files are rejected. Sync downloads the GitHub tarball, rejects links/special
  files and traversal members, validates the upstream layout, and atomically
  replaces `repo/`.
- **Endpoints** (all owner-token gated):
  `GET /api/megalist/status|tree|file?path=…|asset?path=…` and
  `POST /api/megalist/sync`.
- **Catalog:** `_CATALOG` is empty; the first-party `api-mega-list` plugin and
  its agent skill links were removed. Previous personal packs remain in
  `~/.agents/plugins_backup_2026-10-23/`.
- **Frontend:** `js/plugins.js` cache version `20261023-repo1`, `style.css`
  `20261023-megalist2`; `vendor/marked.min.js` added with its MIT license.
- **Terminal sync:** `python3 scripts/sync_api_mega_list.py`.
- **Restart caveat:** the long-running :8080 server predates these edits.
  Restart it when no agent session is active, then refresh Plugins.

- **Hermes Agent first-class setup (2026-10-20 latest):** the installed Hermes
  CLI was already detected as `hermes-cli`, but three integration holes made it
  look unsupported. Fixed:
  1. The Agent Discovery wizard changed the id to a slug of the display name
     ("Hermes Agent" → `hermes-agent`), losing the profile/model source/one-shot
     mapping keyed to `hermes-cli`. `js/app.js` now keeps the catalog/probe id
     for known agents and only slugifies custom agents.
  2. `get_agent_catalog()` had no Hermes entry, so the Catalog/installer could
     not see it. Added `hermes-cli` with the official
     `curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash`
     command and a 600 s `install_timeout` (the installer provisions
     Python/Node/tools).
  3. The Skill Builder fallback table had no `hermes-cli` and fell through to
     `-p`; added `AGENT_BINARIES["hermes-cli"]` and a `-z` branch in
     `_agent_argv`.
  Also: `/api/terminal/start` and `/api/agents/launch` now fall back to
  `agent_registry.get_agent()` so detected-but-unregistered CLIs are launchable,
  and the JS fallback agent lists include Hermes. Verified live: catalog entry is
  `is_installed=true`, availability lists 5 agents, `/api/agents/hermes-cli/models`
  returns 30 models, and a headless probe of the wizard emits id `hermes-cli`
  (custom agents still slug from the name). No Hermes installation was needed:
  `hermes doctor` is clean and Nous Portal auth is present.
  The existing Hermes default (`deepseek-v4-flash`) resolved to the retired
  `deepseek-v4-flash:0731` revision and failed with HTTP 410. Refreshed the
  provider cache through Hermes's own model source and set the default to
  `gemma4:31b`; a real one-shot (`hermes -z ...`) now returns `HERMES_OK`.
  Backups: `~/.hermes/config.yaml.dennis-backup-*` and
  `~/.hermes/provider_models_cache.json.dennis-backup-*`. Tests: 80 pass; the
  only failure is `DockerStatusTests.test_ready`, which expects a running Docker
  daemon (daemon is down and the OS is in local mode).

## Agent Skill Builder upgrade (2026-10-21)

Audit: the existing builder was YouTube-only and had no repository extractor,
review stage or Obsidian publishing. The old pipeline ran `yt-dlp` four times
per URL; `Nm6rVRw8hAU` took 162.98s (8.91s metadata + 154.06s Gemini-fallback
transcript) and `dQw4w9WgXcQ` took 17.53s.

Added `backend/skill_sources.py` (one yt-dlp extraction + one caption fetch,
targeted GitHub docs retrieval, cache, SSRF/redirect guards),
`backend/skill_jobs.py` (dedupe/progress/cancel), and rewrote
`backend/skill_builder.py` around staged drafts. Generation now creates an
editable review draft; **Reject** deletes only the draft, while **Add to Vault**
validates, resolves the real Vault path, refuses silent conflicts, writes
atomically to `vault/Skills/<name>/SKILL.md`, verifies the artifact, then removes
the draft. The Review overlay edits Skill Name, Description and the full
SKILL.md with debounced autosave.

Video mode supports Tutorial-to-Skill (taught method, not a recreation
template) and Reference-Video-to-Skill (scene extraction + recreation prompt
templates, optional character image). YouTube is analysed in place via Gemini;
direct files use the Files API path. Observed/transcript/uncertain facts are
separated and video mode fails honestly when visual analysis is unavailable.

Gemini calls now use a bounded health probe, per-model timeouts, cooldowns and a
persisted model-health cache. `gemini-3.8/3.7-flash` were hanging past 60s and
`3.6/3.5` were intermittently unavailable; the chain now falls through in
bounded time. Measured: extraction 12.75s (`Nm6r…`), 3.55s (`dQw…`), cache hit
0.011s; generation 249.6s → 27.0s first run and 12.7s steady state; reference
video 18.3s; tutorial video 28.4s. Review/autosave/publish/conflict/reject were
exercised over HTTP against an isolated Vault. Direct-video upload through the
Gemini Files API was live-tested (74s total), and `tests/e2e_skill_builder.py`
now drives real Chrome/Playwright through generate → edit/autosave → publish →
verify → generate → reject. `tests/test_server_security.py` was retargeted to
`app.py`; 28 Skill Builder tests and all 127 backend tests pass.
Details and limitations: `SKILL_BUILDER_AUDIT.md`.

## Studio generation console (2026-10-23)

Studio is now a real provider-backed generation console in the header (no
sidebar entry). It replaces the previous simulated preview + `/api/chat`
routing with asynchronous jobs and a local media library.

### Backend

| Module | Responsibility |
| --- | --- |
| `backend/studio_contracts.py` | JSON-schema validation, unknown-field rejection, normalized states (`queued/running/succeeded/failed/cancelled`), output/error normalization, request hashing, redaction, filename safety |
| `backend/studio_catalog.py` | load and validate the pinned catalog; list/filter/detail; modes |
| `backend/studio_secrets.py` | owner-only credential store (`data/studio/credentials.json`, `0600`), env override, hints only |
| `backend/studio_higgsfield.py` | official API client: submit, status, cancel, presigned upload, idempotency, error mapping |
| `backend/studio_jobs.py` | persisted job store, duplicate suppression, submit thread, poll backoff, restart recovery, media capture, no invented progress |
| `backend/studio_media.py` | SSRF-guarded downloads, 200 MB cap, `0600` metadata, Range-friendly serving |
| `backend/studio_collections.py` | persistent playlists/templates, atomic `0600` writes, media hydration and deletion cleanup |
| `backend/studio_providers.py` | provider registry: official API default + optional MCP adapter |
| `backend/studio_mcp.py` | minimal stdio JSON-RPC MCP client, timeouts, redacted stderr, persisted config |
| `backend/studio_tools.py` | agent tool definitions, read-only execution, persisted owner approvals |
| `scripts/studio_mcp_shim.py` | stdio MCP bridge to the Studio tool API; cannot approve its own spending actions |

`backend/catalog/higgsfield-0.1.0.json` pins 84 official workflows (17 image, 67
video) with full input schemas. Runtime never fetches docs.

### HTTP API (owner token required)

```
GET    /api/studio/providers
GET    /api/studio/models?provider=&kind=&mode=&q=&id=
GET    /api/studio/jobs?limit=&state=
GET    /api/studio/jobs/<id>
POST   /api/studio/jobs                         {provider, model_id, params, auto_download}
POST   /api/studio/jobs/<id>/cancel
DELETE /api/studio/jobs/<id>                    (terminal jobs only)
GET    /api/studio/library?kind=&role=&q=&limit=
POST   /api/studio/library/import               {url, kind, title, provider, model_id}
GET    /api/studio/media/<id>                   (Range, token via header or ?token=)
DELETE /api/studio/library/<id>
GET    /api/studio/playlists
POST   /api/studio/playlists                    {name}
POST   /api/studio/playlists/<id>               {name}
DELETE /api/studio/playlists/<id>
POST   /api/studio/playlists/<id>/items         {media_id}
DELETE /api/studio/playlists/<id>/items/<media_id>
GET    /api/studio/templates
POST   /api/studio/templates                    {name, provider, model_id, params}
DELETE /api/studio/templates/<id>
POST   /api/studio/uploads                      {provider, filename, content_type, data_base64}
GET    /api/studio/tools
POST   /api/studio/tools/call                   {name, arguments}
GET    /api/studio/approvals
POST   /api/studio/approvals/<id>               {decision: approve|reject}
POST   /api/studio/providers/higgsfield/credentials
POST   /api/studio/providers/higgsfield/test
DELETE /api/studio/providers/higgsfield/credentials
GET    /api/studio/mcp
POST   /api/studio/mcp/config
POST   /api/studio/mcp/connect|disconnect
```

### Frontend

- `js/studioState.js` - pure helpers (elapsed/clock labels, model/input-mode
  filtering, schema parameter coercion and restore, required fields, usage
  counts, media URLs), covered by `tests/studioState.test.js`.
- `js/studio.js` - provider/model menus, input modes, prompt presets, dynamic
  parameter form, uploads, job polling, single stage with real output
  variations, share/download/import, playlists, templates, history, library,
  local usage, credential setup and MCP configuration.
- `index.html` - three-column Activity workspace, full-width Library and Setup
  panels, and the Studio modal; `css/style.css` - Studio workspace styles.
- Navigation remains header-only. Activity / Library / Setup are the only
  Studio tabs; playlists and templates live inside the existing rails and
  Library side panels rather than adding new top-level tabs.

### Branding (2026-10-23)

Studio has its own mark, modelled on the folded-page/glass-lens concept the
owner supplied:

| Asset | Use |
| --- | --- |
| `assets/studio/studio-icon.svg` | Full-colour master (folded page, ocean photo card, glass lens). Used in the Studio view header tile; safe to reuse for docs, app icons or social cards |
| `assets/studio/studio-mark.svg` | 24px monochrome mark with a punched lens halo; applied via CSS mask so it inherits `currentColor` |

- The header tab now uses `.studio-mark` instead of the `clapperboard` lucide
  icon, so it tints with the tab (slate → blue when active) and keeps the same
  14px optical weight as its neighbours.
- The mark is decorative (`aria-hidden`); the tab still reads "Studio", and the
  header tile image has an empty `alt`.

### Verification

- Python: **229 tests pass** (`python3 -m unittest discover -s tests -p 'test_*.py'`).
  Studio-specific: contracts 20, catalog 8, official adapter 8, jobs 12, MCP 12,
  collections 7, routes 18.
- JS: panelState 23, kanbanState 9, agentDashboards 8, studioState 19.
- Headless Chrome smoke tests: Studio opens from the header; 2 providers, 84
  catalog models, input modes, schema fields, preset chips, stage details,
  playlists/templates in the rails and Library, playlist modal, Library and
  Setup tabs all render. With a mocked API response, a submitted job rendered
  two real variations, variant switching worked, and the stage actions enabled.
- Real Higgsfield generation was **not** run: no API key/secret or credits were
  available. All provider behavior is exercised with fake/mock servers.

### Security / limitations

- Credentials never reach the browser; the setup form clears after save, and
  API responses expose only `configured`, `source`, `hint` and `missing`.
- Media URLs embed the owner token only in the query string because `<video>`
  requests cannot set headers; access logs redact `token=`.
- Official API: no progress endpoint, cancel is queued-only, outputs retained
  by Higgsfield for at least 7 days. Studio therefore shows elapsed time, not a
  fake percentage.
- MCP provider is experimental and explicitly configured; the official API is
  the default. The upstream project's experimental web backend is not used.
- Historical note: the earlier "MCP Connector Hub" section above describes
  `backend/mcp.py` / `js/mcp.js`, which are no longer present in the working
  tree. Studio's MCP connector is a separate, Studio-scoped implementation.

## DeepSeek Harness chat agent (2026-10-27)

Added a sixth Workspace agent, **DeepSeek Harness**, to the existing Agent
dropdown. It is a native in-app chat dashboard, not a spawned CLI: no
`deepseek`/`dsh` binary is installed and none is required.

### Behaviour

- The agent registry carries a `transport` field (`"cli"` by default,
  `"chat"` for built-ins in `BUILTIN_CHAT_AGENTS`). Chat agents are always
  available, show as **built-in** in the Agent dropdown, and report no CLI
  version or session count.
- Chat agents have no PTY or project session. `POST /api/project-sessions`
  rejects them with `code: "chat_agent"`, and the Workspace skips the
  session-oriented screen/panel path when one is selected.
- The Workspace **Model** dropdown is reused. The DeepSeek model source returns
  `deepseek-chat` (default) and `deepseek-reasoner`; the pick is pushed into the
  chat header and remembered in `localStorage` under
  `dennis.deepseek.model`.
- Selecting the chat agent records it in the existing last-selection key, so a
  page reload restores the dashboard (and the last conversation, see below).
- History is owner-scoped and stored in `data/deepseek/conversations.json`
  (mode `0600`, atomic writes, git-ignored). The rail supports open, new and
  delete; an empty conversation is reused instead of duplicated.
- Replies stream over SSE (`text/event-stream`) through the stdlib
  `http.client`; `Stop` aborts the upstream request and keeps the text already
  received. One request per owner is allowed at a time (busy lock).

### Credentials

`data/deepseek/credentials.json` stores the API key (mode `0600`,
git-ignored). `DEEPSEEK_API_KEY` overrides it. `GET /api/deepseek/status`
returns only key status (`configured`, `source`, `hint`, `missing`), the base
URL and model metadata — never the key itself.

The Workspace **Provider** dropdown is the primary setup surface: its
`provider_setup` entry for `deepseek-harness` carries `form: "api_key"`, so the
menu renders an inline key field, Connect and Disconnect actions (plus the
masked hint) instead of a copyable CLI command. This is the same owner-only
credential store the chat card writes to; DeepSeek has no OAuth login, so no
fake browser sign-in is offered. The chat card remains as a fallback and both
surfaces refresh each other through `DeepSeekChat.refreshStatus()`. Deleting
the stored key falls back to the environment key if one is set.

### Verification

- `tests/test_deepseek_harness.py` (15) covers key precedence, redaction,
  model list, conversation CRUD, ownership, busy lock, partial persistence and
  friendly upstream errors; `tests/test_deepseek_routes.py` (8) exercises every
  route with a fake local SSE upstream and asserts the Provider-menu
  `provider_setup` carries `form: "api_key"`. All 23 pass.
- `node --check` passes for `js/deepseekChat.js` and `js/workspace.js`; the JS
  unit tests still pass.
- Headless Chrome (before the Provider-menu form landed): the dropdown lists
  `DeepSeek Harness`; selecting it mounts the chat and shows the key setup card;
  both models list; picking Reasoner updates both model labels; switching to
  OpenCode and back, and a full reload, restore the agent and model; `+ New`
  creates a conversation and the rail `×` deletes it.
- The Provider-menu key form reuses the same routes (`/api/deepseek/status`,
  `POST`/`DELETE /api/deepseek/credentials`) already covered by the route tests;
  its DOM wiring was not re-run in headless Chrome because the host was under
  extreme load (load average > 400) at the time.
- Full suite: 252 tests. On this host the Docker-readiness test timed out
  (`docker_available` false under load) and the known MCP timing test flaked;
  both are unrelated to DeepSeek and pass when the machine is not saturated.
- No real API key or credits were available: streaming is verified against the
  fake SSE upstream only. The setup card correctly reports **API key required**.

### Security / limitations

- The key is sent only from the backend to `api.deepseek.com` over HTTPS; it is
  never embedded in HTML, JS, logs or SSE frames.
- Conversation text is stored unencrypted at rest under `data/` (owner-only
  mode), consistent with the rest of the app's local stores.
- There is no token/usage or cost accounting: DeepSeek usage frames are not
  persisted, and there is no separate server-side abort endpoint beyond
  closing the SSE stream. Reasoner output is shown in a collapsed
  "Reasoning" block, streamed separately from the answer.

## Provider dropdown for agents (2026-10-27)

The Workspace toolbar now has **Agent → Provider → Model** (the provider menu
sits between the existing two). Providers are never hardcoded:

- `backend/model_sources/base.py` normalises an optional `provider` on every
  model row. OpenCode takes the `provider/model` prefix, Hermes reports its
  active provider (from `config.yaml` / `provider_models_cache.json`), Codex
  reports OpenAI, Claude reports Anthropic, Antigravity reports Google DeepMind,
  and DeepSeek Harness reports DeepSeek (or the `DEEPSEEK_BASE_URL` hostname for
  a custom endpoint).
- `backend/model_sources/registry.py` adds `providers(models)` (first-seen
  order, model counts, availability) and `provider_label()`, which keeps an
  unknown CLI provider id verbatim instead of renaming it.
- `GET /api/agents/<id>/models` returns `providers` and `provider_setup`; the
  502 model-list error carries `provider_setup` too, so a logged-out or
  untrusted CLI can be configured from the same menu.
- `backend/agent_registry.py` owns `PROVIDER_SETUP` (CLI-native setup command +
  hint per agent; no credentials). The OS still never stores CLI provider keys.
- `js/workspace.js` adds `state.providerId/providers/providerSetup`, filters the
  Model list by the selected provider, strips OpenCode's redundant
  `provider/` name prefix, remembers the provider per agent in `localStorage`,
  and derives it from the current session model on resume. Picking a provider
  opens the filtered Model menu (nothing restarts until a model is chosen).
- `js/skill-builder.js` uses the same Agent → Provider → Model flow for the
  authoring agent (no restart concern there: the pick is sent with the job).
  It shares the same `/models` payload, filters identically, and shows the same
  copyable setup block. A loading state distinguishes "still listing" from
  "this CLI reported no providers".
- `index.html` / `css/style.css` add the amber Provider dropdown and the copyable
  setup block (both toolbars).

Verified live (server on :8099, headless Chrome): OpenCode reported 6 providers
(groq, nvidia, ollama-cloud, openai, opencode, openrouter; 516 models total),
the menu showed per-provider counts, choosing OpenAI filtered the Model menu to
82 models, and the setup block exposed `opencode auth login`. Claude Code
(untrusted folder) returned the trust error plus its setup hint. The Skill
Builder toolbar was verified the same way (OpenCode → 6 providers, OpenAI → 82
rows, All providers → 516, no page errors). Tests: 252 pass
(`python -m unittest discover -s tests -p 'test_*.py'`).

## Studio Video Kit chat templates (2026-10-28)

The Studio Video Kit now has a chat box directly below its three panels with a
`Template` dropdown on the right (default `None`).

- `GET /api/studio/video-kit/templates` lists approved video-generation skills
  from the shared-brain Vault (`vault/Skills/<name>/SKILL.md`). Skills are
  matched on name/title/description, explicit `tags`/`type`/`category`
  frontmatter, or the opening of the body. Non-video skills are excluded.
- For each entry the endpoint extracts a copy-ready prompt: the fenced block
  under headings such as "Initial generation prompt" or "Reusable prompt
  templates", falling back to the first fenced block and then the body. The
  full SKILL.md is never injected into the composer.
- Selecting a template loads its prompt into the chat box (editable) and syncs
  it to the Studio prompt, so the next Generate uses it. `None` clears an
  untouched template prompt; `Use in Prompt` forces the chat text into the
  prompt; `Clear` empties both when they still match.
- The dropdown is disabled while Panel 1 has a reference URL/file, so a
  template is only chosen when no reference video is supplied. Removing the
  reference re-enables it. The list refreshes with the Studio refresh button.
- Files: `backend/studio_videokit.py` (`list_video_templates`, prompt/skill
  classification), `app.py` (owner-gated route), `index.html`
  (`#studio-vk-chat`), `css/studio-v2.css`, `js/studio.js`.
  Test: `tests/test_studio_routes.py` covers listing, filtering and prompt
  extraction.
- Layout: the kit lives in the ~326px composer column, so its header chips and
  actions wrap onto their own rows and the three panels use an auto-fit grid
  (one full-width column in the composer, more when the workspace widens).
  Panel badges wrap below the panel name instead of colliding with it, and the
  chat footer stacks its actions above a full-width `Template` select.

## Studio activity layout (2026-10-28)

The Generate tab no longer carries a right rail. The Recent Generations,
Playlists and Quick Actions panels were removed because History, Playlists and
the generation modes already cover them:

- `index.html` drops `<aside class="studio-rail">`; the workspace grid is now
  two columns (composer + stage), so the preview uses the reclaimed width.
- The Model details card moved out of the bottom info grid and now sits
  directly under the Model dropdown in the composer (`#studio-model-details`),
  with a `live` marker. `renderModelCard()` already runs on provider/model
  selection, so the title, provider, documented inputs and tags update as soon
  as a different model is chosen.
- Usage & credits stays at the bottom of the stage and now spans the full
  centre column (`.studio-info-grid` is single-column).
- JS guards for the removed hosts already existed (`#studio-jobs-list`,
  `#studio-rail-playlists`, `#studio-history-btn`, quick-action buttons), so
  no data loading or tab routing was lost.

## Model-aware Video Kit, Vault assets and runtime selectors (2026-10-28)

The Video Kit is now an optional, model-aware workflow rather than a separate
prompt toy:

- **Optional by model.** `generate()` first calls `ensureVideoKitForGeneration()`.
  For a pure text-to-video model with a kit reference but no analysis, it runs
  the reverse-engineering pass automatically and uses the resulting prompt. For
  models that document image/video inputs, the kit assets are attached directly
  to the best matching URL field (reference first, then remote avatar/product
  images) and the prompt is only replaced when it is empty.
- **Field mapping.** `injectVideoKitAssets()` orders documented fields by
  semantics (`image_url`/`image_urls` before `first_frame_url`/`end_image_url`,
  `video_url` before `reference_video_url`) so a reference is never attached to
  an unrelated end-frame slot. Only provider-fetchable `http(s)` URLs are sent
  to the generation provider.
- **Any-platform eyes & ears.** `skill_sources.PLATFORM_HOSTS` classifies
  TikTok, Instagram, X/Twitter, Facebook, Vimeo, Dailymotion, Twitch, Reddit,
  Pinterest, Snapchat, LinkedIn, Rumble, Bilibili, Threads, Kwai, Likee and
  Weibo URLs as `kind: "platform"`. `extract_platform_video()` reuses the
  single yt-dlp metadata/caption pass, and `download_platform_video()` resolves
  a playable stream for Gemini when a page is not direct media. Arbitrary web
  pages still fail classification.
- **Image and video eyes.** `skill_builder._image_analysis()` adds Gemini vision
  for still references. Base64 attachments are written to a temp file for
  local probing/upload, so a dropped image or video can be analyzed even before
  a provider URL exists.
- **Vault avatars.** `GET /api/studio/video-kit/avatars` lists image files and
  avatar-ish notes (notes with an image embed, frontmatter avatar/image field,
  or an `Avatars/` path). Typing `Clair`, `Mia`, `Karen`, etc. in the new
  `#studio-vk-avatar-name` datalist resolves the Vault image via
  `GET /api/vault/asset` (owner-token gated, image extensions only). Resolved
  images are also uploaded to the active generation provider so image/video
  models can fetch them; if that upload is unavailable, the local Vault file is
  still passed to the Gemini visual-analysis pass server-side.
- **Add to Vault.** `POST /api/studio/video-kit/save-skill` writes
  `vault/Skills/<slug>/SKILL.md` with the analysis and a reusable prompt. It is
  wired to the analysis card (`Add to Vault`) and to the stage (`Save skill`) so
  a finished render's prompt can be published. New skills immediately appear in
  the Video Kit Template dropdown.
- **Previews.** Reference/avatar/product panels render real `<img>`/`<video>`
  previews from the provider URL, or from the attached base64 data URI before
  upload. Clicking the preview no longer opens the file picker.
- **Runtime selectors below chat.** Agent, Provider and Model now sit directly
  below the Video Kit chat box. The Agent menu is populated from
  `/api/agents/availability` and remembered locally; the provider menu moved out
  of the hero unchanged. Model details remain live underneath.
- Cache versions: `css/studio-v2.css?v=20261028-studio-kit1`,
  `js/studio.js?v=20261028-studio-kit7`.
- Tests: `tests/test_studio_routes.py` covers the Vault avatar listing/asset
  route and save-skill round trip; `tests/test_skill_builder_pipeline.py`
  covers TikTok/Instagram/X URL classification.

## FreeLLMAPI dashboard (2026-10-30)

A standalone **FreeLLMAPI** sidebar section turns the
`open-free-llm-api/awesome-freellm-apis` directory (the data behind
`freellm.net`) into a local catalog and connection surface.

- **Snapshot:** `public-plugins/free-llm-apis/` holds the upstream READMEs,
  code examples, assets and `SNAPSHOT.json` (source commit + sync time).
  `scripts/sync_free_llm_apis.py [--source …]` refreshes it atomically and
  normalises file modes; `DENNIS_FREELLM_REPO` selects a checkout.
- **Parser:** `backend/freellm_catalog.py` reads only `README.md`, extracts the
  four marker-delimited tables (directory, quick reference, featured models,
  top models), and caches by mtime. It never executes repository code and never
  makes a network request.
- **API:** owner-gated `GET /api/freellmapi/catalog` returns the structured
  catalog plus `freellmapi.status()` for `claude-code` and `codex`.
- **UI:** `index.html#freellm-view` + `js/freellmapi.js` + `css/freellmapi.css`
  provide search, tier/card/modality filters, provider detail, featured and top
  models, and a connection form. **Use** prefills base URL + model ID; **Save**
  writes the existing per-agent FreeLLMAPI settings through `POST /api/freellmapi`.
- **Key safety:** a blank key is reused only when the base URL is unchanged, so
  switching providers cannot silently reuse the previous provider's key.
- **Tests:** `tests/test_freellm_catalog.py` (parser, cache, missing snapshot)
  and `tests/test_freellm_routes.py` (owner gate, catalog payload, connect /
  clear, key reuse rule). 9 tests pass; existing 107 backend/security tests and
  the JS suites remain green.

## Security notes

- `~/.claude/settings.json` holds a live `ANTHROPIC_AUTH_TOKEN`; it is never sent
  to the browser or logged. Only an allowlisted set of env vars reaches agents.
- Studio provider credentials live in `data/studio/credentials.json` (mode
  `0600`, git-ignored); env vars override them. They are never returned by the
  API, embedded in job records, written to access logs, or sent to MCP servers
  unless the owner explicitly configures the MCP environment.
- Studio playlists/templates live in `data/studio/playlists.json` and
  `templates.json` (mode `0600`, atomic writes, git-ignored). Templates contain
  only provider/model identifiers and validated parameters; credentials are
  never copied into them.
- The app's own token (`data/auth.json`) gates every endpoint and the WebSocket
  via `X-Dennis-Token`/`?token=`; the WS also checks session ownership.
- File reads, folder listings and mounts are confined to validated project roots
  (realpath + traversal checks); file writes use an atomic temp+rename with a
  2 MB cap. The Preview iframe is sandboxed and receives no credentials: it loads
  only `http(s)` URLs (external dev servers) or owner-scoped same-origin `/api/`
  raw paths, which carry the token only in that iframe request. The Docker socket
  is never mounted.
- `~/.claude.json` was modified to trust the project folder; a timestamped backup
  (`~/.claude.json.dennis-backup-*`) was written first.

## Studio workspaces: Ads Studio + YouTube Studio (2026-11-09)

Stage 2 of the YouTube Studio build landed. The full audit and integration plan
is in `docs/YOUTUBE_STUDIO_AUDIT.md`; the two remaining pre-existing test
failures (`test_studio_routes` credential descriptor keys, `test_plugins`
`rea` pack expectation) are documented there and were not introduced here.

### What changed

| File | Change |
| --- | --- |
| `backend/studio_workspaces.py` | NEW - workspace registry (Ads, YouTube), per-workspace settings, creative project store with soft delete/archive, activity log. JSON stores under `data/studio/workspaces/` (atomic, `0600`). |
| `app.py` | NEW routes: `GET /api/studio/workspaces[/...]`, `POST /api/studio/workspaces/active`, `POST .../projects`, `PATCH .../settings|projects/<id>` (new `do_PATCH`), `DELETE .../projects/<id>`. Also added the missing `GET /api/studio/fashion/model-library` handler (the Fashion "Create Model" library list 404'd before). |
| `js/studioWorkspaceState.js` | NEW - pure hash/section helpers (`#studio/<workspace>/<section>`), node-tested. |
| `js/studioYoutube.js` | NEW - YouTube Studio shell: overview dashboard (real counts, honest empty states), projects CRUD, explicit "arrives in Stage N" placeholders. |
| `js/studioWorkspaces.js` | NEW - workspace selector state, shell switching without reload, hash deep links, back/forward support. |
| `index.html` | Workspace switcher in the Studio header; YouTube sidebar + main panels + modal (hidden by default); new script/CSS tags. All existing Ads tab keys and panels untouched. |
| `css/studio-youtube.css` | NEW - YouTube skin scoped to `#studio-view`; hides Ads provider status while YouTube is active. |
| `tests/test_studio_workspaces.py`, `tests/test_studio_workspace_routes.py`, `tests/studioWorkspaceState.test.js` | NEW - 21 + 12 unit/route tests and 11 node checks. |

### Behaviour

- `STUDIO [ Ads Studio ] [ YouTube Studio ]` selector in the Studio header.
  The active workspace is highlighted, remembered in `localStorage` and mirrored
  to `POST /api/studio/workspaces/active`.
- Deep links: `#studio/ads/history`, `#studio/youtube/projects`, etc. Back and
  forward work; the page never reloads.
- Ads keeps every existing tab (`fashion`, `assets`, `library`, `playlists`,
  `preset`, `history`, `setup`) and its data stores untouched; switching
  workspaces preserves the Ads tab you were on.
- YouTube projects live only in `data/studio/workspaces/youtube/projects.json`;
  Ads projects live only in `data/studio/workspaces/ads/projects.json`. The API
  rejects cross-workspace reads (404) and the UI never mixes them.
- Later-stage sections (Channel Manager, Research, Topics, Scripts, Storyboard,
  Assets, Voice, Animation, Thumbnails, Publishing, Analytics, Calendar) render
  an explicit placeholder with their stage; no fake data anywhere.
- Deleting a project is a soft delete (`deleted_at`); media files are never
  touched.

### Verification

- `python3 -m unittest discover -s tests -p 'test_*.py'`: 348 tests, 2
  pre-existing failures (see audit §13), no new failures.
- `node tests/*.test.js`: all pass (studioState 19, studioWorkspaceState 11,
  panelState, kanbanState, agentDashboards).
- Headless Chrome (Playwright, real server): deep link opens YouTube Studio,
  project create/list/detail works, workspace switching preserves Ads tab
  state and Ads panels render, no console errors, no 404s.

## Next steps

1. If Docker becomes available, run `scripts/build_agent_images.sh` and
   `DENNIS_SANDBOX=docker` and verify the container path end to end.
2. Consider accessing the Model Registry as an additional `ModelSource`
   (the dropdown already depends only on the interface).
3. Persist the last selected agent per owner for CLI agents with no session too
   (the DeepSeek chat agent already records its selection this way); add WS
   auto-reconnect with backoff if the backend restarts while a session is open.

## Centralized TTS Router — Kokoro default voice (2026-11-10)

Hermes Apollo (Jarvis) now speaks through a centralized Kokoro voice engine
instead of browser `speechSynthesis`; the same engine serves Ads Studio and
YouTube Studio. ElevenLabs remains an optional alternative.

### Runtime (Intel macOS, 8 GB, no Docker/Homebrew, no sudo)

- `tts_service/` — isolated CPython 3.12 venv (via the uv binary already on
  disk at `~/.hermes/tools/uv-0.13.5-darwin-x64`), `kokoro-onnx` 0.6.1 +
  `onnxruntime` 1.23.2 CPU (no torch — no cp314/x86_64 wheels exist for the
  system Python 3.14). Official Kokoro v1.0 weights in
  `tts_service/models/` (git-ignored, 325 MB + 28 MB, downloaded by
  `tts_service/setup.sh`).
- `tts_service/server.py` — OpenAI-compatible serving layer, loopback only
  (`127.0.0.1:8880`): `GET /health`, `POST /warmup`, `GET /v1/models`,
  `GET /v1/audio/voices`, `POST /v1/audio/speech` (pcm | wav | mp3 via the
  bundled ffmpeg), 24 kHz mono s16le PCM with `X-Audio-Sample-Rate`.
- `scripts/kokoro_service.py start|stop|restart|status|health` — PID file +
  port probe guarantee exactly ONE shared inference process; `start` no-ops
  when a healthy service already answers (any schema variant).

### Centralized router (`backend/tts.py` + routes in `app.py`)

- Owner-gated: `GET /api/tts/status|config|voices|audio/<file>`,
  `POST /api/tts/config|speech|secret|service(/start|/stop)`.
- Provider registry: **kokoro** (default, local), **elevenlabs** (optional
  cloud; key stored 0600 backend-side via `/api/tts/secret`, quota read from
  the subscription endpoint, never echoed), **browser** (fallback only).
- Three-level selection: global default → per-studio preference
  (apollo/ads/youtube, independent) → voice-preset override (a preset pins
  its provider). **JARVIS — BRITISH MALE** preset is persistent:
  `bm_george` (alt `bm_lewis`), en-GB, speed 1.03, PCM output.
- Speech route relays the Kokoro chunked PCM stream when `stream: true`
  (first sentence starts playing before the full reply is synthesized) and
  returns buffered WAV otherwise; `save: true` persists voiceovers under
  `data/tts/audio` and returns a descriptor + URL.

### Apollo / Jarvis frontend

- `js/tts.js` (DennisTTS): queue, PCM WebAudio streaming, WAV decode,
  cancellation, no-overlap, browser speechSynthesis as explicit fallback
  (configurable per studio), no credentials in the frontend.
- `js/galaxy.js`: `speak(text, onDone)` signature preserved; routes through
  DennisTTS; mic recognition is suspended while Jarvis speaks and resumed
  after; equalizer/toasts/shockwaves/wake-chime behavior unchanged.
- General conversational voice commands no longer produce a fake
  acknowledgement: `submitToSelectedAgent` → `window.DennisAgentBridge`
  (`js/app.js`) → existing `/api/chat` pipeline with the currently selected
  agent + model → the REAL agent reply is trimmed and spoken by Kokoro;
  failures are reported honestly.
- Apollo voice panel (provider, preset, speed, preview, test, service
  start/stop, browser-fallback toggle, live status) + Voice Provider
  Registry card in the workspace registry section + voiceover panels in Ads
  Studio and YouTube Studio (`js/voiceRegistry.js`).

### Installed Hermes Agent (v0.22.2) native TTS

- `~/.hermes/config.yaml` backed up to
  `backups/20261110-tts-integration/hermes-config.yaml.pre-tts.bak`, then
  `tts.provider: openai` with `base_url: http://127.0.0.1:8880/v1`,
  `model: kokoro`, `voice: bm_george`, `api_key: local-kokoro` (placeholder;
  the local service does not validate credentials), `pcm_sample_rate: 24000`.
  STT, voice, model, Telegram/Discord and all other sections untouched.
- Verified against the installed version: `tools.tts_tool.text_to_speech_tool`
  produced a real MP3 through Kokoro (MPEG-1 L3, 24 kHz, 4.08 s), and
  `tools.tts_streaming.OpenAIStreamer` streamed PCM chunks honoring the
  configured sample rate (`available() == True`).

### Measured performance (i5-7360U, 2C/4T, 8 GB)

- Model load 6–17 s once at service start; warm-up synthesis after load.
- Steady-state synthesis: ~2–5× realtime (a 4.6 s Jarvis reply in 9–22 s);
  under thermal/load pressure up to ~8–15× (36 s+). This is the measured
  limit of this CPU — reported, not worked around silently.
- Repeated phrases are instant (disk cache, ~0.02 s).
- Known failure mode fixed relative to earlier local builds: client
  disconnects mid-stream no longer wedge the engine (abandon-aware bounded
  queue; verified by curl -m abort followed by an immediate successful
  request).

### Verification

- `python3 -m unittest tests.test_tts tests.test_tts_routes` — 32 tests OK
  (preset persistence, studio independence, provider pinning, validation,
  secrets never echoed, status/config shapes, audio persistence).
- `python3 tests/e2e_tts_kokoro.py` — PASS: real routes → live Kokoro →
  valid 5.33 s WAV + PCM stream.
- Headless Chrome (real server): all Apollo voice controls render and bind,
  status shows "Kokoro connected • kokoro-v1.0 loaded • 54 voices", Ads/YT
  voiceover panels mount, per-studio presets independent (Apollo=jarvis,
  YouTube=narrator), zero console errors.
- Part 5 pipeline (headless Chrome, real /api/chat): bridge context returns
  the selected agent/model; a submitted question invoked the real agent CLI
  and returned its actual response (honest error text when the agent's own
  upstream is unavailable — no fabricated acknowledgements).
- Full suite: 380 tests; the only 2 failures are pre-existing in
  `test_plugins` and `test_studio_routes` (unrelated modules under active
  edit in this working tree; untouched by the TTS change set).

## Ads Studio: five-step workflow (Model → Product/Props → Dressing → Reference Video → Production) — 2026-11-11

The old four-step Collart flow (Select Reference Video → Pick a Model →
Add Clothing → Output Settings) is rebuilt as a five-step, skip-anywhere
workflow:

1. **Model** — preset / upload / generate (Prompt 1 seeded: character sheet,
   grey background, 9:16, skin tone + facial features preserved). Free image
   tools (Google Flow, ChatGPT, Gemini, Claude, Grok) noted in the copy.
2. **Product/Props** *(optional, renamed from Add Clothing)* — upload box on
   the LEFT, prompt chatbox on the RIGHT (Prompt 2 seeded: "use the attached
   images to create product sheet"); generated product sheets render under the
   chatbox; Preset Images grid below (catalog garments + library entries).
3. **Dressing** *(optional)* — carried strip shows the Step 1 model + Step 2
   products; Prompt 3 seeded ("step 1 model dress/wear like step 2."); image
   edit job dresses the model; results are click-to-select; skip keeps the
   original model look.
4. **Reference Video** *(optional)* — old preset/upload picker plus a new
   storyboard prompt box ("create a storyboard, 9:16, each scene is 0.3sec,
   total duration is 15sec"); Skip jumps to Production and marks the step done.
5. **Production** — every selected medium is displayed in a carried strip;
   two new dropdowns supply prompt text (Model Placement & Environment:
   10 groups × 30 entries; Video Generation Templates: 5 groups × 10 entries,
   in `js/studioTemplates.js`); final prompt box composes
   final + storyboard + environment + video direction; the generated video
   shows with Play / Edit Prompt / Add to Library / Download / Delete.

Cross-cutting behavior:

- **Skipping**: every step except the model identity is optional. Without a
  reference clip the backend plans image-to-video (identity available) or
  text-to-video (prompt only); the engine grid relabels and switches pools
  (`fashion_models` vs new `production_models`).
- **Preset Images auto-save**: uploaded and generated images are saved into
  the preset library after 1 minute (`schedulePresetSave`, 60 s timer). The
  library entries now carry a `kind` (`model` / `product` / `dressed`) so each
  step's preset grid filters its own images; old entries default to `model`.
- **Click-to-select**: any generated, preset or uploaded image in a step is
  click-to-select (products multi-select, dressed single-select); each step
  shows the media selected in earlier steps below it (carried strips), and the
  dock badge counts 5 slots.
- **Preset Video** remains the default preview page; the Assets page and the
  model-library kebab/confirm flows are unchanged.

### Files

- `index.html` — stepper + five step sections (Model moved to step 1; new
  Product/Props split panel; new Dressing step; Reference Video + storyboard
  box; Production with template dropdowns and result actions).
- `js/studioFashion.js` — full rewrite for the five-step state machine
  (identity = dressed || model; products multi-select; engine pool switching;
  composed prompt; carried strips; result actions; 60 s preset auto-save).
- `js/studioTemplates.js` — new: the two template catalogs + optgroup helpers.
- `css/studio-v2.css` — styles for the split panel, thumb grids, carried
  strips, skip rows, result action bar, "optional" step tags.
- `backend/studio_fashion.py` — outfits and reference video are optional in
  `normalize_draft`/`resolve_draft`; `build_prompt` handles missing parts;
  new `production_models` pool; `plan_job`/`prepare_job` pick
  reference/image/text pools by available inputs.
- `backend/studio_model_library.py` + `app.py` — library entries carry `kind`.
- `tests/test_studio_fashion.py` + `tests/test_studio_routes.py` — updated to
  the skippable contract; new tests for the production pool and
  no-reference planning.
- `tests/e2e_ads_studio.py` — new headless-Chrome E2E: 5-step stepper, split
  panel, prompt seeds, skip flows, 10/5 template groups, result actions,
  preset page, zero console errors. Run: `python3 tests/e2e_ads_studio.py --serve`.

### Verification

- `python3 -m unittest tests.test_studio_fashion tests.test_studio_model_library
  tests.test_studio_routes tests.test_studio_contracts tests.test_studio_workspaces
  tests.test_studio_workspace_routes tests.test_studio_jobs
  tests.test_studio_collections` — 132 tests; the only failure is the
  pre-existing `test_providers_are_described_without_secrets`.
- `node --test tests/studioState.test.js tests/studioWorkspaceState.test.js
  tests/panelState.test.js` — 3/3 pass.
- Headless Chrome against the real server: all Ads Studio E2E checks pass,
  including engine-pool switching (reference → image → text), carried strips
  with 4 selected media, dock 4/5 badge, composed prompt assembly, and a stubbed
  submit posting `model`/`outfits`/`reference_video`/`prompt` correctly.

### Follow-up fixes (same day)

1. **Selected media now displays below the steps on EVERY step.** The four
   per-step carried strips were replaced by one `#fashion-carried` bar placed
   directly under the stepper (inside `#studio-panel-fashion`, above all step
   sections). It fills from any step's selection (model / products / dressed /
   reference), so it is visible on steps 1–5, not just 3 and 5.
2. **"Invalid credential" popups are now actionable.** The provider's raw 401
   text (e.g. Higgsfield returning "Invalid credential") was relayed verbatim
   to toasts. `friendlyError()` now detects auth failures
   (`authentication_failed` / `credentials_error` / credential-phrased text),
   names the provider, and explains the fix; the setup banner switches to
   "the provider rejected your credentials — re-save and press Test", and the
   status line keeps the warning until the next successful provider call.
   All 15 error toasts in the Ads Studio module route through it; the flag
   clears on successful uploads/generations. (Credentials themselves are
   validated via the existing Test button in Provider Setup —
   `POST /api/studio/providers/higgsfield/test`.)

E2E (`tests/e2e_ads_studio.py`) gained two phases: credential-error UX
(stubbed 401 → actionable toast + banner + status) and the carried bar
(visible with 4 entries, positioned above every step section).

## Ads Studio: free-model engines replace Higgsfield (Pollinations + Google AI) — 2026-11-11 (2)

The user hit "Higgsfield rejected the credentials" on every upload/generation.
Ads Studio no longer touches any paid provider:

### Engines

- **Pollinations** (keyless, always available) — text → image for Steps 1–2.
- **Google AI** (free-tier `GEMINI_API_KEY` from aistudio.google.com) —
  `gemini-2.5-flash-image` for image editing with **inline base64** references
  (uploads never need a public URL), and **Veo** (`veo-3.0-fast`, `veo-3.0`,
  `veo-2.0` — the engine behind Google Flow) for Step 5 video, text or image
  to video, with a built-in "no background music" negative prompt.

### New backend: `backend/studio_free.py` + `/api/studio/free/*` routes

- `GET  /api/studio/free/models` — engine capabilities + whether a Google key
  is stored (bool only; secrets never reach the browser).
- `POST /api/studio/free/uploads` — uploads are stored in the local media
  library (`data/studio/media`), so uploading **cannot** hit provider
  credentials anymore.
- `POST /api/studio/free/jobs` + `GET /api/studio/free/jobs/<id>` — background
  job registry with the same public shape as Studio jobs (state/outputs/
  terminal); outputs are stored via `studio_media` and served from
  `/api/studio/media/<id>`.
- `POST /api/studio/free/credentials` (+ `/delete`, `/test`) — free Google AI
  key management.
- Verified live: a keyless Pollinations generation produced a real 580×1015
  JPEG in ~9 s through the full route → job → media pipeline.

### Frontend rewires

- `js/studioFashion.js` — uploads use `/api/studio/free/uploads`; Steps 1–3
  generation uses the free pipeline (Google when a key exists, Pollinations
  otherwise; local-upload editing without a key explains exactly what to add);
  Step 5 submits free Veo jobs and polls them; engine grid lists the three Veo
  engines; validation requires the Google key only for video; image defaults
  (resolution/duration/aspect) fall back to engine options.
- Provider Setup card swapped from Higgsfield credentials to **"Free models —
  Google AI (Gemini + Veo)"** with Save/Test/Remove and a Pollinations note;
  `js/studio.js` bindings rewired to the `/api/studio/free/*` routes.
- The legacy Higgsfield backend/routes remain only for the separate Studio
  activity console — the Ads Studio workflow never touches them.

### Verification

- `tests/test_studio_free.py` (15 tests): URL builders, Gemini payload shape
  (inline base64), Veo submit/poll/download (no-music negative, image frame),
  job lifecycle (success/missing-key/404), local media references.
- Ads Studio E2E extended: free engines render (3 Veo cards), key-missing
  banner, stubbed key-rejection → actionable toast + banner, and a stubbed
  submit posting kind=video with image_urls/aspect/duration/composed prompt.
- 128 backend tests + 3 JS suites pass (only the pre-existing
  `test_providers_are_described_without_secrets` failure remains).

### Follow-up: "404 Endpoint not found" on upload (same day)

The user's running server process predated the free routes, so
`POST /api/studio/free/uploads` hit the catch-all 404. Hardening:

- `uploadFile` detects a 404 and tells the owner exactly that: "restart the
  DENNIS server and refresh this page" (a stale build cannot accept the new
  route).
- Asset version strings bumped to `?v=20261112-free2` for
  `studio.js` / `studioFashion.js` / `studioTemplates.js`, so browsers cannot
  serve stale workflow code.
- E2E gained a `phase_free_upload` that POSTs a real PNG through the browser
  (exactly the UI path) and reads it back — proving the no-credential upload
  pipeline; verified live: POST 200 → media served 200 image/png.
- E2E flakiness fixed: studio-visibility retries during app boot, and the
  console check now fails only on JS exceptions (network noise from stubs and
  external preset images is ignored).

## Ads Studio: image-model dropdown + instant attach previews + upload caching — 2026-11-12

Steps 1–3 image generation got explicit engine selection, visible-in-place
previews, and caching:

### Image-model dropdown (Steps 1, 2 and 3)

- Each step panel now has an "Image model (free engines)" dropdown, sharing
  one selection: **Auto (recommended)**, **Gemini 2.5 Flash Image** (labelled
  "— ready" or "— needs free key" from live capabilities), **Pollinations
  Flux — no key**, **Pollinations Turbo — no key (fast drafts)**.
- Explicit selections are honoured and explain themselves immediately:
  Gemini without a key → "add the free key or switch back to Auto";
  Pollinations with local uploads → "needs the Google key to edit uploads".
  (`chooseImageEngine` + backend `_run_image` routing; fixed an old bug where
  an explicit Pollinations engine could be hijacked to Gemini when a key
  existed.)

### Instant previews in the attach boxes

- Selecting/dropping a file now renders it **immediately from a local blob
  URL** with an "Uploading…" badge — model dropzone, product thumbs, Create
  Model reference chip and the Step 4 video dropzone. The thumb flips to the
  stored `/api/studio/media` URL when the upload lands; failures revert with
  the error toast. Multiple product files upload **in parallel**.

### Upload caching

- Server: `upload_once` dedupes by SHA-256 — identical bytes return the
  stored media instantly (verified live: 12 ms first, 3 ms cached, same id).
- Client: `S.uploadCache` (name+size+lastModified key) skips the POST
  entirely for re-selected files.
- Upload paths no longer trigger the heavy full-panel `syncUI`; they use
  targeted renders (model grid, thumbs, carried bar, dock).

### Verification

- `tests/test_studio_free.py` grew to 19 tests: turbo/flux/auto routing,
  explicit-Gemini-without-key hint, upload dedup, plus the previous suite.
- E2E: dropdowns asserted in Steps 1–3 (classified labels), plus all prior
  phases. 132 backend tests pass (only the pre-existing unrelated failure).
- Asset versions bumped to `?v=20261112-free3`.

## Provider registry + per-step Provider/Model dropdowns + Build agents — 2026-11-12

### Provider Setup is now the provider registry

- New `backend/studio_registry.py`: one registry for every generation
  provider — Auto (free engines), Pollinations (keyless), Google AI
  (Gemini + Veo), Higgsfield, fal.ai, Runway, Luma, MiniMax, Replicate.
- `GET /api/studio/registry` — providers, connection status (from stored
  credentials), credential field labels (never values) and honest notes
  (providers without a wired engine adapter say so).
- `GET /api/studio/generation/catalog` — per-provider **classified** models:
  image (Text → Image / Image Edit) and video (Text → Video, Image → Video,
  Reference → Video / Motion Transfer, …), each with the input it needs
  (`needs`: prompt / image / reference_video).
- `POST /api/studio/registry/credentials` (+ `/delete`, `/test`) — save,
  remove and validate any provider from Setup.
- Provider Setup renders the full registry: status dot per provider, expandable
  credential forms, Save/Test/Remove. Connected providers become selectable in
  the step dropdowns (verified live: both Google and Higgsfield show
  Connected from the user's stored keys; 16 image + 67 video Higgsfield
  models classified).

### Per-step Provider + classified Model dropdowns (Ads Studio)

- Steps 1, 2, 3: **Provider** + **Image model** dropdown pair; Step 5:
  **Provider** + **Video model** pair. Models render as `<optgroup>`s grouped
  by mode (the classification), so a Higgsfield video dropdown lists
  Text → Video / Image → Video / Motion Transfer groups.
- Routing follows the selection: free providers → `/api/studio/free/jobs`;
  Higgsfield image models → `/api/studio/jobs` with schema-mapped params;
  Higgsfield video models → `/api/studio/fashion/jobs` with the full draft.
- Model needs are validated before submitting: a Motion Transfer model
  without a Step 4 clip sends the user to Step 4; an Image → Video model
  without a model image sends them to Step 1. Free-engine constraints
  (Google key for Gemini/Veo, public refs for Pollinations) still explain
  themselves up front.
- The old single image-engine dropdown was replaced by the pair; the Step 5
  engine grid and Quick-Config model select stay in sync with the Step 5
  selection.

### Build agents (Ads + YouTube Studio)

- The Build dropdown now exists in **both** headers (Ads header and YouTube
  topbar) plus the Activity console, all driven by one agent list
  (`/api/agents/availability`) showing each agent's own provider · model.
- Separation enforced by design and labeled in the UI: **Build agents run
  tools/workflows** (their own provider + model); **generation uses the
  per-step Provider + Model dropdowns**. Choosing an agent syncs across all
  three hosts (verified live: 8 agents, pick in Ads header updates Activity
  and YouTube labels).

### Verification

- `tests/test_studio_registry.py` (17 tests): registry completeness, no
  secret leakage, connected flags, classification (needs per mode), free
  providers, credential save/delete/test.
- Ads Studio E2E extended: provider+model pairs in all four generation
  steps, connected providers listed (Google/Higgsfield/Pollinations), Step 5
  optgroup classification, and the Setup registry rendering all 9 providers.
  All checks pass; 149 backend tests (only the pre-existing unrelated
  failure), 3/3 JS suites.
- Asset versions bumped to `?v=20261112-registry` — restart the server and
  hard-refresh to pick everything up.

## 2026-11-12 — Workspace toolbar + Memory Galaxy + JARVIS home

Five user requests, all verified in a real browser
(`tests/e2e_2026_11_12_requests.py`, 21/21 checks) plus one live PTY E2E:

1. **Provider dropdown gains a Custom provider (FreeLLMAPI).**
   - `app.py` `/api/agents/<id>/models` now returns `custom_provider`
     (`dennis_freellmapi.status()` + `supported`).
   - `js/workspace.js` always appends a "Custom (FreeLLMAPI)" row (id
     `FreeLLMAPI`, matching the injected model's provider). Selecting it
     filters the model list to the FreeLLMAPI connection made in the sidebar
     FreeLLMAPI view; when not configured the menu stays open on the connect
     form (`renderFreeLLMAPIAuth`), unsupported agents (not claude-code/codex)
     get an honest note. Disconnect falls back to "All providers".

2. **Model switch actually switches the CLI model.**
   - Root cause: OpenCode's TUI takes no `--model` flag and its *shared
     background service* ignores per-client env/config (verified live:
     `OPENCODE_CONFIG` with `default_agent: plan` only applied with
     `--standalone`).
   - New `backend/model_launch.py`: per-agent launch plans. For OpenCode it
     writes `data/opencode-launch/<session>.json` (user's global opencode
     config merged + `"model": <id>`) and launches with `--standalone` +
     `OPENCODE_CONFIG`. For TUI-driven CLIs it generalizes the OpenClaw
     one-shot `model_input` into staged `model_input_steps`
     (`{at, text}` writes).
   - `AgentSession.start/stop` apply/cleanup the plan; openclaw migrated to
     steps. Live-verified: OpenCode footer shows "Build · Qwen3.8 27B Groq"
     after a switch (`tests/manual_opencode_e2e_model.py`).

3. **"New window +" sits beside the window session tabs.**
   - `#ws-windows` is now `flex: 0 1 auto` (shrinks to tabs, scrolls on
     overflow) with the button directly after it and a spacer after the
     button; menu opens `left-0`.

4. **Memory Galaxy is its own page** (sidebar entry under Vault).
   - `#memory-galaxy-view` + `js/memoryGalaxy.js`: vault graph
     (`/api/vault/graph`) as a cosmic force-layout canvas (pan/zoom/filter/
     re-spin), colors per folder, star size = link count.
   - **Preview bar sits ABOVE the workspace bar**: selected star's title,
     path, link count and a note excerpt (`/api/vault/note`), one-tap
     "Open in Vault" (`Vault.openPath`, new public API).
   - Workspace bar: Re-center, Re-spin, filter input, star/link stats.

5. **JARVIS is the default dashboard** (full home view).
   - `#jarvis-view` + `js/jarvisDashboard.js`: arc-reactor core canvas (tap =
     wake `window.hermesApollo`), voice-engine/agents/notes/sessions status
     cards, recent-memory list (opens notes in Vault), quick commands.
   - `switchMainView` fallback is now `jarvis`; `DOMContentLoaded` lands on
     it; sidebar has "JARVIS" (top) and "Memory Galaxy".
   - `docs/JARVIS_V2_AUDIT.md` records the Hermes Apollo audit (reuse/fix/
     remove) awaiting the user's pasted V2 guide.

### Audit fixes inside the Jarvis stack (same session)

- Removed dead `triggerHeaderTab` voice commands (`.header-nav-tab` no longer
  exists) and added jarvis/memory-galaxy/skill/plugins/freellm navigation
  commands.
- `DennisAgentBridge` now follows the live Workspace toolbar via
  `window.Workspace.voiceContext()` instead of the removed DENEVO chat panel's
  static state.
- Deleted repo-root TTS test leftovers (`jarvis_test.wav`, `s1/s3.pcm/.txt`,
  `s2.wav`, `st.txt`).
- `/favicon.ico` answers 204 (was 404 console noise in every session).

### Verification

- `tests/e2e_2026_11_12_requests.py --serve`: 21/21 (default view, preview
  bar placement + content, new-window geometry, Custom provider + connect
  form, no JS errors, no failed resources).
- `tests/manual_opencode_e2e_model.py`: model switch reaches the real CLI.
- `tests/test_backend.py` 82 tests OK; freellm/project-session route tests OK;
  `node --check` clean on all touched JS; asset versions bumped
  (`?v=20261112-jarvis-home`, `?v=20261112-customprov`, `?v=20261112-galaxy1`).

## Installed Plugins rail - 2026-11-12

New sidebar view **Installed Plugins** (`#installed-plugins-view`) lists every
installed app, agent skill pack and GitHub repo with a short tag and lifecycle
status. It is separate from the **Plugins** tab (API Mega List / Composio).

- **Backend `backend/plugins.py`:** `installed_apps()` merges live
  `~/.agents/plugins` folders, the bundled `_CATALOG`, and `_BUILTIN_APPS`
  (API Mega List, FreeLLMAPI). `_plugin_summary` now exposes `title`, `tag`,
  `icon`, `category`, `license`, `source_url`, `installed_at` and `skill_count`.
  `KNOWN_TAGS` + manifest `tag` / `x-dennis.tag` feed the short tags.
- **Endpoint:** owner-gated `GET /api/plugins/installed`; writes reuse
  `/api/plugins/install|toggle|install-skills|uninstall`.
- **Install from GitHub:** `_read_manifest` also accepts
  `.claude-plugin/plugin.json`; `_manifest_skills` understands string skill
  paths, `.claude/skills/` trees and `skills/` parents. Verified by installing
  the upstream `ui-ux-pro-max-skill` clone directly (7 skills detected).
- **Bundled pack:** `public-plugins/ui-ux-pro-max/` (10 MB) mirrors the upstream
  `.claude/skills/` tree at commit `6ae86f61f520` / version `2.15.0`; portable
  `plugin.json` declares 7 skills and `SOURCE.json` records provenance. The
  pack is installed in `~/.agents/plugins/ui-ux-pro-max` and appears as `UX`.
- **Frontend:** `js/installedPlugins.js` + `css/installed-plugins.css`:
  searchable tag-filtered card grid, summary counts, install-from-GitHub form,
  enable/disable, link-skills modal (per-agent checkboxes), uninstall, native
  app links and GitHub links. `js/app.js` routes `installed-plugins`; sidebar
  item carries a live installed count badge.
- **Tests:** `tests/test_plugins.py` updated for the bundled public catalog and
  `installed_apps()` tags/statuses — 10 tests OK. Playwright (installed Chrome)
  confirms 7 cards render, search/filter, install panel, and the link-skills
  modal (7 agent targets) with no JS errors.
