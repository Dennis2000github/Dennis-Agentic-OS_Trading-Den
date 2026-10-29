# Agent Skill Builder — Audit, Upgrade & Verification

_Date: 2026-10-21 · Scope: existing Agent Skill Builder in DENNIS Agentic OS_

## 1. Audit findings (before changes)

The existing builder was **YouTube-only** and ended at "install into
`~/.claude/skills`". It did not contain the repository extractor or the Obsidian
Vault publishing that the upgrade brief assumed, so those pieces were added
rather than repaired.

What existed:

- A synchronous `/api/skill-builder/analyze` and `/api/skill-builder/build`
  flow, plus shell helpers in `agent-skill-builder/scripts/`.
- Four separate `yt-dlp` invocations per URL (metadata JSON, video id, title,
  subtitles), followed by an optional Gemini video-analysis script.
- Direct installation of the generated skill into `~/.claude/skills` with no
  review step and no draft/reject/publish lifecycle.
- No source cache, no async job state, no cancellation, no progress stages, no
  repository input, no Vault target, and no video reverse-engineering mode.
- A single Gemini call for generation, with no per-model timeout or health
  tracking.

## 2. Baseline measurements (real, not estimates)

Measured with `backend.skill_builder.video_meta/transcript` before the rewrite:

| Source | Metadata | Transcript | Total |
|---|---:|---:|---:|
| `Nm6rVRw8hAU` (captions failed, Gemini fallback) | 8.91s | 154.06s | **162.98s** |
| `dQw4w9WgXcQ` (cached SRT) | 3.79s | 13.74s | **17.53s** |

Generation was also slow and unstable before model-health handling:

- First live generation: 69.5s, but the output had fenced nested frontmatter.
- After prompt cleanup: 292.6s with default thinking.
- After forcing `GEMINI_GENERATION_THINKING=low`: still **249.6s**, because the
  model chain spent most of its time on unresponsive models.

## 3. Root causes

1. **Four `yt-dlp` runs per URL.** Metadata, id, title and subtitles each paid
   process startup, network and extraction cost.
2. **Expensive fallback transcription with no cache.** When captions were
   unavailable, every run repeated a long Gemini transcription.
3. **Unbounded Gemini model chain.** `gemini-3.8-flash` and `gemini-3.7-flash`
   accepted requests and did not answer within 60–120s; the chain then tried
   `3.6`, `3.5` and `3.5-lite` sequentially with a 300s per-request timeout and
   no health memory. A trivial "OK" request took 142s.
4. **No review/publish boundary.** Generation wrote directly toward
   `~/.claude/skills`; there was no editable draft, conflict check, atomic Vault
   write or verification.
5. **No shared pipeline for repositories or video.** Mode-specific validation,
   provenance and limitations were absent.

## 4. What was implemented

### Extraction (`backend/skill_sources.py`)

- One `yt-dlp` extraction per YouTube URL (metadata + caption URLs), then one
  caption fetch (`json3` / VTT / SRT).
- Repository retrieval through the GitHub API: README/AGENTS/CLAUDE/docs and
  manifests only, max 12 files, 24 KB per file, 96 KB total, 4-worker bound.
  Source code is not executed.
- Direct-video URLs are validated and limited to 80 MB for Gemini Files API.
- Content-addressed source cache (6h TTL) with provenance and version fields.
- SSRF guard: only public hosts, every redirect revalidated; private, loopback,
  link-local, reserved and multicast targets rejected.

### Jobs (`backend/skill_jobs.py`)

- In-process bounded worker pool, dedupe by source/action key, progress stages,
  elapsed time and cooperative cancellation.
- Job snapshots never contain credentials.

### Review + Vault publishing (`backend/skill_builder.py`, `app.py`)

- Generation always produces a **draft** in
  `agent-skill-builder/drafts/`; nothing is published automatically.
- Shared Review Generated Skill overlay exposes editable **Skill Name**,
  **Description** and the complete **Generated Skill / Prompt**.
- Autosave (debounced) persists edits; validation rejects unsafe names such as
  `../escape`, descriptions under 10 chars and oversized content.
- **Reject** deletes only the draft. Published skills and unrelated Vault
  content are untouched.
