# YouTube Studio — Phase 0/1 Architecture Audit (Master Spec v1.0)

Date: 2026-11-12
Status: **Audit complete. No production code changed.**
Scope: verify the current DENNIS Agentic OS against the *Complete YouTube Studio —
Master Implementation Specification v1.0* (7 studio sections, channel default +
AI top-3 template recommendations, packaging engine, production workspace,
5 intelligence workflows, publishing/analytics).
Prior audit: `docs/YOUTUBE_STUDIO_AUDIT.md` (2026-11-09) — still accurate where cited.

---

## 1. Verification method (what was inspected, how)

Everything below was verified by reading the code and running the system —
not assumed from UI labels:

- **Runtime**: `app.py` (stdlib `http.server.ThreadingHTTPServer`, one port 8080),
  `backend/*` modules, `js/*` IIFE frontend, `css/*`, `tests/*`.
- **State of record**: `STATE.md` (project working spec, last updated 2026-11-12).
- **Live checks**:
  - Python suite: `python3 -m unittest discover -s tests -p 'test_*.py'` →
    **499 tests, 1 pre-existing failure** (`test_studio_routes.
    test_providers_are_described_without_secrets` — stale expectation; descriptor
    now correctly includes `env`/`fields`. Documented 2026-11-09; not caused by
    this work).
  - Node suites: `node --test tests/*.test.js` → **all pass**
    (studioState, studioWorkspaceState, panelState, kanbanState, agentDashboards).
  - OpenCode model inventory: `opencode models` lists **`nvidia/z-ai/glm-5.3-flash`**
    (plus `opencode/glm-5.3-flash(-free)`, openrouter/ollama GLM variants). The
    spec's default model is available through the existing OpenCode integration.
  - Kokoro TTS: `tts_service/models/` contains `kokoro-v10.onnx` + voices;
    `scripts/kokoro_service.py status` → **installed but not currently running**
    (stale PID). Startable with `scripts/kokoro_service.py start`; the TTS router
    also surfaces a start button.
- **Data dir**: `data/studio/` inspected (names/keys only, no secrets printed).

---

## 2. Verified architecture baseline (reusable)

