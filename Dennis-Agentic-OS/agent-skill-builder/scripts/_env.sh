#!/usr/bin/env bash
# Shared bootstrap: locate workspace, load .env, ensure output dir.
# Sourced by the other scripts (never executed directly).

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/output"
mkdir -p "$OUT"

# Locate workspace-local static binaries (ffmpeg/ffprobe).
# NB: this repo path contains a colon, so we must NOT prepend it to PATH —
# instead we hand yt-dlp an explicit --ffmpeg-location.
FFMPEG_LOC="${FFMPEG_LOC:-}"
if [ -z "$FFMPEG_LOC" ] && [ -x "$ROOT/bin/ffmpeg" ]; then
  FFMPEG_LOC="$ROOT/bin"
fi

# yt-dlp args array (safe on bash 3.2 with `set -u` via ${arr[@]+...}).
FFMPEG_ARGS=()
[ -n "$FFMPEG_LOC" ] && FFMPEG_ARGS=(--ffmpeg-location "$FFMPEG_LOC")

if [ -f "$ROOT/.env" ]; then
  # Let variables already exported in the shell win over the file, so callers can
  # do `GEMINI_MODELS=... script.sh` for one-off overrides.
  _keep=""
  for _k in GEMINI_API_KEY GEMINI_MODEL GEMINI_MODELS GEMINI_THINKING \
            GEMINI_ATTEMPTS GEMINI_RETRY_SLEEP FFMPEG_LOC; do
    eval "_v=\${$_k-}"
    if [ -n "$_v" ]; then
      eval "_kv_$_k=\$_v"
      _keep="$_keep $_k"
    fi
  done
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  for _k in $_keep; do
    eval "export $_k=\$_kv_$_k"
  done
  unset _k _v _keep
fi

require() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'error: %s is required but not installed. %s\n' "$1" "${2:-}" >&2
    exit 1
  fi
}

# Resolve yt-dlp whether it is on PATH, importable, or only installed as a
# user-site console script (pip --user, common on macOS without brew).
# PYTHON_BIN is exported by the app so the scripts use the same interpreter
# that has yt_dlp installed, even when launched from a bare GUI environment.
PYTHON_BIN="${PYTHON_BIN:-python3}"
export PYTHON_BIN

_user_bin="$("$PYTHON_BIN" -c 'import sysconfig;print(sysconfig.get_path("scripts", scheme="posix_user"))' 2>/dev/null || true)"
if [ -n "$_user_bin" ] && [ -d "$_user_bin" ]; then
  case ":$PATH:" in
    *":$_user_bin:"*) ;;
    *) PATH="$_user_bin:$PATH"; export PATH ;;
  esac
fi

if command -v yt-dlp >/dev/null 2>&1; then
  YTDLP=(yt-dlp)
elif "$PYTHON_BIN" -c "import yt_dlp" >/dev/null 2>&1; then
  YTDLP=("$PYTHON_BIN" -m yt_dlp)
elif [ -n "$_user_bin" ] && [ -x "$_user_bin/yt-dlp" ]; then
  YTDLP=("$_user_bin/yt-dlp")
else
  YTDLP=()
fi
unset _user_bin

require_ytdlp() {
  if [ "${#YTDLP[@]}" -eq 0 ]; then
    echo "error: yt-dlp is required. install: $PYTHON_BIN -m pip install -U yt-dlp" >&2
    exit 1
  fi
}

# Network guards so yt-dlp can't hang a whole run on a stalled connection.
YTDLP_NET=(--socket-timeout 20 --retries 3 --fragment-retries 3)