- **Add to Vault** resolves the real Vault path, detects conflicts (HTTP 409,
  no silent overwrite), writes atomically through the existing Vault
  confinement helpers, verifies byte-for-byte content and frontmatter, then
  removes the draft. The UI reports the actual absolute path.
- Failures preserve the draft and edits, so retry does not re-extract.

### Video reverse engineering

- Two objectives:
  - **Tutorial → Skill**: extracts the taught method, tools, steps, prompts,
    settings, constraints and timestamps. It explicitly does **not** produce a
    video-recreation template.
  - **Reference video → Skill**: teaches Acquire → Analyze → Extract → Adapt →
    Refine → Generate → Compare → Iterate → Save and includes a copy-ready
    generation prompt template.
- YouTube is analysed in place through Gemini `fileData`; direct files use the
  Files API. Visual failures are reported honestly and video mode refuses to
  produce a scene skill when no visual analysis succeeded.
- Optional character image is accepted only for Reference mode, inspected by
  Gemini, and used to add character-replacement guidance without inventing
  traits the image does not show.
- Provider output is a prompt/template for Seedance 2.5 or compatible tools;
  no provider is assumed to be connected and no output video is claimed.

### Gemini model health

- One bounded parallel probe when no model health is known (8s default).
- Per-model timeout (`GEMINI_MODEL_TIMEOUT`, default 90s; video 180s), overall
  chain deadline, transient-error fallback, cooldown for hung/unavailable
  models, and persistent health in `agent-skill-builder/.cache/model_health.json`.
- Reachable models keep the user's configured preference order; a proven model
  is preferred on later runs. Fast lite models do not silently replace full
  models just because their probe was quicker.

## 5. Post-change measurements

| Scenario | Before | After | Change |
|---|---:|---:|---:|
| YouTube extraction `Nm6rVRw8hAU` (no cache) | 162.98s | **12.75s** | −92% |
| YouTube extraction `dQw4w9WgXcQ` (no cache) | 17.53s | **3.55s** | −80% |
| Cached source | n/a | **0.011s** | — |
| API extraction job `Nm6rVRw8hAU` (live) | — | **12.36s** | — |
| GitHub repo extraction `pallets/flask` (live) | — | **1.97s** (7,363 chars, 3 docs) | — |
| Generation, default chain before health fix | 249.6–292.6s | — | — |
| Generation, first run with health probe | — | **27.0s** | −89% vs 249.6s |
| Generation, steady state | — | **12.7s** | −95% vs 249.6s |
| Reference video → skill (19s clip + character image) | — | **18.3s** | — |
| Tutorial video → skill (14min clip) | — | **28.4s** | — |

Video breakdown:

- Reference: fetch 3.03s, visual analysis 5.80s, generation 9.36s.
- Tutorial: fetch 0.01s (cached), visual analysis 20.08s, generation 8.22s.

The live generations used `gemini-3.5-flash-lite` because the full models were
temporarily `UNAVAILABLE`; the health chain selected it automatically after
bounded failures. Output was complete (7.5k–11.2k chars) with the required
sections.

## 6. Tests actually executed

| Command / check | Result |
|---|---|
| `../.venv/bin/python -m unittest tests.test_skill_builder_pipeline -v` | **28 passed** |
| `../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` | **127 passed, 0 failures** |
| `../.venv/bin/python -m unittest tests.test_server_security -v` | **18 passed** |
| `node --check js/skill-builder.js` | Passed |
| `node --test tests/*.test.js` | **3 suites / all checks passed** |
| Live HTTP extraction job | 12.36s, 16.6k-char transcript |
| Live HTTP generation jobs | 27.0s first, 12.7s steady state |
| Review lifecycle over HTTP | autosave, validation, publish + verify, Vault listing, 409 conflict, reject-only passed |
| Mode validation over HTTP | direct video in Source mode, repo in Video mode, tutorial + character image all returned 400 |
| Job dedupe + cancel over HTTP | dedupe reused the running job; cancel returned `cancelled` |
| Live reference video mode | 18.3s, visual analysis present, correct scene references, character image accepted |
| Live tutorial video mode | 28.4s, method sections and timestamps present, no recreation sections |
| Live direct-video Files API | 74.0s total (0.22s fetch, 48.95s upload/analysis, 24.58s generation), visual facts present |
| Browser E2E (`tests/e2e_skill_builder.py`, real Chrome) | **Passed**: generate → edit/autosave → publish/verify → generate → reject, no uncaught page errors |
| Static UI wiring | 49 `$()` IDs all exist in `index.html`; script version `20261021-skill-review` |

