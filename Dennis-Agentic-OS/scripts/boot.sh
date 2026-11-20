#!/usr/bin/env bash
# Start DENNIS Agentic OS bound to localhost only.
# Idempotent: if a healthy instance is already running, does nothing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
ROOT_DIR="$(dirname "$APP_DIR")"
JARVIS_HOME="${HOME}/.jarvis"
PID_FILE="${JARVIS_HOME}/app.pid"
LOG_FILE="${JARVIS_HOME}/app.log"
URL="http://127.0.0.1:8080/api/jarvis/health"

mkdir -p "$JARVIS_HOME"
chmod 700 "$JARVIS_HOME"

if [ -x "$ROOT_DIR/.venv/bin/python3" ]; then
  PY="$ROOT_DIR/.venv/bin/python3"
else
  PY="$(command -v python3)"
fi

# /api/jarvis/health is owner-authenticated. Fetch the public bootstrap token
# on every probe so a freshly started server is not reported as a false 401.
healthy() {
  command -v curl >/dev/null 2>&1 || return 1
  local tok
  tok="$(curl -fsS --max-time 2 http://127.0.0.1:8080/api/auth/session 2>/dev/null \
    | "$PY" -c 'import sys,json; print(json.load(sys.stdin).get("token",""))' 2>/dev/null || true)"
  curl -fsS --max-time 2 "$URL?token=$tok" >/dev/null 2>&1
}

if healthy; then
  echo "Already healthy at 127.0.0.1:8080 — nothing to do."
  exit 0
fi

# If something else holds the port, refuse rather than spawn a failing process.
if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:8080 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "ERROR: port 8080 is already in use. Run 'npm run stop' first." >&2
  lsof -nP -iTCP:8080 -sTCP:LISTEN 2>/dev/null || true
  exit 1
fi

cd "$APP_DIR"
nohup "$PY" app.py > "$LOG_FILE" 2>&1 &
PID=$!
echo "$PID" > "$PID_FILE"
chmod 600 "$PID_FILE" "$LOG_FILE" 2>/dev/null || true

echo "Started PID $PID (log: $LOG_FILE). Waiting for health..."
for _ in $(seq 1 30); do
  if healthy; then
    echo "OK — DENNIS Agentic OS healthy at http://127.0.0.1:8080/ (localhost only)."
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "ERROR: process exited during startup. Last log lines:" >&2
    tail -20 "$LOG_FILE" >&2 || true
    exit 1
  fi
  sleep 1
done

echo "ERROR: health check timed out. Last log lines:" >&2
tail -20 "$LOG_FILE" >&2 || true
exit 1
