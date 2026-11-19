# COSMOS Multi-Agent Intelligence — Phase 0 Audit & Implementation Plan

**Date:** 2026-11-15
**Scope:** DENNIS Agent OS (`app.py`, `index.html`, `js/*`, `backend/*`, `data/*`)
**Status:** Audit complete. Implementation proceeds additively.

---

## 1. What exists today

### 1.1 OS shell and navigation

- Single-page app served by `app.py` (stdlib `http.server`), one view per
  sidebar item (`index.html` sections `#jarvis-view`, `#studio-view`, …),
  switched by `switchMainView()` in `js/app.js:83`.
- Sidebar items are static buttons (`index.html:115-202`); `initSidebar()`
  (`js/app.js:213`) dispatches by label text. Adding a view requires a button,
  a section, an entry in the `navs`/`VIEWS` arrays and a refresh hook.
- No COSMOS view exists. `js/cosmos.js` is **only** the neural-brain canvas
  animation in the hero banner (`#cosmos-canvas`), not an agent system.
- `js/agentDashboards.js` is a per-project-session **terminal** registry
  (PTY views keyed by session id), unrelated to intelligence agents.

### 1.2 Agent registry and routing

- `backend/agent_registry.py` registers **host CLIs** (OpenCode, Cursor,
  Claude Code, Antigravity, DeepSeek Harness, …), not intelligence agents.
  `DEFAULT_AGENTS`, `HOST_AGENT_PROBES`, `CLI_PROFILES`, `available_agents()`.
- Model/provider routing: `backend/execution_router.py`, `backend/routing.py`,
  `backend/model_catalog.py`, `backend/connections.py`. A single shared
  selector state lives in `localStorage["dennis.studio.agent"]` (Studio) and
  the Workspace agent menu. COSMOS must reuse this, not fork it.
- Text generation adapter: `backend/studio_llm.py` — two profiles
  (`opencode` CLI, default model `nvidia/z-ai/glm-5.3-flash`; or
  `openai_compatible` via the DeepSeek harness). Configured through the
  YouTube workspace settings, env overrides, then defaults. `complete()`
  returns text; `status()` reports provider/model/configured honestly.

### 1.3 Studio shell

- `#studio-view` hosts a workspace switcher:
  `STUDIO [Ads Studio] [YouTube Studio]` (`index.html:1588-1595`),
  implemented by `js/studioWorkspaces.js` + `js/studioWorkspaceState.js`
  with hash deep links `#studio/<workspace>/<section>`.
- `backend/studio_workspaces.py` is a generic workspace boundary:
  registry (`data/studio/workspaces.json`), per-workspace `settings.json`,
  `projects.json`, `activity.json` under `data/studio/workspaces/<id>/`,
  soft deletes, per-workspace isolation, atomic 0600 writes.
  `WORKSPACES = {ads, youtube}`. This is the correct persistence pattern to
  reuse for COSMOS (it already enforces cross-domain isolation).

### 1.4 Ads Studio

- UI: `#studio-ads-sidebar` / `#studio-ads-main`, five-step Generate stepper
  (`Model → Product/Props → Dressing → Reference Video → Production`),
  `index.html:1666-1687`, `js/studioFashion.js` (~3000 lines).
- Sections today: `fashion` (Generate), `assets`, `library`, `playlists`,
  `preset`, `history`, `setup` (`js/studioWorkspaceState.js:14`).
  Tab visibility is owned by `Studio.setTab()` (`js/studio.js:2536`).
- Backend: `backend/studio_fashion.py` (prompt composition, job plans,
  presets), `backend/studio_free.py` (Pollinations/Gemini/Veo free engines),
  `backend/studio_registry.py`, `backend/studio_providers.py`,
  `backend/studio_jobs.py`, `backend/studio_media.py`,
  `backend/studio_contracts.py` (schema validation, lifecycle, approvals).
- **No Create dashboard.** There is no natural-language, intent-driven brief
  builder. There is **no Product Backlog**, no product discovery, no merchant
  or affiliate entity store, and no product templates. `data/studio/templates.json`
  is an empty production-dropdown store (`{"templates": []}`).
