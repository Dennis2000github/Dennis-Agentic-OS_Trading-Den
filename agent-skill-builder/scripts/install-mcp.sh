#!/usr/bin/env bash
# Register the Gemini video MCP with Claude Code (user scope).
# The project-scoped .mcp.json already describes the same servers; this makes
# them available everywhere. Usage: install-mcp.sh
set -euo pipefail
# shellcheck source=scripts/_env.sh
. "$(dirname "$0")/_env.sh"

if ! command -v claude >/dev/null 2>&1; then
  echo "error: 'claude' CLI not found on PATH." >&2
  echo "install Claude Code first: https://docs.claude.com/en/docs/claude-code" >&2
  exit 1
fi

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "error: GEMINI_API_KEY is not set. Add it to $ROOT/.env" >&2
  exit 1
fi

echo "Registering gemini-video MCP (user scope)…"
claude mcp add --scope user gemini-video \
  -e GEMINI_API_KEY="$GEMINI_API_KEY" \
  -e "GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.6-flash}" \
  -e "GEMINI_THINKING=${GEMINI_THINKING:-high}" \
  -- npx -y @ugarchance/mcp-gemini-video-understanding

echo
echo "Registering yt-transcript MCP (user scope)…"
claude mcp add --scope user yt-transcript \
  -- npx -y @thenavidm/youtube-mcp-cli@latest

echo
echo "Done. Verify inside Claude Code with: /mcp"