| Layer | Implementation | Reusable for this spec |
| --- | --- | --- |
| Server | `app.py` monolith; routes forwarded to module dispatchers (`youtube_api.py` is a pure `(method, path, query, body) -> (status, payload)` dispatcher) | Pattern for all new YouTube routes |
| Auth | Single local owner token (`data/auth.json`); `X-Dennis-Token`/`?token=`; all `/api/*` owner-gated | Channel-scoped access on top |
| Persistence | Atomic JSON stores under `data/`, `0600`/`0700`, schema_version, RLock, soft delete (`youtube_common.py`, `studio_workspaces.py`) | **No SQL database exists; none needed** — follow the same conventions |
| Studio shell | `#studio-view` + workspace switcher `STUDIO [Ads Studio][YouTube Studio]`, hash deep links `#studio/youtube/<section>`, no reload (`js/studioWorkspaces.js`) | Keep; extend sections |
| Ads Studio | Fully working 5-step workflow, provider registry (`studio_registry.py`: auto/pollinations/google/higgsfield + 5 credential-only), free engines (`studio_free.py`: Pollinations images keyless; Gemini 2.5 Flash Image; Veo 3.0/2.0 video via Google key), per-step provider/model dropdowns | Untouched; image/video generation reused for thumbnails/scenes |
| Jobs | `studio_jobs.py` (queued/running/succeeded/failed/cancelled, idempotency, restart recovery, cancel queued-only) + `studio_free` in-memory jobs + `skill_jobs.py` | Job conventions for production pipeline |
| Media | `studio_media.py` library (SSRF-guarded downloads, 200 MB, Range serving; **no workspace field yet**) | Thumbnail/scene asset storage |
| TTS | Centralized router `backend/tts.py`: **kokoro (default, local)** / **elevenlabs (optional)** / browser fallback; 3-level selection (global → per-studio `apollo/ads/youtube` → preset); routes + streaming; `js/voiceRegistry.js` mounts a live voiceover panel in YouTube Studio (`#yt-voiceover`) | §11 of spec **already satisfied**; do not duplicate |
| LLM adapter | `backend/studio_llm.py`: OpenAI-compatible `chat/completions`; env `DENNIS_YT_LLM_*` else **DeepSeek Harness credentials**; honest `409 llm_not_configured`; used by `youtube_scripts.generate_script` | Extend for GLM-5.3-Flash default (see decisions) |
| Agents | OpenCode registered (`agent_registry.py`), headless `opencode run -m <model>` (`one_shot_argv`), per-session model launch plans (`model_launch.py`); provider/model lists live from the CLI | "Agent: OpenCode, Model: GLM-5.3-Flash" defaults |
| Editorial backend (Stage 3, done) | `youtube_channels.py` (channels, **versioned master prompts**, 9 agent-prompt roles, compose), `youtube_research.py` (5-criteria 18/25 validation, verdicts, sources/disclaimers, competitors), `youtube_topics.py` (status pipeline, dupes, priorities), `youtube_scripts.py` (versions, fact-check gate, AI generation), `youtube_api.py` dispatcher | Topic Backlog + Research + Scripts backend; extend with template/packaging fields |
| Channel seed | `backend/catalog/business-money-blueprint-0.1.0.json` + `business-money-blueprint-master-prompt.txt` — "The Business Money Blueprint": pillars, 3/week MWF schedule, 10–15 min duration, voice/style, monetization objective | Channel default config; matches spec §4.1 example |
| Memory | Vault (`backend/vault.py`: notes, search, graph, backlinks) + Memory Galaxy page (`js/memoryGalaxy.js`) + Hermes writes plans/runs/memories to `vault/` | Memory Galaxy workflow (§12.5) — extend, don't duplicate |
| Skills | `skill_registry.py` (version ledger), Agent Skill Builder pipeline, installed packs (`~/.agents/plugins`), vault `Skills/` (incl. `cartoon-audio-short` 2D animation kit) | Template "required skills" wiring; **no JadM.H character-animation skill exists** |
| Desktop | Electron shell (spawns `python3 app.py`, sandboxed) | Untouched |

---

## 3. Current YouTube Studio state (verified)

### 3.1 What works end-to-end today

- **Workspace shell**: switcher, per-workspace isolation (Ads data unreachable
  from YouTube routes and vice versa — tested), hash routing, project CRUD
  (create/list/search/filter/archive/restore/soft-delete) against
  `data/studio/workspaces/youtube/projects.json`.
- **Overview**: real counts, honest empty states.
- **Voiceover widget**: real TTS through the central router (Kokoro default).
- **Editorial backend (no UI yet)**: channels, master-prompt versioning,
  agent prompts, research validations, competitors, topics, scripts,
  script AI generation — all routed at `/api/studio/youtube/*`, dispatcher pure
  and unit-testable, seeding on first use from the Business Money Blueprint catalog.

### 3.2 What is broken / incomplete

1. **Frontend↔backend skew (biggest gap).** The entire Stage-3 editorial API has
   **zero frontend callers**. The Channel Manager / Niche Research / Topic
   Studio / Script Studio panels in `index.html` render static markup with dead
   buttons (`#yt-channel-create`, `#yt-validation-create`, `#yt-topic-create`,
   `#yt-script-generate` are bound nowhere).
2. **Double-panel quirk.** `js/studioYoutube.js setSection()` shows the static
   panel *and* the "Coming soon — Stage 3" placeholder simultaneously for those
   four sections.
3. **No tests** for `youtube_channels/research/topics/scripts/api` or
   `studio_llm` (Stage-3 backend shipped untested).
4. **`active_channel_id` is null** in settings; `data/studio/youtube/` was never
   created on this install (seed runs on first editorial request only).
