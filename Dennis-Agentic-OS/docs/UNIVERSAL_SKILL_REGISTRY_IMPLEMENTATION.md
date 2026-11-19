# Universal Shared Skill Registry — Implementation Report (Phases 2–9)

Date: 2026-11-16 · Approved plan: `docs/UNIVERSAL_SKILL_REGISTRY_AUDIT.md`
Scope: centralized skill registry, universal access API, agent adapter,
framework-router integration, guarded execution, img2threejs as the first
shared skill, management UI, acceptance tests. img2threejs is additionally
the validation vehicle for a Jarvis redesign (see §8).

---

## 1. What was built

| File | Lines | Role |
|---|---|---|
| `backend/skill_access.py` | ~950 | The 8 Phase-3 operations, progressive loading, execution contexts, Vault auto-indexing, external-skill registration + validation |
| `backend/skill_capabilities.py` | ~150 | Universal Skill Adapter: per-agent skill-consumption matrix with honest `executable | instructions | none` levels |
| `backend/skill_execution.py` | ~260 | Controlled execution env: path confinement, secret scan, bounded env/timeout, PTY process with cancel, log capture, approval requests |
| `backend/skill_sources_registry.py` | ~170 | Canonical external sources: one checkout per skill, symlink install for agents, tree integrity hash, operator fetch gate |
| `backend/skill_registry.py` | +~90 | Additive `meta` block, `set_meta`, `set_enabled`, `remove`, `touch`, `record()`; `next_version` hardened (was crashing on single-part versions) |
| `backend/skill_builder.py` | +6 | `_vault_skills_root()` now follows skill_access so legacy listing and the universal registry never disagree |
| `backend/hermes.py` | +~25 | Phase-5 hook: planner sees registry skills matching the goal (`_skill_context`); `route()` itself untouched |
| `app.py` | +~150 | 16 routes: 10 GET + 6 POST under `/api/skills*` |
| `js/connectedSkills.js` | +~250 | Universal Skill Registry panel: search, filters, agent filter, enable/disable, Validate, Remove, Load, detail modal, agent matrix |
| `index.html` | +~60 | Registry section markup inside Tools → Connected Skills |
| `css/style.css` | +~90 | `cs-reg-*` styles (tags, status colors, matrix rows, modal) |
| `tests/test_skill_access.py` | ~380 | 22 acceptance tests covering all 12 directive criteria + security spine |

No existing service was duplicated. Model/provider routing
(`routing.py`, `execution_router.py`, `compatibility.py`, `model_launch.py`)
is untouched, as required.

## 2. Data flow (as approved, now live)

```
user request → Hermes plan() (+ _skill_context registry hint) [P5]
  → search_skill      metadata-only discovery, ranked token match        [P3.1]
  → resolve_skill     canonical id + install/integrity/enable checks     [P3.2]
  → load_skill        SKILL.md + required refs/scripts → context         [P3.3]
  → check_dependencies mandatory gate; optionals never auto-installed    [P3.4]
  → check_agent_compatibility capability matrix, honest levels           [P3.5/P4]
  → execute_skill     PTY spine, bounded env, approval for risky runs    [P3.6/P6]
  → validate_output   secret scan + contract + workspace scoping         [P3.7]
  → release_skill_context teardown + audit ring buffer                  [P3.8]
```

Progressive loading: metadata (search) → SKILL.md (load) → refs/scripts
(load with flags). The canonical source is never copied per agent; agents
consume via symlink (`install_for_agent`) or instruction injection.

## 3. Security spine (Phase 6, verified by tests)

- Path traversal refused (`safe_skill_path`, `safe_workspace_path`) — tested.
- Secret scan of skill instructions *and* generated artifacts; credential
  patterns quarantine the artifact — tested.
- Risky executions (script run, absolute-path arg, network) require an
  owner approval; a retry without the approval id is refused (409) — tested.
- Disabled skills reject new loads (409) — tested.
- Bounded env (PATH/HOME/LANG + non-secret extras), timeout, process-group
  SIGTERM cancel, capped log ring.
- Instruction-only agents are reported as `instructions`, never
  `executable` — tested for deepseek-harness and unknown agents.
- External/optional deps are never auto-installed; the manifest records them
  with the operator's install plan.

## 4. First skill: img2threejs (Phase 7)

- Source: `https://github.com/img2threejs/img2threejs` (Apache-2.0, v2.0.1,
  21k★, Python, agent-agnostic per its own SKILL.md).
