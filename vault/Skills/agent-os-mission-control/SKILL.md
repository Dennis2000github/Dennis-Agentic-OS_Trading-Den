---
name: agent-os-mission-control
description: "Build a single \"mission control\" dashboard where multiple AI agents run fixed, repeatable workflows off one shared memory. Use when repeated AI tasks are scattered across tabs and apps and you want one control-tower screen for research, content, SEO, and team review."
---

# Agent OS Mission Control

One screen where every AI agent runs your repeatable jobs against a single shared memory, instead of jumping between tabs and re-explaining your business to each model.

## When to use

- You run several AI models/agents in separate tabs and notes live in yet another app.
- You repeat the same explanation of your business to every tool, every session.
- You have recurring jobs (research, content ideation, SEO, reviewing sales/team calls) that you want done the same way each time.
- You want measurable output (published posts, traffic movement, call scores) rather than one-off chats.

Do not use this as a general chat setup; it is for consolidating repeatable agent work behind one dashboard and one memory.

## Inputs

- An inventory of the agents/models you use and the jobs each one does.
- At least one recurring, time-consuming job that can be written as a fixed procedure.
- Data sources per workflow: trend/news feeds (research), your own top-performing content list (ideation), search performance and query data (SEO), sales/team call recordings or transcripts (review).
- A step-by-step checklist (SOP) if you intend to score process or calls.
- A notes workspace that links notes together (used for shared memory).
- A capable model you can direct in plain language to build tools inside a dashboard.

## Outputs

- A single Mission Control screen listing every agent and the workflows inside it.
- Each workflow's runnable output: drafted posts, SEO articles, generated images/videos, ranked keyword/content ideas, call scores with reasons, and per-day review counts.
- A shared, self-updating memory graph that all agents read from and write to.

## Tool requirements

- A chat/LLM capable of turning a plain request into a working tool or dashboard addition (the source calls this model Claude Opus 5.5).
- Multiple agent endpoints/models that can coexist in one place (source names Claude, Hermes, Antigravity, Codex).
- Obsidian (free, links notes) as the shared-memory store, viewed in "map" mode.
- Agency sources: a news/trend feed, your content analytics, your search-analytics numbers, and a call/transcript source.
- Content production agents (video, SEO article, image) if you want one-click handoff from an idea.
- Any zip/installer or setup guide you already have may be plugged in; do not invent tooling the material does not support.

## Core methods

1. **Map your agents.** List every AI model/agent you use and, for each, the concrete jobs it performs. This becomes the contents of Mission Control.
2. **Build Mission Control first.** Create one screen that lists all agents; treat it as the airport control tower that sees every "plane" and directs it.
3. **Define a workflow as a fixed job.** A workflow = a job the AI does the same way every time. If it can't be described identically on each run, sharpen it before building.
4. **Pick your first workflow from pain.** Find the one boring daily job that takes the longest. That is your first workflow. (The source picked research; yours may be repeated emails, posting, or team checks.)
5. **Build by plain request.** Open the model and ask, in natural language, to build the idea and add it inside the dashboard. Treat the model as capable of converting a plain idea into a working tool.
6. **Stand up a trend/research agent ("Oracle").** Auto-refresh it on a schedule so it reads the day's trending topics for you. Give each item three actions: draft a social post, publish an SEO blog post, or open the original source. Keep a scrollback of previous days.
7. **Stand up an audience-fit agent ("Muse").** Feed it your top content (most views, most retention) and have it output what to make next. Attach a one-click button per idea that routes to a video agent, SEO article agent, or image generation.
8. **Stand up a search/SEO workflow ("Search OS").** Pull your latest visit counts, the queries you appear for, and your rankings onto one screen. Add keyword research that finds terms you plausibly can rank for, then produce content around them.
9. **Stand up a review/scoring workflow ("Sales OS").** Pull the latest team/sales calls, score each one against your SOP checklist out of 10, and attach a written reason. Also show how many calls were reviewed and how many no-shows occurred, by day.
10. **Connect shared memory.** Keep one memory for all agents in a linking notes app (Obsidian), viewed as a connected "memory galaxy." Allow agents to create and update notes themselves (daily updates, recent edits) so context persists across every agent.
11. **Roll out in stages.** Day 1: install and connect the model. Day 2: switch on the research agent for your niche. Day 3: connect the shared memory so every agent knows the business. Then continue step by step.
12. **Iterate and measure.** Every time you improve a workflow, check whether the corresponding metric (e.g., search traffic) moves, and keep upgrading incrementally.

## Workflow (default path)

1. Inventory agents and their jobs.
2. Build the Mission Control screen listing every agent.
3. Choose the single longest recurring daily job as workflow #1.
4. Describe it to the model in plain language and add it to the dashboard.
5. Lock the workflow as a fixed, repeatable procedure.
6. Add the research/trend agent with auto-refresh, per-item actions, and history.
7. Add the audience-fit agent over your top content, with one-click routing to production agents.
8. Add the search/SEO workflow with performance, queries, rankings, and keyword research.
9. Add the review/scoring workflow with SOP scoring, reasons, and daily counts.
10. Connect the single shared memory and let agents write to it.
11. Stage the rollout (install → research on → memory connected → expand).
12. Improve each workflow and verify the metric.

