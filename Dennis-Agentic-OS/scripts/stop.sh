#!/usr/bin/env bash
# Stop the DENNIS Agentic OS server gracefully.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
JARVIS_HOME="${HOME}/.jarvis"
PID_FILE="${JARVIS_HOME}/app.pid"

pids=()
if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    pids+=("$PID")
  fi
  rm -f "$PID_FILE"
fi

# Legacy fallback: the pre-upgrade app was started without a PID file.
while IFS= read -r p; do
  [[ "$p" =~ ^[0-9]+$ ]] || continue
  if ! kill -0 "$p" 2>/dev/null; then continue; fi
  if ps -p "$p" -o command= 2>/dev/null | grep -F "${APP_DIR}/app.py" >/dev/null; then
    already=0
    for q in "${pids[@]:-}"; do [ "$q" = "$p" ] && already=1; done
    [ "$already" -eq 0 ] && pids+=("$p")
  fi
done < <(pgrep -f "${APP_DIR}/app.py" 2>/dev/null || true)

if [ "${#pids[@]}" -eq 0 ]; then
  echo "No running DENNIS Agentic OS server found."
  exit 0
fi

echo "Stopping PID(s): ${pids[*]}"
for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done

for _ in $(seq 1 15); do
  alive=0
  for p in "${pids[@]}"; do kill -0 "$p" 2>/dev/null && alive=1; done
  [ "$alive" -eq 0 ] && break
  sleep 1
done

for p in "${pids[@]}"; do
  if kill -0 "$p" 2>/dev/null; then
    echo "PID $p did not exit; sending SIGKILL."
    kill -9 "$p" 2>/dev/null || true
  fi
done

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:8080 -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; then
  echo "WARNING: something is still listening on 8080:" >&2
  lsof -nP -iTCP:8080 -sTCP:LISTEN 2>/dev/null || true
else
  echo "OK — server stopped, port 8080 free."
fi
