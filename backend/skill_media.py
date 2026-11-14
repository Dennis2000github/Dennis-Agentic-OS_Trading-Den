"""Local media probing, adaptive scene detection and frame sampling.

This module is deliberately dependency-free (stdlib + the ffmpeg/ffprobe
binaries already used by the Skill Builder).  It turns a media file into the
raw observations that later stages normalise into evidence:

* technical stream facts (duration, codec, fps, aspect, audio layout),
* scene boundaries with timestamps,
* a bounded set of representative frames.

Nothing here calls an AI model, writes to the Vault or executes repository
code.  All subprocesses use argv lists (never a shell string) and fixed,
sanitised output paths.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

WORKSPACE = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent-skill-builder")
)
DEFAULT_MAX_FRAMES = int(os.environ.get("DENNIS_SB_MAX_FRAMES", "24"))
DEFAULT_SCENE_THRESHOLD = float(os.environ.get("DENNIS_SB_SCENE_THRESHOLD", "0.32"))
DEFAULT_FRAME_WIDTH = int(os.environ.get("DENNIS_SB_FRAME_WIDTH", "960"))
DEFAULT_SECTION_SECONDS = int(os.environ.get("DENNIS_SB_SECTION_SECONDS", "180"))

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_PTS_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
_FPS_RE = re.compile(r"^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$")


def safe_name(value, fallback="media"):
    name = _SAFE_NAME_RE.sub("-", str(value or "")).strip("-._")
    return (name or fallback)[:80]


def _candidate_dirs():
    dirs = []
    env_dir = (os.environ.get("DENNIS_FFMPEG_DIR") or "").strip()
    if env_dir:
        dirs.append(env_dir)
    dirs.append(os.path.join(WORKSPACE, "bin"))
    path = os.environ.get("PATH") or ""
    for entry in path.split(os.pathsep):
        if entry:
            dirs.append(entry)
    return dirs


def find_binary(name):
    """Resolve ``ffmpeg``/``ffprobe`` without installing anything."""
    exe = name + (".exe" if os.name == "nt" else "")
    for directory in _candidate_dirs():
        candidate = os.path.join(directory, exe)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return shutil.which(name)


def available():
    return {"ffmpeg": find_binary("ffmpeg"), "ffprobe": find_binary("ffprobe")}


def run(argv, timeout=120, cwd=None):
    """Run an argv list with no shell, returning ``(code, stdout, stderr)``."""
    try:
        proc = subprocess.run(
            argv, cwd=cwd, timeout=timeout, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def _rate(value):
    if not value:
        return 0.0
    match = _FPS_RE.match(str(value))
    if match:
        try:
            denominator = float(match.group(2))
            return round(float(match.group(1)) / denominator, 3) if denominator else 0.0
        except (TypeError, ValueError):
            return 0.0
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _aspect(width, height):
    if not width or not height:
        return ""
    known = {(1920, 1080): "16:9", (1080, 1920): "9:16", (1080, 1080): "1:1",
             (1280, 720): "16:9", (720, 1280): "9:16", (1440, 1080): "4:3",
             (1080, 1440): "3:4", (2560, 1440): "16:9", (3840, 2160): "16:9"}
    if (width, height) in known:
        return known[(width, height)]
    try:
        from math import gcd
        divisor = gcd(int(width), int(height))
        w, h = int(width) // divisor, int(height) // divisor
        if w <= 40 and h <= 40:
            return "%d:%d" % (w, h)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return "%.3f" % (float(width) / float(height))


def parse_probe(data):
    """Normalise ``ffprobe -show_format -show_streams`` JSON."""
    data = data or {}
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), {}) or {}
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {}) or {}
    duration = 0.0
    for value in (video.get("duration"), fmt.get("duration")):
        try:
            if value and float(value) > 0:
                duration = round(float(value), 3)
                break
        except (TypeError, ValueError):
            continue
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "aspect": _aspect(width, height),
        "fps": _rate(video.get("avg_frame_rate") or video.get("r_frame_rate")),
        "video_codec": video.get("codec_name") or "",
        "pix_fmt": video.get("pix_fmt") or "",
        "audio_codec": audio.get("codec_name") or "",
        "sample_rate": int(audio.get("sample_rate") or 0),
        "channels": int(audio.get("channels") or 0),
        "container": (fmt.get("format_name") or "").split(",")[0],
        "bitrate": int(fmt.get("bit_rate") or 0),
        "size_bytes": int(fmt.get("size") or 0),
        "has_video": bool(video),
        "has_audio": bool(audio),
    }


def probe(path):
    """Return normalised technical metadata, or ``{"error": ...}``."""
    ffprobe = find_binary("ffprobe")
    if not ffprobe:
        return {"error": "ffprobe is not available"}
    if not path or not os.path.isfile(path):
        return {"error": "media file not found"}
    code, out, err = run(
        [ffprobe, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        timeout=60,
    )
    if code != 0:
        return {"error": (err or "ffprobe failed").strip()[:400]}
    try:
        return parse_probe(json.loads(out))
    except ValueError as exc:
        return {"error": "invalid ffprobe output: %s" % exc}


# ---------------------------------------------------------------------------
# Adaptive sampling
# ---------------------------------------------------------------------------
def plan_sampling(duration, fps=0.0, max_frames=DEFAULT_MAX_FRAMES, min_gap=0.35):
    """Uniform, frame-snapped timestamps, bounded by ``max_frames``.

    Short clips are sampled densely; long clips stay bounded so inference cost
    does not grow with duration.
    """
    try:
        duration = float(duration or 0)
    except (TypeError, ValueError):
        return []
    if duration <= 0 or max_frames <= 0:
        return []
    count = int(max_frames)
    if duration < 3:
        count = min(count, max(1, int(duration * 4) + 1))
    gap = duration / float(count)
    if gap < min_gap and count > 1:
        count = max(1, int(duration / min_gap))
    step = duration / float(count)
    times = []
    frame = (1.0 / fps) if fps and fps > 0 else 0.0
    for index in range(count):
        stamp = step * index + min(step / 2.0, 2.0)
        if stamp >= duration:
            stamp = max(0.0, duration - 0.05)
        if frame:
            stamp = round(round(stamp / frame) * frame, 3)
        if not times or stamp - times[-1] >= min_gap * 0.5:
            times.append(round(stamp, 3))
    return times


def parse_scene_times(text, duration=0.0, max_scenes=60):
    """Turn ffmpeg ``showinfo`` stderr into scene boundaries."""
    stamps = []
    for match in _PTS_RE.finditer(text or ""):
        try:
            value = float(match.group(1))
        except ValueError:
            continue
        if value <= 0.05:
            continue
        if not stamps or value - stamps[-1] > 0.05:
            stamps.append(value)
        if len(stamps) >= max_scenes:
            break
    try:
        total = float(duration or 0)
    except (TypeError, ValueError):
        total = 0.0
    if total <= 0 and stamps:
        total = stamps[-1] + 0.5
    boundaries = [0.0] + sorted(stamps)
    if total > 0:
        boundaries = [b for b in boundaries if b < total]
        boundaries.append(round(total, 3))
    scenes = []
    for index in range(len(boundaries) - 1):
        start, end = boundaries[index], boundaries[index + 1]
        if end - start <= 0.05:
            continue
        scenes.append({"index": len(scenes), "start": round(start, 3),
                       "end": round(end, 3), "duration": round(end - start, 3)})
    return scenes


def detect_scenes(path, threshold=DEFAULT_SCENE_THRESHOLD, duration=0.0,
                  max_scenes=60, timeout=180):
    """Detect hard cuts with ffmpeg's scene score; never raises."""
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg or not path or not os.path.isfile(path):
        return [], "ffmpeg is not available"
    expression = "select='gt(scene,%s)',showinfo" % threshold
    code, _out, err = run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", path,
         "-an", "-sn", "-dn", "-vf", expression, "-f", "null", "-"],
        timeout=timeout,
    )
    scenes = parse_scene_times(err, duration=duration, max_scenes=max_scenes)
    if not scenes and duration:
        scenes = [{"index": 0, "start": 0.0, "end": round(float(duration), 3),
                   "duration": round(float(duration), 3)}]
    if code != 0 and not scenes:
        return [], (err or "scene detection failed").strip()[:300]
    return scenes, ""


