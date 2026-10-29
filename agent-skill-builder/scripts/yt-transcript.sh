#!/usr/bin/env bash
# YouTube -> clean plain-text transcript ("ears").
# Usage: yt-transcript.sh <URL> [--timed]
set -euo pipefail
# shellcheck source=scripts/_env.sh
. "$(dirname "$0")/_env.sh"

URL="${1:-}"
TIMED=0
[ "${2:-}" = "--timed" ] && TIMED=1

if [ -z "$URL" ]; then
  echo "usage: yt-transcript.sh <URL> [--timed]" >&2
  exit 2
fi

require_ytdlp
require "$PYTHON_BIN"

VID="$("${YTDLP[@]}" "${YTDLP_NET[@]}" --no-warnings --skip-download --print id "$URL" 2>/dev/null | head -1)"
if [ -z "$VID" ]; then
  echo "error: could not resolve a video id from: $URL" >&2
  exit 1
fi

TITLE="$("${YTDLP[@]}" "${YTDLP_NET[@]}" --no-warnings --skip-download --print title "$URL" 2>/dev/null | head -1 || true)"
printf 'video: %s  (%s)\n' "$VID" "${TITLE:-unknown title}"

# 1. Try human + auto captions (English first, then any).
"${YTDLP[@]}" "${YTDLP_NET[@]}" "${FFMPEG_ARGS[@]+"${FFMPEG_ARGS[@]}"}" --no-warnings --skip-download \
  --write-subs --write-auto-subs \
  --sub-langs "en.*,en,-live_chat" \
  --sub-format "srt/vtt/best" \
  --convert-subs srt \
  -o "$OUT/%(id)s.%(ext)s" "$URL" >/dev/null 2>&1 || true

SRC="$(ls -1 "$OUT/$VID"*.srt 2>/dev/null | head -1 || true)"

if [ -n "$SRC" ]; then
  DEST="$OUT/$VID.txt"
  [ "$TIMED" = "1" ] && DEST="$OUT/$VID.timed.txt"
  "$PYTHON_BIN" - "$SRC" "$DEST" "$TIMED" <<'PY'
import re, sys
src, dest, timed = sys.argv[1], sys.argv[2], (sys.argv[3] == "1")
raw = open(src, encoding="utf-8", errors="replace").read().replace("\r", "")
blocks = re.split(r"\n\n+", raw)
out, seen = [], None
for b in blocks:
    lines = [l for l in b.split("\n") if l.strip()]
    if not lines:
        continue
    ts = [l for l in lines if "-->" in l]
    text = " ".join(l for l in lines if "-->" not in l and not l.strip().isdigit())
    text = re.sub(r"<[^>]+>", "", text).strip()
    if not text or text == seen:
        continue
    seen = text
    if timed and ts:
        out.append(f"[{ts[0].split(' --> ')[0]}] {text}")
    else:
        out.append(text)
with open(dest, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print(f"wrote {dest} ({len(out)} lines)")
PY
  echo "transcript: $([ "$TIMED" = "1" ] && echo "$OUT/$VID.timed.txt" || echo "$OUT/$VID.txt")"
  exit 0
fi

# 2. Fallback: download audio and transcribe with whisper.
if command -v whisper >/dev/null 2>&1 || "$PYTHON_BIN" -c "import whisper" >/dev/null 2>&1; then
  echo "no captions found; falling back to whisper (this can take a while)…" >&2
  "${YTDLP[@]}" "${YTDLP_NET[@]}" "${FFMPEG_ARGS[@]+"${FFMPEG_ARGS[@]}"}" --no-warnings -x --audio-format mp3 \
    -o "$OUT/%(id)s.%(ext)s" "$URL" >/dev/null 2>&1 || true
  AUDIO="$(ls -1 "$OUT/$VID".mp3 2>/dev/null | head -1 || true)"
  if [ -n "$AUDIO" ]; then
    "$PYTHON_BIN" -m whisper "$AUDIO" --model base --output_format txt --output_dir "$OUT" >/dev/null 2>&1 || true
    [ -f "$OUT/$VID.txt" ] && { echo "transcript: $OUT/$VID.txt"; exit 0; }
  fi
fi

# 3. Fallback: transcribe via Gemini (no whisper/torch needed).
if [ -n "${GEMINI_API_KEY:-}" ]; then
  echo "no captions and no whisper; transcribing via Gemini…" >&2
  GEM="$(dirname "$0")/gemini-video-analyze.sh"
  GEM_OUT="$(bash "$GEM" "$URL" "Transcribe this video verbatim. Prefix each line with [mm:ss]. Output only the transcript, no commentary." 2>/dev/null || true)"
  if [ -n "$GEM_OUT" ]; then
    printf '%s\n' "$GEM_OUT" > "$OUT/$VID.txt"
    echo "transcript (via Gemini): $OUT/$VID.txt"
    exit 0
  fi
fi

echo "error: no captions available, whisper is not installable here, and Gemini fallback failed." >&2
echo "hint: set GEMINI_API_KEY in .env (the yt-visual-analysis skill also covers audio)." >&2
exit 1
