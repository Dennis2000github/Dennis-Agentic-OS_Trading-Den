#!/usr/bin/env bash
# Build one prebuilt image per agent CLI (the CLI is installed at build time).
# Sessions never install or download anything at start.
#
# Usage:
#   scripts/build_agent_images.sh            # build every known image
#   scripts/build_agent_images.sh opencode   # build one
#
# DOCKER_BIN overrides the docker executable; DOCKER_HOST is honoured.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER="${DOCKER_BIN:-docker}"
PREFIX="${DENNIS_SANDBOX_IMAGE_PREFIX:-dennis-agent}"

if ! command -v "$DOCKER" >/dev/null 2>&1; then
  for candidate in /usr/local/bin/docker /opt/homebrew/bin/docker \
                   "/Applications/Docker.app/Contents/Resources/bin/docker" \
                   "$HOME/.docker/bin/docker" "$HOME/.rd/bin/docker"; do
    if [ -x "$candidate" ]; then DOCKER="$candidate"; break; fi
  done
fi

if ! command -v "$DOCKER" >/dev/null 2>&1; then
  echo "Docker CLI not found. Install Docker or set DOCKER_BIN." >&2
  exit 1
fi

if ! "$DOCKER" info >/dev/null 2>&1; then
  echo "Docker is installed but its daemon is not running. Start Docker and retry." >&2
  exit 1
fi

AGENTS=("$@")
if [ ${#AGENTS[@]} -eq 0 ]; then
  AGENTS=(opencode antigravity codex claude-code cursor openclaw)
fi

for agent in "${AGENTS[@]}"; do
  dockerfile="$ROOT/docker/agent-${agent}.Dockerfile"
  if [ ! -f "$dockerfile" ]; then
    echo "No Dockerfile for agent '${agent}' at $dockerfile" >&2
    exit 1
  fi
  echo "==> building ${PREFIX}-${agent}:latest"
  "$DOCKER" build -f "$dockerfile" -t "${PREFIX}-${agent}:latest" "$ROOT"
done

echo "Done. Images:"
for agent in "${AGENTS[@]}"; do
  echo "  ${PREFIX}-${agent}:latest"
done
