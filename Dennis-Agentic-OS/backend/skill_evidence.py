"""Normalized, timestamp-aligned evidence for one video source.

The evidence object is the single truth that every later stage reads.  It
keeps source timestamps and provenance, and it never upgrades an observation
into a claim: each record says where it came from.

Shape (version 1)::

    {
      "version": 1,
      "source": {kind,id,url,title,duration_seconds,width,height,fps,
                 content_hash,fetched_at},
      "technical": {container,video_codec,audio_codec,sample_rate,channels,
                     aspect,bitrate,size_bytes},
      "scenes": [{index,start,end,duration,frame_ref}],
      "frames": [{index,t,ref}],
      "audio": {narration,dialogue,music,sfx,silence,voice,wpm,segments},
      "timeline": {cuts,cuts_per_minute,mean_scene_seconds,
                    median_scene_seconds,longest_scene,shortest_scene,
                    first_three_seconds,reveal_beats},
      "limitations": [...]
    }
"""

import json
import math
import re

VERSION = 1
MAX_SCENES = 120

_TS = r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d{1,3})?"
_ARROW_LINE_RE = re.compile(
    r"^\s*\[?(%s)\]?\s*[-–>]+\s*\[?(%s)?\]?\s*(.+)$" % (_TS, _TS))
_BRACKET_LINE_RE = re.compile(r"^\s*\[(%s)\]\s*(.+)$" % _TS)
_TIMESTAMP_ONLY_RE = re.compile(r"^[\d:.,\s]+$")


def _seconds(value):
    try:
        parts = str(value).replace(",", ".").split(":")
        if len(parts) == 3:
            return round(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]), 3)
        if len(parts) == 2:
            return round(int(parts[0]) * 60 + float(parts[1]), 3)
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def parse_timed_transcript(text):
    """Parse SRT/VTT cues and ``[mm:ss] …`` lines into timed segments."""
    segments = []
    current = None

    def flush():
        if current and current["text"]:
            segments.append({
                "start": current["start"], "end": current["end"],
                "text": current["text"][:500]})

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            current = None
            continue
        if line.isdigit() and current is None:
            continue
        match = _ARROW_LINE_RE.match(line)
        if match:
            flush()
            body = " ".join(match.group(3).split())
            current = {
                "start": _seconds(match.group(1)),
                "end": _seconds(match.group(2)) if match.group(2) else None,
                "text": "" if _TIMESTAMP_ONLY_RE.match(body or "") else body,
            }
            continue
        match = _BRACKET_LINE_RE.match(line)
        if match:
            flush()
            current = None
            body = " ".join(match.group(2).split())
            if body and not _TIMESTAMP_ONLY_RE.match(body):
                segments.append({"start": _seconds(match.group(1)),
                                 "end": None, "text": body[:500]})
            continue
        if current is not None and not _TIMESTAMP_ONLY_RE.match(line):
            current["text"] = (current["text"] + " " + line).strip()
    flush()
    return [item for item in segments if item.get("start") is not None]


def _normalise_scenes(raw_scenes, frames):
    frame_by_index = {frame.get("index"): frame for frame in frames or []}
    scenes = []
    for item in raw_scenes or []:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0.0, float(item.get("start", 0)))
            end = float(item.get("end", start))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        index = len(scenes)
        frame = frame_by_index.get(index)
        scenes.append({
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "frame_ref": (frame or {}).get("ref") or "",
        })
        if len(scenes) >= MAX_SCENES:
            break
    return scenes


