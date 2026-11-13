---
name: yt2skill
description: Meta-skill that turns one video, or a batch of 5-15 videos, into a reusable expert Claude skill. Runs the youtube (ears) + yt-visual-analysis (eyes) pipeline, distills the core methods, workflows and decision frameworks, and writes a clean SKILL.md. Use when the user says "turn these videos into a skill/agent" or "build an expert agent from this playlist".
---

# yt2skill — Video(s) → Expert Agent

The payoff step of the Agent Skill Builder stack: distill the understanding from
many videos into a single reusable specialist skill.

## Inputs

- One URL, or a list / playlist of 5–15 high-quality videos on one topic.
- Optional: a target skill name and domain (e.g. `forex-ea-agent`,
  `outreach-copy-agent`, `video-editor-agent`).

## Workflow

1. **Collect (ears).** For each video run the `youtube` skill →
   `output/<id>.txt` + `.json`. Keep a manifest `output/manifest.json` mapping
   id → title → channel → url.
2. **See (eyes).** For videos whose meaning is visual, run the
   `yt-visual-analysis` skill → `output/<id>.visual.md`.
3. **Extract.** Across the whole corpus, extract:
   - **Core methods** — named procedures the experts repeat.
   - **Workflows** — ordered steps, with the tool/command used at each step.
   - **Decision frameworks** — "if X then Y" rules and heuristics.
   - **Best practices & pitfalls** — do / don't, common mistakes.
   - **Vocabulary** — domain terms so the skill speaks the language.
   - **Sources** — which video + timestamp each idea came from.
4. **Synthesize into a SKILL.md.** Use
   `templates/expert-skill/SKILL.md` as the skeleton. Write a crisp `name` and a
   `description` that names the trigger situations. Keep the body procedural and
   actionable; no filler.
5. **Deliver.** Write the result to
   `agent-skill-builder/skills/<skill-name>/SKILL.md` and run
   `bash scripts/install-skills.sh <skill-name>` to link it into
   `~/.claude/skills/`.

## Output contract

- `skills/<skill-name>/SKILL.md` — valid frontmatter (`name`, `description`)
  plus method/workflow/decision/pitfall sections.
- `output/manifest.json` — provenance for every source video.
- A short summary to the user: skill name, videos used, and how to invoke it.

## Quality bar

- A skill is only "expert" if a fresh agent can follow it end to end without
  rereading the source videos.
- Every non-obvious rule should be traceable to `output/` evidence.
- Prefer 5 sharp videos over 15 redundant ones; drop duplicates and drop videos
  whose signal is only motivation with no method.
- Deduplicate: if two videos teach the same step, merge it once.

## Guardrails

- Do not invent steps that appear in no source.
- Keep each output SKILL.md focused on one domain; split if it sprawls.
- Preserve attribution so the user can re-check a claim.