def extract_frames(path, timestamps, out_dir, width=DEFAULT_FRAME_WIDTH,
                   timeout=90):
    """Extract one JPEG per timestamp. Returns ``(frames, error)``."""
    ffmpeg = find_binary("ffmpeg")
    if not ffmpeg:
        return [], "ffmpeg is not available"
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    scale = "scale='min(%d,iw)':-2" % int(width)
    for index, stamp in enumerate(timestamps):
        target = os.path.join(out_dir, "%04d.jpg" % index)
        code, _out, err = run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-ss", "%.3f" % float(stamp), "-i", path,
             "-frames:v", "1", "-vf", scale, "-q:v", "3", target],
            timeout=timeout,
        )
        if code == 0 and os.path.isfile(target) and os.path.getsize(target) > 0:
            frames.append({"index": index, "t": round(float(stamp), 3),
                           "path": target,
                           "ref": os.path.basename(os.path.dirname(target)) +
                                  "/" + os.path.basename(target)})
    if not frames:
        return [], (err or "no frames could be extracted").strip()[:300]
    return frames, ""


# ---------------------------------------------------------------------------
# Acquisition for frame/scene work
# ---------------------------------------------------------------------------
def download_section(url, out_dir, max_seconds=DEFAULT_SECTION_SECONDS,
                     ytdlp_argv=None, timeout=240):
    """Download at most ``max_seconds`` of a video for local analysis.

    Only used when the user explicitly opts into frame/scene extraction.
    Returns ``(path, error)``.  The caller owns the file.
    """
    os.makedirs(out_dir, exist_ok=True)
    argv = list(ytdlp_argv or [])
    if not argv:
        exe = shutil.which("yt-dlp")
        if exe:
            argv = [exe]
    if not argv:
        return None, "yt-dlp is not available"
    template = os.path.join(out_dir, "source.%(ext)s")
    code, _out, err = run(
        argv + [
            "--no-playlist", "--no-warnings", "--no-progress", "--quiet",
            "-f", "bv*[height<=720]+ba/b[height<=720]/b",
            "--download-sections", "*0-%d" % int(max_seconds),
            "-o", template, url,
        ],
        timeout=timeout,
    )
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("source.") and not name.endswith(".part"):
            path = os.path.join(out_dir, name)
            if os.path.getsize(path) > 0:
                return path, ""
    return None, (err or "the video section could not be downloaded").strip()[:300]


def analyse_media(path, out_dir, max_frames=DEFAULT_MAX_FRAMES,
                  threshold=DEFAULT_SCENE_THRESHOLD, duration_hint=0):
    """Probe → scenes → frames. Returns a dict with warnings, never raises."""
    result = {"technical": {}, "scenes": [], "frames": [], "warnings": []}
    probed = probe(path)
    if probed.get("error"):
        result["warnings"].append(probed["error"])
        probed = {}
    result["technical"] = probed
    duration = probed.get("duration_seconds") or duration_hint or 0
    scenes, scene_error = detect_scenes(
        path, threshold=threshold, duration=duration)
    if scene_error:
        result["warnings"].append("scene detection: %s" % scene_error)
    result["scenes"] = scenes
    times = plan_sampling(duration, fps=probed.get("fps") or 0,
                          max_frames=max_frames)
    frames, frame_error = extract_frames(path, times, out_dir)
    if frame_error:
        result["warnings"].append("frame extraction: %s" % frame_error)
    result["frames"] = frames
    return result


def temp_dir(prefix="dennis-media-"):
    return tempfile.mkdtemp(prefix=prefix)
