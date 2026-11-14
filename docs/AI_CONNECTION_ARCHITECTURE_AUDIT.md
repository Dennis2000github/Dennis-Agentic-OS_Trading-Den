# AI Connection Source & Free/Paid Routing — Phase 1 Architecture Audit

> Date: 2026-11-14 · Scope: unified AI connection architecture (Provider vs
> Gateway/Proxy routes), unified model registry, compatibility validation,
> free/paid routing. **No code was modified during this audit.**
>
> Evidence: every claim below was verified by reading the current source.
> Line numbers refer to the working tree at audit time.

---

## 1. Existing architecture map

```
index.html + js/workspace.js          Workspace UI
  Agent ▸ Provider (filter) ▸ Model   (three dropdowns, provider list is
        │                              DERIVED from the CLI's own model list)
        │
        ▼
app.py  (stdlib http.server, single owner token, ~5,100 lines)
  GET  /api/agents                     legacy registry (data/agents.json)
  GET  /api/agents/availability        merged: registered + chat + detected CLIs
  GET  /api/agents/<id>/models         per-agent model discovery (see §3)
  POST /api/project-sessions           create session
  POST /api/project-sessions/<id>/start          launch CLI (PTY or Docker)
  POST /api/project-sessions/<id>/model          validated model switch
  POST /api/project-sessions/active/model        legacy chat switcher (BROKEN, §5)
  GET/POST/DELETE /api/freellmapi      per-agent endpoint settings store
  GET  /api/freellmapi/catalog         static free-model catalog (vendored)
  POST /api/chat                       legacy one-shot chat via CLI subprocess
  GET/POST /api/deepseek/*             in-process OpenAI-compatible chat client
  GET/POST /api/deepseek-native/*      DeepSeek Web-UI process supervisor
        │
        ▼
backend/agent_registry.py             agent registry + CLI profiles + setup hints
backend/model_sources/*               per-agent model discovery (9 sources)
backend/session_manager.py            owns CLI processes (PTY/Docker), busy state
backend/model_launch.py               per-agent launch plans (OpenCode config file)
backend/freellmapi.py                 per-agent endpoint env injection (CC/Codex)
backend/deepseek_harness.py           in-process chat client (no PTY)
backend/hermes.py                     one-shot CLI orchestrator (plan/delegate)
backend/sandbox.py                    PtyProcess / Docker / ExecContext
backend/auth.py                       single owner token (data/auth.json)
```

**Persistence:** flat JSON files under `data/` (atomic writes, 0600 for
secrets). No database, no migration framework in the OS core. `data/` is
git-ignored.

## 2. Current implementation inventory

### 2.1 Agent registry (`backend/agent_registry.py`)

- Registry file: `data/agents.json`. Defaults seeded: `antigravity`,
  `cursor`, `openclaw`; builtin chat agent `deepseek-harness` (no binary,
  in-process). Detected host CLIs (10 probes): `claude-code`, `opencode`,
  `codex`/`codex-cli`, `gemini-cli`, `aider`, `goose`, `hermes-cli`,
  `cursor`, `openclaw`, `antigravity`.
- `CLI_PROFILES` define how each CLI accepts a model: startup flag
  (`--model`/`-m`), generated config (OpenCode), staged TUI typing
  (OpenClaw), or interactive-only (Claude Code; discovered by driving a
  short-lived PTY `/model` picker).
- `PROVIDER_SETUP` holds per-agent credential hints (`opencode auth login`,
  `cursor-agent login`, `freellmapi` form for claude-code/codex, Google
  sign-in for Antigravity, `api_key` form for DeepSeek Harness).
- Agents are **not** coupled to providers in the registry — provider
  knowledge is implicit in CLI flags + `freellmapi._AGENT_ENVS`.
- Live model switch: only `deepseek-harness` reports
  `supports_live_model_switch`; everything else restarts the CLI.

### 2.2 Model discovery (`backend/model_sources/`, exposed at
`GET /api/agents/<id>/models`)

| Agent id | Mechanism | Auth | Provider labels |
|---|---|---|---|
| `opencode` | `opencode models` (text) | CLI auth store | 516 models / 6 providers observed |
| `claude-code` | PTY `/model` picker scrape (brittle) | CLI login | Anthropic |
| `codex`/`codex-cli` | `codex debug models` (JSON) | CLI login | OpenAI |
| `antigravity` | `agy models` | Google sign-in | Google DeepMind |
| `cursor` | PTY `/model` picker | Cursor account | per-model prefix |
| `openclaw` | `openclaw models list --json` | CLI config | per-item |
| `hermes-cli` | `~/.hermes` cache files | `hermes model --refresh` | provider from config |
| `deepseek-harness` | static list (no network) | API key (env/file) | DeepSeek |

