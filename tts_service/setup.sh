#!/bin/bash
# DENNIS Kokoro TTS service — one-time/idempotent setup for Intel macOS.
# Uses the uv binary already present on the machine (no sudo, no Homebrew,
# no Docker). Provisions an isolated CPython 3.12 venv and downloads the
# official Kokoro v1.0 ONNX weights on first run.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$DIR")"

UV="${UV:-$HOME/.hermes/tools/uv-0.13.5-darwin-x64/uv}"
if [ ! -x "$UV" ]; then
  UV="$(command -v uv || true)"
fi
if [ -z "${UV:-}" ] || [ ! -x "${UV:-}" ]; then
  echo "ERROR: uv not found (looked in ~/.hermes/tools/uv-*/ and PATH)." >&2
  echo "Install uv user-locally (no sudo): curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

echo "==> Creating isolated Python 3.12 venv (uv: $UV)"
"$UV" python install 3.12 >/dev/null 2>&1 || true
"$UV" venv --python 3.12 "$DIR/.venv" >/dev/null

echo "==> Installing inference packages (kokoro-onnx / onnxruntime CPU)"
"$UV" pip install --python "$DIR/.venv/bin/python" "kokoro-onnx>=0.6.1" >/dev/null

mkdir -p "$DIR/models"
MODEL="$DIR/models/kokoro-v1.0.onnx"
VOICES="$DIR/models/voices-v1.0.bin"
BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

download() { # url dest expected_size
  local url="$1" dest="$2" expected="$3" size=0
  if [ -f "$dest" ]; then size=$(stat -f%z "$dest" 2>/dev/null || echo 0); fi
  if [ "$size" = "$expected" ]; then echo "    ok: $(basename "$dest")"; return 0; fi
  echo "    downloading $(basename "$dest") ($expected bytes)…"
  for i in 1 2 3 4 5; do
    curl -fsSL -C - --retry 5 --retry-delay 3 -o "$dest.part" "$url" && break
    sleep 5
  done
  size=$(stat -f%z "$dest.part" 2>/dev/null || echo 0)
  if [ "$size" != "$expected" ]; then
    echo "ERROR: download incomplete ($size/$expected bytes): $dest" >&2; exit 1
  fi
  mv "$dest.part" "$dest"
}

echo "==> Official Kokoro v1.0 weights"
download "$BASE/kokoro-v1.0.onnx"   "$MODEL"  325532387
download "$BASE/voices-v1.0.bin"    "$VOICES" 28214398

echo "==> Smoke test (loads model, ~15-20 s first time)"
"$DIR/.venv/bin/python" - "$DIR/models" <<'PY'
import sys, time
sys.path.insert(0, sys.argv[1] and ".")
import os
os.chdir(os.path.dirname(os.path.abspath(sys.argv[1])) if False else ".")
from kokoro_onnx import Kokoro
t0 = time.time()
k = Kokoro(os.path.join(sys.argv[1], "kokoro-v1.0.onnx"),
           os.path.join(sys.argv[1], "voices-v1.0.bin"))
voices = k.get_voices()
assert "bm_george" in voices and "bm_lewis" in voices, voices
print(f"    model ok in {time.time()-t0:.0f}s — {len(voices)} voices, bm_george present")
PY

echo "Setup complete. Start the service with:"
echo "  python3 scripts/kokoro_service.py start   (from the repo root)"
