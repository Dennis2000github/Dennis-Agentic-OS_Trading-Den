# YouTube Studio — Phase 0 Audit & Integration Plan

Date: 2026-11-09
Status: audit complete; **Stage 2 (workspace foundation) implemented and
verified** — see §11 and `STATE.md` ("Studio workspaces", 2026-11-09).
Scope: extend the existing Studio into a multi-workspace creative production platform
without breaking the existing Ads/creative Studio.

---

## 1. Executive summary

DENNIS Agentic OS is a **Python standard-library HTTP server** (`app.py`,
`DennisOSHandler`) serving a **single-page, vanilla-JS frontend** (`index.html`,
`js/*.js`, `css/*.css`). There is **no SQL database, no Supabase, no ORM, and no
router library**. Persistence is JSON files under the git-ignored `data/`
directory with atomic writes and process-local locks.

The existing **Studio** is a real, provider-backed creative generation console:

- one `#studio-view` inside `index.html`;
- tab switching in `js/studio.js` (`setTab`, line 2254);
- a Collart-style shell for an ad/fashion video workflow (`js/studioFashion.js`);
- Higgsfield image/video generation, async jobs, local media library,
  playlists, templates, history, provider setup, and an owner-approval tool
  surface for agents.

There is **no component literally named "Ads Studio"** in the UI. The name
already exists in the provider catalog as the Higgsfield model
`higgsfield/ads-studio/v1.0` ("Ads Studio — Generate ads"). For this project we
treat the existing Studio (Generate/Fashion + Assets + Library + Presets) as the
**Ads Studio** workspace and relabel it in navigation only. Existing tab keys,
routes and DOM must remain intact.

YouTube Studio is therefore **new frontend + new backend modules + new JSON
stores**, reusing the existing Studio shell, provider plumbing, media library,
job conventions, auth, and test harness. There is currently **no TTS, no music,
no transcription, no timeline editor, no video renderer, no YouTube OAuth, and
no YouTube publishing/analytics**. A bundled ffmpeg 6.1.1 binary exists but is
not wired to any renderer. These gaps are addressed by adapters or reported
limitations, never faked.

---

## 2. Current Studio architecture

### 2.1 Runtime

| Layer | Implementation |
| --- | --- |
| Server | `app.py`, `http.server.ThreadingHTTPServer`, single port 8080 |
| Auth | owner token from `data/auth.json`; `X-Dennis-Token` or `?token=`; every `/api/*` route owner-gated by `_require_api_owner` (`app.py:716`) except `/api/auth/session` |
| Persistence | atomic JSON in `data/`, `0600` for secrets, `0700` directories |
| Long jobs | `threading.Thread`, `ThreadPoolExecutor`, persisted JSON job stores |
| Frontend | vanilla JS IIFEs, global namespaces (`window.Studio`, `window.StudioFashion`), no bundler, no client router |
| Styling | prebuilt Tailwind + `css/style.css` + scoped `css/studio-v2.css`; dark UI |
| Tests | `unittest` (Python) and `node --test` (JS state); Playwright available for smoke tests |

### 2.2 Studio backend modules

| Module | Responsibility |
| --- | --- |
| `backend/studio_contracts.py` | JSON-schema subset validation, field derivation, normalized states/errors/outputs, request hashing, redaction, filename safety |
| `backend/studio_catalog.py` | pinned provider catalog load/validate/filter |
| `backend/catalog/higgsfield-0.1.0.json` | 84 pinned workflows: 17 image, 67 video; modes include text/image/reference-to-video, edit, extend, restyle, motion transfer |
| `backend/studio_providers.py` | provider registry + `BaseProvider` contract; **only two providers registered**: `higgsfield` (official API) and `higgsfield_mcp` (experimental) |
| `backend/studio_higgsfield.py` | official Higgsfield HTTP client |
| `backend/studio_mcp.py` | stdio JSON-RPC MCP client |
| `backend/studio_jobs.py` | persisted async job store + poller; states `queued/running/succeeded/failed/cancelled`; no invented progress; restart recovery |
| `backend/studio_media.py` | local media library: SSRF-guarded download, 200 MB cap, opaque media ids, Range serving |
| `backend/studio_collections.py` | playlists + reusable templates |
| `backend/studio_secrets.py` | owner-only credential store; specs already exist for `higgsfield`, `fal`, `runway`, `luma`, `google_veo`, `minimax`, `replicate` |
| `backend/studio_fal.py`, `studio_runway.py`, `studio_luma.py` | thin official clients that are **not registered** in the provider registry |
| `backend/studio_tools.py` | agent tool schema + owner approval queue for mutating calls |
| `backend/studio_fashion.py` | prompt composition/config for the Collart-style fashion/ads workflow |
| `backend/studio_videokit.py` | reference-video analysis (yt-dlp/ffmpeg/Gemini where installed) |
| `backend/studio_model_library.py` | saved model photos + hidden presets for the fashion workflow |

