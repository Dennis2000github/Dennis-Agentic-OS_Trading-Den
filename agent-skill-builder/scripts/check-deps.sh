#!/usr/bin/env bash
# Report which Agent Skill Builder dependencies are present.
set -uo pipefail

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
miss() { printf '  \033[31m✗\033[0m %s\n' "$1"; }

have() { command -v "$1" >/dev/null 2>&1; }

BIN="$(cd "$(dirname "$0")/.." && pwd)/bin"
PYTHON_BIN="${PYTHON_BIN:-python3}"

_user_bin="$("$PYTHON_BIN" -c 'import sysconfig;print(sysconfig.get_path("scripts", scheme="posix_user"))' 2>/dev/null || true)"
if [ -n "$_user_bin" ] && [ -d "$_user_bin" ]; then
  case ":$PATH:" in
    *":$_user_bin:"*) ;;
    *) PATH="$_user_bin:$PATH" ;;
  esac
fi

echo "Agent Skill Builder — dependency check"
echo

echo "Required:"
if have yt-dlp; then
  ok "yt-dlp ($(yt-dlp --version 2>/dev/null))"
elif "$PYTHON_BIN" -c "import yt_dlp" >/dev/null 2>&1; then
  ok "yt-dlp (via $PYTHON_BIN -m yt_dlp)"
elif [ -n "$_user_bin" ] && [ -x "$_user_bin/yt-dlp" ]; then
  ok "yt-dlp ($_user_bin/yt-dlp)"
else
  miss "yt-dlp  → install: $PYTHON_BIN -m pip install -U yt-dlp"
fi

if have "$PYTHON_BIN"; then ok "python ($("$PYTHON_BIN" -V 2>&1))"
else miss "python ($PYTHON_BIN)"; fi

if have curl; then ok "curl"
else miss "curl"; fi

echo
echo "Optional:"
if [ -x "$BIN/ffmpeg" ]; then ok "ffmpeg (workspace ./bin)"
elif have ffmpeg; then ok "ffmpeg (system)"
else miss "ffmpeg  → run: bash scripts/fetch-ffmpeg.sh"; fi

if [ -x "$BIN/ffprobe" ]; then ok "ffprobe (workspace ./bin)"
elif have ffprobe; then ok "ffprobe (system)"
else miss "ffprobe  → run: bash scripts/fetch-ffmpeg.sh"; fi

if have node && have npx; then ok "node $(node -v 2>/dev/null) + npx"
else miss "node/npx  → needed for MCP servers"; fi

if have whisper || "$PYTHON_BIN" -c "import whisper" >/dev/null 2>&1; then
  ok "openai-whisper (transcript fallback when no captions)"
else
  miss "openai-whisper (optional; unavailable on Python 3.14) → Gemini fallback covers this"
fi

if [ -n "${GEMINI_API_KEY:-}" ]; then ok "GEMINI_API_KEY is set"
elif [ -f "$(dirname "$0")/../.env" ] && grep -q '^GEMINI_API_KEY=.\+' "$(dirname "$0")/../.env"; then
  ok "GEMINI_API_KEY found in .env"
else
  miss "GEMINI_API_KEY  → https://aistudio.google.com/apikey (put it in .env)"
fi

echo
echo "Note: yt-dlp is the only hard requirement for the 'ears' step."
