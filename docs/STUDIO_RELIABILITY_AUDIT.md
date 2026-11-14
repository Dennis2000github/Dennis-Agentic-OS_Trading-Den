# Studio Reliability Audit — DENNIS Agent OS

**Date:** 2026-11-12
**Scope:** Studio generation console (classic Studio + Ads Studio workspace), backend provider pipeline, MCP connector, supporting workflows.
**Method:** Static audit → live diagnostics against the running backend → repairs → full regression (unit, route, browser E2E).

---

## 1. Executive summary

The Studio generation system's architecture is fundamentally sound: a stdlib HTTP backend with owner-token auth, a thread-based job runner with persistent JSON job records, an idempotency-guarded submission path, and a pinned 84-model Higgsfield catalog. Ads Studio exists as a first-class workspace inside the same Studio view and shares the same pipeline — it was preserved untouched except for two defect fixes.

The reported "connection errors / provider errors" were **real, not cosmetic**. The decisive finding:

> **Every stored provider credential in this installation is invalid.** Live, non-billable verification shows Higgsfield (`AUTHENTICATION_FAILED`, provider message "Invalid credentials"), the Google AI key (`AUTHENTICATION_FAILED`, "API key not valid"), and all of fal/Runway/Luma/MiniMax/Replicate unconfigured. All 7 historical generation jobs in `data/studio/jobs.json` failed with `authentication_failed`. The only fully operational generation path is the keyless Pollinations free engine.

Two systemic defects made this state invisible and the errors misleading:

1. **Configured was displayed as Connected.** The Setup registry reported `connected: true` for Higgsfield while every actual request was rejected. The old "Test" button for five providers returned a canned `ok: true` without touching the network.
2. **No diagnostics existed.** Nothing could tell the owner *which pipeline stage* failed (backend? credentials? provider API? catalog?).

Repairs delivered: a staged, non-billable **provider diagnostics service** with 14 explicit states and a new Setup-panel UI (per-provider "Check" + "Check all"); honest credential testing; stale-response and state-preservation fixes in the frontend; two data-loss bugs fixed (silent save no-op in the Ads model library, unbounded job polling in Ads); a credential-leak fix (Gemini key in URL); and 61 new tests. 588 Python tests, 5 JS test files and 2 browser E2E suites pass.

**Action required by the owner (not code):** re-save valid Higgsfield and Google AI keys in Provider Setup. No code change can make invalid credentials work, and the system now says exactly that.

---

## 2. Existing architecture (verified)

```
Browser (index.html fetch monkey-patch injects X-Dennis-Token on every /api call)
  └─ js/studio.js  (classic console)  ─ js/studioFashion.js (Ads 5-step) ─ studioYoutube.js
        │  GET /api/auth/session → token (media URLs use ?token=)
        ▼
app.py  (http.server, port 8080; _require_api_owner on every /api route;
         _reject_cross_site CSRF shield; 40 MB body cap)
  ├─ /api/studio/providers, models, jobs, uploads, library, playlists,
  │   templates, video-kit/*, fashion/*, free/*, registry/*, mcp/*, approvals, tools
  ├─ STUDIO_RUNNER = StudioRunner(REGISTRY)      ← engine registry (studio_providers.py)
  └─ studio_registry.py                          ← setup registry (credentials UI)
        ▼
backend/studio_providers.py   ProviderRegistry: higgsfield + higgsfield_mcp
backend/studio_jobs.py        JobStore (data/studio/jobs.json, atomic 0600) +
                              monitor thread + 4-worker poll pool, backoff 2→15s,
                              MAX_POLL_FAILURES=8, recover() on restart
backend/studio_higgsfield.py  Official API client (auth Key KEY:SECRET, idempotency-key,
                              presigned uploads, status/cancel paths URL-validated)
backend/studio_mcp.py         stdio JSON-RPC MCP session (5 MB cap, redacted stderr)
backend/studio_free.py        Ads free engines: Pollinations (keyless) + Gemini/Veo,
                              in-memory jobs, 6 h TTL
backend/studio_media.py       media store (SSRF-guarded downloads, 200 MB cap)
backend/studio_secrets.py     data/studio/credentials.json 0600, env vars win
backend/studio_catalog.py     pinned JSON catalogs under backend/catalog/
```

