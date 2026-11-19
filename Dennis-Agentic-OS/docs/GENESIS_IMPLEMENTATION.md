# GENESIS — COSMOS Production Dashboard

**Status: implemented and verified — 2026-11-19 (feature branch `feature/cosmos-genesis`)**

GENESIS is the system-level production workflow of the DENNIS Agent OS COSMOS
layer. It is **not** a sixth intelligence agent: the five intelligence agents
(ANDROMEDA, POLARIS, PLEIADES, QUASAR, HELIOS) keep their identities, their
two permanent divisions (Ads Intelligence / YouTube Intelligence), and their
dashboards untouched. GENESIS owns everything after intelligence:

```
COSMOS agents  →  recommendations  →  backlogs  →  GENESIS  →  published videos
(intelligence)     (review cards)     (approval)   (production,  (destination-
                                                    render, QA,   confirmed)
                                                    approval)
```

---

## 1. Dashboard architecture

* **Route:** `#cosmos/genesis` — a direct, reload-safe deep link parsed by
  `js/cosmosState.js` (`GENESIS_ID`). The COSMOS agent rail renders a
  visually distinct GENESIS tab (`cosmos-tab-genesis`, gradient mark,
  "Production · Render · Publish") after the five agent tabs.
* **Frontend module:** `js/cosmosGenesis.js` — self-contained renderer that
  takes over the COSMOS host (`#cosmos-agent`) while its tab is active.
  `js/cosmosDashboard.js` delegates rendering (`render()`,
  `selectAgent()`, `reloadCurrentAgent()`) when the active view is GENESIS.
* **Tabs:** Overview · Production Queue · Projects · Render Center ·
  Publishing.
* **State:** every count, badge, preview and status is read from the
  persisted backend records — nothing is simulated, and no percentage
  progress is invented (render progress is stage-based).

## 2. Production lifecycle

```
queue (eligible backlog records)
  → intake (project created, source advanced in its own lifecycle)
  → in_production (brief, scenes, references, audio, export settings)
  → render (queued → preparing → [waiting_media] → assembling → complete|failed)
  → quality_review (real ffprobe checks; pass | warn | fail)
  → awaiting_approval → approved (explicit owner approval)
  → publishing (publishing | scheduled → succeeded | failed)
  → published (project + source backlog record advanced)
```

### Intake gates (backend-enforced)

| Source | Eligible | Intake action |
|---|---|---|
| Ads Product Backlog (`ads_backlog.py`) | `approved`, `ready_for_creation` | product → `in_production` |
| YouTube Topic Backlog (`youtube_topics.py`) | `scripted` **with** a linked script | topic → `in_production` |

* Archived/deleted records and rejected topics can never enter production
  (`409 not_eligible`).
* One active production project per source record (`409 duplicate_project`).
* Publishing advances the source record to `published` **only** after the
  destination API confirms the upload.

## 3. Data model

All GENESIS state lives under `data/cosmos/genesis/` (0700 dir, 0600 files,
atomic tmp+`os.replace` writes, one process-wide lock — the same conventions
as every other COSMOS store):

| File | Contents |
|---|---|
| `projects.json` | production projects (`gp_…`) |
| `renders.json` | render jobs (`gr_…`) |
| `publications.json` | publication attempts (`gpb_…`) |
| `oauth-tokens.json` | YouTube OAuth tokens (never served by any API) |
| `tmp/` | render workspaces (cleaned per render) |

Project highlights (full schema in `backend/genesis_store.py` /
`backend/genesis.py`):

* `source` — kind, id, status at intake, channel, recommendation/execution
  ids, config snapshot.
* `provenance` — division, agent, recommendation, execution, backlog item,
  product/topic id, script id, channel id, studio project id, created_via.
* `scenes[]` — title, description, `media_id` (Studio media library
  reference), optional `generation` spec, status.
* `references` — model/product/props media id lists (all validated against
  the media library).
* `audio` — voiceover / music / captions media ids + inline SRT.
* `settings` — aspect ratio, width/height, fps, export format, target
  platforms.
* `publishing_metadata` — title, description, tags, disclosure, sponsored
  flag, privacy, thumbnail.