## Decision framework

| If | Then | Because |
| --- | --- | --- |
| A daily job is repetitive and eats hours | Make it workflow #1 | Highest-return, easiest-to-define automation |
| You need to know what the industry is talking about | Use the research/trend agent (Oracle) | External trends are not the same as audience demand |
| You need to know what your own audience wants | Use the audience-fit agent (Muse) over your top content | Your own performance data predicts your next best content |
| You depend on search/AI-search discovery | Build the SEO workflow (Search OS) | Rankings and traffic are measurable and compound |
| A team runs repeated calls/processes | Use SOP-based scoring (Sales OS) | Replaces hours of manual review with scores plus reasons |
| Agents keep forgetting your business | Connect shared memory first | Every agent then knows the business without re-explaining |
| You don't know what to build | Start with your longest boring weekly job | Concrete scope beats open-ended brainstorming |
| An idea is ready to produce | Use one-click routing to the right production agent | Removes manual handoff between ideation and output |

## Failure handling

- **Trend data looks stale:** rely on auto-refresh and confirm the last-refresh timestamp before acting; run an on-demand refresh if needed.
- **Workflow output is inconsistent:** the procedure is not fixed enough; rewrite it as the same steps every run.
- **Scores feel unfair or vague:** tighten the SOP checklist (the questions asked, clarity, steps followed) before trusting the score.
- **Agents lose context or contradict each other:** verify the shared memory is actually connected and that agents can write to it; check for missing or unlinked notes.
- **Metric doesn't move after a change:** treat the change as unproven; keep iterating and re-measure rather than assuming the workflow works.
- **Handoff produces the wrong media type:** route each idea explicitly to video, article, or image rather than a generic production step.

## Success criteria

- One screen shows every agent you use and the workflows inside each.
- The primary job completes before you would otherwise start it manually.
- Outputs are produced the same way on every run.
- Metrics are visible and improving (e.g., one site moved from about 420 to 1,300 search clicks/day after upgrades; multiple sites grew the same way).
- Agents write and update shared-memory notes without you authoring them.
- Review workflows return per-call scores with reasons and daily reviewed/no-show counts.

## Best practices

- Define every workflow as the same job every time before building it.
- Start with the single longest recurring task, not with the most ambitious one.
- Build by describing the idea to the model in plain language and asking it to add it to the dashboard.
- Keep exactly one shared memory so context is never duplicated across agents.
- Give each idea a one-click path to the right production agent.
- Keep history: previous days' research and past scores stay available.
- Improve one workflow at a time and re-check the matched metric.

## Pitfalls

- Staying in scattered tabs and re-explaining your business to each AI.
- Confusing what the world is talking about with what your own audience wants.
- Scoring team output without a clear, step-by-step SOP.
- Building a workflow that is not repeatable, so results vary run to run.
- Letting memory fragment across apps instead of one linked store.
- Assuming SEO success from promises instead of reading the charts.
- Trying to build everything at once instead of staging the rollout.

## Vocabulary

- **Agent OS:** a single system where multiple AI agents run under one control screen with one shared memory.
- **Mission Control:** the main dashboard screen listing every agent and its workflows.
- **Workflow:** a job the AI performs the same way every time.
- **Hermes Oracle:** auto-refreshing research agent that surfaces current trending topics with per-item actions and history.
- **Hermes Muse:** audience-fit agent that analyzes your top content and proposes what to make next, with one-click production routing.
- **Search OS:** SEO workflow showing visits, queries, and rankings, plus keyword research feeding content.
- **Sales OS:** review workflow that scores team/sales calls against an SOP out of 10 with reasons and daily counts.
- **SOP:** standard operating procedure; a step-by-step checklist for how a task or call should go.
- **Memory galaxy:** the Obsidian map view of linked notes, where each note is a star and lines show connections.
- **Shared memory:** one common memory store all agents read and write so context persists.

## Sources

- Title: *Claude Opus 5.5 AgentOS is Absolutely WILD!* — URL: https://www.youtube.com/watch?v=wXD_AU1qEsE
  - Provenance: YouTube video (id `wXD_AU1qEsE`), upload date 2026-11-09; description plus timed transcript (first 12,000 chars). Provided as untrusted source data.
  - Parts used: Mission Control / control-tower framing; the five workflows (Hermes Oracle, Hermes Muse, Search OS, Sales OS, shared memory); the "workflow = same job every time" definition; build-by-plain-request method; "start with your longest boring daily job"; the 30-day rollout stages; SEO result figures (420 → 1,300 clicks/day); SOP-based call scoring; Obsidian "memory galaxy" shared memory.
  - Parts not used: promotional offers, pricing/community links, and coaching-call/member marketing content.