- `js/studioTemplates.js` contains only **prompt text catalogs** for Step 5
  (environment + motion), not commerce templates.

### 1.5 YouTube Studio

- UI: `#studio-youtube-sidebar` / `#studio-youtube-main`, 8 sections
  (`create, templates, projects, backlog, publishing, analytics,
  intelligence, settings`) in `js/studioYoutube.js` and
  `index.html:2638-3015`. Templates/publishing/analytics are honest
  placeholders today.
- Topic Backlog: `backend/youtube_topics.py`, JSON
  `data/studio/youtube/topics.json`, eight statuses
  (`idea, researching, validated, approved, scripted, in_production,
  published, rejected`), forward-only transitions, Jaccard duplicate
  detection (block ≥ 0.85, warn ≥ 0.55). `create_topic(channel_id, payload)`.
- Channel configuration: `backend/youtube_channels.py`,
  `data/studio/youtube/channels.json`. One seeded active channel
  ("The Business Money Blueprint") with niche, audience, pillars,
  editorial guidelines, publishing defaults, agent prompts.
- Research: `backend/youtube_research.py` — manual analyst validations
  (5 criteria, 18/25 approval gate), competitor observations with
  `observed|assumption` basis. **No live web search**; honesty enforced.
- Scripts: `backend/youtube_scripts.py` uses `studio_llm` with channel master
  prompt + scriptwriter extension; strict JSON.
- API: `backend/youtube_api.py` dispatcher under `/api/studio/youtube/*`,
  wired in `app.py` (GET 1381, POST 2980, PATCH 5029, DELETE 5113).
- **No recommendation cards, no discovery→backlog recommendation flow**, no
  video templates (Phase 3 placeholder), no Ads/YouTube shared intelligence.

### 1.6 Orchestration / memory / approvals

- `backend/hermes.py` is the in-OS orchestrator for **CLI sub-agents**
  (Kanban, plans, runs in `data/hermes.json`, subprocess runner). It is not a
  domain-intelligence engine.
- `backend/vault.py` + Memory Galaxy store markdown memory; `backend/digest.py`
  builds daily digests.
- `backend/studio_contracts.py` provides schema validation, provider
  capabilities, approval states and redaction.
- Guarded autonomy: existing Studio work is draft/approval based; publishing
  is OAuth-gated (Phase 10). No automatic external publishing exists.

### 1.7 Data on disk (relevant)

```
data/studio/templates.json                 {"templates": []}
data/studio/workspaces.json                {"version":1,"active":"ads"}
data/studio/workspaces/youtube/settings.json
data/studio/workspaces/youtube/projects.json
data/studio/youtube/channels.json          seeded channel
data/studio/youtube/topics.json            (lazily created)
data/agents.json                           host CLI registry (not COSMOS)
```

---

## 2. Gap analysis against the requested architecture

| Requirement | Status | Gap |
|---|---|---|
| ANDROMEDA/POLARIS/PLEIADES/QUASAR/HELIOS | **Missing** | No registry, identity, role, capability, execution or dashboard |
| One independent dashboard per agent | **Missing** | `js/cosmos.js` is a canvas; no agent view |
| Ads left / YouTube right in every dashboard | **Missing** | No such layout |
| Per-agent execution history | **Missing** | Only host-CLI sessions (`session_manager`) |
| Configuration-driven discovery | **Missing** | No Ads templates, no YouTube templates, no snapshotting |
| Shared intelligence services + domain adapters | **Missing** | Discovery logic does not exist at all |
| Recommendation cards + provenance | **Missing** | Topic backlog accepts manual topics only |
| Ads Product Backlog | **Missing** | `templates.json` empty; no product store |
| YouTube Topic Backlog | **Exists** | Reuse as the only YouTube draft destination |
| Ads Studio Create (intent → brief) | **Missing** | Only the 5-step Generate workflow exists |
| Affiliate / merchant integrity | **Missing** | No affiliate entities; must not be implied |
| Performance separation (HELIOS) | **Missing** | No analytics ingestion; must not invent data |
| Approval/publishing guardrails | **Exists partially** | Keep them; no new auto-publish path |
| Tests for agents/divisions | **Missing** | Add unit + integration + JS state tests |