`tests/test_server_security.py` previously imported the removed `server.py`; it
now imports `app.py` and its 18 security tests pass.

## 7. Integration status

**Completed and live-tested**

- YouTube metadata + transcript extraction, cache and cache hit.
- GitHub repository extraction (README/pyproject, bounded docs).
- Async jobs, progress, dedupe, cancellation.
- Draft review, autosave, validation, conflict, atomic Vault publish and
  verification, reject.
- Vault-aware skill listing/reading.
- Gemini text generation with model health and fallback.
- YouTube visual analysis for both video objectives.
- Optional character image inspection (Reference mode).
- Direct video-file upload through Gemini Files API.
- Full browser UI flow through real Chrome/Playwright.

**Implemented but not live-tested**

- Forced visual-analysis failure path in the real UI (covered by unit test).

**Unavailable / not attempted**

- Seedance 2.5 or any video generation provider: no provider credentials are
  present on this machine, so no generated video was produced. The feature
  outputs prompts and manual handoff steps, as required.
- Several full Gemini models were intermittently `UNAVAILABLE`; this was
  handled by fallback, not fixed upstream.

## 8. Security controls

- Source text is wrapped in explicit `UNTRUSTED SOURCE … START/END` delimiters;
  the prompt says never to follow instructions inside it.
- Repository content is read as data only; no repo code is executed.
- URL schemes are limited to HTTP(S); private/loopback/link-local/reserved
  targets and unsafe redirects are rejected.
- Downloads are size- and time-bounded; YouTube/GitHub/media requests have
  timeouts.
- Draft/skill names are regex-validated; Vault writes use existing realpath
  confinement and atomic temp+rename.
- The Gemini key stays server-side and is never returned by status or job APIs.
- Publishing verifies the saved artifact before deleting the draft.

## 9. Files changed

New:

- `backend/skill_sources.py` — extraction, validation, SSRF/redirect guard, cache.
- `backend/skill_jobs.py` — bounded job runner.
- `backend/skill_builder.py` — drafts, review/publish, Gemini/model health, video.
- `tests/test_skill_builder_pipeline.py` — 28 pipeline tests.
- `tests/e2e_skill_builder.py` — real Chrome/Playwright review/publish/reject test.
- `requirements-dev.txt` — Playwright development dependency.
- `SKILL_BUILDER_AUDIT.md` — this report.

Modified:

- `app.py` — Skill Builder GET/POST routes, request validation, legacy adapters.
- `index.html` — mode selector, progress panel, shared review overlay.
- `css/style.css` — mode/progress/review styles.
- `js/skill-builder.js` — two modes, jobs, review/autosave/reject/publish.
- `agent-skill-builder/.gitignore` — ignore runtime drafts/cache.
- `tests/test_server_security.py` — retargeted from removed `server.py` to `app.py`.
- `STATE.md` — change log entry.

## 10. Remaining limitations

- Generation latency depends on Gemini availability; the health chain removes
  stalls but cannot make an unavailable model fast.
- `gemini-3.5-flash-lite` may be selected under capacity pressure, which can
  reduce prose quality compared with full Flash models.
- Video analysis quality depends on Gemini's video understanding and source
  access; unavailable visual details are deliberately left unknown.
- Seedance/video generation remains unavailable until provider credentials are
  supplied; only prompt templates and manual handoff are produced.
- The browser E2E requires Google Chrome plus the `playwright` Python package
  (`pip install -r requirements-dev.txt`); it is not part of stdlib-only runs.

## 11. How to run

```bash
cd "Dennis-Agentic-OS"
../.venv/bin/python app.py 8080
```

Open the existing **Agent Skill Builder** tab. Paste a YouTube/GitHub URL, or
switch to **Video Reverse Engineering**. After generation, review the name,
description and full skill, then **Reject** or **Add to Vault**. Approved files
land at `vault/Skills/<name>/SKILL.md`.

Tests:

```bash
../.venv/bin/python -m unittest tests.test_skill_builder_pipeline -v
../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
../.venv/bin/python tests/e2e_skill_builder.py   # real Chrome, live generation
node --test tests/*.test.js
```