* `quality_review` — verdict + per-check results (see §5).
* `approval` — owner approval record (who/when/note/review verdict).
* `render_ids`, `publication_ids`, `history[]` — append-only.

Projects **reference** existing records (backlog items, media library
entries, studio projects) — the original stores are never duplicated.

## 4. API routes

Dispatched through the existing COSMOS dispatcher: `app.py` forwards
`/api/cosmos/*` to `cosmos_api.handle`, which delegates the `genesis`
subtree to `backend/genesis_api.py` (pure `(method, path, query, body) →
(status, payload)`).

| Method + path | Purpose |
|---|---|
| `GET /api/cosmos/genesis/overview` | real counts + recent projects/renders/publications + engine + destinations |
| `GET /api/cosmos/genesis/queue?division=&q=` | eligible Ads products + scripted YouTube topics |
| `GET/POST /api/cosmos/genesis/projects` | list / intake |
| `GET/PATCH/DELETE /api/cosmos/genesis/projects/{id}` | detail / edit / soft delete |
| `POST …/projects/{id}/render` | start a real render |
| `POST …/projects/{id}/quality-review` | run ffprobe-based QA |
| `POST …/projects/{id}/approval` | owner approval / withdrawal |
| `POST …/projects/{id}/publish` | publish (explicit approval flag required) |
| `POST …/projects/{id}/archive` + `reopen` | archive / return to production |
| `GET …/renders?project_id=&status=`, `GET …/renders/{id}` | render state |
| `POST …/renders/{id}/cancel` | cancel (queued) / request cancel (running) |
| `GET …/render-engine` | honest ffmpeg/ffprobe availability |
| `GET …/publishing/destinations` | connection + authorization status |
| `POST …/publishing/youtube/authorize` · `/disconnect` | OAuth flow |
| `GET …/publishing/youtube/callback` | OAuth redirect (answers HTML) |
| `GET/POST …/publications…`, `…/retry`, `…/cancel` | publication history |

The COSMOS overview payload also exposes `counts.genesis` so the agent rail
badge shows live render/schedule activity.

## 5. Render adapters

Two clearly separated stages:

1. **AI media generation** — always delegated to the existing Studio
   provider registry and the shared asynchronous `StudioRunner` (wired in
   `app.py` via `genesis_render.set_runner(STUDIO_RUNNER)`). Scenes that
   declare a `generation` spec submit real provider jobs (`auto_download`)
   and the render waits for their real terminal states; failed provider
   jobs fail the render with the provider's own error. GENESIS never calls
   a provider itself and never fabricates media.
2. **Final assembly / rendering** — the FFmpeg adapter
   (`backend/genesis_render.py`):
   * resolves `ffmpeg`/`ffprobe` via the existing `skill_media.find_binary`
     (PATH + `DENNIS_FFMPEG_DIR` + `agent-skill-builder/bin`); when missing,
     the render center reports *unavailable* and refuses to run
     (`409 renderer_unavailable`) — nothing pretends to render;
   * validates the manifest (every scene must have real media);
   * encoder probing picks `libx264` → `h264_videotoolbox` → … → `mpeg4`
     and says which one it used; missing encoders fail honestly;
   * video clips are normalized (scale/pad/setsar/fps) and concatenated;
     still images are looped for their scene duration; missing clip audio is
     synthesized silent so the concat filter stays valid; voiceover/music
     are mixed; SRT captions are burned via the `subtitles` filter when the
     build supports it (otherwise a warning is recorded and QA flags it);
   * job states are real: `queued → preparing → waiting_media → assembling
     → complete | failed | cancelled`, with stage detail text — no
     synthetic percentages;
   * the finished file is probed (duration) and saved into the **existing
     Studio media library** (`studio_media.add_bytes`), then linked into the
     project; a failure stores the trimmed ffmpeg stderr.

Verified locally: FFmpeg 6.1.1-tessus, real 1920×1080/30 fps H.264+AAC MP4
renders with burned captions, image-loop scenes, mixed clip audio, and
honest failure reporting.

## 6. Quality review

`genesis.run_quality_review` probes the actual rendered file with ffprobe
and records every check:

render exists · output in media library · file exists · video stream ·
resolution vs export settings · aspect ratio · audio stream · audio
integrity · frame rate vs settings · duration · missing scene assets ·
captions (burned/supplied) · per-platform requirements (e.g. Shorts vertical
+ ≤60 s, Meta duration) · sponsored/affiliate disclosure · destination
metadata (title required, description warn).

Verdict: `fail` (blocking) / `warn` / `pass`. Owner approval requires a
non-fail review; publishing requires approval **and** a review that matches
the current render (`quality_review_stale` otherwise).

## 7. Publishing adapters + OAuth

* **YouTube** (implemented): installed-app OAuth 2.0 against Google's real
  endpoints — client from `GOOGLE_OAUTH_CLIENT_ID`/`_SECRET` or the existing
  `data/studio/youtube/google-oauth.json` file (same convention as
  `youtube_api`); auth URL with `youtube.upload` scope, offline access,
  one-time state; token exchange + refresh at `oauth2.googleapis.com`;
  refresh tokens stored **only** in `data/cosmos/genesis/oauth-tokens.json`
  (0600). Upload uses the **resumable** YouTube Data API v3 protocol; a
  publication is `succeeded` only when Google returns a real video id
  (`remote_id`, `remote_url`); thumbnails upload via the thumbnails endpoint
  (missing thumbnails become recorded warnings, never silent).
* **TikTok / Instagram / Facebook**: listed with `available: false` and an
  explicit "no adapter implemented" reason. They are never shown as
  connected.
* **Hard rules** (enforced in code, covered by tests):
  * tokens/secrets never appear in any API response or log — status
    responses carry booleans and scope lists only;
  * a saved OAuth client configuration does **not** count as authorized —
    authorization requires a stored refresh token from a completed flow;
  * publishing requires the explicit `approved: true` flag on the request
    (`409 approval_required`), project approval, and a passing review;
  * duplicates are rejected (`publication_in_progress`, `already_published`,
    `retry_required` for failed attempts — retry is explicit);
  * scheduled publications (`schedule_at`, future, ISO-8601) are stored as
    `scheduled` and only published when the scheduler thread finds them due
    (started by `app.py`); nothing auto-publishes;
  * approval and review freshness are re-checked in the upload thread
    immediately before uploading (`approval_revoked`, `quality_review_stale`).

**Verified:** the full flow with mocked Google endpoints (token exchange,
resumable init `Location`, PUT, video id) — real HTTP paths are used, and
failure paths record the destination's own error. A live end-to-end upload
still requires the owner's Google Cloud OAuth client + consent
(see §10).

## 8. Navigation consolidation

| Removed | New home | Deep-link behaviour |
|---|---|---|
| Ads Studio "Product Backlog" sidebar entry + panel | COSMOS Ads backlog block (full management) | `#studio/ads/backlog` → `#cosmos/andromeda` |
| YouTube Studio "Topic Backlog" sidebar entry + panel | COSMOS YouTube backlog block (full management) | `#studio/youtube/backlog`, `…/topics` → `#cosmos/andromeda` |
| YouTube Studio "Publishing" sidebar entry + panel | GENESIS Publishing Center | `#studio/youtube/publishing`, `…/calendar` → `#cosmos/genesis` |
| YouTube Studio "YouTube Intelligence" sidebar entry + panel | QUASAR's YouTube domain (+ research block) | `#studio/youtube/intelligence`, `…/research` → `#cosmos/quasar` |

* `js/studioWorkspaceState.js` no longer lists the removed sections and maps
  legacy ids through `LEGACY_REDIRECTS`; `js/studioWorkspaces.applyHash`
  performs the redirect (browser back/forward safe, hash rewritten).
* **Ads Studio keeps:** Create · Generate (five-step: Model / Product-Props
  / Dressing / Reference Video / Production) · Assets · Library ·
  Playlists · History · Provider Setup. Ads Create still loads its product
  selector from the COSMOS Product Backlog (`/api/studio/ads/create/context`
  → `ads_backlog`). The duplicate backlog screen (`#studio-panel-backlog`,
  `StudioCreate.showBacklog`, its JS and CSS) was removed; the backend
  (`ads_backlog.py`, `/api/cosmos/backlog/ads*`) is untouched and remains
  the single store.
