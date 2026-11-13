#!/usr/bin/env bash
# YouTube -> metadata JSON (title, channel, duration, description, chapters).
# Usage: yt-metadata.sh <URL>
set -euo pipefail
# shellcheck source=scripts/_env.sh
. "$(dirname "$0")/_env.sh"

URL="${1:-}"
if [ -z "$URL" ]; then
  echo "usage: yt-metadata.sh <URL>" >&2
  exit 2
fi

require_ytdlp
require "$PYTHON_BIN"

# yt-dlp's -J dump can be several MB; never pass it as an argv (E2BIG on macOS).
RAW="$(mktemp "${TMPDIR:-/tmp}/ytmeta.XXXXXX.json")"
trap 'rm -f "$RAW"' EXIT

if ! "${YTDLP[@]}" "${YTDLP_NET[@]}" --no-warnings --skip-download -J "$URL" > "$RAW"; then
  echo "error: yt-dlp could not fetch metadata for $URL" >&2
  exit 1
fi

"$PYTHON_BIN" - "$RAW" "$OUT" <<'PY'
import json, os, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
out = sys.argv[2]
vid = data.get("id", "video")
meta = {
    "id": vid,
    "title": data.get("title"),
    "channel": data.get("uploader") or data.get("channel"),
    "url": data.get("webpage_url"),
    "upload_date": data.get("upload_date"),
    "duration_seconds": data.get("duration"),
    "view_count": data.get("view_count"),
    "description": data.get("description"),
    "chapters": [
        {"start": c.get("start_time"), "title": c.get("title")}
        for c in (data.get("chapters") or [])
    ],
    "tags": data.get("tags") or [],
}
path = os.path.join(out, f"{vid}.json")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(meta, fh, indent=2, ensure_ascii=False)
print(f"wrote {path}")
print(f"  title:    {meta['title']}")
print(f"  channel:  {meta['channel']}")
print(f"  duration: {meta['duration_seconds']}s")
if meta["chapters"]:
    print(f"  chapters: {len(meta['chapters'])}")
PY
