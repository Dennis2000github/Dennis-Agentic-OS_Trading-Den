# Jarvis Redesign via img2threejs — Test Plan

Date: 2026-11-16 · Depends on: Universal Skill Registry (implemented, see
`docs/UNIVERSAL_SKILL_REGISTRY_IMPLEMENTATION.md`) + the operator fetch of
the img2threejs canonical checkout (§3 of the implementation report).

## Goal

Use the img2threejs skill — through the registry, not ad-hoc — to redesign
the Jarvis neural-core visual: the 2D canvas vortex becomes a
quality-gated, animation-ready procedural Three.js model.

## Inputs

- Reference image: capture of the current Jarvis core
  (`#jarvis-core-canvas` + `.jarvis-space` backdrop; a screenshot of the
  running dashboard is the honest reference).
- Skill: `skill://img2threejs` (registered; validation currently reports
  `canonical_source fetched=False` until the fetch gate is cleared).

## Steps (each maps to a registry operation)

1. `search_skill("three.js core visual")` → confirm img2threejs ranks.
2. `resolve_skill` → installed, enabled, integrity ok.
3. `load_skill(agent_id=<executable agent>)` → context + SKILL.md.
4. `check_dependencies` → python3 present; node optional (GLB track).
5. `check_agent_compatibility` → pick an `executable` agent; if only
   `instructions` is available for the chosen agent, the run is refused —
   that is the adapter being honest, not a failure.
6. `execute_skill` → the skill's own staged pipeline
   (`forge/state.py init` → `forge/next.py` → pass gates) with its
   `.img2threejs/state.json` checklist as the authority; optional GLB
   export stays separate from the core procedural build.
7. `validate_output` → secret scan + the skill's own gates (≥2-angle
   silhouette, identity features, animation contract if motion is added).
8. `release_skill_context` → artifact returns to the originating workspace
   (Studio media library convention), then integrate:
   `#jarvis-core-canvas` becomes/hosts the three.js renderer; HUD chrome,
   wake words and Hermes telemetry unchanged.

## Acceptance

- One canonical skill, executed through the existing execution manager,
  artifact back in the workspace, no per-agent copies.
- Jarvis keeps its existing behaviours (telemetry, clock, mode toggle,
  voice wake) — the redesign is the core visual only.
- GLB generation, if run, is a separate optional pass.

## Blocker (needs you)

The canonical checkout fetch — one of:
`git clone --depth 1 https://github.com/img2threejs/img2threejs <repo>/data/skill-sources/img2threejs`,
or an allowlist for that command, or a local archive path. After the fetch,
`POST /api/skills/img2threejs/fetched` stamps it and `validate_skill`
goes green; steps 1-8 then run for real.
