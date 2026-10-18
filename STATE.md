# STATE

_Last updated: 2026-10-17_

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
   UI never sees it: there is no `/api/system/readiness` handler in `server.py`
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
- `server.py` - `/api/system/readiness`, file-tree endpoint, session `current_model`
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

### Backend (`backend/`, wired into `server.py`)

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
  (ignore paths), `backend/sandbox.py` (UTF-8 locale defaults), `server.py`
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
Trading Den iframe served that page from the **OS** origin (`server.py`), which
has none of DenMa's routes - `/api/history/spread-candles`, `/api/live-snapshot`
and `/ws` all returned 404 while `/api/status` hit the OS's own endpoint.

Fix: the dashboard runs on its own origin instead of being embedded.

- `backend/denma.py` (new) - supervisor: finds a Python with `ccxt`/`aiohttp`/
  `aiodns` (`DENMA_PYTHON`, else `trading_den/.../.venv`, else
  `~/PycharmProjects/MarketMaking/.venv`, else `sys.executable`), spawns
  `app.py --host 127.0.0.1 --port 8000`, health-checks, restarts, stops on exit.
- `server.py` - auto-starts the backend on launch (`DENNIS_NO_DENMA=1` disables),
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
- `tests/e2e_workspace.py` - starts `server.py` with `DENNIS_NO_DENMA=1`.

Config: `DENMA_HOST`/`DENMA_PORT` (default `127.0.0.1:8000`), `DENMA_PYTHON`.
Verified: OS auto-started the backend in ~6 s; through the OS port `/api/status`,
`/api/markets`, `/api/live-snapshot`, `/api/history/spread-candles`,
`/api/history/pnl`, `/api/screener`, `/api/triangular` all 200 and `/ws`
handshakes 101 (stable over time). `tests/test_backend.py` 38 passed, JS 23 + 8
passed.

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
- `server.py` - endpoints `GET /api/project-sessions/<id>/run` (status),
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
    `server`/`_meta` payload (remotes -> http transport, packages ->
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
- `server.py` - endpoints (all owner-authenticated): `GET /api/mcp/servers`,
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

## Security notes

- `~/.claude/settings.json` holds a live `ANTHROPIC_AUTH_TOKEN`; it is never sent
  to the browser or logged. Only an allowlisted set of env vars reaches agents.
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

## Next steps

1. If Docker becomes available, run `scripts/build_agent_images.sh` and
   `DENNIS_SANDBOX=docker` and verify the container path end to end.
2. Consider accessing the Model Registry as an additional `ModelSource`
   (the dropdown already depends only on the interface).
3. Persist the last selected agent per owner; add WS auto-reconnect with backoff
   if the backend restarts while a session is open.
