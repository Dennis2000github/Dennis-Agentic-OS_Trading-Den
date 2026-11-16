# Universal Shared Skill Registry & On-Demand Skill Loading — Phase 1 Architecture Audit

> Date: 2026-11-16 · Scope: map the existing DENNIS Agent OS architecture
> before implementing the Universal Shared Skill Registry directive.
> **No application code, dependency, database, Vault content or
> configuration was modified during this audit.** Read-only verification of
> the current working tree on branch `audit/perf-security` (49 pre-existing
> uncommitted files; none touched).
>
> Evidence: every claim below was verified by reading the current source.
> Line numbers refer to the working tree at audit time.

---

## 1. Executive verdict

The platform already has **four skill surfaces** but **no canonical registry**:

| Surface | File | What it does | Gap vs. the directive |
|---|---|---|---|
| Skill Builder pipeline | `backend/skill_builder.py` (2,396 lines) | Generates a skill from a video/source, stages it, publishes to the Vault, then registers version+provenance in `data/skill_registry.json` | Publication registry only — no capability tags, I/O spec, deps, agent compatibility, runtime, enable/disable |
| Skill Registry store | `backend/skill_registry.py` (112 lines) | `data/skill_registry.json`: name → path, version history (last 50), sources, source hash | Name-keyed (not a canonical ID), no metadata fields the directive requires |
| Skill Builder listing | `backend/skill_builder.py::list_skills` (:318) | Scans `vault/Skills/`, `agent-skill-builder/skills/`, `~/.claude/skills/` and reports `installed` | Filesystem scan, no central source of truth |
| Unified Connected Skills UI | `js/connectedSkills.js` (321 lines) | Merges 4 sources (Vault notes, Skill Builder registry, plugin packs, agent-linked SKILL.md), dedupes by name, source badges, deep links | Read-only merge; no load/execute/validate operations |

**Conclusion: extend in place.** A new `backend/skill_access.py` service in
front of the existing Vault + `data/skill_registry.json` +
`backend/agent_registry.py` + `backend/session_manager.py` +
`backend/sandbox.py` delivers all 8 Phase-3 operations without duplicating
any service. The registry store is extended with a **backward-compatible**
schema (v1 records stay valid). No new database, no new daemon, no new
framework.

One pre-existing hazard must be fixed in passing (it is not new code):
`backend/skill_registry.py::next_version` (:57-72) crashes with
`ValueError` when a stored version has only one dot-separated part (e.g.
`"Optional"`). The current file does not trigger it, but the extension
adds version reads on every discovery call, so `next_version` gets a
safe int() parse.

---

## 2. Existing architecture map