---

## 3. Implementation map

### 3.1 Files to create — backend

| File | Responsibility |
|---|---|
| `backend/cosmos_common.py` | Paths (`data/cosmos/`), errors, atomic 0600 JSON, ids, text cleaning, JSON extraction, hashing |
| `backend/cosmos_registry.py` | The five agents, identities, division responsibilities, capability definitions, strict JSON output contracts |
| `backend/cosmos_config.py` | 5 editable Ads templates, 5 editable YouTube templates, active selections, effective configuration snapshots |
| `backend/cosmos_store.py` | Execution history + recommendation/provenance store, scoped reads |
| `backend/cosmos_engine.py` | Task routing, prompt composition, shared LLM execution, qualification/filtering, dedupe, result persistence |
| `backend/cosmos_backlog.py` | Draft routing (Ads Product Backlog / existing YouTube Topic Backlog), reject/refine, affiliate validation records |
| `backend/cosmos_api.py` | Pure HTTP dispatcher for `/api/cosmos/*` |
| `backend/ads_backlog.py` | Ads Product Backlog store with the specified lifecycle |
| `backend/studio_create.py` | Ads Studio Create: intent → structured brief, validation, handoff to existing production project store |

### 3.2 Files to create — frontend

| File | Responsibility |
|---|---|
| `js/cosmosState.js` | Pure, node-testable state helpers (agent/division normalization, per-agent/per-division state, card actions, deep links) |
| `js/cosmosDashboard.js` | Reusable agent dashboard UI: header, agent tabs, two permanent divisions, config selectors, composers, cards, history, modals |
| `js/studioCreate.js` | Ads Studio Create form + brief preview + handoff to Generate |
| `css/cosmos.css` | OLED design-system styles scoped to `#cosmos-view` (two columns → stacked on narrow screens) |
| `tests/cosmosState.test.js` | Node tests for the pure frontend state |

### 3.3 Files to modify

| File | Change |
|---|---|
| `index.html` | Sidebar `COSMOS` item; `#cosmos-view` section; `COSMOS` script/CSS includes; Ads Studio `Create` nav item + panel |
| `js/app.js` | `switchMainView('cosmos')`, nav array, `VIEWS`, refresh hook |
| `js/studioWorkspaceState.js` | Add `create` to Ads sections + label (additive; default stays `fashion`) |
| `js/studio.js` | Show/hide the new Ads Create panel in `setTab()` |
| `backend/studio_workspaces.py` | Add `create` to the Ads legacy tab list (descriptor only) |
| `app.py` | Import/dispatch `/api/cosmos/*` and `/api/studio/ads/create/*`; register error handling |
| `STATE.md` | Append implementation notes (repo convention) |

### 3.4 Data model (new, versioned, additive)

```
data/cosmos/config.json
  {version, active_ads_template_id, active_youtube_template_id,
   active_youtube_channel_id, updated_at}

data/cosmos/executions.json
  {version, executions:[
    {id:"exe_…", agent_id, division, capability, task, status, model,
     provider, prompt_sha256, config_snapshot, source_notes_used,
     result:{summary, items:[…]}, recommendation_ids, duplicates_skipped,
     insufficient_results, error, started_at, finished_at, duration_ms}]}

data/cosmos/recommendations.json
  {version, recommendations:[
    {id:"rec_…", agent_id, division, execution_id, capability, template_id,
     config_snapshot, title, summary, payload:{…}, sources, relevance_score,
     opportunity, competition, evidence_status, status:"proposed|drafted|rejected|refined",
     dedupe_key, user_decision, destination:{type,id,created_iso},
     affiliate_validation, refund_of, created_at, updated_at}]}

data/cosmos/ads/backlog.json
  {version, products:[…]}   lifecycle:
  discovered → drafted → reviewing → approved → ready_for_creation →
  in_production → published → archived

data/studio/ads/create-briefs.json
  {version, briefs:[{id, instruction, product, creative_style, audience,
   platforms, format, duration, reference_assets, affiliate_destination,
   disclosures, prompt, template_id, status, handoff_project_id, …}]}
```