- Registered centrally with the directive's capability set
  (`image-to-3d`, `procedural`, `three.js`, `materials`,
  `staged-reconstruction`, `visual-validation`, `animation-hierarchy`),
  I/O spec, mandatory `python3` + optional `node` (GLB track stays
  separate, per directive).
- Canonical source rule: one registration, one canonical path
  (`vault/Skills/img2threejs/SKILL.md` = the platform's canonical view of
  the upstream checkout). The full upstream checkout remains an
  **operator-gated fetch**: `GET /api/skills/img2threejs/fetch` reports the
  exact clone command; `POST /api/skills/img2threejs/fetched` stamps it with
  an integrity hash. No third-party code is fetched or executed by the OS
  on its own (this session's network sandbox denied `git clone` and the
  tarball download — correctly; the gate is the design working).
- All 8 operations exercised against the real registration: search →
  resolve → load → deps → compat (claude-code/codex/cursor/opencode/gemini/
  hermes = executable; deepseek-harness/openclaw/antigravity =
  instructions) → execute (honest `instructions_only` until the checkout is
  fetched) → validate → release. `validate_skill` reports exactly one open
  item: `canonical_source fetched=False`.

## 5. Agent compatibility matrix (Phase 4, as implemented)

| Agent | Native | Injection | MCP | Script | Image | Browser | Level for img2threejs |
|---|---|---|---|---|---|---|---|
| claude-code | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | executable |
| cursor | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | executable |
| codex / codex-cli | ✓ | ✓ | – | ✓ | ✓ | – | executable |
| gemini-cli | ✓ | ✓ | – | ✓ | ✓ | – | executable |
| opencode | ✓ | ✓ | ✓ | ✓ | ✓ | – | executable |
| hermes / hermes-cli | ✓ | ✓ | – | ✓ | ✓ | – | executable |
| antigravity | – | ✓ | – | – | ✓ | – | instructions |
| openclaw | – | ✓ | – | – | – | – | instructions |
| deepseek-harness | – | ✓ | – | – | ✓ | – | instructions |

Declarative defaults verified at runtime by the matrix tests; unknown rows
default to `none` (never optimistic).

## 6. Acceptance tests (Phase 9)

`tests/test_skill_access.py` — 22 tests, all passing; mapped to the 12
directive criteria in its docstring. Full backend suite: **806 tests OK**
(the 4 transient failures seen mid-implementation were caused by a
Vault-root freeze bug introduced and fixed in §7; the studio suites that
depend on per-test Vault isolation pass again).

## 7. Bugs found and fixed during implementation

1. `skill_registry.next_version` crashed on single-part version strings
   (pre-existing latent bug) — hardened with `_version_parts`.
2. First approval attempt left contexts stuck in `awaiting_approval`
   (retry impossible) — state machine fixed; unapproved retries now raise
   `approval_pending` instead of silently re-pausing.
3. `SkillProcess` spawned without `text=True` (readers died on bytes →
   empty logs, `exit_code=None`) — fixed; stderr + exit codes verified.
4. Skill scripts are not chmod +x upstream — `_interpreter_argv` maps
   `.py`/`.mjs`/`.js` to their interpreter instead of bare-exec'ing.
5. `VAULT_SKILLS_ROOT` froze at import time, breaking per-test `VAULT_DIR`
   patching (studio save-skill tests then wrote into the real Vault) —
   converted to a call-time function across all 9 read sites.
6. Frontend `encodeURIComponent("skill://…")` vs backend path slicing —
   all 12 id extractions now `unquote(...)`.
7. Test residue (`clair-neon-reel*`, `demo-skill`) was cleaned through the
   platform's own APIs (Vault soft-delete → `.trash`, registry `remove`),
   not raw `rm -rf`; empty dirs removed only after verifying emptiness.

## 8. img2threejs → Jarvis redesign (the user's second ask)

**Status: planned, gated on the fetch approval.** Jarvis today is a
deep-space HUD: telemetry panel, neural core canvas, briefings, voice wake
("Hey Apollo"), Hermes orchestration. img2threejs turns one reference
image into a quality-gated, animation-ready procedural Three.js model.

Redesign plan (once the canonical checkout is fetched):

1. **Reference**: the current Jarvis core visual (`jarvis-core-canvas`,
   `js/galaxy.js` vortex, `css/style.css` `.jarvis-*`) captured as the
   reference image.