```
index.html + js/*.js (vanilla, IIFE modules, window.X = {init, refresh})
  ├─ js/connectedSkills.js   Tools → Connected Skills (4-source merge, read-only)
  ├─ js/skill-builder.js     Skill Builder authoring lifecycle
  ├─ js/installedPlugins.js  Plugin packs + per-agent skill linking
  └─ js/workspace.js         Project sessions, agent dropdown, Code & Preview
        │
        ▼
app.py  (stdlib http.server, single owner token, 5,759 lines)
  ├─ GET  /api/skill-builder/skills          skill_builder.list_skills()
  ├─ GET  /api/skill-builder/skill?name=     skill_builder.read_skill()
  ├─ POST /api/skill-builder/install         skill_builder.install_skill()   → symlink into ~/.claude/skills
  ├─ GET  /api/skill-builder/registry        skill_registry.list_skills()   → data/skill_registry.json
  ├─ POST /api/skill-builder/drafts/<id>/publish  publishes to vault/Skills/<name>/ + registry.register()
  ├─ GET  /api/plugins/skills                plugins.list_skills()           → agent skill dirs, deduped
  ├─ POST /api/plugins/install-skills        plugins.install_skills()        → symlink/copy into AGENT_SKILL_DIRS
  ├─ POST /api/plugins/connect-skill         plugins.exam_connect()          → copy SKILL.md into Vault
  ├─ GET  /api/agents, /api/agents/availability  agent_registry.available_agents()
  ├─ POST /api/project-sessions/<id>/start   session_manager → sandbox runner (PTY or Docker)
  ├─ POST /api/studio/approvals/<id>         studio_tools.ApprovalStore.decide()
  └─ WS   /ws                                backend/ws.py terminal frames
        │
        ▼
backend/
  skill_registry.py    data/skill_registry.json  (publication registry, v1)
  skill_builder.py     generation + drafts + Vault publication
  skill_sources.py     source fetch/cache (YouTube, GitHub, platforms)
  skill_jobs.py        persisted resumable job runner
  agent_registry.py    agents.json + CLI profiles + detection + one_shot_argv()
  execution_router.py  model→connection env resolution (cli_native|api_via_cli|api)
  routing.py           free/paid policy (MODE_FREE_FIRST … authorizations)
  compatibility.py     agent×protocol capability matrix + evaluate()
  session_manager.py   AgentSession: PTY/Docker lifecycle, per-session model switch
  sandbox.py           LocalPtyRunner + DockerRunner (locked-down container)
  vault.py             Obsidian-compatible notes, path-confined, .trash deletes
  plugins.py           plugin packs + AGENT_SKILL_DIRS per-agent skill linking
  studio_tools.py      agent-facing tools + owner ApprovalStore (guard pattern)
  projects.py          validate_path(): allowed-roots confinement
data/   skill_registry.json · agents.json · plugins_state.json · routing.json
        project_sessions.json · studio/{workspaces,jobs,media,approvals}.json
vault/  plain Obsidian folder; vault/Skills/<name>/SKILL.md is the canonical copy
~/.claude/skills, ~/.cursor/skills, ~/.codex/skills, ~/.gemini/skills,
~/.config/opencode/skills, ~/.hermes/skills, ~/.agents/skills   (agent views)
```

---

## 3. Phase-1 checklist mapping (the 9 areas the directive named)

### 3.1 Apollo / Vault storage and indexing — REUSE, mostly
- "Apollo" is the **voice core** (`index.html:917`, `js/galaxy.js`, "Hey
  Apollo" wake word) — it is *not* a storage/indexing service. The only
  storage subsystems are the **Vault** and `data/*.json` stores.
- `backend/vault.py`: Obsidian-compatible `.md` notes under `vault/`,
  path-confined (`resolve()` :108 rejects `..` escapes via realpath check),
  atomic writes (:123), 2 MB note cap, soft-delete to `vault/.trash/` (:242),
  search (:380) and link graph (:330).
- Skill canonical home today: `vault/Skills/<name>/SKILL.md`
  (`skill_builder.VAULT_SKILLS_SUBDIR = "Skills"`, :54).
- **Reuse:** canonical skill source stays in `vault/Skills/<name>/`; all
  metadata goes to `data/skill_registry.json`. No second copy anywhere.

### 3.2 Existing Skill Registry + install workflows — REUSE + EXTEND
- Publication registry: `backend/skill_registry.py` — `list_skills()` :33,
  `get(name)` :52, `next_version()` :57, `register()` :75. Store
  `data/skill_registry.json` currently holds 2 records (`taken` 1.35.0,
  `approved-skill` 1.35.0) with `path`, `current_version`, `published_at`,
  `sources[]`, `versions[]` (last 50, `source_hash`, `draft_id`, `files`).
- Install workflows (3, all existing):
  1. `skill_builder.install_skill(name)` :391 — symlink
     `vault/Skills/<name>` → `~/.claude/skills/<name>` (Claude Code only).
  2. `plugins.install_skills(name, selected_agents)` :481 — symlink (copy
     fallback) a plugin's skills into each chosen agent's dir from
     `AGENT_SKILL_DIRS` (plugins.py:53-61).
  3. `plugins.exam_connect(exam_id)` — copies a discovered SKILL.md into the
     Vault without installing any runtime (report-first install).
- **Reuse:** `skill_registry.register()` stays the version/provenance writer.
  The extension adds a parallel `access` block per skill (Phase 2) without
  changing these writers.

### 3.3 Agent Registry + capabilities — REUSE
- `backend/agent_registry.py`: `DEFAULT_AGENTS` :17 (antigravity, cursor,
  openclaw), `BUILTIN_CHAT_AGENTS` :59 (deepseek-harness), `CLI_PROFILES` :80
  (how each CLI is driven: models_cmd, model_flag, resume/continue flags),
  `HOST_AGENT_PROBES` :207 (11 detectable CLIs), `detect_installed()` :408,
  `available_agents()` :560, `one_shot_argv()` :641, `get_augmented_env()` :305.
- Capabilities recorded today are **connectivity** capabilities
  (`backend/compatibility.py::AGENT_MATRIX`, protocol → env_template, status
  `compatible|partial|incompatible|not_tested|auth_required|…`), **not**
  skill-consumption capabilities. There is no field anywhere for "native
  skill support / instruction injection / MCP / script execution / image
  input / browser preview" (verified: no such keys in `agents.json`,
  `CLI_PROFILES`, `AGENT_MATRIX`, `plugins.AGENT_SKILL_DIRS`).