### 3.5 API changes (all additive)

```
GET    /api/cosmos/overview
GET    /api/cosmos/agents
GET    /api/cosmos/agents/{id}
GET    /api/cosmos/agents/{id}/executions?division=&limit=
GET    /api/cosmos/agents/{id}/recommendations?division=&status=
POST   /api/cosmos/agents/{id}/execute        {division, task, source_notes, template_id?, channel_id?}
GET    /api/cosmos/executions/{id}
GET    /api/cosmos/recommendations?agent_id=&division=&status=
GET    /api/cosmos/recommendations/{id}
POST   /api/cosmos/recommendations/{id}/draft
POST   /api/cosmos/recommendations/{id}/reject   {note?}
POST   /api/cosmos/recommendations/{id}/refine   {instruction?}
POST   /api/cosmos/recommendations/{id}/validate-affiliate {confirmed, url?, program?, evidence?}
GET    /api/cosmos/config
POST   /api/cosmos/config/active                 {domain, template_id} | {channel_id}
PATCH  /api/cosmos/templates/ads/{id}
PATCH  /api/cosmos/templates/youtube/{id}
GET    /api/cosmos/backlog/ads?status=&q=
POST   /api/cosmos/backlog/ads
GET    /api/cosmos/backlog/ads/{id}
PATCH  /api/cosmos/backlog/ads/{id}
POST   /api/cosmos/backlog/ads/{id}/status       {status}
GET    /api/cosmos/backlog/youtube               (read-only summary)

GET    /api/studio/ads/create/briefs
POST   /api/studio/ads/create/briefs
GET    /api/studio/ads/create/briefs/{id}
POST   /api/studio/ads/create/briefs/{id}/handoff
DELETE /api/studio/ads/create/briefs/{id}        (soft delete)
```

### 3.6 Execution model

1. Validate agent + division + capability.
2. Resolve the **effective configuration** (ads template or channel + YouTube
   template; explicit instruction > active config > template defaults >
   system defaults). Persist that snapshot on the execution.
3. Build provenance context (recent agent recommendations, active backlog
   titles, optional owner-supplied source notes).
4. Compose system + user prompts from `cosmos_registry` (role instructions,
   honesty rules, strict JSON contract) and call `studio_llm.complete()` —
   the existing provider/model routing.
5. Parse and validate JSON. ANDROMEDA discovery items are qualified against
   the active configuration; low-relevance/unrelated items are filtered and
   reported honestly. Fewer than 5 qualified items yields
   `insufficient_results: true`, never padded fake cards.
6. Persist the execution (per agent, per division) and qualified
   recommendations with full provenance. No auto-drafting, no auto-approval,
   no publishing.
7. Other agents persist role-shaped result cards (strategy, creative,
   evidence, performance) in the execution. HELIOS measurements require an
   explicit `basis: measured|estimate` and a source.

### 3.7 Backward compatibility / migration

- All stores are **new files** with `version: 1`; nothing existing is
  rewritten. First read lazily seeds defaults.
- YouTube drafts use the existing `youtube_topics.create_topic()`; no schema
  change. Provenance travels in `notes` and in the recommendation record.
- Ads backlog is new and separate from `jobs.json`/`media.json`/
  `templates.json`; the Generate pipeline is untouched.
- Ads Studio `create` is a new section/panel; `fashion` remains the default
  landing. Existing tab keys and the five-step workflow are unchanged.
- No new dependencies, no new framework, no frontend rewrite.
- Affiliate links are never generated automatically; publishing remains
  OAuth-gated and user-approved.

### 3.8 Test plan

- `tests/test_cosmos_registry.py` — five agents, two divisions, capabilities.
- `tests/test_cosmos_config.py` — 5+5 templates, edits persist, snapshots
  frozen per execution, unrelated changes do not rewrite history.