### 2.3 Studio HTTP API (existing)

Read: `GET /api/studio/providers`, `/models`, `/jobs`, `/jobs/<id>`,
`/library`, `/media/<id>`, `/playlists`, `/templates`, `/video-kit/*`,
`/tools`, `/approvals`, `/mcp`, `/fashion/config`.

Write: `POST /api/studio/jobs`, `/jobs/<id>/cancel`, `/uploads`,
`/library/import`, `/playlists`, `/templates`, `/tools/call`,
`/approvals/<id>`, `/providers/<id>/credentials`, `/mcp/*`;
`DELETE /api/studio/jobs/<id>`, `/library/<id>`, `/playlists/*`, `/templates/*`.

### 2.4 Studio frontend

- `index.html` — `#studio-view` (line 1286) with a header, a `collart-shell`
  sidebar (Generate, Assets, Library, Playlists, Preset Video, History, Setup),
  and panels: `#studio-panel-activity`, `-library`, `-playlists`, `-templates`,
  `-history`, `-setup`, `-fashion`, `-assets`, `-preset`.
- `js/studio.js` — provider/model selection, schema-driven compose form,
  uploads, polling, stage/output, library, playlists, templates, history,
  setup. `setTab()` is the de-facto studio router.
- `js/studioFashion.js` — 4-step ads/fashion workflow, asset grids, preset page,
  model library.
- `js/studioState.js` — pure helpers, unit-tested.
- `css/studio-v2.css` — scoped Studio skin (`#studio-view`, `.collart-*`).

### 2.5 Navigation

`js/app.js:switchMainView()` toggles top-level views by adding/removing
`hidden`. Studio is a header/sidebar entry (`#sidebar-nav-studio`); it is **not**
a URL route. There is no hash routing and no back/forward behavior.

---

## 3. Capability inventory (honest)

| Capability | Status | Evidence |
| --- | --- | --- |
| Text/LLM completion | Partial | DeepSeek Harness streams OpenAI-compatible chat (`backend/deepseek_harness.py`) but has no generic non-streaming completion helper; host CLI agents run via `backend/runner.py` |
| Image generation | Yes | Higgsfield 17 image models; fal/runway/luma clients exist but are not registered |
| Video generation | Yes | Higgsfield 67 video models |
| Voice / TTS | **No** | no TTS module, no voice models, no voice provider |
| Music generation | **No** | none |
| Sound effects | **No** | none |
| Transcription | **No dedicated provider** | ffmpeg/ffprobe and Gemini analysis exist in the Skill Builder/Video Kit, not a reusable transcription adapter |
| Video rendering / assembly | **No** | no render module, no timeline; only an unused bundled ffmpeg 6.1.1 (`agent-skill-builder/bin/ffmpeg`, x86_64) |
| Timeline / NLE | **No** | none |
| Storyboard / scenes | **No** | none |
| Local media library | Yes | image/video/audio/zip allowed, 200 MB cap, 400 items |
| Subtitle storage | Not allowed yet | `ALLOWED_CONTENT_TYPES` has no `text/vtt` / `text/plain` |
| Background jobs | Yes (generation only) | persisted generation job store + poller |
| Agent approvals | Yes | `studio_tools.py`, mutating tools require owner approval |
| Project management | code projects only | `backend/projects.py` manages filesystem project roots, not creative projects |
| Session management | agent sessions only | `backend/session_store.py`, `session_manager.py` |
| YouTube OAuth/publishing/analytics | **No** | none |
| YouTube data for research | Not integrated | Composio catalog has a `youtube` toolkit (OAuth2, 59 tools) but no connected account; YouTube Data API not implemented |
| Storage | Local filesystem only | no cloud storage adapter |

---

## 4. Reusable components