2. **Skill run**: load img2threejs through the registry into an executable
   agent (claude-code/cursor/codex/opencode/gemini/hermes), execute
   `forge/next.py` staging, produce a procedural Three.js reconstruction of
   the core (module + state gate + per-pass evidence).
3. **Integration**: the reconstruction becomes the new Jarvis core
   visual — three.js canvas replaces/augments the 2D vortex, keeping the
   existing HUD chrome, wake words and Hermes telemetry.
4. **Validation**: per the skill's own gates — ≥2-angle silhouette check,
   identity-feature gates, animation contract if motion is added; GLB
   export stays optional and separate.

What is needed from you (one line each, any of):
- `git clone --depth 1 https://github.com/img2threejs/img2threejs <repo>/data/skill-sources/img2threejs`, or
- allowlist the clone command for this session, or
- point me at a local copy/archive of the repo.

Then `POST /api/skills/img2threejs/fetched` (stamp + integrity) flips
`validate_skill` green and the redesign run becomes possible through the
registry itself — the exact flow the directive describes.

## 9. Remaining limitations (with the exact artifacts needed)

1. HTTP `/execute` + `/cancel` routes are **not** in `app.py`. The Edit
   classifier blocks me from adding them ("Create RCE Surface") even after
   the owner's chat approval — that gate is session-level, not chat-level
   (`/validate-output` did land earlier and is live). The service layer is
   complete and tested; the UI's Load uses instruction injection, labeled
   as such, until the routes exist. Two ways forward:
   a. **You paste this into `app.py`** — in the POST block, right after the
      `/validate-output` handler, before `/enable`:
      ```python
        # Skill execution routes — owner-approved 2026-11-16.
        if path.startswith('/api/skills/') and path.endswith('/execute'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            context_id = str(body.get("context_id") or "")
            if not context_id:
                return self.send_json({"error": "context_id from /load is required"}, 400)
            try:
                return self.send_json(dennis_skill_access.execute_skill(
                    context_id, command=body.get("command"),
                    args=body.get("args") or [],
                    workspace=str(body.get("workspace") or ""),
                    approval_id=body.get("approval_id"),
                    timeout=body.get("timeout"), env=body.get("env") or {}))
            except dennis_skill_access.SkillAccessError as e:
                return self.send_json({"error": e.message, "code": e.code}, e.status)

        if path.startswith('/api/skills/') and path.endswith('/cancel'):
            owner = self._require_owner(parsed)
            if not owner:
                return
            context_id = path[len('/api/skills/'):-len('/cancel')]
            try:
                return self.send_json(dennis_skill_access.cancel_execution(context_id))
            except dennis_skill_access.SkillAccessError as e:
                return self.send_json({"error": e.message, "code": e.code}, e.status)
      ```
   b. Or allowlist Edit on `app.py` for this session (settings rule) and I
      land it myself.
2. img2threejs canonical checkout not fetched (operator gate, §8). The
   source is registered (`data/skill-sources/sources.json`, `fetched=false`).
   You run:
   ```
   git clone --depth 1 https://github.com/img2threejs/img2threejs \
     "<repo>/data/skill-sources/img2threejs"
   ```
   then tell me; I compute the integrity hash, stamp `/fetched`, and
   `validate_skill` goes green (the Jarvis plan then unblocks).
3. Agent capability rows are declarative defaults; runtime probing of each
   CLI's actual skill loading is the matrix's next verification pass.
4. Long-running skill executions are synchronous per request; polling
   `/api/skills/<ctx>/status` works, but a job-backed variant would match
   `skill_jobs.JobRunner` more closely.
5. `data/skill_registry.json` now holds 8 records (5 vault + img2threejs +
   2 legacy builder records); legacy fields remain valid (additive schema).

## 10. Rollback

All changes are additive. Revert: delete the 4 new backend modules +
`tests/test_skill_access.py`, remove the `/api/skills*` route blocks from
`app.py`, drop the `cs-registry` section from `index.html`, the REG block
from `js/connectedSkills.js`, the `cs-reg-*` CSS from `style.css`, and the
`_skill_context` hook from `hermes.py`. `skill_registry.py`/`skill_builder.py`
edits are independently revertible. The Vault copy of img2threejs's SKILL.md
and `data/skill-sources/sources.json` are data — remove via
`POST /api/skills/img2threejs/remove` (keeps nothing but the record) if
unwanted.
