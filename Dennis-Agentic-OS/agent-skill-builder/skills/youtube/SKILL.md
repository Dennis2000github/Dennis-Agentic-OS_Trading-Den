---
name: youtube
description: Give Claude "ears" for YouTube. Fetch clean transcripts, metadata (title, channel, duration, description, chapters), and optionally download audio/video or list a whole playlist, using yt-dlp. Use whenever the user pastes a YouTube URL and wants the words, a summary, or the content of the video.
---

# YouTube (Ears)

Turn a YouTube URL into clean, model-readable text. This skill is the transcript
half of the Agent Skill Builder stack — the "ears".

## When to use

- The user pastes a `youtube.com` / `youtu.be` link and asks what it says.
- You need a transcript to summarize, quote, or distill into a skill.
- You need metadata (title, channel, duration, chapters) for provenance.

## Prerequisites

`yt-dlp` is required. If it is missing, install it:

```bash
# macOS (recommended, isolated)
python3 -m pip install -U yt-dlp
# or
brew install yt-dlp
```

Verify: `yt-dlp --version`

## Workflow

All commands live in this workspace under `agent-skill-builder/scripts/`.
Call them with a URL as the first argument.

1. **Transcript (fast, preferred).** Prefer the human/auto subtitles — no video
   download needed:

   ```bash
   bash scripts/yt-transcript.sh "<URL>"
   ```

   This writes `output/<video-id>.txt` and prints the path. It tries, in order:
   manual captions, then auto-generated captions, then a whisper fallback only
   if neither exists (requires `openai-whisper`; slow).

2. **Metadata only** (no subtitles):

   ```bash
   bash scripts/yt-metadata.sh "<URL>"
   ```

   Writes `output/<video-id>.json` with title, channel, upload date, duration,
   description and chapter markers when available.

3. **Audio only** (for Whisper or Gemini upload):

   ```bash
   yt-dlp -x --audio-format mp3 -o "output/%(id)s.%(ext)s" "<URL>"
   ```

4. **Playlist**: append `--yes-playlist`; iterate IDs first with
   `yt-dlp --flat-playlist --print id "<PLAYLIST_URL>"`.

## Output contract

- Transcript text: `output/<video-id>.txt` (plain text, no timestamps) — this is
  what you summarize or feed into `yt2skill`.
- Timestamped transcript: add `--timed` and read `output/<video-id>.timed.txt`.
- Metadata: `output/<video-id>.json`.

Always report the resolved **video id, title, channel and duration** so the
user can confirm you analyzed the right video.

## Guardrails

- Never fabricate a transcript. If captions and whisper are both unavailable,
  say so and offer the audio-download path.
- Cite the timestamp ranges you relied on for any specific claim.
- Respect the source: do not reproduce long verbatim passages beyond what the
  user needs.