def timeline_stats(scenes, duration=0.0):
    durations = [scene["duration"] for scene in scenes if scene.get("duration")]
    cuts = max(0, len(scenes) - 1)
    total = float(duration or (scenes[-1]["end"] if scenes else 0) or 0)
    stats = {
        "scenes": len(scenes),
        "cuts": cuts,
        "cuts_per_minute": round(cuts / (total / 60.0), 2) if total > 0 else 0.0,
        "mean_scene_seconds": round(sum(durations) / len(durations), 2) if durations else 0.0,
        "median_scene_seconds": round(sorted(durations)[len(durations) // 2], 2) if durations else 0.0,
        "longest_scene": max(scenes, key=lambda s: s["duration"], default=None),
        "shortest_scene": min(scenes, key=lambda s: s["duration"], default=None),
        "first_three_seconds": [
            {"start": s["start"], "end": s["end"], "duration": s["duration"]}
            for s in scenes if s["start"] < 3.0
        ],
        "reveal_beats": [
            {"t": s["start"], "scene": s["index"]}
            for s in scenes[:6]
        ],
    }
    return stats


def build_timeline(scenes, duration=0.0, segments=None):
    stats = timeline_stats(scenes, duration)
    if segments:
        # Which scene each narrated segment starts in — factual alignment only.
        alignment = []
        for segment in segments[:400]:
            start = segment.get("start")
            if start is None:
                continue
            scene = next((s for s in scenes if s["start"] <= start < s["end"]), None)
            if scene:
                alignment.append({"scene": scene["index"], "t": start,
                                  "words": segment.get("text", "")[:160]})
        stats["narration_alignment"] = alignment[:120]
    return stats


def build_audio(transcript, segments, duration=0.0):
    words = len(re.findall(r"\S+", transcript or ""))
    wpm = round(words / (float(duration) / 60.0), 1) if duration and words else 0.0
    return {
        "narration": bool(words),
        "dialogue": False,
        "music": "unknown",
        "sfx": [],
        "silence": [],
        "voice": "unknown",
        "words": words,
        "wpm": wpm,
        "segments": segments[:400],
    }


def normalize(source, technical=None, scenes=None, frames=None,
              transcript="", timed_transcript="", warnings=None):
    """Assemble a versioned evidence object from raw observations."""
    meta = (source or {}).get("metadata") or {}
    transcript = transcript or (source or {}).get("transcript") or ""
    timed_transcript = timed_transcript or (source or {}).get("timed_transcript") or ""
    technical = dict(technical or {})
    frames = frames or []
    normalized_scenes = _normalise_scenes(scenes, frames)
    duration = (technical.get("duration_seconds") or
                meta.get("duration_seconds") or 0)
    segments = parse_timed_transcript(timed_transcript)
    limitations = list(warnings or [])
    if not technical:
        limitations.append("technical metadata unavailable (ffprobe not run)")
    if not normalized_scenes:
        limitations.append("no scene boundaries available")
    if not frames:
        limitations.append("no representative frames extracted")
    evidence = {
        "version": VERSION,
        "source": {
            "kind": (source or {}).get("kind") or "",
            "id": (source or {}).get("id") or meta.get("id") or "",
            "url": (source or {}).get("url") or meta.get("url") or "",
            "title": (source or {}).get("title") or meta.get("title") or "",
            "duration_seconds": duration or 0,
            "width": technical.get("width") or meta.get("width") or 0,
            "height": technical.get("height") or meta.get("height") or 0,
            "fps": technical.get("fps") or meta.get("fps") or 0,
            "content_hash": (source or {}).get("content_hash") or "",
            "fetched_at": (source or {}).get("fetched_at") or 0,
            "view_count": meta.get("view_count"),
        },
        "technical": technical,
        "scenes": normalized_scenes,
        "frames": [{"index": f.get("index"), "t": f.get("t"),
                    "ref": f.get("ref")} for f in frames],
        "audio": build_audio(transcript, segments, duration),
        "timeline": build_timeline(normalized_scenes, duration, segments),
        "limitations": limitations,
    }
    return evidence


def validate(evidence):
    """Return a list of schema errors (empty means valid)."""
    errors = []
    if not isinstance(evidence, dict):
        return ["evidence must be an object"]
    if evidence.get("version") != VERSION:
        errors.append("unsupported evidence version")
    source = evidence.get("source")
    if not isinstance(source, dict) or not source.get("id"):
        errors.append("source.id is required")
    scenes = evidence.get("scenes")
    if not isinstance(scenes, list):
        errors.append("scenes must be a list")
    else:
        previous = -1.0
        for scene in scenes:
            if not isinstance(scene, dict):
                errors.append("scene is not an object")
                continue
            try:
                start, end = float(scene.get("start")), float(scene.get("end"))
            except (TypeError, ValueError):
                errors.append("scene times are not numeric")
                continue
            if start < 0 or end <= start:
                errors.append("scene %s has invalid bounds" % scene.get("index"))
            if start < previous - 0.001:
                errors.append("scenes are not chronological")
            previous = start
            if abs((end - start) - float(scene.get("duration") or 0)) > 0.01:
                errors.append("scene %s duration mismatch" % scene.get("index"))
    return errors


def prompt_block(evidence, limit=14000):
    """Compact, human+t model readable evidence block for prompts."""
    if not evidence:
        return "(no structured evidence)"
    lines = []
    source = evidence.get("source") or {}
    technical = evidence.get("technical") or {}
    lines.append("SOURCE: %s (%s) duration=%ss %sx%s @%sfps" % (
        source.get("title") or source.get("id"), source.get("kind"),
        source.get("duration_seconds"), source.get("width"),
        source.get("height"), source.get("fps")))
    if technical:
        lines.append("TECHNICAL: container=%s video=%s audio=%s %sHz/%sch aspect=%s" % (
            technical.get("container") or "?", technical.get("video_codec") or "?",
            technical.get("audio_codec") or "?", technical.get("sample_rate") or 0,
            technical.get("channels") or 0, technical.get("aspect") or "?"))
    stats = evidence.get("timeline") or {}
    lines.append("TIMELINE: scenes=%s cuts=%s cuts/min=%s mean=%ss" % (
        stats.get("scenes"), stats.get("cuts"), stats.get("cuts_per_minute"),
        stats.get("mean_scene_seconds")))
    scenes = evidence.get("scenes") or []
    if scenes:
        lines.append("SCENES (start-end s | duration | frame):")
        for scene in scenes:
            lines.append("  [%.1f-%.1f] %.1fs %s" % (
                scene.get("start", 0), scene.get("end", 0),
                scene.get("duration", 0), scene.get("frame_ref") or ""))
    audio = evidence.get("audio") or {}
    lines.append("AUDIO: narration=%s words=%s wpm=%s music=%s" % (
        audio.get("narration"), audio.get("words"), audio.get("wpm"),
        audio.get("music")))
    segments = audio.get("segments") or []
    if segments:
        lines.append("NARRATION SEGMENTS (first 40):")
        for segment in segments[:40]:
            lines.append("  [%.1f] %s" % (segment.get("start", 0),
                                          segment.get("text", "")))
    if evidence.get("limitations"):
        lines.append("LIMITATIONS: " + "; ".join(evidence["limitations"]))
    text = "\n".join(lines)
    return text[:limit]


def compact(evidence):
    """Small JSON-safe summary used in API responses."""
    return {
        "version": evidence.get("version"),
        "source": evidence.get("source"),
        "technical": evidence.get("technical"),
        "scene_count": len(evidence.get("scenes") or []),
        "frame_count": len(evidence.get("frames") or []),
        "timeline": evidence.get("timeline"),
        "limitations": evidence.get("limitations") or [],
    }


def dumps(evidence):
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True)


def clamp(value, low=0.0, high=1.0):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


def mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else 0.0


def finite(value, default=0.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default
