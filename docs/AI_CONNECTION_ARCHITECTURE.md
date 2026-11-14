# Unified AI Connection Source & Free/Paid Routing — Implementation Report

> Date: 2026-11-14 · Updated 2026-11-15 for the flattened Agent + Model
> workspace UI and the restored free FreeLLMAPI setup.
> Companion to `docs/AI_CONNECTION_ARCHITECTURE_AUDIT.md` (Phase 1 audit).
> This report covers Phases 2–9: what was implemented, what is tested,
> what is partial, and what remains.

---

## 1. Architecture before and after

**Before** (verified in the audit): provider knowledge was scattered —
per-agent CLI model discovery (`backend/model_sources/`), one settings
store that could point Claude Code / Codex at a custom endpoint
(`backend/freellmapi.py`), an in-process DeepSeek client, and a hardcoded
client-side "Custom (FreeLLMAPI)" provider row. No connection concept, no
unified model registry, no compatibility validation, no free/paid policy
(the dashboard "Prefer Free" toggle fired a toast only).

**After**: a centralized, extensible AI connection layer, additive to
everything that existed:

```
Workspace UI
  Agent ▸ Model   (one merged list; picking a model picks its route)
        │
        ▼
app.py  /api/connections*  /api/compatibility*  /api/routing*
        /api/agents/<id>/models  (+ additive connection fields)
        /api/project-sessions/<id>/model  (validates conn: ids)
        /api/deepseek/...  (routes in-process chat via connections)
        │
        ▼
backend/connections.py        Connection registry (provider|gateway),
                              presets, 0600 store, live health probes
backend/model_catalog.py      Unified model registry: conn:<id>:<native>
                              ids, discovery, aliases, manual models, staleness
backend/compatibility.py      Agent × protocol capability matrix + evaluation
backend/routing.py            Free First / Paid First / Manual, overrides,
                              paid-fallback authorization ledger
backend/execution_router.py   Route resolution → env vars / in-process config
        │
        ├── agent_registry.get_augmented_env  → session CLI env injection
        ├── session_manager.AgentSession.start → native model id for argv/TUI
        └── deepseek_harness.request_override  → in-process API routing

Existing integrations preserved: FreeLLMAPI (surfaced as derived gateway
connections), CLI-native model discovery, all agent dashboards/terminals,
DeepSeek native Web UI, Hermes orchestrator, Studio.
```

Key architectural principles implemented:

- **Provider and Gateway remain separate internal connection types, but the
  user does not pick a source.** The Model dropdown merges every compatible
  route into one list, grouped by category; selecting a model selects its
  route. Exactly one route is active per session.
- **Model identity is namespaced** (`conn:<connection_id>:<native_id>`) so
  gateway ids can never collide with CLI ids while the runtime always gets
  its native id verbatim.
- **Compatibility is capability-based**, not list-based: a model is only
  "compatible" when a documented, validated agent↔protocol path exists,
  credentials resolve, the endpoint is healthy, and the model is registered
  on the connection.

## 2. Files created

| File | Purpose |
|---|---|
| `backend/connections.py` | Connection registry: provider/gateway records, presets, CRUD, secrets (0600), live endpoint tests, health cache |
| `backend/model_catalog.py` | Unified model registry: namespaced ids, protocol discovery, normalization, aliases, manual registration, staleness, history |
| `backend/compatibility.py` | Agent protocol capability matrix + `Agent+Source+Connection+Protocol+Model` evaluation |
| `backend/routing.py` | Free/Paid policy, per-agent/task overrides, paid-fallback authorization, route resolution, fallback plans |
| `backend/execution_router.py` | Env resolution for connection models, FreeLLMAPI passthrough, native-id translation, DeepSeek in-process override |
| `js/connections.js` | AI Connections manager view (providers, gateways, routing policy, add/edit/test/discover) |
| `css/connections.css` | Styles for the connections view (follows existing conventions) |
| `tests/test_connections.py` | 13 tests: CRUD/validation, redaction, derived FreeLLMAPI, live probes (fake upstream) |
| `tests/test_model_catalog.py` | 7 tests: ids, normalization, discovery, staleness, manual models, aliases |
| `tests/test_compatibility.py` | 11 tests: matrix evaluation, auth-required, incompatible, not-tested honesty |
| `tests/test_routing.py` | 12 tests: modes, overrides, paid authorization, fallback plans |
| `tests/test_execution_router.py` | 13 tests: env injection, FreeLLMAPI unchanged, native ids, DeepSeek override |
| `tests/test_connection_routes.py` | 9 HTTP tests: gating, CRUD, test/discover, compatibility, routing endpoints |
| `tests/e2e_connections_smoke.py` | Real-Chrome checks: Agent + Model toolbar (no Source/Provider), Manage connections, AI Connections manager view, modal, save/remove round-trip, zero console errors |
| `tests/e2e_freellm_user_flow.py` | Real-Chrome end-to-end: flattened Model list → Via FreeLLMAPI saved model → isolated temp-session switch |
| `tests/e2e_unified_models.py` | Real-Chrome + mock Anthropic gateway: gateway models appear in the merged dropdown with chips, native ids in tooltips, and a working backend switch |
| `docs/AI_CONNECTION_ARCHITECTURE_AUDIT.md` | Phase 1 audit deliverable |