- **Reuse:** agent identity, binary resolution, one-shot argv, env
  augmentation. **Extend:** a new declarative skill-capability table
  (Phase 4 adapter) — additive, keyed by agent id, defaults conservative.

### 3.4 Framework Router + task routing — PARTIAL; do not confuse layers
- `backend/routing.py` = **model** free/paid policy (free_first / paid_first /
  manual, per-agent + per-task overrides, paid-fallback authorization with
  24 h TTL :154-184). `backend/execution_router.py` = **transport**
  resolution (cli_native / api_via_cli / api). Neither routes *tasks*.
- Task routing that exists: `backend/hermes.py::HermesOrchestrator`
  — `decompose()` :81, `route()` :202, `assign_task()` :236,
  `delegate()` :455 → `_run_dispatch()` :504 → `_start_task()` :556
  (one sub-agent subprocess per task, bounded concurrency).
- A static "Framework Router" card exists in `index.html:638-705`
  (LangChain/LangGraph/LlamaIndex/CrewAI pills, mostly hardcoded).
- **Reuse:** Hermes `route()`/`delegate()` is the natural hook for skill
  discovery in routing (Phase 5). The model/provider routing files stay
  untouched (directive: "Do not change model/provider routing").

### 3.5 Agent execution manager — REUSE
- `backend/session_manager.py::AgentSession` :235 — PTY session per agent,
  `start()` :421, `write()` :516, `resize()` :558, `stop()` :566, file-watch
  hooks :283, SSE/WebSocket emit :384.
- `backend/sandbox.py` — `LocalPtyRunner` :303 / `DockerRunner` :336 behind
  one interface; Docker path runs non-root, CPU/memory/pids limits, dropped
  capabilities, `no-new-privileges`, only the validated project folder at
  `/workspace`, secret env allowlist :54-62 (provider keys only).
- `backend/runner.py` — dev-server supervisor for Code & Preview.
- **Reuse:** skill execution goes through `session_manager` / `sandbox`
  runners — never a new ad-hoc `os.system`. `skill_builder._agent_author()`
  (:833) already shows the sanctioned pattern: resolve agent →
  `one_shot_argv` → `_run(argv, timeout, env)` → capture output.

### 3.6 MCP + tool integration — REUSE the approval spine
- `backend/studio_tools.py` — agent-facing tool surface with **owner
  approval queue**: read-only tools run immediately; mutating tools
  (`MUTATING` :23) create a pending approval the owner decides in UI or via
  `POST /api/studio/approvals/<id>` (app.py:3572). `ApprovalStore` :160,
  `data/studio/approvals.json`.
- `backend/studio_mcp.py` — minimal MCP stdio client (`initialize`,
  `tools/list`, `tools/call`, timeouts, redacted stderr).
- `backend/composio.py` — external app/tool catalog + connections.
- `scripts/studio_mcp_shim.py` — bridges the tool surface to MCP clients.
- **Reuse:** the Studio approval pattern is the model for "require approval
  for risky operations" (Phase 6). MCP client code is available if a skill
  is exposed as tools; instruction injection is the primary path.

### 3.7 Dependency + runtime management — REUSE POLICY
- `backend/skill_production.py` — dependency manifest with mandatory /
  conditional / external split and a **never-auto-install policy** + install
  plan (from the Skill Builder phases).
- `backend/preflight.py` — readiness checks (Docker, Node, build tools,
  agent images), exposed at `GET /api/system/readiness`.
