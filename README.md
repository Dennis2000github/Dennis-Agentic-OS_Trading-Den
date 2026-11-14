# DENNIS Agentic OS

> **Your AI Brain. Your Tools. Your Universe.**  
> An Agentic Operating System frontend based on the `dennis_agentic_os_v5.png` architecture mockup.

---

## Desktop app

The browser version remains available at http://localhost:8080. To run DENNIS
in its desktop shell, install Node.js and Python, then run npm install followed
by npm run desktop from the repository root.

The desktop shell starts app.py on loopback and opens the main DENNIS window.

## 🚀 Overview

**DENNIS Agentic OS** provides a cyber-futuristic dashboard interface for orchestrating autonomous coding agents, managing LLM model providers, tracking central credentials, and visualizing memory graphs.

### Key Components:
1. **Top Header & Navigation**:
   - Brand logo with cyan-to-magenta geometric wing emblem.
   - User profile dropdown (`Dennis Manosa`) and plan status badge.
2. **Left Navigation & Hermes Core**:
   - Quick sections: `Workspace`, `Studio`, `Trading Den`, `Vault`, `Settings`.
   - **HERMES Apollo** widget with real-time reactive spinning galaxy vortex, voice wake command ("Hey Apollo"), and Jarvis-like voice assistant.
3. **Hero Dashboard (Top Banner)**:
   - Dynamic real-time digital clock and localized date greeting.
   - Interactive **Neural Brain Cosmos Canvas** with constellation nodes, synapsing electrical pulses, and pinned 3D document badges (`project-plan.md`, `research.md`, etc.).
4. **Middle Studio Tier**:
   - **DENEVO Agent Chat**: Multi-agent conversational panel, step-by-step task breakdown accordion, integration badges (Claude Code, GitHub, Node.js), message input, microphone, and orchestrator selectors.
   - **Project IDE & Live Preview**: File tree navigator with dynamic syntax-highlighted code editor and an in-app live browser preview screen.
5. **Bottom Management Grid**:
   - **Agent Registry**:
     - Dynamic discovery of host system agents (`Antigravity`, `Claude Code`, `OpenCode`, `Codex CLI`, `Hermes Agent`, `Cursor`, `OpenClaw`) plus the DeepSeek Harness launcher for its official native Web UI.
     - **In-OS Installer Engine**: Run installation commands directly within the OS terminal for uninstalled catalog agents (e.g. `pip install aider-chat`, `npm install -g @google/gemini-cli`).
     - **Pre-Flight Diagnostics**: Real-time executable testing (`--version`) with exit code checks to prevent false/misleading statuses.
     - **Active Agent Switching**: Dynamic binding with DENEVO Chat (`Agent Mode` switcher) allowing seamless handoffs between orchestrators.
   - **Model Registry**: Filterable by `All` / `Free` / `Paid` with a "Prefer Free" toggle.
    - **Framework Router**: LangChain, LangGraph, LlamaIndex, CrewAI.
   - **Quick Tools**: Shortcuts to Obsidian, Graphify, Repository Search, Terminal, and New Session.
6. **Footer Bar**:
   - System metadata architecture summary and memory layer links.

### Agent Skill Builder

The existing **Agent Skill Builder** tab turns sources into reviewable
`SKILL.md` files:

- **Source → Skill**: YouTube tutorial transcripts and targeted GitHub
  repository documentation (README/AGENTS/docs/manifests; source code is never
  executed).
- **Video Reverse Engineering**: Tutorial → Skill or Reference video → Skill.
  YouTube is analysed in place with Gemini; direct files use the Files API.
  Reference mode supports an optional character image and emits reusable
  Seedance 2.5/compatible prompt templates. No provider is assumed connected.
- Every run produces a draft. The shared **Review Generated Skill** screen lets
  you edit the name, description and full skill, then **Reject** or **Add to
  Vault**. Publishing validates fields, refuses silent conflicts, writes
  atomically to `vault/Skills/<name>/SKILL.md`, verifies the file and reports the
  actual path.