## 3. Files modified

| File | Change |
|---|---|
| `backend/config.py` | `write_json(..., mode=)` — explicit permission control for secrets stores (backward compatible) |
| `backend/agent_registry.py` | `get_augmented_env` now routes through `execution_router.apply_env` (falls back to FreeLLMAPI for its models, passthrough otherwise) |
| `backend/session_manager.py` | Connection-scoped ids translated to native ids for CLI argv/config/TUI input; `RoutingError` fails loudly instead of falling back; `get_or_create` refreshes stale `model_id`/`cli_session_id` (audit bug #5) |
| `backend/deepseek_harness.py` | Thread-local `request_override` context (endpoint/key/model per request) + `record_as` persistence; zero behaviour change without an override |
| `app.py` | New route sections (connections/compatibility/routing); extended `/api/agents/<id>/models` and model-switch validation; DeepSeek chat routing hook; **audit fixes**: `/api/agents/install` NameError, `/api/agents/launch` escaped-`\n` launcher script |
| `index.html` | Workspace toolbar reduced to Agent + Model (no Source/Provider dropdowns); Manage connections entry in the model menu; AI Connections sidebar entry + manager view; script/CSS wiring |
| `js/workspace.js` | Flattened unified model renderer: merges FreeLLMAPI, provider/gateway and CLI models into one grouped list; route-labelled selection; connection-scoped ids persist per window |
| `js/app.js` | `connections` view registration |
| `README.md` | AI Connections feature summary |
| `STATE.md` | Architecture state section |

## 4. Database migrations

None required — the OS has no database (verified in the audit). Three new
JSON stores were introduced alongside the existing ones, all written
atomically:

- `data/connections.json` (mode 0600 — holds API keys)
- `data/model_catalog.json`
- `data/routing.json`

No existing file was restructured; `data/freellmapi.json` remains the
source of truth for FreeLLMAPI settings and is only *read* by the new
layer (deleting a derived connection deletes the underlying FreeLLMAPI
settings, mirroring the existing FreeLLMAPI UI). The orphaned
`data/providers_config.json` was left untouched (deleting user data needs
approval) and is documented as safe to remove.

## 5. New API routes

All owner-token gated, cross-site guarded, following existing conventions.

**Connections**
- `GET /api/connections[?type=]` — public views + presets + capability matrix
- `POST /api/connections` — create (validated base URL, type, provider id)
- `GET /api/connections/<id>` — detail + models
- `PATCH /api/connections/<id>` — update (health cache invalidated on change)
- `DELETE /api/connections/<id>` — delete (derived → clears FreeLLMAPI settings)
- `POST /api/connections/<id>/test` — live probe → honest health
- `POST /api/connections/<id>/discover` — model discovery (502 with `retry` on failure)
- `GET /api/connections/<id>/models[?refresh=1]` — unified records (stale-on-failure, never raises away known models)
- `POST /api/connections/<id>/models` — manual registration (`remove: true` deletes)
- `DELETE /api/connections/<id>/models?native_id=` — remove manual model
- `POST /api/connections/<id>/aliases` — record gateway → upstream mapping
- `GET /api/connections/<id>/health` — cached health + effective status

**Compatibility**
- `GET /api/compatibility` — the agent capability matrix
- `GET /api/compatibility/<agent_id>[?model=]` — compatible connections + single-model evaluation

**Routing**
- `GET /api/routing/preferences` — global + agent + task preferences
- `POST /api/routing/preferences` — set global mode / agent override / task override
- `POST /api/routing/resolve` — resolve an execution route (honest statuses: `resolved` / `authorization_required` / `no_route`)
- `POST /api/routing/authorize-paid` — explicit paid-fallback consent (24 h ledger entry)
- `POST /api/routing/fallback` — propose a fallback plan after a failure (never executes)

**Extended existing**
- `GET /api/agents/<id>/models` — adds `connection_sources`, `connection_models` (legacy fields unchanged)
- `POST /api/project-sessions/<id>/model` — accepts + validates `conn:` ids through the compatibility registry
- `POST /api/deepseek/conversations[/<id>/messages]` — accept connection-scoped chat models

## 6. Provider connections supported

Presets (backend-served, UI-rendered dynamically — nothing hardcoded in the
Workspace): OpenAI, Anthropic, Google Gemini (OpenAI-compatible endpoint),
Groq, Cerebras, NVIDIA NIM, DeepSeek — plus *Custom provider* (manual base
URL + key + protocol). Any OpenAI- or Anthropic-compatible official
endpoint can be registered without code changes.

*Implemented and unit-tested; live calls require real credentials (none
available in this environment — see §14 test honesty).*

## 7. Gateway connections supported

Presets: OpenRouter, LiteLLM proxy (local), **9Router** (local
Anthropic-compatible router, matching the user's existing manual setup at
`localhost:20128`), Custom OpenAI-compatible, Custom Anthropic-compatible —
plus the **derived FreeLLMAPI** gateways that mirror the existing per-agent
FreeLLMAPI settings for Claude Code / Codex. Gateways are *not* assumed
free and *not* assumed to share one protocol: each record carries its own
`protocol` and `pricing` classification.

## 8. Agent compatibility matrix

From `backend/compatibility.py` (served at `/api/compatibility`):

| Agent | OpenAI-protocol endpoints | Anthropic-protocol endpoints | Execution |
|---|---|---|---|
| claude-code | — | **compatible** (env-injection; verified daily use via FreeLLMAPI) | api_via_cli |
| codex / codex-cli | **compatible** (env-injection; verified daily use) | — | api_via_cli |
| deepseek-harness | **compatible** (in-process client) | — | api |
| opencode | not_tested (documented config mechanism, not validated) | — | cli_native |
| aider | not_tested (documented `OPENAI_API_BASE`) | — | cli_native |
| antigravity, cursor, openclaw, gemini-cli, goose, hermes-cli | incompatible (no documented custom-endpoint mechanism) | incompatible | cli_native |

CLI-native models (no `conn:` prefix) always pass through to the agent's
own model list — the pre-existing contract, unchanged.

## 9. Model discovery implementation

- **Protocols**: OpenAI-compatible (`GET {base}/models`, Bearer) and
  Anthropic-compatible (`GET {base}/v1/models`, `x-api-key` +
  `anthropic-version`).
- **Normalization**: every record preserves the runtime-native id verbatim
  (`native_id`) and gains a namespaced internal id. Context windows,
  modalities and pricing are extracted from upstream metadata only;
  everything absent is `unknown` — never invented. Upstream pricing
  metadata (e.g. OpenRouter's `pricing: {prompt: 0}`) classifies free vs
  paid when the endpoint publishes it.
- **Persistence & staleness**: records persist in
  `data/model_catalog.json`; a failed refresh marks entries `stale`
  (distinct from confirmed unavailable) and keeps them listed, with the
  error surfaced in the response.
- **Manual registration** for endpoints without model-listing APIs, with a
  clear "manual" marker; model aliases record known gateway → upstream
  mappings.
- **Agent-native discovery unchanged**: `model_sources/` (CLI parsing,
  PTY pickers, static lists) keeps working exactly as before.

## 10. Execution routing implementation

`backend/execution_router.py` resolves one binding per (agent, model):

1. Connection-scoped model → protocol env template for the agent
   (e.g. claude-code + Anthropic gateway → `ANTHROPIC_BASE_URL`,
   `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY=""`, `ANTHROPIC_MODEL`),
   injected through `agent_registry.get_augmented_env` — the same choke
   point FreeLLMAPI has always used, so both PTY and Docker paths inherit
   it (Docker's secret allowlist already forwards these vars).
2. FreeLLMAPI-configured model → existing `freellmapi.apply_agent_env`
   (byte-identical behaviour).
3. Everything else → untouched CLI-native auth.

The session layer translates `conn:` ids to the native id for CLI argv,
generated OpenCode configs and TUI staged input, and keeps the full id in
session state. `RoutingError` makes a broken route fail loudly (502 with a
reason) instead of silently falling back. The DeepSeek Harness in-process
client routes through any compatible OpenAI-protocol connection via a
thread-local per-request override (`record_as` keeps the connection id on
the conversation). **All agent dashboards, terminals and session workflows
are untouched** — the router only feeds configuration into them.

## 11. Free/Paid routing implementation

- Modes: **Free First** (default), **Paid First**, **Manual**; per-agent
  and per-task overrides (task > agent > global).
- Pricing classification comes from connection metadata + upstream
  metadata only. `unknown` is never assumed free. Six classes:
  free / free_tier / paid / subscription / local / unknown.
- **Paid fallback never happens silently**: with no free route left,
  resolution returns `authorization_required`; the user grants consent
  explicitly (`/api/routing/authorize-paid`, 24 h ledger with reason), or
  enables the global "Allow paid fallback" switch.
- `POST /api/routing/fallback` proposes an ordered, compatibility-checked
  fallback plan after a failure — it never executes anything itself.
- The workspace's existing selection flow is manual by nature and is
  unaffected by the policy; the policy governs *automatic* resolution.

## 12. Workspace UI changes

The workspace toolbar has exactly two selectors: **Agent** and **Model**.
There is no Source, Connection or Provider dropdown. The Model menu is one
merged, searchable list grouped by route category, in this order:

1. **Via FreeLLMAPI** — the saved FreeLLMAPI endpoint model plus the
   catalog models that belong to that endpoint (`endpoint_match: true`).
   Catalog rows for other providers' endpoints are hidden: they cannot
   work against the saved endpoint, and listing them would imply they can.
2. **Via Provider · &lt;name&gt;** — models discovered on compatible API
   provider connections.
3. **Via Gateway · &lt;name&gt;** — models discovered on compatible
   gateway/proxy connections (derived FreeLLMAPI connections are skipped
   so they never duplicate section 1).
4. **Agent's own models (CLI login)** — the CLI-native list, unchanged.

Rows show the human display name; the native model id (and endpoint/
compatibility reason) stays in the row tooltip. Chips show honest facts
only: pricing (`free` / `paid` / `local`; unknown is never shown as free),
context window, `tools`/`vision` when the catalog reports support, and a
saved/default badge. Incompatible or unavailable rows are disabled with a
reason instead of silently disappearing.

Picking a model carries its route: the model id itself (native or
`conn:<connection>:<native>`) identifies the connection, and the backend
resolves the concrete endpoint at launch. Picking a FreeLLMAPI catalog
model also saves it as the agent's model, so the next launch uses it even
without a live session. A **Manage connections…** entry at the bottom of
the Model menu opens the AI Connections manager.

- New **AI Connections** manager view: provider/gateway panes, cards with
  real health dots, Test / Models (discover, add/remove, aliases) / Edit /
  Remove, the add-connection modal with presets, and the routing
  preference control (three modes + paid-fallback switch + per-agent
  override note).
- The dashboard "Prefer Free" toggle in the Model Registry panel remains
  decorative (pre-existing); the real policy control lives in AI
  Connections.

## 13. Authentication and security changes

- Secrets live only in `data/connections.json` (mode 0600, atomic writes,
  mkstemp) and are injected into agent environments at launch — never
  returned by any API (`public_view` returns `configured`/`source`/`hint`
  only, same convention as DeepSeek/Studio/FreeLLMAPI stores).
- `config.write_json(mode=)` makes the 0600 guarantee explicit.
- No authorization headers or keys are logged; route tests assert
  redaction (`test_connections.py`, `test_connection_routes.py`).
- Base-URL validation blocks embedded credentials, placeholder URLs and
  plain-HTTP to non-local hosts (local gateways like LiteLLM/9Router stay
  allowed).
- Browser sign-in is still never treated as API access: the DeepSeek
  native Web-UI integration remains an isolated process supervisor.
- New routes are owner-gated and cross-site guarded like all others
  (verified in `test_connection_routes.py`).

## 14. Test results (with honest status labels)

| Suite | Result | Status |
|---|---|---|
| Full Python suite (`unittest discover`) | **759 tests, OK** (682 on the connection base; 77 are unrelated Ads/Cosmos tests currently in the working tree) | Automated, executed |
| New `test_connections.py` | 13/13 pass | Automated, executed |
| New `test_model_catalog.py` | 7/7 pass | Automated, executed |
| New `test_compatibility.py` | 11/11 pass | Automated, executed |
| New `test_routing.py` | 12/12 pass | Automated, executed |
| New `test_execution_router.py` | 13/13 pass | Automated, executed |
| New `test_connection_routes.py` (real HTTP server) | 9/9 pass | Integration, executed |
| JS suites (`node --test`) | 5/5 files pass | Automated, executed |
| `tests/e2e_connections_smoke.py` (real Chrome via CDP) | 19/19 checks pass | Integration (manual-grade), executed |
| `tests/e2e_freellm_user_flow.py` (real Chrome, isolated temp session) | 13/13 checks pass | Integration (manual-grade), executed |
| `tests/e2e_unified_models.py` (real Chrome + mock Anthropic gateway) | 16/16 checks pass | Integration (manual-grade), executed |
| Live endpoint tests against real paid providers | Not run — no paid provider credentials in this environment | **Blocked by missing credentials** (fake-upstream HTTP servers used instead) |
| Real FreeLLMAPI call (`claude -p`, injected env) | exit 0, stdout `OK`, no unrecognized-model warning | Executed against the live free tier |
| Docker sandbox execution of connection models | Not executed (Docker daemon not running here) | Blocked by environment |
| `tests/e2e_workspace.py` | Fails on stale assertions (`header-nav-tab` absent from HEAD, registry-card count drift) | **Pre-existing**, verified unrelated: the failing selectors do not exist in the last commit, so the drift predates this work; superseded for this feature by `e2e_connections_smoke.py` |

Integration checks against a real server instance (executed): connections
registry + presets + matrix, `/api/agents/opencode/models` legacy fields
intact (535 models / 7 providers) with additive connection fields,
claude-code models unchanged, routing defaults, honest `no_route` /
`authorization_required` outcomes, compatibility matrix endpoints, and
frontend asset serving.

## 15. Regression test results

- All pre-existing tests still pass (682 on this feature's base; 759 in
  the current working tree including unrelated Ads/Cosmos tests).
- FreeLLMAPI route tests, DeepSeek harness/route tests, session store
  tests, server security tests: all green.
- `/api/agents/<id>/models` verified returning identical legacy fields
  plus additive ones.
- All agent dashboards/terminals untouched (no changes to `sandbox.py`,
  `ws.py`, dashboard JS).
- JS suites and the connection smoke E2E pass in a real browser.

## 16. Known limitations

1. **Unvalidated agent paths stay "not_tested"** (opencode, aider): the
   mechanisms are documented and encoded but not exercised end-to-end here.
2. **Endpoint without a model-listing API**: health shows
   "Configured — no model-listing API"; models must be registered manually
   (clearly labelled).
3. **`unknown` pricing connections are not auto-selected under Free
   First** (deliberate, per spec); classify pricing in AI Connections or
   use Manual/override.
4. **Model-switch "read-back" remains advisory** for CLI agents (the CLI,
   not the OS, decides the final model); the audit-documented limitation
   is unchanged and now bypassed for connection models (env +
   native-id application are deterministic).
5. **The stale `e2e_workspace.py`** needs a refresh against the current UI
   (pre-existing drift, out of scope).
6. **Legacy `POST /api/project-sessions/active/model`** remains dead code
   (audit finding; its UI was removed earlier). Not resurrected to avoid
   scope creep.
7. **FreeLLMAPI connect form triplication** (workspace / skill-builder /
   freellmapi views) is preserved for compatibility; consolidation is
   future work.

## 17. Remaining work (planned, not implemented)

- Validate the opencode config-generation path for gateways and flip its
  matrix entry to compatible (mechanism already coded as `not_tested`).
- Optional background model-catalog refresh (currently on-demand with
  cache; the service layer already supports `refresh`).
- Per-task routing UI (API + policy exist; the manager view exposes
  per-agent overrides, task overrides are API-only).
- Usage/cost tracking where providers expose billing data.
- Consolidate the three FreeLLMAPI connection forms onto the connections
  registry.
- Refresh `tests/e2e_workspace.py` selectors.

## 18. Rollback instructions

All changes are additive and file-scoped. To roll back:

1. `git checkout HEAD -- backend/config.py backend/agent_registry.py
   backend/session_manager.py backend/deepseek_harness.py app.py index.html
   js/workspace.js js/app.js README.md STATE.md` (revert modified files).
2. Delete the new files: the five `backend/{connections,model_catalog,
   compatibility,routing,execution_router}.py`, `js/connections.js`,
   `css/connections.css`, the new test files, and the two docs.
3. Optionally delete the new stores `data/connections.json`,
   `data/model_catalog.json`, `data/routing.json` (they contain only
   user-entered connection config; nothing else reads them).
4. No database migrations need reverting; no existing store format changed.
