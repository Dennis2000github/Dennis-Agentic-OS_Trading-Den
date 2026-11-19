# Agent Skill Builder — Implementation Report (Phases 1–8)

Date: 2026-11-12
Scope: the approved directive "upgrade the Agent Skill Builder into an
evidence-grounded video reverse-engineering and executable skill-construction
engine". All work is in the active nested repo
`DENNIS AGENTIC OS_Trading Den/Dennis-Agentic-OS`. Nothing is committed; the
full change set lives in the working tree.

---

## 1. What was built

### 1.1 Backend (Phases 1–7)

| Module | Lines | Role |
|---|---|---|
| `backend/skill_media.py` | 356 | ffprobe/ffmpeg probe, adaptive sampling plan, scene detection, opt-in frame extraction, safe names, section download |
| `backend/skill_evidence.py` | 335 | Normalized, timestamp-aligned evidence model (`version: 1`), timeline stats, SRT/VTT + `[mm:ss]` cue parsing, prompt block, compact API summary |
| `backend/skill_analysis.py` | 294 | Reverse-engineering prompt, JSON repair, claim normalization (`observed|transcript|metric|hypothesis` + confidence), timeline consistency checks |
| `backend/skill_classify.py` | 351 | **Separate** style and format taxonomies/prompts, technical-value overrides, multi-output configurations, markdown renderers |
| `backend/skill_production.py` | 522 | Environment detection, smallest-viable-stack selection, dependency manifest (mandatory/conditional/external), never-auto-install policy, install plan |
| `backend/skill_compiler.py` | 536 | Package writer (SKILL.md, CONFIG.yaml, STRATEGY/STYLE/FORMAT/PRODUCTION/DEPENDENCIES/ANALYSIS.md, examples, tests, assets, scripts, manifest), YAML renderer, validation (hashes, unsafe paths, missing refs, credential patterns, required sections) |
| `backend/skill_tests.py` | 178 | Static checks (required gate / recommended report-only), replication prompt + evaluation (8 criteria), anti-copy n-gram overlap |
| `backend/skill_registry.py` | 112 | `data/skill_registry.json` version/provenance registry (outside the Vault) |

Modified in place:

- `backend/skill_builder.py` — 12-stage `STAGES`, analysis cache (30-day TTL),
  `_collect_evidence`, `_gemini_json`, `_build_intelligence`, expanded
  `run_generation` (fetch → evidence → intelligence → package → draft →
  optional replication), schema-v2 `create_draft`, package-aware verified
  `publish_draft` + registry hook.
- `backend/skill_jobs.py` — rewritten as a persisted, resumable job runner
  (`agent-skill-builder/.cache/jobs.json`). Unfinished jobs become
  `interrupted` after a restart and keep their `request` for resubmission.
- `app.py` — Skill Builder routes: request serialization
  (`extract_frames`, `replicate`, `replication_topic`, extended dedupe key),
  `GET /api/skill-builder/registry`, `GET /api/skill-builder/package?draft=`.

### 1.2 Frontend (Phase 8)

`index.html`

- Third composer mode `data-sb-mode="video"` (**Video Reverse Engineering**);
  `source` remains the default, `templates` unchanged.
- Objective radios enabled (**Tutorial → Skill** / **Reference Video → Skill**)
  with a dynamic `#sb-objective-note`.
- `#sb-character-field` / `#sb-character-url` (only video + reference).
- `#sb-frame-options` (video-only): `#sb-frames` and `#sb-replicate` are
  **opt-in per job**, plus `#sb-replication-topic`.
- `#sb-eyes` enabled in all modes (auto-checked in video mode).
- Right rail: **Recent jobs** panel (`#sb-jobs`, `#sb-jobs-refresh`).
- Review overlay: tab strip (`data-sb-rtab`) Skill / Analysis / Style / Format /
  Production / Dependencies / Tests / Package with `sb-review-panel` panels,
  including `#sb-review-tests` and `#sb-package-list` + `#sb-package-preview`.
- Header pill now reads **Evidence → Skill**; script cache-bust bumped to
  `js/skill-builder.js?v=20261112-video-reverse`.

`js/skill-builder.js` (1817 lines)

- `STATE_STAGES` mirrors the backend's 12 stages exactly (contract-tested).
- `setMode` handles video, toggling objective/character/frame sections and the
  URL label; `getObjective` reads the checked radio.
- `submitJob` sends `extract_frames`, `replicate`, `replication_topic`,
  `character_image_url`.
- Review: `setReviewTab` / `renderReviewPanels` (style/format/production/
  dependencies render as separate JSON documents; tests tab renders static
  checks with required-vs-recommended badges, package errors, replication
  criteria and content-overlap); `loadPackagePreview` + `showPackageFile`
  fetch `/api/skill-builder/package?draft=` and preview individual files.
- Jobs rail: `loadJobs` / `renderJobs` / `attachJob` (attach to a running job) /
  `resumeJob` (resubmit an interrupted job with its stored request) /
  `openJobDraft` / per-job cancel.
- `onJobDone` renders an evidence summary (technical facts, scene/frame counts,
  cuts/min, limitations) above the visual previews.

`css/style.css` — review tabs/panels, monospace `sb-review-pre`, test
check-list styling, package browser grid, jobs rail rows/status dots,
responsive fallback at ≤1180px.

### 1.3 Invariants preserved