5. **One stale pre-existing test failure** (§1) — unrelated module, fix separately.
6. Kokoro service not currently running (startable; UI offers the control).

### 3.3 Spec-to-current mapping (the 7 required sections)

| Master spec section | Current state |
| --- | --- |
| 1. Create (prompt-first) | **Missing** — no composer screen |
| 2. Templates | **Missing** for YouTube (Ads has its own generation-template catalogs) |
| 3. Projects | **Exists** (workspace project store); needs production stages/assets |
| 4. Topic Backlog | **Half**: backend pipeline exists; no visual cards, no template fields |
| 5. Publishing | **Missing** (no OAuth, no upload code anywhere) |
| 6. Analytics | **Missing** (no Analytics API code; honest placeholders only) |
| 7. YouTube Intelligence | **Missing** as workflows; building blocks exist (vault+Memory Galaxy, research store, LLM adapter) |
| Channel connection (§5) | **Missing** (Google OAuth client not configured; Composio has a YouTube toolkit entry but no connected account and no local executor) |
| Channel default + AI top-3 (§4) | **Missing** (no template entity at all) |
| Packaging engine (§7) | **Missing** (no thumbnails/titles/pairing/duration/hooks/score) |
| Production workspace (§9) | **Missing** (no renderer, no storyboard/scene stores, no pipeline jobs) |
| Voice (§11) | **Exists** (Kokoro default + ElevenLabs optional) — reuse as-is |
| Memory Galaxy (§12.5) | **Foundation exists** (vault + graph page + Hermes memory) |
| Background jobs (§18) | Conventions exist (`studio_jobs`, `skill_jobs`); YouTube production job store missing |

---

## 4. Required changes

### 4.1 Data (all additive JSON stores; no SQL)

New under `data/studio/youtube/`: `templates.json` (template library + custom),
`template-recommendations.json` (per-topic top-3 + explanations + ranking
version), channel-default template stored in channel record/workspace settings,
`packages.json` (per-topic video package: thumbnail concepts, title variants,
pairing reviews, duration plan, hooks, packaging score, approval records,
package versions), `production/` (per-project stage states, scene/asset job
refs), `publish.json`, `analytics.json`, `jobs.json` (production pipeline jobs,
separate bounds from Ads jobs). Extend topics with template selection +
packaging references. Extend `studio_media` records with optional
`workspace/project_id/asset_type` fields (defaults preserve Ads records).

### 4.2 Backend

1. Wire the existing editorial API to the frontend (no backend change needed for CRUD).
2. `youtube_templates.py` — template library, channel default, per-topic
   recommendation engine (content-based first; Muse hook later), snapshot on selection.
3. `youtube_packaging.py` — thumbnails (3 concepts via configured image
   providers), titles (5 variants), pairing review, duration planner,
   hooks, packaging score, published-style preview payload, approval + versioning.
4. `youtube_jobs.py` — production pipeline jobs (research consolidation →
   … → final approval), reusing studio job state machine conventions.
5. `studio_llm.py` — add provider profiles; default per spec = GLM-5.3-Flash
   (OpenCode headless `opencode run -m nvidia/z-ai/glm-5.3-flash`, or an
   OpenAI-compatible GLM endpoint when the owner configures a key). Keep
   DeepSeek as a selectable profile. Honest `not_configured` behavior stays.
6. `youtube_oauth.py` + `youtube_publish.py` + `youtube_analytics.py` — official
   Google OAuth 2.0 (PKCE, loopback), resumable upload, Analytics API; **feature-
   flagged off until the owner supplies a Google Cloud OAuth client**.
7. Renderer (`studio_render.py`) — scene-based ffmpeg composition; honest
   progress; Rosetta/x86_64 caveat on this machine.

### 4.3 Frontend

1. Rebuild YouTube sidebar to the 7 spec sections (Create, Templates, Projects,
   Topic Backlog, Publishing, Analytics, YouTube Intelligence) + Settings entry;
   retire the 14-section stage-badge list; keep hash deep links working.
