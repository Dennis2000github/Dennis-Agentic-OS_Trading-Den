# Agent Skill Builder

A workspace for turning YouTube videos into reusable Claude Code **skills** and
expert agents. It implements the stack: **give it ears, then eyes, then build
expert agents.**

## The stack

| Step | Goal | Tool | Cost |
|------|------|------|------|
| 01 – The Gap | Claude Code sees only a URL | — | — |
| 02 – Give it Ears | Full transcript + metadata | `skills/youtube` (`SKILL.md`, yt-dlp) | Free |
| 03 – Give it Eyes | Real frames + audio understanding | Gemini (AI Studio) + Gemini Video MCP | Free API key |
| 04 – Build Expert Agents | Distill videos into specialist skills | `skills/yt2skill` → `skills/<name>/` | Free |

## Layout

```
agent-skill-builder/
├── install.sh                 # one-shot setup
├── .mcp.json                  # Claude Code MCP servers (Gemini video + transcripts)
├── .env.example               # copy to .env, add GEMINI_API_KEY
├── skills/
│   ├── youtube/               # EARS   — transcript + metadata
│   ├── yt-visual-analysis/    # EYES   — Gemini frame/audio breakdown
│   └── yt2skill/              # META   — videos -> expert skill
├── templates/expert-skill/    # skeleton for generated skills
├── scripts/                   # yt-transcript, yt-metadata, gemini-video-analyze,
│                              # fetch-ffmpeg, install-skills, install-mcp, check-deps
├── bin/                       # static ffmpeg + ffprobe (gitignored; fetch-ffmpeg.sh)
└── output/                    # generated transcripts, metadata, visual notes (gitignored)
```

## Quickstart

```bash
cd agent-skill-builder
bash install.sh            # deps, fetch ffmpeg, create .env, link skills into ~/.claude/skills
bash install.sh --with-mcp # also register the MCP servers (user scope)
```

Then add your Gemini key to `.env`:

```
GEMINI_API_KEY=...
```

Get one at <https://aistudio.google.com/apikey>.

### Gemini model config (`.env`)

```bash
# Primary -> fallback chain, tried in order on overload (503) / rate limit (429)
GEMINI_MODELS=gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash,gemini-3.5-flash-lite
# Single-model override (only used if GEMINI_MODELS is unset)
# GEMINI_MODEL=gemini-3.6-flash
# Reasoning effort for agentic work: minimal | low | medium | high | off
GEMINI_THINKING=high
GEMINI_ATTEMPTS=3
GEMINI_RETRY_SLEEP=8
```

Notes:
- There is **no literal "agentic mode" flag** in the API — the equivalent is
  `thinkingConfig.thinkingLevel` (`minimal|low|medium|high`), which this script
  sends as `GEMINI_THINKING`. `high` = deepest reasoning.
- `2.5-flash` is deprecated for new keys. `3.8`/`3.7` are strongest but are
  frequently overloaded; the chain falls through automatically.
- Any of these also work as one-off shell overrides, e.g.
  `GEMINI_MODELS=gemini-3.5-flash-lite GEMINI_THINKING=off bash scripts/gemini-video-analyze.sh <URL>`.
- The `.mcp.json` MCP server reads `GEMINI_API_KEY` / `GEMINI_MODEL` /
  `GEMINI_THINKING` from the environment (project-scope files use `${VAR}`
  expansion, so export them or rely on the user-scope registration).

## Usage

### 1. Ears — transcript + metadata

```bash
bash scripts/yt-transcript.sh "https://youtu.be/VIDEO_ID"
bash scripts/yt-metadata.sh  "https://youtu.be/VIDEO_ID"
```

Or in Claude Code: *"Get the full transcript of this video: <URL>"* (uses the
`youtube` skill; `/youtube` also invokes it directly).

### 2. Eyes — visual + audio analysis

With the MCP registered, in Claude Code:

> Use the gemini-video tool to analyze this video: <URL>

Without the MCP, use the curl fallback:

```bash
bash scripts/gemini-video-analyze.sh "https://youtu.be/VIDEO_ID"
```

### 3. Expert agents — videos → skill

In Claude Code:

> Analyze these 10 videos using both transcript and visual analysis.
> Extract the core methods, workflows, decision frameworks and best practices.
> Turn them into a reusable skill called `editor-agent` and write a clean SKILL.md.

The `yt2skill` skill drives the pipeline and writes
`skills/<name>/SKILL.md`; then run `bash scripts/install-skills.sh <name>`.

### Quick test prompt

> Analyze this YouTube video thoroughly (transcript + visual analysis): <URL>.
> Then create a short expert skill based on its core method.

## Installing prerequisites

```bash
# transcript engine (required)
python3 -m pip install -U yt-dlp

# static ffmpeg + ffprobe into ./bin (no Homebrew needed)
bash scripts/fetch-ffmpeg.sh
```

Run `bash scripts/check-deps.sh` any time to see what's missing.

The scripts locate `yt-dlp` whether it is on `PATH`, importable as a module
(`python3 -m yt_dlp`), or installed as a `pip --user` console script
(`~/Library/Python/3.x/bin/yt-dlp`). When launched from the DENNIS app, the
server passes its own interpreter via `PYTHON_BIN`, so the scripts keep working
even when the OS starts the app with a bare `PATH`.

Whisper is optional and currently **not installable on Python 3.14** (no torch
wheel). The transcript script falls back to Gemini transcription when captions
are missing, so this is covered.

Because this repo's path contains a colon, the scripts do not modify `PATH`;
they pass `--ffmpeg-location ./bin` to yt-dlp explicitly.

## MCP servers

Registered at user scope (available in every project):

- `gemini-video` — `@ugarchance/mcp-gemini-video-understanding` (needs `GEMINI_API_KEY`)
- `yt-transcript` — `@thenavidm/youtube-mcp-cli`

`.mcp.json` declares the same two servers at project scope as a portable
alternative. First use of `gemini-video` may take ~45s while npx caches the
package — that is normal.

Alternatives for stronger visual understanding:
[`aigentive/youtube-gemini-mcp`](https://github.com/aigentive/youtube-gemini-mcp),
[`DerYUYU/gemini-video-mcp`](https://github.com/DerYUYU/gemini-video-mcp).

## Notes

- `.env`, `output/`, and `bin/` are gitignored — never commit keys, media, or
  the ffmpeg binaries.
- Transcript order: human captions → auto-captions → whisper → Gemini.
- Gemini accepts a YouTube URL directly via `fileData.fileUri`; no manual upload
  needed for public videos. Transient "high demand" (503) responses are retried.
- Verify with `claude mcp list` — both servers should read `✔ Connected`.
