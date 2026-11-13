#!/usr/bin/env bash
# YouTube -> visual + audio breakdown via the Gemini API ("eyes").
# Fallback for when the gemini-video MCP is not registered.
#
# Usage: gemini-video-analyze.sh <URL> ["custom prompt"]
#
# Models & reasoning are configurable via .env:
#   GEMINI_MODELS   comma-separated primary -> fallback chain
#                   (default: gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,
#                             gemini-3.5-flash,gemini-3.5-flash-lite)
#   GEMINI_MODEL    single-model override (back-compat; used if GEMINI_MODELS unset)
#   GEMINI_THINKING thinking/reasoning level for agentic work:
#                   minimal | low | medium | high  (default: high; "off" disables)
#   GEMINI_ATTEMPTS retries per model on overload/rate limit (default: 3)
#   GEMINI_RETRY_SLEEP seconds between retries (default: 8)
set -euo pipefail
# shellcheck source=scripts/_env.sh
. "$(dirname "$0")/_env.sh"

URL="${1:-}"
PROMPT="${2:-Analyze this video for an expert skill. Produce: (1) a concise visual summary, (2) a timestamped breakdown [mm:ss] of what is shown and why it matters, (3) any on-screen code, slides, tools, URLs or diagrams as artifacts, and (4) gaps where the visuals were ambiguous.}"

if [ -z "$URL" ]; then
  echo "usage: gemini-video-analyze.sh <URL> [prompt]" >&2
  exit 2
fi

if [ -z "${GEMINI_API_KEY:-}" ]; then
  echo "error: GEMINI_API_KEY is not set." >&2
  echo "get a free key at https://aistudio.google.com/apikey, then add it to $ROOT/.env" >&2
  exit 1
fi

require curl
require "$PYTHON_BIN"

DEFAULT_MODELS="gemini-3.8-flash gemini-3.7-flash gemini-3.6-flash gemini-3.5-flash gemini-3.5-flash-lite"
if [ -n "${GEMINI_MODELS:-}" ]; then
  MODELS="$(printf '%s' "$GEMINI_MODELS" | tr ',' ' ')"
elif [ -n "${GEMINI_MODEL:-}" ]; then
  MODELS="$GEMINI_MODEL"
else
  MODELS="$DEFAULT_MODELS"
fi
THINKING="${GEMINI_THINKING:-high}"
ATTEMPTS="${GEMINI_ATTEMPTS:-3}"
RETRY_SLEEP="${GEMINI_RETRY_SLEEP:-8}"

VID="$("$PYTHON_BIN" - "$URL" <<'PY'
import re, sys
u = sys.argv[1]
m = re.search(r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})", u)
print(m.group(1) if m else "video")
PY
)"

TMP="$(mktemp -t dennis-gemini.XXXXXX)"
trap 'rm -f "$TMP" "$TMP.req"' EXIT

SB_URL="$URL" SB_PROMPT="$PROMPT" SB_THINKING="$THINKING" "$PYTHON_BIN" > "$TMP.req" <<'PY'
import json, os, sys
url = os.environ["SB_URL"]
prompt = os.environ["SB_PROMPT"]
thinking = os.environ["SB_THINKING"]
gen = {"temperature": 0.3, "maxOutputTokens": 8192}
if thinking and thinking.lower() not in ("none", "off"):
    gen["thinkingConfig"] = {"thinkingLevel": thinking}
print(json.dumps({
    "contents": [{
        "parts": [
            {"fileData": {"fileUri": url}},
            {"text": prompt},
        ]
    }],
    "generationConfig": gen,
}))
PY

# Try one model. Returns 0 = success, 1 = try next model, 2 = fatal.
try_model() {
  model="$1"
  endpoint="https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent"
  attempt=1
  while :; do
    if ! curl -sS -X POST "$endpoint" \
        -H "Content-Type: application/json" \
        -H "x-goog-api-key: ${GEMINI_API_KEY}" \
        --data-binary "@$TMP.req" -o "$TMP"; then
      echo "error: request to Gemini failed." >&2
      return 2
    fi

    status="$("$PYTHON_BIN" -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('error',{}).get('status',''))" "$TMP" 2>/dev/null || true)"
    if [ -z "$status" ]; then
      return 0
    fi

    case "$status" in
      UNAVAILABLE|RESOURCE_EXHAUSTED|UNKNOWN|INTERNAL|DEADLINE_EXCEEDED)
        if [ "$attempt" -ge "$ATTEMPTS" ]; then
          echo "gemini: ${model} unavailable ($status) after ${attempt}; falling through." >&2
          return 1
        fi
        echo "gemini: ${model} busy ($status); retry ${attempt}/${ATTEMPTS}…" >&2
        attempt=$((attempt + 1))
        sleep "$RETRY_SLEEP"
        ;;
      NOT_FOUND)
        echo "gemini: ${model} not available; falling through." >&2
        return 1
        ;;
      *)
        msg="$("$PYTHON_BIN" -c "import json,sys;print(json.load(open(sys.argv[1])).get('error',{}).get('message','')[:200])" "$TMP" 2>/dev/null || true)"
        echo "error: gemini ${model}: ${status} ${msg}" >&2
        return 2
        ;;
    esac
  done
}

USED=""
for model in $MODELS; do
  rc=0
  try_model "$model" || rc=$?
  if [ "$rc" -eq 0 ]; then
    USED="$model"
    break
  fi
  if [ "$rc" -eq 2 ]; then
    exit 1
  fi
done

if [ -z "$USED" ]; then
  echo "error: all models failed (${MODELS}); check GEMINI_API_KEY / quota." >&2
  exit 1
fi

"$PYTHON_BIN" - "$TMP" "$OUT/$VID.visual.md" "$USED" <<'PY'
import json, sys
raw, dest, model = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(raw, encoding="utf-8"))
if "error" in data:
    msg = data["error"].get("message", "unknown error")
    print(f"error: Gemini API: {msg}", file=sys.stderr)
    sys.exit(1)
cands = data.get("candidates") or []
if not cands:
    print("error: Gemini returned no candidates (video may be private, too long, or unsupported).", file=sys.stderr)
    sys.exit(1)
parts = cands[0].get("content", {}).get("parts", [])
text = "\n".join(p.get("text", "") for p in parts).strip()
if not text:
    print("error: empty response.", file=sys.stderr)
    sys.exit(1)
with open(dest, "w", encoding="utf-8") as fh:
    fh.write(text + "\n")
print(text)
print(f"\n---\nmodel: {model} | saved: {dest}", file=sys.stderr)
PY
