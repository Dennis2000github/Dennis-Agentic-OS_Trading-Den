#!/usr/bin/env bash
# Agent Skill Builder — one-shot setup.
# Usage: bash install.sh [--with-mcp]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Agent Skill Builder setup"
echo

echo "==> 1/5  Dependencies"
bash "$HERE/scripts/check-deps.sh" || true
echo

echo "==> 2/5  Static binaries (ffmpeg + ffprobe -> ./bin)"
if [ "${SKIP_FFMPEG:-0}" = "1" ]; then
  echo "skipped (SKIP_FFMPEG=1)"
else
  bash "$HERE/scripts/fetch-ffmpeg.sh" || echo "warning: ffmpeg fetch incomplete (optional)"
fi
echo

echo "==> 3/5  Environment"
if [ ! -f "$HERE/.env" ]; then
  cp "$HERE/.env.example" "$HERE/.env"
  echo "created $HERE/.env — add your GEMINI_API_KEY (https://aistudio.google.com/apikey)"
else
  echo "kept existing $HERE/.env"
fi
echo

echo "==> 4/5  Installing Claude Code skills -> ~/.claude/skills"
bash "$HERE/scripts/install-skills.sh"
echo

echo "==> 5/5  MCP servers"
if [ "${1:-}" = "--with-mcp" ]; then
  bash "$HERE/scripts/install-mcp.sh"
else
  echo "Skipped. The project .mcp.json is picked up automatically when this folder"
  echo "is trusted in Claude Code. To register globally instead, run:"
  echo "  bash $HERE/scripts/install-mcp.sh"
fi
echo

echo "Done. Try in Claude Code:"
echo '  "Get the transcript of <YouTube URL>, then analyze it visually with Gemini,'
echo '   then turn it into an expert skill called my-topic-agent."'