**Two registries, by design:** the *engine* registry (`studio_providers.py`) holds adapters that can actually generate; the *setup* registry (`studio_registry.py`) holds credential definitions for 9 providers. The mismatch between them was the source of the "provider shows in the UI but fails" class of errors.

**Ads Studio** (five-step ad workflow: Model → Product/Props → Dressing → Reference Video → Production) is implemented by `js/studioFashion.js` + `backend/studio_fashion.py` + `backend/studio_free.py` + `studio_model_library.py`. It shares the job runner, media library, auth and polling contract with classic Studio. **It was preserved**; only defects were repaired.

### Frontend → backend route map (all verified, no missing handlers)

Every studio API call in `js/*.js` resolves to a real handler in `app.py` (GET 1259–1470, POST 2598–3030, PATCH 4570–4590, DELETE 4630–4720). Auth: all routes owner-gated; only `/api/auth/session` is public. Server-side routes with no frontend caller (test-only): `/api/studio/youtube/*` surface, `/api/studio/tools`, `/api/studio/approvals`, legacy `/api/studio/providers/<id>/credentials` POST, `GET /api/studio/workspaces/active`.

---

## 3. Provider integration inventory (honest status)

| Provider | Adapter | Registered engine | Credential test | Diagnostics verdict (live) |
|---|---|---|---|---|
| Higgsfield | `studio_higgsfield.py` — complete (submit/poll/cancel/upload/test) | yes | real (presign request) | **AUTHENTICATION_FAILED** — stored key/secret rejected by provider |
| Higgsfield MCP | `studio_mcp.py` + `HiggsfieldMcpProvider` | yes (experimental) | real when configured | NOT_CONFIGURED (no MCP server configured) |
| Google AI / Veo | `studio_free.py` — complete for Ads workflow | yes (free engine) | real (models list) | **AUTHENTICATION_FAILED** — stored key invalid |
| Pollinations | `studio_free.py` | yes (keyless) | reachability probe | **OPERATIONAL** (host reachable, 1.3 s) |
| Auto | routing layer over free engines | yes | via google/pollinations | follows the above |
| fal.ai | `studio_fal.py` client only — **no BaseProvider adapter, unreachable dead code** | no | real via client (used by diagnostics) | NOT_CONFIGURED |
| Runway | `studio_runway.py` client only — dead code | no | real via client | NOT_CONFIGURED |
| Luma | `studio_luma.py` client only — dead code; no upload support | no | real via client | NOT_CONFIGURED |
| MiniMax | no adapter at all | no | none possible | NOT_CONFIGURED (presence-only checks) |
| Replicate | no adapter at all | no | none possible | NOT_CONFIGURED (presence-only checks) |

Nothing was auto-enabled. fal/Runway/Luma clients were **not** registered as generation engines (they lack a `BaseProvider` wrapper and catalog wiring); their credentials are verified honestly by diagnostics and reported as `PARTIALLY_OPERATIONAL` — "authentication verified, generation adapter not wired."

---

## 4. Root causes identified (per defect)

Severity scale: Critical / High / Medium / Low.