See [`SKILL_BUILDER_AUDIT.md`](SKILL_BUILDER_AUDIT.md) for the audit, baseline
and post-optimization measurements, test results and remaining limitations.

### Studio (generation console)

The **Studio** tab (top header; no sidebar entry) submits real generation jobs
to Higgsfield:

- **Official API is the default.** Add `API key` + `Secret` under
  **Studio → Setup**, or provide `HIGGSFIELD_API_KEY` / `HIGGSFIELD_SECRET` in
  the backend environment. Credentials are stored server-side (`data/studio/`,
  mode `0600`), never returned to the browser and never written to logs.
- **Pinned catalog.** `backend/catalog/higgsfield-0.1.0.json` contains 84
  documented image/video workflows and their full input schemas. The runtime
  never fetches documentation. Regenerate with
  `python3 scripts/build_higgsfield_catalog.py`; see
  [`backend/catalog/README.md`](backend/catalog/README.md).
- **Schema-driven composer.** Provider and model dropdowns drive a form built
  from the selected model's documented parameters. Unknown or invalid fields
  are rejected by the backend rather than silently dropped.
- **Real job states.** Activity shows `queued` / `running` / `succeeded` /
  `failed` / `cancelled`, plus elapsed time. The official API does not report
  progress, so Studio deliberately shows no percentage or ETA. Only queued
  requests can be cancelled.
- **Local media library.** Finished outputs are downloaded (SSRF-guarded,
  size-capped) into `data/studio/media/` and served only to the owner, with
  Range support for video playback. Remote provider outputs can also be
  explicitly imported into the local library.
- **Three-column workspace.** The Activity tab keeps the composer, the live
  stage (single output plus real provider variations), and the recent-job rail
  together. Prompt presets, input modes, model details, quick actions and a
  local usage panel make the documented model schema easier to explore.
- **Playlists and templates.** Named playlists and reusable generation templates
  persist server-side under `data/studio/` (`0600`, git-ignored). Templates store
  the provider, model and validated parameters only, never credentials.
- **Honest credits.** Studio shows local job and library counts. Provider
  credits, per-job cost and completion time are not exposed by Higgsfield's
  API, so Studio does not invent them.
- **Agent tools.** `GET /api/studio/tools` and `POST /api/studio/tools/call`
  expose read-only tools directly; mutating tools (submit/cancel) create an
  owner approval first. `scripts/studio_mcp_shim.py` bridges the same surface
  to MCP clients over stdio.
- **Optional MCP provider.** A configured `higgsfield-mcp-unified` stdio server
  can be used as an experimental secondary provider. It is never the default.
  The community project's experimental web backend (browser cookies /
  Cloudflare) is intentionally not used. Note: the upstream MCP server may
  ignore model-specific parameters it does not implement; the official API
  path validates every parameter strictly.
- **MCP bridge.** `python3 scripts/studio_mcp_shim.py` exposes Studio's tools to
  an MCP client over stdio. It reads the owner token from `data/auth.json` (or
  `DENNIS_TOKEN`) and talks to `DENNIS_API_URL` (default
  `http://127.0.0.1:8080`). Mutating tools still require owner approval.

Real generation requires valid Higgsfield credentials and available credits.
The test suite uses mocks and a pinned catalog; it never spends credits.

### DeepSeek Harness (official native Web UI)

Selecting **DeepSeek Harness** in Workspace launches the official
`@deepseek-ai/dsh` Web UI as a local service and embeds it in the Workspace
screen. The OS supervises the process; the Harness application owns its chat,
sessions, settings, model configuration, and native plugin manager. The OS does
not recreate those screens.

- Requires Node.js/npm (`npx`) on the host. On first launch, `npx` downloads
  and runs the official package; launch can take up to 75 seconds.
- The UI uses the upstream default `http://127.0.0.1:3080/`. The OS keeps the
  launch URL printed by Harness, including its launch credential, for the
  embedded view. If another process already serves that address, the OS reuses
  it.