| Need | Reuse |
| --- | --- |
| Studio shell, sidebar, panel toggling | `index.html #studio-view`, `js/studio.js setTab`, `css/studio-v2.css` |
| Provider credential UX/storage | `studio_secrets.py` (already lists fal/runway/luma/veo/minimax/replicate), Studio Setup panel |
| Image/video generation | `studio_providers.REGISTRY`, `studio_jobs`, `studio_media` |
| Media library + serving | `studio_media` (extend records and filters, do not replace) |
| Background job patterns | `studio_jobs` (state machine, retry/backoff, persistence, pruning) |
| Tool approvals for agents | `studio_tools` pattern |
| Agent execution | `agent_registry` + `runner`, DeepSeek Harness chat API |
| Validation/redaction | `studio_contracts` |
| HTTP client | `studio_http.request_json` (stdlib urllib) |
| ffmpeg | bundled `agent-skill-builder/bin/ffmpeg` / `ffprobe`; resolve via env then PATH |
| Auth/security | owner token middleware, SSRF guard, `0600` stores |
| Tests | `tests/test_studio_*`, `tests/studioState.test.js`, real-server route test harness |

---

## 5. Missing capabilities that must be added

1. **Workspace registry + isolation** (ads vs youtube), per-workspace settings,
   projects, activity.
2. **YouTube project domain model** (channel, project, research, topic, script,
   version, storyboard, scene, asset metadata, voiceover, job, render,
   thumbnail, publish record, analytics, calendar, channel prompt versions).
3. **Generic LLM adapter** for script/research/topic agents with honest
   "not configured" behavior.
4. **TTS/voice adapter** with provider capability declarations and local
   fallback where available.
5. **Video renderer** (ffmpeg) with real progress, cancellation, retry, and
   scene-based composition (no fake timeline editor).
6. **Asset generation orchestration** per scene/asset (reuse Studio jobs).
7. **YouTube OAuth 2.0** (PKCE/loopback), resumable upload, thumbnails,
   playlists, scheduling, status, and Analytics API — or manual export when
   not connected.
8. **Production pipeline state machine** with stage statuses, approvals, retries.
9. **Workspace-aware media records** (workspace/project/session/asset type/
   version) without altering existing Ads records.
10. **Publishing calendar** and honest analytics (manual mode clearly labeled).
11. **Channel master-prompt versioning** + per-agent task extensions (channel
    content supplied by the owner; not present in the repo).

---

## 6. Integration points (exact)

| File | Change |
| --- | --- |
| `index.html` | Add workspace switcher to the Studio header/sidebar; add hidden YouTube workspace containers; keep every existing `data-studio-tab` and panel untouched |
| `js/studio.js` | Gate `setTab` by active workspace; do not rename existing tab keys; call new `window.StudioWorkspaces.sync()`; keep Ads refresh path unchanged |
| new `js/studioWorkspaces.js` | Workspace state, hash routing (`#studio/ads/...`, `#studio/youtube/...`), persistence, deep links, no reload |
| new YouTube JS modules | Overview, Channel Manager, Research, Topics, Scripts, Storyboard, Assets, Voice, Animation, Thumbnails, Publishing, Analytics, Calendar |
| `css/studio-v2.css` or new `css/studio-youtube.css` | Scope all new rules under `#studio-view[data-workspace="youtube"]`; reuse tokens |
| `app.py` | Add `/api/studio/workspaces/*` and `/api/studio/youtube/*` route blocks; keep existing Studio blocks byte-for-byte where possible |
| new `backend/studio_workspaces.py` | Workspace registry + generic creative project store |
| new `backend/youtube_*.py` | Domain stores, pipeline, OAuth, upload, analytics |
| `backend/studio_media.py` | Add optional `workspace`, `project_id`, `session_id`, `asset_type`, `version`, `parent_id`, `tags`; defaults preserve old records; add `text/vtt`, `text/plain`, `application/json` |
| `backend/studio_providers.py` | Add provider categories/capabilities; do not remove Higgsfield/MCP |
| new `backend/studio_llm.py`, `backend/studio_voice.py`, `backend/studio_render.py` | Adapters |
| `js/app.js` | On `switchMainView('studio')`, restore the last workspace; no change to other views |

---

## 7. Data architecture (no database exists)

### 7.1 Decision

Do **not** introduce SQLite/Supabase. Follow the existing JSON store
conventions: `data/studio/...`, atomic `os.replace`, `threading.RLock`,
`0600`, schema version, lazy defaults, soft delete, bounded lists.