- `skill_builder.setup()` :867 — workspace installer (deps check, ffmpeg
  fetch, skill links).
- **Reuse:** `check_dependencies` must mirror `skill_production`'s
  mandatory/conditional/external model and must not auto-install optionals.

### 3.8 Permissions, sandboxing, execution approval — REUSE + EXTEND
- Owner auth: `app.py::_require_owner` :860 (token via
  `backend/auth.py::AUTH.owner_for`).
- Cross-site guard: `app.py::_is_cross_site` :870 (Sec-Fetch-Site / Origin).
- Path confinement: `vault.resolve()` (vault.py:108), `projects.validate_path`
  (projects.py:58, allowed-roots), `skill_sources.validate_public_url`
  (SSRF guard), `sandbox` container hardening, `SECRET_ENV_ALLOWLIST`
  (sandbox.py:54).
- Approvals: `studio_tools.ApprovalStore` (§3.6).
- **Reuse:** skill path validation = `vault.resolve()`-style confinement +
  `projects.validate_path()` for workspace-scoped file access. Risky-op
  approval = `ApprovalStore` pattern. Secret isolation = existing env
  allowlists; skill instructions/artifacts get a credential-pattern scan
  (the compiler already has one: `skill_compiler` validation "credential
  patterns", per docs/SKILL_BUILDER_IMPLEMENTATION.md §1.1).

### 3.9 Studio + workspace artifact storage — REUSE
- Studio: `backend/studio_workspaces.py` — `data/studio/workspaces/<id>/`,
  registry `data/studio/workspaces.json` (:35-36); jobs
  `backend/studio_jobs.py` with `_store_outputs` :385 (≤8 outputs, auto
  download into the media library); media served at
  `GET /api/studio/media/<id>` (app.py:1543).
- Vault assets: `GET /api/vault/asset?path=` (app.py:1851) — token-guarded.
- **Reuse:** skill-generated artifacts return to the originating workspace
  via the project-session workspace + Studio media library conventions
  (store record + served URL), not a new artifact store.

---

## 4. What does NOT exist (must be built, minimally)

1. **Canonical skill registry** with the directive's metadata fields
   (capability tags, I/O spec, runtime deps, compatible agents, execution
   requirements, install/validation status, update metadata, enable/disable).
2. **Universal Skill Access API** — the 8 operations
   (`search_skill`, `resolve_skill`, `load_skill`, `check_dependencies`,
   `check_agent_compatibility`, `execute_skill`, `validate_output`,
   `release_skill_context`) exist nowhere (verified: no such identifiers in
   `backend/`).
3. **Agent skill-capability model** — today only CLI/protocol capabilities
   exist (§3.3).
4. **Skill-aware task routing** — Hermes routes tasks to agents by role,
   with no registry lookup (§3.4).
5. **Skill execution sandbox/approval wrapper** — the primitives exist
   (sandbox, approvals, path confinement) but no skill-scoped composer.
6. **img2threejs** — zero references repo-wide (verified). The GitHub
   source `https://github.com/img2threejs/img2threejs` is not yet vendored;
   a source URL alone is not a runnable skill until it is fetched, examined
   report-first (the `plugins.examine_*` pattern) and registered with its
   real metadata.

---

## 5. Proposed component map (Phase 2–9 target)

New files (all additive; nothing existing is renamed):

| File | Role | Reuses |
|---|---|---|
| `backend/skill_access.py` | The 8 Phase-3 operations; progressive loading; execution context | `skill_registry` (store), `vault` (paths), `skill_builder` (SKILL.md parse), `skill_production` (deps model), `agent_registry` (binaries/env), `session_manager`/`sandbox` (execution), `studio_tools.ApprovalStore` (risky ops) |
| `backend/skill_capabilities.py` | Declarative per-agent skill-consumption capability matrix + format support | `agent_registry` ids, `plugins.AGENT_SKILL_DIRS` |
| `backend/skill_execution.py` | Skill-scoped runner: path validation, workspace scoping, timeout/cancel, log capture, secret scan, cleanup | `sandbox`, `projects.validate_path`, `skill_jobs` (cancel/state pattern), `vault.resolve` |

Extended files (minimal, backward-compatible):

| File | Change |
|---|---|
| `backend/skill_registry.py` | `register()` accepts optional `meta` block; `get()`/`list_skills()` surface it; `next_version()` hardened; **v1 records keep working** (missing fields default) |
| `app.py` | New `/api/skills/…` route block following the existing if-chain + `_require_owner` convention (reads + writes) |
| `js/connectedSkills.js` | Registry tab gains the Phase-8 columns/actions; new fetch calls join the existing `Promise.all` merge |
| `backend/hermes.py` | Optional: `route()` consults `skill_access.search_skill` when a task names a capability (Phase 5; Hermes behaviour otherwise unchanged) |

Explicitly NOT touched: `routing.py`, `execution_router.py`,
`compatibility.py` (model/provider routing), `model_launch.py`,
`deepseek_*`, agent CLIs, Vault note format, existing dashboards/CLI
sessions.

Data flow (Phase 5 happy path):

```
user request → Hermes route()/skill-aware router
  → skill_access.search_skill(capability)        (metadata only)
  → rank + check_agent_compatibility(agent_ids)  (capability matrix)
  → resolve_skill(id)                            (integrity + enabled check)
  → load_skill(id, agent_id)                     (SKILL.md + required refs/scripts)
  → check_dependencies(id)                       (mandatory only; optionals NEVER auto-installed)
  → execute_skill(id, ctx, approval=…)           (session_manager/sandbox; ApprovalStore for risky ops)
  → validate_output(ctx, artifact)               (contract + secret scan)
  → artifact → originating workspace + registry record; release_skill_context(id)
```

Canonical source rule: one copy in `vault/Skills/<name>/`; agents get
symlinks (the `plugins.install_skills` mechanism) or instruction injection —
never per-agent file copies.

---

## 6. Exact files and services to modify (final list for approval)

**New:** `backend/skill_access.py`, `backend/skill_capabilities.py`,
`backend/skill_execution.py`, `tests/test_skill_access.py`,
`tests/test_skill_capabilities.py` (matrix + rejection tests).

**Modify:** `backend/skill_registry.py` (schema v1→v1.1 additive,
`next_version` hardening), `app.py` (routes), `js/connectedSkills.js`
(UI columns/actions), `index.html` (registry panel markup + script tag),
optionally `backend/hermes.py` (Phase 5 hook),
`tests/e2e_skill_builder.py`-style e2e for acceptance (Phase 9),
`docs/UNIVERSAL_SKILL_REGISTRY_IMPLEMENTATION.md` (report), `README.md`
+ `STATE.md` (changelog entry).

**Rollback:** all changes are additive; reverting the new files + route
block + UI block restores the current tree. The one behavioral fix
(`next_version` parse) is independently revertible.

---

## 7. Implementation plan (phases 2–9, for approval)

1. **Phase 2 — registry schema.** Add the directive's metadata fields to
   `skill_registry.json` records as an additive `meta`/`access` block
   (canonical id, tags, I/O spec, deps, compatible agents, execution
   requirements, install/validation status, update metadata, enabled flag).
   Keep `register()` signature compatible.
2. **Phase 3 — Skill Access API.** `backend/skill_access.py` with the 8
   operations + progressive loading (metadata → SKILL.md → refs/scripts).
   Routes: `GET /api/skills`, `GET /api/skills/search?q=`,
   `GET /api/skills/<id>`, `POST /api/skills/<id>/load`,
   `POST /api/skills/<id>/execute`, `POST /api/skills/<id>/validate`,
   `POST /api/skills/<id>/release`, `POST /api/skills/<id>/dependencies`,
   `POST /api/skills/<id>/compatibility`, `POST /api/skills/<id>/enable`,
   `POST /api/skills/<id>/remove` (unregisters; **never deletes Vault
   source**).
3. **Phase 4 — adapter.** `backend/skill_capabilities.py`: per-agent
   capability rows (native skills / instruction injection / MCP / local
   script / file access / image input / browser preview / formats) with
   conservative defaults; `check_agent_compatibility` refuses to claim
   executable when only instructions are available.
4. **Phase 5 — routing hook.** Skill discovery inside Hermes task routing
   (optional, behind the existing route flow; model routing untouched).
5. **Phase 6 — execution + security.** `backend/skill_execution.py`:
   path validation (no traversal), workspace-scoped access, dep check
   before exec, agent permissions, ApprovalStore for risky ops, log
   capture, cancel/cleanup, secret scan of instructions + artifacts,
   no auto-install of optional deps.
6. **Phase 7 — img2threejs.** Examine the GitHub source report-first
   (`plugins.examine_*` pattern), register one canonical copy under
   `vault/Skills/img2threejs/` (fetch requires network approval), keep
   optional GLB generation separate from the core procedural Three.js
   skill.
7. **Phases 8–9 — UI + acceptance.** Registry columns/actions in
   `connectedSkills.js`; `tests/test_skill_access.py` covering the 12
   acceptance criteria (install-once, discovery by multiple agents, two
   agents loading the same canonical skill, no duplication, incompatible
   rejection, dep check, execution via existing manager, artifact return,
   update without breaking refs, disable blocks new invocations, existing
   workflows green).

**Suggested order of review gates:** schema (P2) → API (P3) → adapter (P4)
→ execution/security (P6) → router (P5) → first skill (P7) → UI (P8) →
tests (P9).

---

## 8. Agent compatibility test matrix (to be implemented in Phase 4/9)

| Agent (id) | Transport | Native skills | Instruction injection | Tool/MCP | Local script | File access | Image input | Browser preview | Formats |
|---|---|---|---|---|---|---|---|---|---|
| claude-code | CLI | yes (`~/.claude/skills`) | yes | yes (MCP) | yes | yes | yes | yes | agentskills, MCP |
| cursor | CLI | yes (`~/.cursor/skills`) | yes | yes | yes | yes | yes | yes | agentskills |
| codex / codex-cli | CLI | yes (`~/.codex/skills`) | yes | partial | yes | yes | yes | no | agentskills |
| gemini-cli | CLI | yes (`~/.gemini/skills`) | yes | partial | yes | yes | yes | no | agentskills |
| opencode | CLI | yes (`~/.config/opencode/skills`) | yes | yes | yes | yes | yes | no | agentskills |
| hermes-cli | CLI | yes (`~/.hermes/skills`) | yes | partial | yes | yes | yes | no | agentskills |
| antigravity | CLI | unverified | unverified | unverified | unverified | unverified | unverified | unverified | none documented |
| openclaw | CLI | unverified | unverified | unverified | unverified | unverified | unverified | unverified | none documented |
| deepseek-harness | chat API | **no** (no filesystem) | yes (system prompt) | no | **no** | **no** | via API | no | instructions only |

The table is *declarative defaults to be verified at runtime*, not claims:
`check_agent_compatibility` returns the evidence (which capability row was
used) and refuses "executable" when only instruction injection is
available. `antigravity`/`openclaw` rows are "unverified" until probed —
the adapter must not silently claim support.

---

## 9. Risks and remaining limitations of this audit

1. **img2threejs source is not on disk** and no network fetch was made
   here; Phase 7 registration requires either the user-supplied archive
   path or an approved fetch (same policy as the `faceless-videos-skill-v2`
   gap recorded in `docs/SKILL_BUILDER_PHASE0_AUDIT.md` §0.3).
2. The audit is static; live execution behaviour of each agent CLI's skill
   loading was not probed (that is exactly what the Phase-4 matrix tests
   must do at runtime).
3. `app.py` is a 5,759-line monolith; the new routes are one contiguous
   block to keep the diff reviewable.
4. `data/skill_registry.json` has 2 legacy records; the additive schema
   must read them without migration (verified shape: name/path/version/
   sources/versions only).
5. Working tree is uncommitted by design (49 files); the audit doc itself
   is a new file only.

---

## 10. Deliverables status (directive §DELIVERABLES)

1. Architecture audit — this document. ✅
2. Component + data-flow map — §2, §5. ✅
3. Exact files/services to modify — §6. ✅
4. Implementation plan — §7. ✅
5. Minimal implementation — pending approval.
6. Agent compatibility test matrix — §8 (to be executed in Phase 4/9).
7. End-to-end test results — pending implementation (Phase 9).
8. Remaining limitations — §9.

**Stopping here for review, per the directive. No implementation until the
audit and integration plan are approved.**