- Choose/add a workspace in Harness before creating sessions; Harness requires
  a selected workspace for its session composer.
- The launcher API is owner-authenticated: `GET /api/deepseek-native/status`
  and `POST /api/deepseek-native/start`.
- The prior OS-built chat panel is no longer loaded. The separate OS Plugins
  section remains for API Mega List and Composio; Harness plugins are managed
  in the Harness UI itself.

Upstream launch documentation: [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).

### Workspace provider dropdown

The Workspace toolbar lists **Agent → Provider → Model**. The Provider dropdown
is derived from what the selected agent's CLI actually reports — for OpenCode
each `provider/model` id contributes its provider (OpenAI, OpenRouter, Ollama
Cloud, …), Hermes contributes its configured provider, and single-provider CLIs
show one entry. Nothing is hardcoded or invented: `GET
/api/agents/<id>/models` returns both `providers` (id, label, model count,
availability) and the `models`; a provider is only listed if a model named it.

- **Switch provider.** Picking one filters the Model list to that provider and
  opens it so you can pick the model. "All providers" shows everything. The
  chosen provider is remembered per agent in `localStorage`; opening a session
  derives it from that session's current model.
- **Configure provider.** Because provider credentials belong to each CLI, the
  menu shows that CLI's own setup command with a Copy button (`opencode auth
  login`, `cursor-agent login`, `openclaw configure`, `codex login`, `hermes
  model --refresh`, `claude` → `/login`, or the DeepSeek key card). After
  configuring, press Refresh in the same menu.
- **Honest errors.** If a CLI cannot list models (untrusted folder, logged out,
  missing Hermes cache), the same menu shows the actionable error and the setup
  hint instead of a fake provider list.
- **Same control in the Skill Builder.** The authoring toolbar uses the same
  Agent → Provider → Model order and the same `/models` payload, so the agent
  that writes a skill is chosen and filtered the same way.

### Parallel workspace windows and project names

The window bar above the Workspace toolbar holds one tab per project session.
Each tab is an independent window with its own **Agent → Provider → Model** and
its own CLI process, so several sessions can keep running at once.

- **Add a window.** Press **+ New window** and pick an agent. The new window
  creates a project session, then opens its provider and model menus so you can
  choose before pressing **Start session**. The previously active window keeps
  running untouched.
- **Switch windows.** Selecting a tab swaps the toolbar context and shows that
  session's live dashboard; sessions in other windows are never stopped or
  reloaded.
- **Close a window.** Closing a tab only removes the view. The session and its
  process stay available from the Sessions list, where they can be reopened in
  the active window.
- **Rename a project.** The pencil next to any session name edits the display
  title inline (`PATCH /api/project-sessions/<id>`, 120-character limit). The
  title updates the sidebar row and the window tab; the project folder on disk
  is never renamed.
- **Persistence.** Open windows, their sessions and the active tab are stored in
  `localStorage` (`dennis.workspace.windows`) and restored on reload.

### Cursor and OpenClaw requirements

These agents are optional and are shown in the dropdown even before their CLI
is installed. Install and authenticate them on the host when using the local
runner:

- Cursor: install with `curl https://cursor.com/install -fsS | bash`, then run
  `cursor-agent login`.
- OpenClaw: install with `curl -fsSL https://openclaw.ai/install-cli.sh | bash`,
  then run `openclaw configure` and ensure at least one model is configured.
  OpenClaw requires Node.js 24.22.1+ or 26.12.1+.

For Docker sessions, build their images with
`scripts/build_agent_images.sh cursor openclaw` (or build all images with no
arguments). Cursor models are read from its interactive `/model` picker;
OpenClaw models/providers come from `openclaw models list --json`.

---

## 📁 File Structure