### 7.2 Layout

```
data/studio/
  credentials.json              (existing)
  jobs.json                     (existing Ads generation jobs)
  media.json / media/           (existing shared media index + files)
  playlists.json                (existing)
  templates.json                (existing)
  model-library.json            (existing)
  workspaces.json               (new: registry, active workspace per client)
  workspaces/
    ads/settings.json           (new: Ads workspace production settings)
    youtube/
      settings.json             (new: provider categories, render defaults, schedule)
      channels.json             (new)
      channel-prompts.json      (new: versioned master prompts)
      projects.json             (new: YouTube projects)
      research.json             (new)
      topics.json               (new)
      scripts.json              (new: scripts + versions)
      storyboards.json          (new: storyboards + scenes)
      assets.json               (new: asset metadata -> media_id)
      voiceovers.json           (new)
      renders.json              (new)
      thumbnails.json           (new)
      publish.json              (new: publish records)
      analytics.json            (new: imported/manual snapshots)
      calendar.json             (new)
      jobs.json                 (new: production pipeline jobs)
      oauth.json                (new, 0600: tokens only)
      research-cache.json       (new: sourced data with provenance)
```

### 7.3 Relationships (logical)

- `channel 1—N project`, `channel 1—N channel_prompt_version`
- `project 1—N topic`, `project 1—N script`, `project 1—N asset`
- `script 1—N script_version`, `project 1—N storyboard 1—N scene`
- `scene N—N asset` (references, never copies)
- `project 1—1 current publish record 1—N upload attempts`
- `job` references `project_id`, `stage`, optional `scene_id`
- media records optionally reference `workspace`, `project_id`, `session_id`

### 7.4 Isolation rules

- Every YouTube store read/write requires `workspace="youtube"` at the service
  layer; Ads routes never enumerate YouTube stores.
- Ads media records without a workspace are treated as `ads` on read; never
  rewritten or deleted by YouTube code.
- Cross-workspace references are rejected in the store layer, not just the UI.
- Deleting a project detaches references and soft-deletes; it never deletes
  shared media files silently.

### 7.5 Migrations

No SQL migrations. Each store declares `schema_version`. On load:
missing fields get defaults; unknown future versions raise a clear error;
before any structural rewrite, write a timestamped `.bak` copy (matching
existing `data/*.bak-*` practice). New fields are additive.

---

## 8. Proposed service design

### 8.1 Workspaces

`backend/studio_workspaces.py`
- `WORKSPACES = ("ads", "youtube")`
- descriptors: id, label, subtitle, capabilities, default route
- `get_active()/set_active()` per browser client (cookie/localStorage is UI;
  server stores last active as a convenience only)
- `list_projects(workspace)`, `create_project`, `update_project`,
  `duplicate`, `archive`, `soft_delete`
- project schema: id, workspace, title, status, stage, channel_id, topic_id,
  thumbnail_media_id, created/updated, archivable, tags, metadata

### 8.2 Provider router

Extend `studio_providers.BaseProvider` with `categories()` and
`capabilities_for(category)`; register only real adapters:

- `higgsfield` — image, video (existing)
- `higgsfield_mcp` — image, video (existing, experimental)
- `fal`, `runway`, `luma` — image/video, **only after** their clients are
  wrapped in `BaseProvider` contracts and tested; otherwise leave unregistered
- `deepseek` — script/LLM (only when key configured; reuses Harness transport)
- `macos_say` — voice, local macOS only (detected; no cloud key)
- optional `elevenlabs` / `openai_tts` — voice, only when the owner configures
  a key; capabilities from the actual API, never assumed
- `ffmpeg_local` — rendering (not a network provider)

Workspace settings select a provider per category; unknown/unconfigured
providers show explicit empty states and block the action.

### 8.3 Renderer

`backend/studio_render.py`
- resolve ffmpeg: `DENNIS_FFMPEG` → `agent-skill-builder/bin/ffmpeg` → PATH
- scene-based composition: background image/video + character/main visual +
  overlays + VO + music + SFX + burned subtitles
- image scenes get deterministic pan/zoom (Ken Burns); no invented AI motion
- outputs: 1920×1080 30 fps (default), 1080×1920, 1080×1080; H.264/AAC MP4
- real progress from `-progress pipe:1`; cancellation kills the process group;
  errors captured verbatim (redacted); render registered in media library