### D1 — Critical · Stored credentials invalid; UI claimed otherwise
- **Files:** `data/studio/credentials.json`, `backend/studio_registry.py` (`_connected`, `registry()`), `js/studio.js` (`renderRegistry`).
- **Evidence:** `data/studio/jobs.json` — 7/7 jobs `authentication_failed` ("Invalid credentials", 2026-10-28 → 2026-11-11). Live `POST /api/studio/registry/test {provider:higgsfield}` → `authentication_failed`. Live diagnostics → same. Google key likewise rejected.
- **Root cause:** status conflation — `connected` meant "credentials file has values", never verified against the provider; Setup displayed "Connected" from that flag.
- **Correction:** staged diagnostics (`backend/studio_diagnostics.py`) verify auth with real non-billable requests; Setup panel now shows the verified state, failing stage, response time, and the recommended action. Registry rows keep "configured" semantics; only verified providers render as Connected in diagnostics.
- **Tests:** `tests/test_studio_diagnostics.py` (24 tests), route tests `StudioRepairRegressionTests`, live verification (§12).

### D2 — Critical · Ads model-library save was a silent no-op
- **File:** `app.py` `do_POST` (`/api/studio/fashion/model-library`).
- **Evidence:** two consecutive `if path == …model-library` blocks; the first returned `list_library()` unconditionally, making the `add_model` handler unreachable dead code.
- **Root cause:** copy/paste route duplication.
- **Correction:** removed the shadowing block; POST now persists and returns `{"model": …}`; GET unchanged.
- **Tests:** `test_model_library_post_saves_and_returns_the_model` (regression, route-level).

### D3 — High · Canned "Test OK" for unwired providers
- **File:** `backend/studio_registry.py` `test_provider()`.
- **Evidence:** fal/runway/luma/minimax/replicate returned `{"ok": True, "message": "Credentials stored…"}` with zero network I/O.
- **Correction:** fal/Runway/Luma now verify through their real clients (auth round-trip); minimax/replicate report `verified: None` with an explicit "authentication is not verified" message; auth failures return `ok: False`.
- **Tests:** `test_test_provider_honest_about_missing_adapters` (rewritten), `RegistryTestProviderHonestyTests` (new).

### D4 — High · Stale provider/model responses could overwrite the current selection
- **File:** `js/studio.js` `loadModels`, `loadModelDetail`, `chooseProvider`, `chooseModel`.
- **Evidence:** no request sequencing: switching providers A→B let a slow response for A overwrite B's model list; the detail guard only compared `modelId`.
- **Correction:** monotonic `S.requestSeq` epoch, bumped on every provider/model switch; all models/detail resolutions (success *and* failure) are dropped when their epoch is stale.
- **Tests:** covered by Ads Studio browser E2E (provider switching flows) + JS state tests; logic is race-guarded by construction.

### D5 — High · Transient failures wiped valid frontend state and mislabeled errors
- **File:** `js/studio.js` (`api`, `loadProviders`, `loadModels`, `loadModelDetail`, `generate`).
- **Evidence:** any fetch rejection cleared models/detail; every failure rendered as "Backend unreachable", even provider-side 4xx/5xx; no timeouts (a hung backend blocked requests indefinitely).
- **Correction:** `api()` now has a 45 s abort timeout and classifies network failures (`backend_unreachable`, `backend_timeout`) separately from HTTP/provider errors; `describeError()` maps error codes to actionable messages (401 → reload, `authentication_failed` → re-save credentials, `rate_limited` → wait, etc.); `loadModels` keeps the last known model list on refresh failures and only clears when the provider genuinely has no data; `loadProviders` never clears existing state.
- **Tests:** Ads Studio browser E2E ("No JS exceptions", credential error UX still actionable); JS syntax + full suite.

### D6 — High · Unbounded polling in Ads Studio job watcher
- **File:** `js/studioFashion.js` `watchJob`.
- **Evidence:** the poll interval swallowed all errors and never stopped — a deleted job (404) or a down backend polled every 2.5 s forever.
- **Correction:** failure budget of 8 (mirrors backend `MAX_POLL_FAILURES`), immediate stop + clear error on 404.
- **Tests:** full Ads Studio E2E (submit + poll lifecycle still green).