```
DENNIS Agentic OS/
├── index.html               # Main dashboard UI (incl. Hermes orchestrator + Kanban board)
├── css/
│   └── style.css            # Cyber-glassmorphism styles, glows, scrollbars & syntax theme
├── js/
│   ├── app.js               # Application state, clock, file tree switcher, chat & modals
│   ├── cosmos.js            # HTML5 Canvas neural brain constellation particle simulation
│   ├── vault.js             # Vault: markdown tree, editor, preview, wikilinks & graph
│   ├── kanbanState.js       # Pure Kanban column/status logic (node-testable)
│   ├── kanban.js            # Hermes board renderer + orchestrator API client
│   ├── hermes.js            # In-OS Hermes orchestrator panel
│   ├── plugins.js           # Plugins view: upstream API Mega List repository workspace
│   ├── installedPlugins.js  # Installed Plugins rail: apps, packs, tags, GitHub install
│   ├── composio.js          # Local Composio Dashboard shell and native API controls
│   ├── studio.js            # Studio generation console controller
│   ├── studioState.js       # Pure Studio helpers (node-testable)
│   ├── deepseek_native.py   # Supervisor for the official DeepSeek Harness Web UI
│   └── galaxy.js            # HTML5 Canvas Hermes galaxy vortex + Apollo voice core
├── app.py                   # HTTP/WebSocket server, Studio/agent/vault APIs
├── assets/
│   └── studio/              # Studio mark (mask-safe SVG) + colour icon (SVG/PNG)
├── scripts/
│   ├── build_higgsfield_catalog.py  # regenerate the pinned Studio catalog
│   └── studio_mcp_shim.py           # expose Studio tools to MCP clients
├── backend/
│   ├── vault.py             # Obsidian-compatible markdown vault (Hermes second brain)
│   ├── plugins.py           # Agent-agnostic plugin manager (open Agent Skills standard)
│   ├── composio.py          # Composio plugin installer and optional local adapter APIs
│   ├── hermes.py            # Orchestrator: plan/route/delegate sub-agents + Kanban state
│   ├── studio_*.py          # Studio catalog, contracts, providers, jobs, media, tools, MCP
│   ├── deepseek_harness.py  # Native DeepSeek chat: key storage, history, SSE streaming
│   └── catalog/             # pinned Higgsfield model snapshot
├── vault/                   # Markdown vault (incl. Hermes/ plans, runs & memories)
└── dennis_agentic_os_v5.png # Reference architecture mockup
```

---

## ⚡ How to Run

Launch the local server:

```bash
python3 app.py 8080
```