- Reject / **Add to Vault** unchanged; name/description/content remain
  editable; autosave draft; conflict handling with explicit overwrite.
- Publication refuses when `validation.passed` is false; package writes are
  confined to `vault/Skills/<name>/` and verified per file.
- Single-file publish path for source-mode drafts unchanged
  (`run_generation` compiles a package only for `mode == "video"` or when
  evidence exists).
- Nothing is installed automatically; generated scripts are never executed;
  frame extraction is opt-in per job.
- Style and format remain separate classifiers with separate outputs.
- Every analysis claim carries `observed|transcript|metric|hypothesis` +
  confidence; no virality or causation claims anywhere (static check enforced).

---

## 2. Test results

New suites (all passing):

| File | Tests |
|---|---|
| `tests/test_skill_media_evidence.py` | 13 (incl. real-ffmpeg integration, skipped if binaries absent) |
| `tests/test_skill_intelligence.py` | 17 |
| `tests/test_skill_compiler.py` | 19 |
| `tests/test_skill_replication.py` | 8 |
| `tests/test_skill_jobs_resume.py` | 8 |
| `tests/test_skill_builder_ui.py` | 7 (static UI contract: modes, ids, tab/panel pairing, JS↔HTML id audit, stages parity, cache-bust) |

Pre-existing regression suites: `tests/test_skill_builder_pipeline.py`
(29 tests) unchanged and passing; `node --test tests/*.test.js` 5/5.

Full Python suite: **499 tests, 498 pass, 1 failure** —
`test_studio_routes.StudioRouteTests.test_providers_are_described_without_secrets`,
a pre-existing mismatch caused by earlier *uncommitted* Studio work
(`backend/studio_catalog.py` adds `fields`/`env` to provider credentials).
Not caused by this task and not fixed here per the no-unrelated-changes rule.
(`test_studio_mcp` timeout and `test_backend.DockerStatusTests.test_ready` are
environment-flaky and pass in isolation.)

Static checks: `node --check js/skill-builder.js` clean; JS↔HTML id audit
reports only the seven `sb-free-*` ids that the JS itself injects.

---

## 3. Bugs found and fixed during implementation

- Compiler manifest self-hash cycle → manifest now lists itself by name only
  (`manifest_file` / `manifest_bytes`).
- YAML renderer: booleans/None and inline lists-of-dicts now render correctly.
- `validate_package` required-section matcher now matches headings flexibly
  (`_has_heading`); recommended sections are reported but never gate.
- SRT/VTT multi-line cue parsing (arrow lines, continuation lines, cue
  numbers) and `[mm:ss]` bracket lines; timestamp-only bodies dropped.
- Transcript/text fallbacks and `view_count` propagation into evidence.
- `static_checks` required-vs-recommended gating.

---

## 4. Limitations

1. `faceless-videos-skill-v2` reference archive is **absent**, so the
   directive's reference-package acceptance test (Phase 7/9 sign-off) cannot
   run. **Blocked on: the archive path from the user.**
2. Analysis is Gemini-only by decision; video generators stay
   external/unconfigured.
3. Review panels render profiles as pretty JSON; the compiled package tab
   shows the human-readable STYLE.md / FORMAT.md / etc. content.
4. The UI contract tests are static (no browser automation in CI);
   `tests/e2e_skill_builder.py` exists for manual end-to-end runs.
5. Everything is uncommitted, deliberately, pending user review.

---

## 5. Rollback

- New files: delete the eight `backend/skill_*.py` modules (except
  `skill_builder.py` / `skill_jobs.py`), the six new test files, and
  `docs/SKILL_BUILDER_IMPLEMENTATION.md`; remove the STATUS UPDATE block from
  `docs/SKILL_BUILDER_PHASE0_AUDIT.md`.
- Modified files: `git checkout -- backend/skill_builder.py backend/skill_jobs.py app.py index.html js/skill-builder.js css/style.css`
  restores the pre-task working tree for this feature (this also reverts
  pre-existing unrelated uncommitted changes in those files, so prefer
  `git diff` review + selective `git apply -R` of the skill-builder hunks if
  the unrelated Studio work must be kept).
- Runtime artifacts created during use (`agent-skill-builder/.cache/jobs.json`,
  `.cache/media/`, `.cache/analysis/`, `data/skill_registry.json`,
  `vault/Skills/<name>/` packages) can be deleted independently; the app falls
  back to empty state.

---

## 6. Acceptance status vs. the directive

| Directive requirement | Status |
|---|---|
| Evidence-grounded reverse engineering (labelled claims, no causation) | ✅ enforced in prompts + normalizer + static checks + tests |
| Separate style & format classifiers/outputs | ✅ `skill_classify.py`, separate review tabs, package files |
| Executable skill packages (SKILL.md + CONFIG + tests + manifest) | ✅ compiler + validation + verified publish |
| Production/dependency reconstruction without auto-install | ✅ `skill_production.py`, install plan is data-only |
| Replication/anti-copy testing | ✅ opt-in per job, overlap guard, criteria evaluation |
| Persisted, resumable jobs | ✅ `skill_jobs.py` + UI jobs rail |
| Registry with provenance | ✅ `data/skill_registry.json` (outside Vault) |
| UI: video mode, tabs, package preview, review gates preserved | ✅ Phase 8 complete |
| Reference-package acceptance test | ⛔ blocked — needs `faceless-videos-skill-v2` path |