2. **Create** prompt-first composer (channel selector, template selector,
   format/style/aspect/duration, agent=OpenCode, model=GLM-5.3-Flash,
   voice=Kokoro, Create button → creates topic/project; no backend workflow
   internals on screen).
3. **Templates** visual gallery (categories, preview, use/customize/duplicate).
4. **Topic Backlog** visual cards (filters, sorting, default vs AI top-3
   template selection, packaging status).
5. **Packaging editor** (published-style preview, thumbnails, titles, duration,
   hooks, score, approval).
6. Wire the four dead editorial panels to the existing API.
7. Publishing / Analytics panels behind the feature flag with honest empty states.
8. **YouTube Intelligence** hub (Oracle, Muse, Search OS, Review OS, Memory
   Galaxy link) — independently executable, shared channel context.

---

## 5. Dependencies & risks

| Risk | Mitigation |
| --- | --- |
| `app.py` is a ~5k-line monolith with uncommitted working-tree changes everywhere | Additive route blocks only; never reorder existing conditions; tests before/after each phase |
| Google OAuth needs client id/secret (not present) | Build behind feature flag; manual-export fallback; nothing fakes "connected" |
| GLM via OpenCode headless = subprocess per call (latency) | Acceptable for script/idea generation; queue via jobs; keep OpenAI-compatible profile as alternative |
| ffmpeg binary is x86_64 (Rosetta) on this Intel Mac | Detect failure, report honestly, `DENNIS_FFMPEG` override |
| No character-animation skill installed ("JadM.H" not found) | Template "required skills" stay declarative; wire what exists (cartoon-audio-short kit, image/video providers); never pretend a skill ran |
| Reordering the sidebar could break deep links/state | Keep `studioWorkspaceState.js` pure helpers node-tested; update tests with the section registry |
| Quota/cost | Paid generation (Veo, ElevenLabs, Higgsfield) stays behind explicit owner action, as today |

---

## 6. Implementation sequence (maps master-spec §22 to this codebase)

1. **Phase 2** — Navigation to 7 sections + Settings; wire the 4 dead editorial
   panels; fix the double-panel quirk; add editorial backend tests; seed channel.
2. **Phase 3** — Template library + channel default (data model + gallery UI).
3. **Phase 4** — AI top-3 recommendations (content-based; Muse-ready schema).
4. **Phase 5** — Topic Backlog visual cards + template selection integration.
5. **Phase 6** — Packaging engine (thumbnails/titles/pairing/duration/hooks/score/preview).
6. **Phase 7** — Approval + package versioning + handoff to Projects.
7. **Phase 8** — Production workspace: stages, storyboard, scene generation
   (Veo/Pollinations/Gemini image), Kokoro narration, ffmpeg renderer, LLM default GLM.
8. **Phase 9** — Intelligence hub (Oracle/Muse/Search OS/Review OS/Memory Galaxy).
9. **Phase 10** — Publishing + Analytics (feature-flagged until OAuth client).
10. **Phase 11** — Muse performance feedback into template ranking.
11. **Phase 12** — Full regression + acceptance tests (38 items of spec §21).

Each phase: inspect → reuse → implement → test → report → approval before risky changes.

---

## 7. Decisions required before Phase 2

1. **LLM default**: route generation through OpenCode headless with
   `nvidia/z-ai/glm-5.3-flash` (spec default), or keep the OpenAI-compatible
   adapter (DeepSeek today) and only add GLM as an option?
2. **Google OAuth client**: supply client id/secret now, or ship publishing/
   analytics feature-flagged with manual export until provided?
3. **Stale test fix**: repair `test_providers_are_described_without_secrets`
   (update expectation to include `env`/`fields`) with this work, or leave it?
4. **Sidebar restructure**: confirm replacing the 14-section stage list with the
   7 spec sections + Settings (deep links for old sections redirect sensibly).