* **YouTube Studio keeps:** Create · Templates · Projects (+Scripts) ·
  Analytics · Settings. `youtube_topics.py`, `youtube_research.py`,
  `youtube_scripts.py`, channels and all their endpoints are untouched —
  COSMOS now exposes the management UI for them.
* **COSMOS backlog management** (in `cosmosDashboard.js`, rendered once per
  active agent view): search, status filters, forward-only status
  transitions, add product (modal), edit product (PATCH), soft delete
  (DELETE), provenance per row; topics: search, filters, status
  transitions (backend rules surface their errors), details modal with
  provenance, script/project/published links, manual topic creation.
* **QUASAR research integration:** QUASAR's YouTube division renders the
  migrated "Research & Evidence" block — saved validations (18/25 rule,
  sources, flags), validation→topic conversion, competitor observations
  (`observed`/`assumed` basis) — using the existing
  `/api/studio/youtube/research|competitors` endpoints.

## 9. Testing

All executed 2026-11-19 on `feature/cosmos-genesis` (Python 3.14.5, Node 24):

* `python3 -m unittest tests.test_genesis` — **24 tests, OK** (run twice,
  clean bytecode): intake gates, duplicate/eligibility rejections,
  provenance, real-ffmpeg render (complete + failure paths),
  renderer-unavailable behaviour, quality review pass/fail + approval
  blocking, publish approval gates, mocked YouTube upload success with
  remote id + duplicate rejection, upload failure + explicit retry,
  scheduled-not-published, honest OAuth status, token secrecy, API surface,
  cosmos dispatch routing.
* Affected COSMOS/Studio/YouTube suites: `test_cosmos_api`,
  `test_cosmos_backlog`, `test_cosmos_config`, `test_cosmos_engine`,
  `test_cosmos_registry`, `test_cosmos_store`, `test_cosmos_workflows`,
  `test_ads_backlog`, `test_studio_create`, `test_studio_workspaces`,
  `test_studio_workspace_routes`, `test_youtube_editorial` — **163 tests,
  OK**.
* Full `python3 -m unittest discover -s tests` — 836 tests; failure set
  diffed against the pristine `HEAD` worktree: **identical pre-existing
  failures only** (test_upif 21, test_plugins 1, missing
  `backend.freellmapi` module 5 — unrelated to COSMOS/Studio/GENESIS; 3
  other base failures were already fixed by the in-tree
  `model_sources/registry.py` repair).
* JS: `node tests/cosmosState.test.js` — **15 OK** (GENESIS routing,
  refresh survival, tones), `node tests/studioWorkspaceState.test.js` —
  **21 OK** (removed sections + all legacy redirects), `studioState.test.js`
  19 OK, `panelState.test.js` 23 OK, `kanbanState.test.js` 9 OK,
  `agentDashboards.test.js` 8 OK.
* E2E: `python3 tests/e2e_ads_studio.py --serve` — **ALL ADS STUDIO E2E
  CHECKS PASSED** (five-step workflow intact, no JS exceptions).
* Browser verification (Playwright/Chrome): GENESIS tabs render, rail shows
  5 agents + GENESIS, redirects work (`#studio/ads/backlog` →
  `#cosmos/andromeda`, `#studio/youtube/publishing` → `#cosmos/genesis`,
  `#studio/youtube/intelligence` → `#cosmos/quasar`), removed sidebar
  entries absent, kept sections present, **zero console errors**, live
  intake → render (real ffmpeg) → QA pass → approval loop exercised on the
  running server.

## 10. Known limitations

* **YouTube publishing end-to-end** requires owner-provided Google Cloud
  credentials (OAuth client id/secret) and a one-time consent; the flow,
  token handling and resumable upload are implemented and unit-tested with
  mocked Google responses, but a real upload has not been performed in this
  environment (no credentials configured). Status reporting is honest about
  this (`configured` vs `authorized`).
* **Other destinations** (TikTok, Instagram, Facebook) have no adapters
  yet; the UI shows them as unavailable.
* FFmpeg assembly re-encodes to the project's export settings; it does not
  yet support transition effects, text overlays or multi-track mixing
  beyond voiceover+music.
* The GENESIS queue surfaces backlog records that are eligible *now*; it
  does not auto-pull recommendations — drafting stays an explicit owner
  action in the agent dashboards, by design.