- `tests/test_cosmos_store.py` — per-agent/per-division scoping, provenance,
  recommendation lifecycle.
- `tests/test_cosmos_engine.py` — mocked LLM: success, invalid JSON, LLM
  failure, 5-item qualification, filtering, insufficient results, dedupe,
  HELIOS measurement guard.
- `tests/test_ads_backlog.py` — lifecycle, forward-only transitions, dedupe.
- `tests/test_cosmos_backlog.py` — draft routes to the right backlog, reject
  never inserts, YouTube duplicate handling.
- `tests/test_cosmos_api.py` — dispatcher route contract.
- `tests/test_studio_create.py` — brief composition, affiliate rule, handoff
  project, no auto-render.
- `tests/test_cosmos_workflows.py` — end-to-end ANDROMEDA in both divisions
  with config changes; agent isolation.
- `tests/cosmosState.test.js` — agent/division state isolation + actions.
- Regression: existing `unittest` suites and JS tests unchanged; Ads Generate
  and YouTube Studio stores untouched.

---

## 4. Execution order (as requested)

1. **Phase 1–2** — reusable dashboard framework + mandatory two divisions.
2. **Phase 3** — Ads/YouTube configuration adapters and template stores.
3. **Phase 4** — agent capabilities, orchestration, execution history.
4. **Phase 5** — recommendation cards, provenance, backlog routing.
5. **Phase 6** — Ads Studio Create (Generate preserved).
6. **Phase 7** — HELIOS performance ingestion with measured/estimate rules.
7. **Phase 8** — regression + final verification.

Destructive changes, external publishing, or provider/affiliate actions are
not part of this plan and will not be performed without explicit approval.

---

## 9. Implementation status — 2026-11-15

All eight phases above are implemented and verified:

- **Backend** (`backend/`): `cosmos_common`, `cosmos_registry`,
  `cosmos_config`, `cosmos_store`, `cosmos_engine`, `cosmos_backlog`,
  `cosmos_api`, `ads_backlog`, `studio_create`; routes wired in `app.py`
  (`/api/cosmos/*`, `/api/studio/ads/create/*`); Ads `legacy_tabs` extended
  with `create` and `backlog`.
- **Frontend**: `js/cosmosState.js`, `js/cosmosDashboard.js`,
  `js/studioCreate.js`, `css/cosmos.css`; additive wiring in `index.html`,
  `js/app.js` (including `#cosmos/<agent>` deep links and hash cleanup),
  `js/studio.js` (`setTab` + `Studio.prefillPrompt`),
  `js/studioWorkspaceState.js`.
- **Configuration**: 5 + 5 editable templates, one active template per
  domain, active YouTube channel; every execution stores its own
  configuration snapshot (edits never rewrite history).
- **Honesty rules**: all discovery output is an AI-hypothesis-labelled
  candidate, qualified against the active configuration (relevance ≥ 60 +
  category/pillar match); `< 5` qualified results are reported as
  insufficient without filler; affiliate validation is a separate owner
  operation; nothing publishes automatically.
- **Verification**: 760 Python tests OK (`python3 -m unittest discover -s
  tests`), all JS state suites OK, and a Playwright browser smoke
  (21/21 checks) covering the five-agent tab rail, Ads-left/YouTube-right
  divisions, deep links, empty states, Ads Create brief composition and the
  Product Backlog add/delete round-trip.

---

## Status addendum — 2026-11-19 (GENESIS)

This document describes the COSMOS intelligence layer as implemented on
2026-11-15 and remains accurate for the five-agent architecture. Since then,
the production workflow described in section 2 ("Missing") has been built as
**GENESIS**, a system-level production dashboard inside COSMOS — not a sixth
agent. GENESIS owns production intake, project workspaces, final FFmpeg
rendering, quality review, owner approval and publishing for both divisions;
the Ads Product Backlog and the YouTube Topic Backlog / Publishing /
YouTube Intelligence Studio screens were consolidated into COSMOS. See
**docs/GENESIS_IMPLEMENTATION.md** for the current architecture, API routes,
render/publishing adapters, test results and known limitations.