Open [http://localhost:8080](http://localhost:8080) in your browser.

### Trading Den (embedded)

The **Trading Den** (`trading_den/MMcloneLoopEngineering`) is embedded in the OS and
runs as part of the **same server** — one process, one port, one origin:

- The DenMa engine is hosted in-process by `app.py` on a private, ephemeral
  loopback port; its REST API and `/ws` feed are bridged through the OS port.
- On first launch the OS re-executes itself under an interpreter that has the
  DenMa dependencies (`ccxt`, `aiohttp`, `aiodns`) so everything stays in one
  process. Install them with
  `pip install -r trading_den/MMcloneLoopEngineering/requirements.txt`, or point
  `DENMA_PYTHON` at an interpreter that already has them.
- If no such interpreter is found, the engine transparently falls back to a
  supervised child process on `127.0.0.1:8000` (still proxied through the one OS
  origin). Set `DENNIS_NO_DENMA=1` to disable the Trading Den entirely.

### Vault (Obsidian-compatible)

The **Vault** sidebar section is a self-contained Markdown knowledge base built
into the OS:

- Notes are plain `.md` files stored in `vault/` (change with `DENNIS_VAULT_DIR`)
  with any folder layout. They open in Obsidian unchanged.
- Features: folder tree, CodeMirror Markdown editor, live preview, `[[wikilinks]]`
  (create-on-click for missing notes), backlinks, full-text search and an
  interactive link **Graph** (also reachable from the *Graphify* quick tool).
- Backed by `backend/vault.py` + `/api/vault/*`; all paths are confined to the
  vault and deletes are soft (moved to `vault/.trash/`).

### Hermes (orchestrator agent)

**Hermes** is the first-class orchestrator. It turns a goal into work performed
by the registered CLI **sub-agents**, and uses the **Vault as its second brain**:

- Pipeline: `goal -> plan (decompose) -> route (assign roles) -> delegate
  (parallel fan-out) -> aggregate -> remember`.
- `backend/hermes.py` owns the state (`data/hermes.json`), role routing and the
  subprocess fan-out; plans, results and memories are written as Markdown notes
  under `vault/Hermes/` (`Plans/`, `Runs/` and free-form memories).
- A goal is decomposed into a five-role pipeline (research, plan, implement,
  verify, document). In **Autonomous** mode Hermes assigns each role to the
  least-loaded available sub-agent (and keeps *verify* off the implementer).
- Planning brain: `vault` (default, deterministic + recalled Vault context) or
  `cli` (route the goal through an installed sub-agent CLI in print mode).
- Endpoints: `GET /api/hermes/state|tasks|runs|agents|memory` and
  `POST /api/hermes/plan|run|delegate|tasks`, plus a live per-task WebSocket at
  `/api/hermes/ws?task=<id>`.
- **Kanban board**: embedded full-width in the OS **Hermes** view (open it from
  the sidebar or via the Apollo voice core). Tasks are draggable between the
  `Backlog / Assigned / Running / Review / Done` columns, can be assigned to a
  sub-agent manually or left **Autonomous**, and stream their CLI output live.

### Plugins (API Mega List + Composio Dashboard)

The **Plugins** sidebar section has two tabs: the real
[`cporter202/API-mega-list`](https://github.com/cporter202/API-mega-list)
repository workspace and a local Composio Dashboard shell backed by the
existing owner-gated Composio adapter.

The API Mega List tab is not a separate API catalog. It renders the
repository's own READMEs and files, including the output of its `settings/`
pipeline.

- **Source of truth:** `public-plugins/api-mega-list/repo/` is an upstream
  snapshot; `SNAPSHOT.json` records the commit and sync time. There is no
  generated second index and no custom Apify runner.
- **Browse:** repository tree for the main README, all 23 core category
  READMEs, the featured collection, sponsored partners, and the pipeline
  scripts. Inline links in the READMEs open the corresponding file in the
  workspace; external links open the provider.
- **Read:** GitHub-flavoured Markdown is rendered with the vendored
  `vendor/marked.min.js`; inline HTML is sanitised and relative assets are
  served through `/api/megalist/asset`. Large READMEs open as a preview with a
  **Render full file** action.
- **Pipeline:** `settings/fetch_apify_actors.js` →
  `settings/generate_readme_clean.js` → `settings/validate_partner_collections.js`
  are shown as repository files. The workspace reads their generated output;
  run the pipeline in the repository itself when you want to regenerate it.
- **Sync:** **Sync upstream** downloads the latest GitHub tarball, validates
  every archive member, and atomically replaces the snapshot. It does not
  execute repository code.
- **Backend:** `backend/megalist.py` behind owner-token-gated
  `GET /api/megalist/status|tree|file?path=…|asset?path=…` and
  `POST /api/megalist/sync`. Use `python3 scripts/sync_api_mega_list.py` for the
  same sync from a terminal.
- **Start fresh:** previous personal skill packs remain backed up at
  `~/.agents/plugins_backup_2026-10-23`; the old first-party catalog entry and
  its installed skill links were removed.

### Installed Plugins (apps, skill packs and GitHub repos)

The **Installed Plugins** sidebar rail is the registry view for everything the
OS has installed or bundled. It is separate from the **Plugins** tab (the API
Mega List / Composio console) and from **FreeLLMAPI**; each card links back to
its native app when one exists.

- **One list, short tags:** every app, skill pack and GitHub repo carries a
  short tag (`UX`, `MCP`, `API`, `LLM`, `REV`, …) plus a lifecycle status:
  `Installed`, `Disabled`, `Built-in`, `Bundled` or `Available`.
- **Live merge:** `GET /api/plugins/installed` merges the real folders under
  `~/.agents/plugins`, the bundled catalog in `public-plugins/`, and the apps
  shipped with the OS (API Mega List, FreeLLMAPI). Installed packs are read
  from their manifest, so new folders appear without a code change.
- **Install from GitHub:** paste a repository URL (or a local folder) in the
  rail. The backend clones with `git clone --depth 1`, detects a root
  `plugin.json`, a Claude `.claude-plugin/plugin.json`, `skills/*/SKILL.md` or
  `.claude/skills/*/SKILL.md`, then installs the pack under
  `~/.agents/plugins/<name>`.
- **Actions:** install a bundled pack, enable/disable, link its skills into
  selected agents (Claude Code, Codex, Cursor, Gemini CLI, OpenCode, Hermes,
  generic `~/.agents/skills`), open the native app, open the GitHub repo, or
  uninstall (removing its agent skill links).
- **Bundled packs:** Composio (`MCP`), REA (`REV`) and
  [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)
  (`UX`, 7 skills: orchestrator, banner design, brand, design, design system,
  slides, UI styling). API Mega List (`API`) and FreeLLMAPI (`LLM`) are built-in
  apps rather than agent-skill packs.
- **Bundled source:** `public-plugins/ui-ux-pro-max/` is a portable copy of the
  upstream `.claude/skills/` tree; `plugin.json` and `SOURCE.json` record the
  version, commit and license. Reinstall it with
  `POST /api/plugins/install {"catalog_id":"ui-ux-pro-max"}`.
- **Endpoints:** `GET /api/plugins/installed`; the install/toggle/link/uninstall
  actions reuse the owner-gated `POST /api/plugins/*` routes.
- **Frontend:** `js/installedPlugins.js` + `#installed-plugins-view`, styled by
  `css/installed-plugins.css`; `js/app.js` routes the
  `installed-plugins` view and sidebar label.

#### Composio

The **Composio Dashboard** tab provides local Dashboard-style views for
Overview, Toolkits, Connections, MCP clients, and Setup. It preserves toolkit
catalog loading, toolkit tool inspection, connected-account visibility,
credential storage, MCP preview/wiring with backups, plugin-pack installation,
and CLI/SDK runtime checks through `backend/composio.py`.

The **Open native dashboard** action still opens the official authenticated
Dashboard at [`dashboard.composio.dev`](https://dashboard.composio.dev) for
Platform / For You features not exposed by the local adapter.

The portable plugin pack remains in `public-plugins/composio/` for agent-side
installation and MCP configuration outside the Dashboard.

### FreeLLMAPI (free model catalog + per-agent connections)

The **FreeLLMAPI** sidebar section is a local dashboard over
[`open-free-llm-api/awesome-freellm-apis`](https://github.com/open-free-llm-api/awesome-freellm-apis),
the directory behind [`freellm.net`](https://freellm.net). It lists 505 free
models from 30 providers with search, tier/credit-card/modality filters, base
URLs, API-key links, context windows, rate limits, and the top models by weekly
usage.

- **Source of truth:** `public-plugins/free-llm-apis/README.md`, a snapshot
  recorded in `SNAPSHOT.json` (commit + sync time).
  `python3 scripts/sync_free_llm_apis.py` refreshes it from a local checkout;
  `DENNIS_FREELLM_REPO` can point at another checkout.
- **Backend:** `backend/freellm_catalog.py` parses the repository's own
  generated Markdown tables behind owner-gated `GET /api/freellmapi/catalog`.
  No repository code is executed and nothing is scraped per request.
- **Connect:** choose a provider/model, paste the provider key, and **Save
  connection**. The dashboard writes the existing per-agent FreeLLMAPI settings
  through `POST/DELETE /api/freellmapi` (`data/freellmapi.json`, owner-only) for
  Claude Code or Codex CLI, then the model appears in that agent's model menu.
- **Tests:** `python3 -m unittest tests.test_freellm_catalog tests.test_freellm_routes`.