### D7 — Medium · Gemini API key leaked into download URLs
- **File:** `backend/studio_free.py` `veo_download()`.
- **Evidence:** `uri + "?key=" + quote(key)` — credential in URL query, visible to proxy/access logs.
- **Correction:** key now travels only in the `x-goog-api-key` header.
- **Tests:** `test_download_keeps_the_key_out_of_the_url` (new).

### D8 — Medium · fal health check could pass on network failure
- **File:** `backend/studio_fal.py` `test_credentials()`.
- **Evidence:** 404, status 0 and "any non-401/403" all returned `ok: True` — an invalid key on a retired endpoint reported as valid.
- **Correction:** only 2xx or a 404-from-authenticated-host pass; network/5xx now raise `verification_unavailable` (retryable) instead of success.
- **Tests:** `ClientProviderDiagnosticsTests.test_fal_auth_failure_is_honest` + fal presence in `check_all`.

### D9 — Medium · No diagnostics service or UI (mission gap)
- **Correction:** `backend/studio_diagnostics.py` (new, ~480 lines) + routes `GET /api/studio/diagnostics`, `POST /api/studio/diagnostics/check`, `POST /api/studio/diagnostics/check-all` + Setup panel with per-provider rows, verified-stage summary, last-checked, response time, "Check" and "Check all" actions.
- **Tests:** 24 unit tests + 6 route tests + live browser E2E.

### D10 — Low · Duplicate `ProviderRequestError` definitions
- **Files:** `studio_higgsfield.py` (own class) vs `studio_http.py` (shared). Left as-is deliberately: higgsfield's conversion path in `studio_providers.py:_from_request_error` keys off its own class; unifying is a refactor with regression risk and no behavioral gain today. Documented for the next refactor.

### D11 — Low · Asymmetric credential routes
- **Files:** `app.py` POST `/api/studio/providers/<id>/credentials` (higgsfield-only, legacy) vs DELETE (any secrets id). Preserved for backward compatibility (tests pin both); the registry routes are the supported surface. Documented.

### D12 — Low · `higgsfield_mcp` models silently empty on MCP errors
- **File:** `studio_providers.py` `HiggsfieldMcpProvider.models()` — swallows `ProviderError` → returns `[]`. Mitigated by the descriptor's `model_error` + notes; deeper fix deferred (MCP is experimental).

### D13 — Low · Stale test harness references
- `tests/e2e_workspace.py` spawned nonexistent `server.py` (fixed → `app.py`); `tests/studioWorkspaceState.test.js` asserted the pre-restructure YouTube sections (updated concurrently by the owner; verified passing).

---

## 5. Connection failures and their causes (decision table)

| Symptom | Actual cause | Where it surfaced | Now reported as |
|---|---|---|---|
| "Connection error / Backend unreachable" | Backend not running, or fetch had no timeout and hung | `js/studio.js loadProviders` | `backend_unreachable` / `backend_timeout` + "start backend + Refresh" |
| "Provider cannot be reached" | Provider network/TLS/DNS failure | jobs, tests | `NETWORK_ERROR` / `TIMEOUT` (retryable) at stage `api_reachability` |
| "Provider authentication failure" | Stored Higgsfield/Google keys invalid | all 7 historical jobs | `AUTHENTICATION_FAILED` at stage `authentication` + "re-save credentials" |
| "Model loading failure" | models/detail fetch wiped on transient error | `loadModels` | state preserved, actionable note |
| "Upload failure" | provider upload (presign/PUT) errors | `/api/studio/uploads` | normalized provider error codes |
| "Generation submission failure" | provider 4xx/5xx at submit | job create | `invalid_request` / `rate_limited` / `insufficient_credits` … with retryability |
| "Job status sync failure" | transient poll errors | monitor | bounded backoff (8 failures max), preserved |
| "Output retrieval failure" | download errors | `_store_outputs` | per-output `download_error`, job still succeeds |

---

## 6. Pipeline verification status