- **not** a nonlinear editor: scene edits are per-scene regeneration/render

### 8.4 Pipeline / jobs

`backend/youtube_jobs.py`
- persisted jobs: `id, project_id, stage, type, state, progress, attempts,
  error, output_refs, created/updated/finished, cancel_requested`
- stages: research, competitor, topic, validation, script, approval,
  fact_check, storyboard, scene_approval, assets, voice, animation, assembly,
  thumbnail, qa, user_approval, publishing, analytics
- worker pool (2–4), one job per stage per project, idempotency keys
- retry failed jobs; cancel queued/running where safe; history retained
- no fake percentages: progress only where a tool reports it (ffmpeg,
  upload bytes); otherwise state + elapsed time

### 8.5 YouTube integration

`backend/youtube_oauth.py`
- installed-app OAuth 2.0 authorization-code + PKCE, loopback redirect
- client id/secret from env or `studio_secrets` (never frontend)
- scopes: `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`
- token store `data/studio/youtube/oauth.json` `0600`, refresh handled
  server-side; no Google password ever requested/stored
- if no client configured: connection reports `not_configured`; manual export
  remains available

`backend/youtube_publish.py`
- resumable upload (`upload/youtube/v3/videos`), thumbnail set, playlist
  insert, chapters in description, visibility/schedule (`publishAt` +
  `private`), language/audience/disclosure fields
- success requires a real `videoId`; otherwise state is `failed`/`uploading`
- publish record states: `draft/ready/scheduled/uploading/uploaded/failed`
- manual export builds a ZIP/folder: MP4 + thumbnail + metadata JSON + SRT

`backend/youtube_analytics.py`
- official `youtubeAnalytics/v2/reports` when connected; otherwise a manual
  mode where the owner enters values and they are labeled "Manual"
- no synthesized statistics, no placeholder numbers

### 8.6 Research

- YouTube Data API (via OAuth or API key) for real search demand/competitor
  stats when configured; every stored fact carries `source`, `fetched_at`,
  `sourced=true|false`
- AI analysis can produce **assessments** only; UI labels them "Estimate"
- manual research notes for unsourced work
- no invented RPM/search volume/subscribers

### 8.7 Master prompt / agents

- `channel-prompts.json` stores immutable versions (`id, version, body,
  created_at, note, is_default`); no prompt text in frontend
- task extensions stored separately per agent role and composed server-side:
  channel master → task extension → project context
- a "duplicate to channel" operation copies a version, does not alias it
- **MASTER PROMPT 01 is not present in this repository.** It must be supplied
  or seeded as an editable version; it will not be hardcoded.

---

## 9. Frontend plan

- Workspace switcher in the Studio shell:
  `STUDIO [ Ads Studio ] [ YouTube Studio ]`, active state, keyboard accessible.
- Ads keeps every existing tab and panel; only the nav label changes.
- YouTube gets its own left navigation (Overview, Channel Manager, Niche
  Research, Topic Studio, Script Studio, Storyboard, Asset Studio, Voice
  Studio, Animation Studio, Thumbnail Studio, Publishing, Analytics, Calendar)
  and its own panels inside `#studio-view`.
- Shared shell, shared modals (`openModal` pattern), shared toast/empty/error
  states, dark theme.
- Hash routing so `#studio/youtube/overview` deep-links; unknown hashes fall
  back to Overview. Switching workspace never reloads the page and never
  destroys the other workspace's DOM/state.
- New JS split into small IIFEs with one global (`window.YouTubeStudio`) plus
  pure state helpers in a `youtubeState.js` for node tests.

---

## 10. Risks & conflicts

