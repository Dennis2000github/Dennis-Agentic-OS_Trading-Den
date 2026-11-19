#!/usr/bin/env bash
# Download static ffmpeg + ffprobe into ./bin (macOS x86_64).
# Idempotent: skips binaries that already run.
set -euo pipefail
# shellcheck source=scripts/_env.sh
. "$(dirname "$0")/_env.sh"

BIN="$ROOT/bin"
mkdir -p "$BIN"

if [ -x "$BIN/ffmpeg" ] && "$BIN/ffmpeg" -version >/dev/null 2>&1; then
  echo "ffmpeg already present: $("$BIN/ffmpeg" -version 2>/dev/null | head -1)"
else
  echo "downloading ffmpeg (darwin-x64)…"
  curl -sSL --max-time 300 -o "$BIN/ffmpeg" \
    "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1/ffmpeg-darwin-x64"
  chmod +x "$BIN/ffmpeg"
  xattr -c "$BIN/ffmpeg" 2>/dev/null || true
fi

fetch_ffprobe() {
  local snap="${FFPROBE_SNAPSHOT:-ffprobe-127602-g2d49d0e6243}"
  local tmp
  tmp="$(mktemp -t ffprobe.XXXXXX).zip"
  for _ in 1 2 3 4; do
    if curl -sSL --max-time 180 -o "$tmp" "https://evermeet.cx/ffmpeg/${snap}.zip" \
       && unzip -tq "$tmp" >/dev/null 2>&1; then
      unzip -oq "$tmp" -d "$BIN"
      chmod +x "$BIN/ffprobe"
      xattr -c "$BIN/ffprobe" 2>/dev/null || true
      rm -f "$tmp"
      return 0
    fi
    sleep 15
  done
  rm -f "$tmp"
  return 1
}

if [ -x "$BIN/ffprobe" ] && "$BIN/ffprobe" -version >/dev/null 2>&1; then
  echo "ffprobe already present: $("$BIN/ffprobe" -version 2>/dev/null | head -1)"
else
  echo "downloading ffprobe (evermeet snapshot; may retry on rate-limit)…"
  if fetch_ffprobe; then :; else
    echo "warning: could not fetch ffprobe (rate-limited). ffmpeg alone still works" >&2
    echo "         for most yt-dlp audio/video tasks; re-run later to retry." >&2
  fi
fi

echo
ls -la "$BIN"