Records are normalized to `{id, name, description, is_default, available,
reason, provider}` (`model_sources/base.py:_norm`). **Discovery never
fabricates** (`ModelListError` on failure; cache TTL 300 s). Providers are
grouped from the CLI's own output — there is no independent provider
concept. On discovery failure the route falls back to the single
FreeLLMAPI-configured model, else 502 with `provider_setup`.

### 2.3 Execution path

Model selection → `POST /api/project-sessions/<id>/model` (validates against
a fresh CLI list; 409 confirm when busy; restart with read-back) →
`session_store.set_model` → `AgentSession.start()` applies the model via
profile flag / launch plan / TUI input → env = host + PATH augmentation +
`freellmapi.apply_agent_env` + launch-plan env → PTY (or Docker with a
secret allowlist) → WebSocket frames to the dashboard.

### 2.4 Connection-like stores today

| Store | File | Type | Notes |
|---|---|---|---|
| FreeLLMAPI per-agent settings | `data/freellmapi.json` (0600) | endpoint+key+model per agent | env injection for claude-code (`ANTHROPIC_*`) and codex (`OPENAI_*`) only when the configured model is selected |
| FreeLLMAPI catalog | vendored `public-plugins/free-llm-apis/README.md` | static free-provider catalog | parsed by `backend/freellm_catalog.py`; tiers `permanent`/`renewable` |
| DeepSeek credentials | `data/deepseek/credentials.json` (0600) | API key | `DEEPSEEK_BASE_URL` override supported |
| Studio provider credentials | `data/studio/credentials.json` (0600) | 7 media providers | separate registry, honest diagnostics |
| Legacy `data/models.json` | missing on disk | Model Registry panel source | `/api/models` returns `[]`; UI for it is dead |
| `data/providers_config.json` | orphaned | **no code references it** | placeholder keys; safe to delete (0644) |

## 3. Existing working integrations

1. **FreeLLMAPI settings → CLI env injection** — functional and tested
   (`tests/test_freellm_routes.py`). It is a *catalog + settings store*, not
   a proxy: the external CLI makes the request.
2. **Per-agent CLI model discovery + validated model switching** — the core
   workspace flow; covered by `tests/test_backend.py`.
3. **DeepSeek Harness (in-process API client)** — OpenAI-compatible SSE
   client, key redaction, busy locking; 23 tests.
4. **DeepSeek native Web-UI supervisor** (`:3080`) — process supervision +
   iframe; the OS never touches its credentials.
5. **Hermes orchestrator** — one-shot CLI sub-agents, Vault memory.
6. **Studio provider registry** — a good precedent for honest connection
   diagnostics (`ProviderError`, credential hints, no secret leakage).
7. **DenMa reverse proxy** — infra proxy only, not LLM routing.

## 4. Broken or incomplete integrations

