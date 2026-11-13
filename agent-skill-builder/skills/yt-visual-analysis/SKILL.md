---
name: yt-visual-analysis
description: Give Claude "eyes" for YouTube. Send a video to Gemini for on-screen frame understanding (slides, code, UI, diagrams, whiteboards) and audio-aware analysis, then return a timestamped visual breakdown. Use after the `youtube` skill when the meaning lives on screen, not just in the words.
---

# YouTube Visual Analysis (Eyes)

The transcript tells you what is said; this skill tells you what is **shown**.
It is the "eyes" half of the Agent Skill Builder stack.

## When to use

- Tutorials where the value is a screen recording: code, terminals, dashboards,
  slides, spreadsheets, diagrams, whiteboards, UI walkthroughs.
- A speaker says "as you can see here…" or "this chart…".
- You need to verify a claim against what actually appears on screen.

## Prerequisites

- A free Gemini API key from Google AI Studio: <https://aistudio.google.com/apikey>
  (starts with `AIza...`). Put it in `agent-skill-builder/.env` as
  `GEMINI_API_KEY=...` (see `.env.example`). Never hardcode it.
- One of the following, in order of preference:
  1. The **Gemini Video MCP** registered in `.mcp.json` (recommended) — gives
     Claude native video understanding tools.
  2. The `scripts/gemini-video-analyze.sh` fallback, which calls the Gemini API
     directly with `curl` (supports a YouTube URL via `fileData.fileUri`). It
     walks a primary→fallback model chain from `GEMINI_MODELS` on overload/rate
     limits and sends a `thinkingLevel` from `GEMINI_THINKING` for agentic depth.

## Workflow

1. **Get ears first.** Run the `youtube` skill to produce
   `output/<video-id>.txt` and `.json`. Pass the transcript alongside the video
   so Gemini can align speech to frames.
2. **Analyze.** Preferred path (MCP): ask Claude to use the Gemini video tool on
   the URL, e.g. *"Use the gemini-video tool to analyze this video: <URL>"*.
   Fallback path:

   ```bash
   bash scripts/gemini-video-analyze.sh "<URL>" "visual analysis: on-screen text, code, diagrams, UI, and any demo steps"
   ```

   The script prints a timestamped markdown breakdown and saves it to
   `output/<video-id>.visual.md`.
3. **Merge.** Produce a combined understanding: for each key section, pair the
   spoken point (from the transcript) with the on-screen evidence (from Gemini).

## Output contract

`output/<video-id>.visual.md` containing, at minimum:

- `## Visual Summary` — what the video shows end to end.
- `## Timestamped Breakdown` — `[mm:ss]` → what is on screen → why it matters.
- `## On-screen Artifacts` — code blocks, slide titles, tools/URLs, charts.
- `## Gaps` — moments where visuals were ambiguous or could not be read.

## Guardrails

- Ground every visual claim in a timestamp. If Gemini is unsure, mark it under
  `## Gaps` rather than guessing.
- Do not paste large screenshots back to the user; describe them.
- Treat the API key as a secret: read it from the environment, never print it.