| Pipeline | Status | Verification |
|---|---|---|
| A Provider discovery | **Verified** | route tests + E2E (providers/models/catalog/detail render, 84 models) |
| B Credentials & auth | **Verified** (mechanism); owner must supply valid keys | secrets unit tests, diagnostics tests, no-leak tests |
| C File uploads | **Verified** | route tests (base64→provider→URL), free-upload E2E |
| D Generation submission | **Verified for Pollinations (live)**; Higgsfield blocked by invalid credentials (owner action) | job runner tests (validation, dedupe, idempotency), live free job |
| E Job lifecycle | **Verified** | queued/running/succeeded/failed/cancelled + recovery tests |
| F Result retrieval | **Verified** | media download/store/serve tests + E2E preview |
| G MCP | **Verified (isolation)** | mcp session tests; MCP failure cannot affect the official API path |
| H Studio tools / playlists / templates / Video Kit | **Verified** | route tests + collections tests + E2E |

Billable generation was **not** executed (per execution rules). The manual procedure is in §11.

---

## 7 & 8. Test results

**Baseline (before repairs):** 196 Python studio tests OK; 2 JS tests failing (stale YouTube-section assertions); Ads E2E failing on the registry count (masked by D9's new panel).

**Final (after repairs):**

| Suite | Result |
|---|---|
| Full Python suite (`tests/test_*.py`, discover) | **588 tests OK** |
| Studio-focused (14 modules) | 257 tests OK |
| Server security + backend | 100 tests OK |
| JS: studioState, studioWorkspaceState, agentDashboards, kanbanState, panelState | **all pass** |
| Browser E2E: Ads Studio (Chrome CDP, `--serve`) | **ALL CHECKS PASSED** |
| Browser E2E: Playwright re-check | **ALL PASSED** |
| `e2e_workspace.py` phase 1/2 | pre-existing failures on the workspace/agent-CLI surface (terminal focus, session routing, panel layout) — **out of scope**, UI drift from the YouTube-Studio rework; harness launchability restored |

**New tests added (61):**
- `tests/test_studio_diagnostics.py` — 24 tests: state contract, configured-vs-verified, per-provider staged verification with mocked HTTP, isolation in `check_all`, registry honesty.
- `tests/test_studio_routes.py::StudioRepairRegressionTests` — 7 tests: model-library POST save (D2), diagnostics routes (auth, shape, isolation, 400).
- `tests/test_studio_free.py` — Veo key-out-of-URL regression (D7).
- `tests/test_studio_registry.py` — rewritten honesty test (D3).
- `tests/e2e_ads_studio.py` — diagnostics panel browser checks.

---

## 9. Files modified

| File | Change |
|---|---|
| `backend/studio_diagnostics.py` | **new** — staged provider diagnostics service |
| `app.py` | import + 3 diagnostics routes; removed duplicate model-library POST block (D2) |
| `backend/studio_registry.py` | honest `test_provider()`; `registry_providers()` helper |
| `backend/studio_free.py` | `veo_download()` header-only auth (D7) |
| `backend/studio_fal.py` | honest `test_credentials()` (D8) |
| `js/studio.js` | request-epoch guards (D4), error classification + timeouts + state preservation (D5), diagnostics UI |
| `js/studioFashion.js` | bounded job polling (D6) |
| `index.html` | Provider diagnostics panel in Setup |
| `css/studio-v2.css` | diagnostics + status-tone styles |
| `tests/test_studio_diagnostics.py`, `tests/test_studio_routes.py`, `tests/test_studio_free.py`, `tests/test_studio_registry.py`, `tests/e2e_ads_studio.py` | new/updated tests |

Unchanged by design: `studio_jobs.py`, `studio_providers.py`, `studio_higgsfield.py`, `studio_contracts.py`, `studio_secrets.py`, `studio_media.py`, `studio_mcp.py`, Ads workflow logic, all user data.

---

## 10. Fixes implemented (summary)

1. Staged provider diagnostics (backend service + API + Setup UI) — D1/D9.
2. Honest credential testing everywhere — D3/D8.
3. Frontend stale-response immunity + state preservation + actionable errors + timeouts — D4/D5.
4. Ads model-library save restored — D2.
5. Bounded Ads job polling — D6.
6. Credential-leak fix (Veo) — D7.
7. Test harness repairs — D13.

---

## 11. Remaining limitations & manual live-generation procedure

**Limitations (honest):**
- Invalid credentials are an **owner action**, not a code fix. The system now pinpoints this; nothing else can be verified end-to-end for Higgsfield until valid keys are saved.
- fal/Runway/Luma adapters remain unregistered (clients exist; no `BaseProvider` wrapper, no catalogs). Diagnostics verify their credentials only. Wiring them is future work with a clear template (see §13).
- MiniMax/Replicate have no clients; diagnostics = presence-only, explicitly unverified.
- MCP availability is in-memory; after a backend restart an explicit Connect is required (existing behavior, documented).
- `e2e_workspace.py` phase failures are pre-existing workspace-UI drift, untouched here.

**Manual live-generation verification (billable — requires explicit owner authorization):**
1. Save valid credentials in Provider Setup → run "Check" → expect `OPERATIONAL`.
2. Classic Studio: select Higgsfield → a cheap text-to-image model → minimal prompt → Generate → confirm job reaches `succeeded`, output downloads to Library.
3. Ads Studio: free engine (Pollinations) generates immediately; for Higgsfield video use one short `text_to_video` job.
4. Confirm the job appears in History with `provider_request_id` persisted and cancel behaves as `queued_only`.

---

## 12. Live provider verification results (2026-11-12, non-billable)

Ran against a live instance of the repaired backend (`POST /api/studio/diagnostics/check-all`):

| Provider | Status | Stage | Latency |
|---|---|---|---|
| auto | AUTHENTICATION_FAILED | authentication | 508 ms |
| pollinations | **OPERATIONAL** | generation (reachability verified) | 1337 ms |
| google | AUTHENTICATION_FAILED | authentication | 567 ms |
| higgsfield | AUTHENTICATION_FAILED | authentication | 397 ms |
| fal / runway / luma / minimax / replicate | NOT_CONFIGURED | configuration | ≤1 ms |

Provider messages (redacted, from the providers themselves): Higgsfield — "Invalid credentials"; Google — "API key not valid. Please pass a valid API key."

**Interpretation:** the "Studio shows errors" report was accurate — with these credentials, no paid generation can succeed. The repaired system now proves it in one click instead of failing at job time.

---

## 13. Recommended next improvements

1. **Wire the fal adapter** (highest value): add `FalBaseProvider` around `FalClient` + a pinned fal model catalog; its queue API maps 1:1 onto the existing runner (submit/status/cancel URLs from the submit response). Runway/Luma follow the same pattern; Luma needs the URL-input (no upload) UI hint already supported by `capabilities.upload=false`.
2. **Persist last diagnostic results** to `data/studio/diagnostics.json` so the Setup panel shows history across restarts, plus a small ring of past runs (mission option: diagnostic history).
3. **Retry controls in the UI**: a "Retry" button on failed jobs whose error is `retryable` (backend already keeps `idempotency_key`, so a retry reuses the same key — no duplicate paid jobs).
4. **Credential health surfacing**: after a job fails with `authentication_failed`, auto-offer the Setup "Check" for that provider.
5. **MCP session re-connect on demand**: lazily re-establish `_MCP_SESSION` when a poll finds it dead (currently requires explicit Connect after backend restart).
6. **Fix the workspace E2E drift** (`tests/e2e_workspace.py`) in a workspace-scoped pass.

---

*Prepared as part of the Studio reliability mission. All "verified" claims above correspond to a named passing test or a documented live run; everything else is labeled as assumption or owner action.*