| Item | Status | Evidence |
|---|---|---|
| `POST /api/agents/install` | **Broken**: `NameError: active_id` on every valid install request | app.py L3969 uses `active_id` before it is ever assigned in `do_POST` |
| `POST /api/agents/launch` | **Broken**: launcher script written with literal `\n` (backslash-n), whole file is one comment line; reports success anyway | app.py L4388–4392 (`f.write("...\\n")`) |
| `POST /api/project-sessions/active/model` + `/active/agent` | **Dead by design**: reads `active_session_id` from the legacy *chat* store (`sess-*` ids) and looks it up in the *project-session* store (`ps-*` ids) → always 404; callers ignore the response | app.py L4510–4546; also persists the model before restart and skips validation |
| `backend/model_sources/omniroute.py` | **Dead stub** that fabricates placeholder models (violates the package's no-fabrication contract); never registered in `_SOURCES` | omniroute.py L15–21; registry.py L22–32 |
| Dashboard "Model Registry" panel (Free/Paid filters, Prefer Free toggle) | **Decoration**: 5 hardcoded rows; toggle fires a toast only | index.html L551–648; app.js L892–925 |
| app.js model switcher + `data/models.json` | **Dead UI**: targets markup that doesn't exist; data file missing | app.js L404–598 |
| `js/deepseekChat.js` | **Orphaned** (not loaded by index.html); workspace.js still calls into it (silent no-ops) | index.html L4190; workspace.js L1171… |
| Model "read-back" after switch | **Theater**: `current_model` is set from the request itself, mismatch can never fire | session_manager.py L497 vs app.py L4626–4630 |
| 9Router | **External, unmanaged**: the user runs it manually at `localhost:20128` (Trading Den Claude settings); nothing in the OS starts/monitors/routes it | `trading_den/MMcloneLoopEngineering/.claude/setting.venv` |
| LiteLLM | **Missing** (no code) | — |
| OpenRouter | **Indirect**: appears only when the OpenCode CLI itself lists `openrouter/…` models; label exists in `_PROVIDER_LABELS`; no OS client, no `OPENROUTER_API_KEY` handling | registry.py L90; opencode.py |
| FreeLLMAPI `model_entry` | **Over-optimistic**: always `available=True, is_default=True` without a live check | freellmapi.py L181–193 |
| FreeLLMAPI connect form | **Triplicated** (workspace.js, skill-builder.js, freellmapi.js) | L1022/L502/L404 respectively |

## 5. Identified architectural limitations

1. **No connection abstraction.** Provider knowledge is scattered across
   CLI flags, `_AGENT_ENVS`, `PROVIDER_SETUP` hints and JS constants.
   Adding a gateway means touching backend + frontend + per-agent code.
2. **Model identity has no namespace.** A model id is just a string from
   the CLI; two sources (CLI default vs FreeLLMAPI override) can collide,
   and the current FreeLLMAPI integration resolves the collision by
   *replacing* the CLI entry with the same id.
3. **No unified model registry** with capability metadata, aliases,
   availability staleness, or discovery history. The legacy
   `data/models.json` registry is disconnected and unwritten.
4. **No compatibility validation.** Nothing prevents selecting a model a
   given agent cannot execute via a given protocol; availability in a list
   is the only signal (the spec explicitly forbids treating that as ready).
5. **No free/paid routing policy.** The "Prefer Free" toggle is cosmetic;
   no policy, no overrides, no paid-fallback authorization.
6. **No connection health model.** FreeLLMAPI entries are assumed
   available; no test/latency/status lifecycle.
7. **FreeLLMAPI supports only 2 agents** (claude-code, codex) via env
   injection; other CLIs cannot use a custom endpoint at all.
8. **Browser-based integrations** (DeepSeek Web UI) are correctly isolated
   as process supervision — they must not be forced into the API model.

## 6. Files and services requiring modification (proposed)

**New backend modules**
- `backend/connections.py` — connection source registry (provider +
  gateway), presets, CRUD, secrets (0600), connection test/health.
- `backend/model_catalog.py` — unified model registry: discovery per
  protocol, normalization, aliases, manual registration, staleness,
  history.
- `backend/compatibility.py` — agent protocol capability matrix +
  `Agent + Source + Connection + Protocol + Model` validation.
- `backend/routing.py` — free/paid policy, per-agent/task overrides, paid
  fallback authorization ledger, route resolution.
- `backend/execution_router.py` — resolves env/config per execution
  (bridges sessions ↔ connections; preserves `freellmapi` behaviour).

**Modified backend**
- `app.py` — new `/api/connections*`, `/api/compatibility*`, `/api/routing*`
  routes; extend `/api/agents/<id>/models` and the model-switch validation;
  fix the two broken routes found in §4 (documented, low-risk).
- `backend/agent_registry.py` — env augmentation delegates to the execution
  router (falls back to `freellmapi`).
- `backend/deepseek_harness.py` — optional per-request endpoint/key
  override hook (thread-local) so the in-process client can route through a
  connection.
- `backend/session_manager.py` — no structural change (it already applies
  launch plans + env); validation of connection-scoped model ids happens in
  the route layer.

**Frontend**
- `index.html` — Connection Source dropdown in the workspace toolbar;
  Connections manager view; routing preferences panel.
- `js/workspace.js` — source selector state + connection dropdown +
  unified model menu sections; persists per window/agent.
- `js/connections.js` — new manager view (add/edit/remove/test/discover,
  status, routing preferences).
- `css/style.css` — styles for the new dropdowns/cards.

**Tests**
- `tests/test_connections.py`, `tests/test_model_catalog.py`,
  `tests/test_compatibility.py`, `tests/test_routing.py`,
  `tests/test_connection_routes.py` (server-route tests on an ephemeral
  port, following `test_server_security.py`).

## 7. Proposed migration plan

1. **Additive first.** All new backend modules are opt-in: with no
   connections configured, every existing response is byte-identical.
2. **Connection-scoped model ids** (`conn:<connection_id>:<native_id>`)
   are recognizable by prefix; existing ids keep their meaning.
3. **FreeLLMAPI is preserved**, not replaced: its per-agent settings remain
   the source of truth and are *surfaced* as derived gateway connections
   (`freellmapi:<agent>`); editing them via the new UI delegates to the
   existing `freellmapi.set_config`.
4. **`/api/agents/<id>/models`** gains additive fields
   (`connection_sources`, `connections`); the `models` array keeps its
   shape; connection models are appended only when a connection exists.
5. **Model switching** accepts connection-scoped ids via the model catalog
   validation path; the FreeLLMAPI fallback and 409 confirm flow are
   untouched.
6. **DeepSeek Harness** gains an opt-in route override; without a
   connection binding it behaves exactly as before.
7. No destructive migrations: new stores are new files
   (`data/connections.json`, `data/model_catalog.json`, `data/routing.json`).
   `data/providers_config.json` (orphaned) is left in place and documented.

## 8. Backward-compatibility risks

| Risk | Mitigation |
|---|---|
| Frontend assumes `models[]` entries are CLI models | connection models carry `connection_id` + `source`; UI sections them; existing consumers ignore unknown fields |
| FreeLLMAPI env injection vs connection injection double-apply | execution router resolves exactly one binding per (agent, model): FreeLLMAPI wins for its exact configured model, connections handle `conn:` ids |
| Legacy chat switcher (`active/model`) broken | out of scope to resurrect its dead UI; the per-session route remains the validated path (documented) |
| Docker allowlist blocks new env vars | execution-router env vars join the same allowlist (`ANTHROPIC_*`, `OPENAI_*` already present) |
| Secrets exposure | new store follows the 0600 + redaction conventions; `status()` returns hints only; routes never echo keys |
| UI regression in Provider dropdown | the CLI-native flow is untouched when no Connection Source is chosen; localStorage keys are new |

## 9. Recommended implementation phases

- **Phase 2 — Connection Source Abstraction**: `connections.py`, presets,
  CRUD + test + health, persistence, FreeLLMAPI surface-as-derived.
- **Phase 3 — Unified Model Registry**: `model_catalog.py`, protocol
  discovery, normalization, aliases, manual registration, staleness.
- **Phase 4 — Compatibility Registry**: `compatibility.py` capability
  matrix + validation API; diagnostics.
- **Phase 5 — Workspace UI**: Connection Source selector, dynamic
  Connection dropdown, unified Model menu, compatibility/status chips.
- **Phase 6 — Unified Execution Router**: `execution_router.py`, env
  resolution in `agent_registry`, DeepSeek in-process override hook,
  model-switch validation for connection ids.
- **Phase 7 — Free/Paid Routing**: `routing.py`, preferences API, per-agent
  overrides, paid-fallback authorization; make the Prefer Free toggle real.
- **Phase 8 — Provider & Gateway Manager UI**: connections manager view +
  routing preferences.
- **Phase 9 — Integration & Regression**: full test suite run, integration
  checks, implementation report.

## Appendix A — Verified facts used by the design

- `claude-code` accepts any Anthropic-compatible endpoint via
  `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` (+ `ANTHROPIC_MODEL`,
  `ANTHROPIC_API_KEY=""`); `codex` via `OPENAI_BASE_URL` + `OPENAI_API_KEY`
  (verified by `freellmapi.apply_agent_env`, in use today).
- DeepSeek's in-process client resolves `DEEPSEEK_BASE_URL`/key **at
  request time** (`deepseek_harness.base_url()` L137, `api_key()` L145,
  consumed at L653) → per-request routing override is feasible.
- The Docker secret allowlist already forwards `ANTHROPIC_BASE_URL`,
  `ANTHROPIC_MODEL`, `OPENAI_BASE_URL` (sandbox.py L71–78).
- OpenRouter exposes an OpenAI-compatible `/api/v1` (documented in the
  vendored catalog; used only as reference data today).
- The user's local 9Router listens on `http://localhost:20128/v1` and is
  Anthropic-compatible (Trading Den Claude settings).
- Test conventions: stdlib `unittest`, `python3 -m unittest discover -s
  tests`, ephemeral-port server tests, `FakeCtx` for model sources.