| Risk | Mitigation |
| --- | --- |
| `studio_media` index is shared and has no workspace field | Additive fields; default `ads` on read; optional filters; never migrate/delete existing records |
| `setTab` assumes a fixed tab list and forces `fashion` fallback | Add workspace gate before fallback; keep existing tab ids; regression tests for every Ads tab |
| `Studio.refresh()` runs on view switch | Make YouTube init idempotent; do not reset Ads state |
| `app.py` is a 4.5k-line monolith | Add new route blocks in the existing style; avoid reordering existing conditions; factor handlers into modules |
| Job pruning (`MAX_JOBS=200`) could delete YouTube jobs if reused | Separate `youtube/jobs.json` store with its own bounds |
| No LLM/TTS configured by default | Explicit `not_configured` errors and empty states; never fake output |
| ffmpeg binary is x86_64 | Detect Rosetta failure; report clearly; allow `DENNIS_FFMPEG` override |
| Upload cap 25 MB / request cap 40 MB | Keep for browser uploads; large renders stay server-side; add a dedicated chunked upload only if needed |
| Existing `studio_v2.css` is tightly scoped to Ads markup | New YouTube CSS under `#studio-view[data-workspace="youtube"]`; don't edit Ads selectors |
| Untracked user work (fashion, freellm, composio) | Never reset/revert; additive edits only; run tests before/after |
| Existing test failure | `tests.test_studio_routes.test_providers_are_described_without_secrets` expects old credential keys (`env`,`fields` extra). Pre-existing; fix separately with owner approval |
| No master prompt file | Store as editable versioned data; flag missing input |
| No cloud storage | Local storage only; adapter interface can be added later |

---

## 11. Implementation sequence (staged, testable)

### Stage 2 — Workspace foundation  ✅ DONE (2026-11-09)
1. `backend/studio_workspaces.py` + `data/.../workspaces.json`. ✅
2. Workspace selector UI + hash routing + deep links. ✅
3. YouTube shell/nav/empty panels; Ads untouched. ✅
4. Tests: isolation, routing, existing Ads tab regression. ✅

### Stage 3 — Research & editorial
1. Channel Manager + versioned channel prompts.
2. Niche Research + competitor records + provenance.
3. Topic Studio (statuses, clusters, scoring, filters).
4. Script Studio (versions, approvals, citations, fact-check checklist).
5. Tests: CRUD, versioning, status transitions, no fabricated metrics.

### Stage 4 — Creative production
1. Storyboard + scenes (per-scene edit/regenerate/reorder/approve).
2. Asset Studio (generate/upload/replace/version/attach).
3. Voice Studio (adapter + preview + pronunciation + export).
4. Animation Studio (scene timeline, per-scene generation, transitions).
5. Thumbnail Studio (1280×720, variations, text overlay, approval).
6. Renderer + render jobs.
7. Tests: render fixtures, cancellation, media registration, provider mocks.

### Stage 5 — Publishing & analytics
1. Native OAuth + secure token store.
2. Publishing Manager + resumable upload + scheduling + thumbnails.
3. Manual export fallback.
4. Publishing Calendar (MWF default, editable).
5. Analytics (official or labeled manual).
6. Tests: mocked Google endpoints, auth failures, "no false success".

### Stage 6 — Automation
1. Pipeline orchestrator + retries/cancel/resume.
2. Agent prompt composition + task extensions.
3. Production monitoring/activity.
4. End-to-end acceptance run.

---

## 12. Acceptance mapping

- Ads preservation: regression suite over all existing tabs/routes/media/
  playlists/templates/providers + visual smoke.
- Workspace isolation: API tests prove YouTube queries never return Ads data
  and vice versa.
- Content pipeline: project→topic→script→storyboard→scene→asset→voice→render
  with explicit states and approvals.
- Publishing: OAuth-required upload; manual export; status derives from real
  API response; no "published" without `videoId`.
- Quality: no fake progress, no fabricated analytics, no silent deletion, no
  cross-workspace access, no broken Ads workflows.

---

## 13. Baseline verification (this audit)

- Studio Python tests: 95 run, **1 pre-existing failure**
  (`test_providers_are_described_without_secrets`: credential descriptor now
  includes `env` and `fields`; test expectation is stale). All other Studio
  tests pass.
- `tests/studioState.test.js`: 19/19 pass.
- No production code changed.

---

## 14. Open decisions required before Stage 2

1. **Master Prompt 01** is absent from the repo. Supply the text, or approve
   seeding an editable placeholder version in Channel Manager.
2. **Voice provider**: is macOS `say` acceptable as the default local adapter,
   or should Voice Studio require a configured cloud TTS key (ElevenLabs/OpenAI)
   and show a not-configured state until then?
3. **LLM provider**: confirm DeepSeek Harness credentials may be reused for
   script/research agents (server-side only).
4. **YouTube OAuth**: provide a Google Cloud OAuth client id/secret, or build
   publishing behind a feature flag with manual export only for now.
5. **Naming**: confirm the existing Studio is to be labeled "Ads Studio" while
   all existing tab keys/routes stay unchanged.
