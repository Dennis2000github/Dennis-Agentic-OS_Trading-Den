"""GENESIS render orchestration — AI generation + final assembly.

The architecture distinguishes two things that are often confused:

1. **AI media generation** — clip/image/voice generation. GENESIS never
   implements its own provider calls: any generation goes through the
   existing Studio provider registry and the asynchronous ``StudioRunner``
   (``app.py`` wires the runner in via :func:`set_runner`). Outputs land in
   the shared Studio media library exactly like Studio jobs do.

2. **Final assembly / rendering** — turning scene media that already exists
   (from the media library) plus optional voiceover/music/captions into one
   exported video file. This is a *renderer*, not a model. The adapter uses
   the local ``ffmpeg``/``ffprobe`` binaries when they are installed. When
   they are not installed, the render center reports them honestly as
   unavailable — nothing pretends to render.

Render job states reflect real execution only: ``queued → preparing →
[waiting_media] → assembling → complete | failed | cancelled``. Progress is
stage-based; there are no synthetic percentages.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from . import cosmos_common as common
from . import genesis_store as store
from . import skill_media
from . import studio_media

POLL_INTERVAL = 2.0
MAX_PROVIDER_WAIT = 60 * 60  # one hour ceiling for provider-backed media
FFMPEG_TIMEOUT = 60 * 60

_status_lock = threading.RLock()
_runner = None
_engine = None


class RenderError(Exception):
    """A user-facing render failure with a stable code."""

    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


def set_runner(runner):
    """Wire the shared StudioRunner (called once by ``app.py``)."""
    global _runner
    _runner = runner


# ---------------------------------------------------------------------------
# Engine availability — honest reporting, never assumed
# ---------------------------------------------------------------------------
def availability():
    ffmpeg = skill_media.find_binary("ffmpeg")
    ffprobe = skill_media.find_binary("ffprobe")
    version = None
    if ffmpeg:
        try:
            out = subprocess.run(
                [ffmpeg, "-version"], capture_output=True, text=True, timeout=10
            )
            match = re.search(r"ffmpeg version (\S+)", out.stdout or "")
            version = match.group(1) if match else None
        except (OSError, subprocess.SubprocessError):
            version = None
    return {
        "engine": "ffmpeg",
        "available": bool(ffmpeg and ffprobe),
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "version": version,
        "runner": bool(_runner),
    }


def _require_binaries():
    info = availability()
    if not info["available"]:
        missing = [name for name in ("ffmpeg", "ffprobe") if not info.get(name)]
        raise RenderError(
            "renderer_unavailable",
            "FFmpeg is not installed (%s missing). Install ffmpeg to enable final rendering."
            % " and ".join(missing),
            409,
        )
    return info["ffmpeg"], info["ffprobe"]


def _run(argv, timeout=120):
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout
        )
    except OSError as exc:
        raise RenderError("renderer_failed", "Could not run %s: %s" % (os.path.basename(argv[0]), exc), 500)
    except subprocess.TimeoutExpired:
        raise RenderError("renderer_timeout", "%s timed out." % os.path.basename(argv[0]), 500)
    return result.returncode, (result.stdout or ""), (result.stderr or "")


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------
def probe(path):
    """Return parsed ffprobe JSON for a media file (raises RenderError on failure)."""
    _, ffprobe = _require_binaries()
    code, stdout, stderr = _run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=30,
    )
    if code != 0:
        raise RenderError("probe_failed", (stderr or "ffprobe failed").strip()[:400], 500)
    try:
        return json.loads(stdout or "{}")
    except ValueError as exc:
        raise RenderError("probe_failed", "ffprobe returned unreadable output: %s" % exc, 500)


def parse_fps(rate):
    """Parse an ffprobe rational frame rate string like ``30000/1001``."""
    if not rate:
        return None
    match = re.match(r"^(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)$", str(rate))
    if match:
        denominator = float(match.group(2))
        if denominator:
            return float(match.group(1)) / denominator
        return None
    try:
        return float(rate)
    except (TypeError, ValueError):
        return None


def _h264_encoder(ffmpeg):
    """Pick the best available H.264 encoder; honest failure when none exists."""
    code, stdout, _ = _run([ffmpeg, "-hide_banner", "-encoders"], timeout=20)
    if code != 0:
        raise RenderError("renderer_unavailable", "ffmpeg -encoders failed.", 500)
    names = set(re.findall(r"^ [AVS]\.\S*\s+(\S+)", stdout, re.MULTILINE))
    for candidate in ("libx264", "h264_videotoolbox", "h264_qsv", "h264_nvenc", "mpeg4"):
        if candidate in names:
            return candidate
    raise RenderError(
        "no_video_encoder",
        "No usable H.264/MPEG-4 encoder was found in the installed ffmpeg build.",
        500,
    )


def _subtitles_filter_available(ffmpeg):
    code, stdout, _ = _run([ffmpeg, "-hide_banner", "-filters"], timeout=20)
    return code == 0 and re.search(r"\bsubtitles\b", stdout) is not None


# ---------------------------------------------------------------------------
# Validation + manifest
# ---------------------------------------------------------------------------
def _scene_clip(project, scene):
    """Resolve one scene to a concrete media file path."""
    media_id = scene.get("media_id")
    if not media_id:
        return None, "missing"
    record = studio_media.find(media_id)
    if record is None:
        return None, "missing_media_%s" % media_id
    if scene.get("kind") == "image" or (record.get("kind") == "image"):
        return record, "image"
    try:
        path, _ = studio_media.open_path(media_id)
    except studio_media.MediaError:
        return None, "file_missing_%s" % media_id
    if not path or not os.path.isfile(path):
        return None, "file_missing_%s" % media_id
    return record, "video"


def _generation_ready(scene):
    generation = scene.get("generation") or {}
    return bool(generation.get("provider") and generation.get("model_id"))


def build_manifest(project, allow_generation=False):
    """Validate project inputs and build the concrete render manifest.

    With ``allow_generation`` a scene without media is accepted when it has a
    complete provider/model generation spec — the render thread generates that
    media through the shared StudioRunner before assembly. Assembly itself
    always calls this in strict mode, so ffmpeg never receives a missing path.

    Raises RenderError with a precise reason when required assets are absent.
    """
    settings = project.get("settings") or {}
    audio = project.get("audio") or {}
    clips = []
    pending = []
    for scene in project.get("scenes") or []:
        if not scene.get("media_id"):
            if allow_generation and _generation_ready(scene):
                pending.append(scene)
                continue
            raise RenderError(
                "scene_without_media",
                "Scene '%s' has no media. Generate or attach media before rendering."
                % (scene.get("title") or scene.get("id")),
            )
        record, kind = _scene_clip(project, scene)
        if record is None:
            raise RenderError("scene_media_missing", "Scene '%s': %s." % (scene.get("title") or scene.get("id"), kind))
        try:
            path, _ = studio_media.open_path(record["id"])
        except studio_media.MediaError:
            raise RenderError("scene_media_missing", "Scene '%s': the media file is missing on disk." % scene.get("title"))
        if not path or not os.path.isfile(path):
            raise RenderError("scene_media_missing", "Scene '%s': the media file is missing on disk." % scene.get("title"))
        clips.append({
            "scene_id": scene.get("id"),
            "title": scene.get("title"),
            "media_id": record["id"],
            "path": path,
            "kind": record.get("kind") or scene.get("kind") or "video",
            "duration_hint": scene.get("duration_hint"),
        })
    if not clips and not pending:
        raise RenderError("empty_timeline", "The project timeline has no scenes to render.")

    audio_tracks = {}
    for key in ("voiceover_media_id", "music_media_id"):
        media_id = audio.get(key)
        if media_id:
            record = studio_media.find(media_id)
            path = None
            if record:
                try:
                    path, _ = studio_media.open_path(media_id)
                except studio_media.MediaError:
                    path = None
            if record is None or not path or not os.path.isfile(path):
                raise RenderError("audio_missing", "%s media %s is missing." % (key, media_id))
            audio_tracks[key] = {"media_id": media_id, "path": path}

    captions_srt = audio.get("captions_srt")
    captions_media = None
    if audio.get("captions_media_id"):
        captions_media = studio_media.find(audio["captions_media_id"])

    return {
        "clips": clips,
        "pending_scenes": pending,
        "audio_tracks": audio_tracks,
        "captions_srt": captions_srt,
        "captions_media_id": audio.get("captions_media_id"),
        "captions_media": captions_media,
        "settings": {
            "width": settings.get("width"),
            "height": settings.get("height"),
            "fps": settings.get("fps"),
            "aspect_ratio": settings.get("aspect_ratio"),
            "export_format": settings.get("export_format") or "mp4",
        },
    }


def _manifest_snapshot(project):
    """Persisted description of what the job was asked to render.

    This is provenance, not a claim of success: scene entries keep the real
    media ids (or the generation spec that still has to run).
    """
    settings = project.get("settings") or {}
    audio = project.get("audio") or {}
    clips = []
    for scene in project.get("scenes") or []:
        entry = {
            "scene_id": scene.get("id"),
            "title": scene.get("title"),
            "media_id": scene.get("media_id"),
            "kind": scene.get("kind") or "video",
        }
        if _generation_ready(scene) and not scene.get("media_id"):
            entry["generation"] = {
                "provider": scene["generation"].get("provider"),
                "model_id": scene["generation"].get("model_id"),
            }
        clips.append(entry)
    return {
        "clips": clips,
        "audio_tracks": {
            key: audio.get(key)
            for key in ("voiceover_media_id", "music_media_id")
            if audio.get(key)
        },
        "captions_srt": bool(audio.get("captions_srt")),
        "captions_media_id": audio.get("captions_media_id"),
        "settings": {
            "width": settings.get("width"),
            "height": settings.get("height"),
            "fps": settings.get("fps"),
            "aspect_ratio": settings.get("aspect_ratio"),
            "export_format": settings.get("export_format") or "mp4",
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
ACTIVE_STATUSES = ("queued", "preparing", "waiting_media", "assembling")


def create_render(project):
    """Create a queued render job for a project and start real execution."""
    _require_binaries()
    # Validation happens before the record exists, so an invalid timeline
    # never leaves a phantom job. Generation-only scenes are allowed here and
    # resolved through the StudioRunner inside the worker.
    build_manifest(project, allow_generation=True)
    with store.acquire():
        for existing in store.list_renders(project_id=project["id"], limit=50):
            if existing.get("status") in ACTIVE_STATUSES:
                raise RenderError(
                    "render_in_progress",
                    "Render %s is already %s for this project." % (existing["id"], existing["status"]),
                    409,
                )
        now = common.now()
        record = {
            "id": common.new_id("gr_"),
            "project_id": project["id"],
            "division": project.get("division"),
            "status": "queued",
            "stage": "queued",
            "detail": "Queued.",
            "error": None,
            "warnings": [],
            "manifest": _manifest_snapshot(project),
            "provider_jobs": [],
            "captions_burned": False,
            "media_id": None,
            "output_size": None,
            "duration_seconds": None,
            "engine": "ffmpeg",
            "engine_version": availability().get("version"),
            "cancel_requested": False,
            "created_at": now,
            "created_iso": common.iso(now),
            "started_at": None,
            "finished_at": None,
        }
        store.insert_render(record)
        # Record the attempt immediately: failed jobs are part of the project's
        # provenance too, not only successful ones.
        try:
            from . import genesis

            genesis.store.update_project(project["id"], {
                "render_ids": list(dict.fromkeys((project.get("render_ids") or []) + [record["id"]])),
                "updated_at": common.now(),
                "updated_iso": common.iso(),
            })
        except common.CosmosError:
            pass
    thread = threading.Thread(target=_execute, args=(record["id"],), daemon=True, name="genesis-render")
    thread.start()
    return store.get_render(record["id"])


def cancel_render(render_id):
    record = store.get_render(render_id)
    status = record.get("status")
    if status in ("complete", "failed", "cancelled"):
        raise RenderError("render_terminal", "Render %s is already %s." % (render_id, status), 409)
    if status == "assembling":
        raise RenderError(
            "render_not_cancellable",
            "ffmpeg assembly has already started; it cannot be interrupted safely. "
            "Wait for the job to finish and start a new render if needed.",
            409,
        )
    return store.update_render(render_id, {
        "status": "cancelled" if status == "queued" else status,
        "cancel_requested": True,
        "detail": "Cancellation requested.",
        "finished_at": common.now() if status == "queued" else record.get("finished_at"),
    })


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _update(render_id, **patch):
    return store.update_render(render_id, patch)


def _cancelled(render_id):
    current = store.get_render(render_id)
    return current.get("status") == "cancelled" or current.get("cancel_requested")


def _fail(render_id, code, message):
    try:
        if _cancelled(render_id):
            store.update_render(render_id, {
                "status": "cancelled",
                "stage": "cancelled",
                "detail": "Cancelled.",
                "finished_at": common.now(),
            })
            return
        store.update_render(render_id, {
            "status": "failed",
            "stage": "failed",
            "error": {"code": code, "message": message[:800]},
            "finished_at": common.now(),
        })
    except common.CosmosError:
        pass


def _complete(render_id, patch):
    patch = dict(patch)
    patch.update({
        "status": "complete",
        "stage": "done",
        "detail": "Render complete.",
        "finished_at": common.now(),
    })
    try:
        store.update_render(render_id, patch)
    except common.CosmosError:
        pass


def _execute(render_id):
    try:
        record = store.get_render(render_id)
    except common.CosmosError:
        return
    try:
        ffmpeg, _ = _require_binaries()
        if _cancelled(render_id):
            _update(render_id, status="cancelled", detail="Cancelled.", finished_at=common.now())
            return

        started = time.time()
        _update(render_id, status="preparing", stage="prepare", detail="Validating project inputs.", started_at=started)
        project = _project(record["project_id"])
        # Validate in permissive mode first: generation-only scenes are
        # resolved below through the shared StudioRunner.
        build_manifest(project, allow_generation=True)

        # Stage: provider-backed generation for scenes that need it.
        _generate_missing_media(render_id, project)
        if _cancelled(render_id):
            _update(render_id, status="cancelled", detail="Cancelled.", finished_at=common.now())
            return
        manifest = build_manifest(_project(record["project_id"]))

        # Stage: assemble with ffmpeg.
        _update(render_id, status="assembling", stage="assemble", detail="Assembling %d clip(s) with ffmpeg." % len(manifest["clips"]))
        data, warnings, captions_burned, output_duration = _assemble(ffmpeg, record, manifest)

        if _cancelled(render_id):
            # Cancellation arrived while ffmpeg was running: the assembled
            # bytes are discarded and the job is honestly reported cancelled.
            _update(render_id, status="cancelled", stage="cancelled",
                    detail="Cancelled before the output was saved.", finished_at=common.now())
            return

        # Stage: finalize — persist into the shared media library.
        _update(render_id, stage="finalize", detail="Saving the rendered video into the media library.")
        media_record = studio_media.add_bytes(
            data,
            "video/mp4",
            kind="video",
            filename="%s-genesis-render.mp4" % record["project_id"],
            meta={
                "title": "%s — final render" % (project.get("title") or "Production"),
                "role": "output",
                "provider": "genesis-ffmpeg",
                "model_id": availability().get("version"),
                "job_id": render_id,
                "duration": output_duration,
            },
        )

        _complete(render_id, {
            "media_id": media_record["id"],
            "output_size": media_record.get("size"),
            "duration_seconds": output_duration,
            "captions_burned": captions_burned,
            "warnings": warnings,
            "detail": "Render complete — media %s." % media_record["id"],
        })

        # Link the finished render into the project record.
        _link_render(record["project_id"], render_id, media_record["id"])
    except RenderError as exc:
        _fail(render_id, exc.code, exc.message)
    except common.CosmosError as exc:
        _fail(render_id, exc.code, exc.message)
    except Exception as exc:  # never leave a job stuck in a running state
        _fail(render_id, "render_error", str(exc))


def _project(project_id):
    from . import genesis  # local import avoids a cycle at module load

    return genesis.store.get_project(project_id)


def _link_render(project_id, render_id, media_id):
    from . import genesis

    project = genesis.store.get_project(project_id)
    render_ids = list(project.get("render_ids") or [])
    if render_id not in render_ids:
        render_ids.append(render_id)
    genesis.store.update_project(project_id, {
        "render_ids": render_ids,
        "media_id": media_id,
        "status": "quality_review" if project.get("status") in ("in_production", "failed") else project.get("status"),
        "stage": "quality_review",
        # A new output invalidates the previous review and owner approval; both
        # were made about a different file and must not carry over silently.
        "quality_review": None,
        "approval": None,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
        "history": genesis._append(project, "render_complete", "Render %s → media %s" % (render_id, media_id)),
    })


def _generate_missing_media(render_id, project):
    """Submit provider-backed jobs for scenes that declare generation intent.

    Media generation always goes through the shared StudioRunner; this module
    never talks to a provider itself. Scenes without media and without a
    usable generation spec fail the render with a precise error.
    """
    pending_scenes = [scene for scene in (project.get("scenes") or []) if not scene.get("media_id")]
    if not pending_scenes:
        return
    if _runner is None:
        raise RenderError(
            "runner_unavailable",
            "%d scene(s) still need generated media and the Studio job runner is not wired." % len(pending_scenes),
        )
    jobs = {}
    titles = {}
    for scene in pending_scenes:
        generation = scene.get("generation") or {}
        if not (generation.get("provider") and generation.get("model_id")):
            raise RenderError(
                "scene_without_media",
                "Scene '%s' has no media and no generation provider/model configured."
                % (scene.get("title") or scene.get("id")),
            )
        params = dict(generation.get("params") or {})
        params.setdefault("prompt", generation.get("prompt") or scene.get("description") or scene.get("title") or "")
        params.setdefault("duration", scene.get("duration_hint") or 5)
        params.setdefault("aspect_ratio", (project.get("settings") or {}).get("aspect_ratio") or "16:9")
        try:
            job, _duplicate = _runner.submit(
                generation["provider"], generation["model_id"], params, auto_download=True
            )
        except Exception as exc:
            raise RenderError(
                "provider_submit_failed",
                "Scene '%s': %s" % (scene.get("title") or scene.get("id"), exc),
            )
        jobs[scene["id"]] = job["id"]
        titles[scene["id"]] = scene.get("title") or scene.get("id")
    if not jobs:
        return
    _update(render_id, status="waiting_media", stage="generate",
            detail="Waiting for %d provider job(s): %s." % (len(jobs), ", ".join(jobs.values())),
            provider_jobs=list(jobs.values()))

    deadline = time.time() + MAX_PROVIDER_WAIT
    pending = dict(jobs)
    while pending:
        if _cancelled(render_id):
            _update(render_id, status="cancelled", detail="Cancelled.", finished_at=common.now())
            return
        if time.time() > deadline:
            raise RenderError("provider_timeout", "Provider jobs did not finish within one hour.")
        for scene_id, job_id in list(pending.items()):
            try:
                job = _runner.store.get(job_id)
            except Exception as exc:
                raise RenderError("provider_job_lost", "Provider job %s: %s" % (job_id, exc))
            if not job:
                raise RenderError("provider_job_lost", "Provider job %s was not found." % job_id)
            state = job.get("state")
            if state == "succeeded":
                media_ids = [o.get("media_id") for o in (job.get("outputs") or []) if o.get("media_id")]
                if not media_ids:
                    raise RenderError(
                        "provider_no_output",
                        "Provider job %s finished without downloadable media." % job_id,
                    )
                _attach_scene_media(project["id"], scene_id, media_ids[0])
                del pending[scene_id]
            elif state in ("failed", "cancelled"):
                error = job.get("error") or {}
                raise RenderError(
                    "provider_job_failed",
                    "Scene '%s': provider job %s %s (%s)." % (
                        titles.get(scene_id) or scene_id, job_id, state,
                        error.get("message") or error.get("code") or "no detail",
                    ),
                )
        if pending:
            time.sleep(POLL_INTERVAL)
    _update(render_id, detail="All scene media is ready.", provider_jobs=list(jobs.values()))


def _attach_scene_media(project_id, scene_id, media_id):
    from . import genesis

    project = genesis.store.get_project(project_id)
    scenes = []
    for scene in project.get("scenes") or []:
        if scene.get("id") == scene_id:
            scene = dict(scene)
            scene["media_id"] = media_id
            scene["status"] = "ready"
        scenes.append(scene)
    genesis.store.update_project(project_id, {
        "scenes": scenes,
        "updated_at": common.now(),
        "updated_iso": common.iso(),
    })


# ---------------------------------------------------------------------------
# FFmpeg assembly
# ---------------------------------------------------------------------------
def _probe_clip(path):
    try:
        data = probe(path)
    except RenderError:
        return {"has_audio": False, "duration": 0.0}
    has_audio = any(stream.get("codec_type") == "audio" for stream in data.get("streams") or [])
    duration = 0.0
    try:
        duration = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and stream.get("duration"):
            try:
                duration = max(duration, float(stream["duration"]))
            except (TypeError, ValueError):
                pass
    return {"has_audio": has_audio, "duration": duration}


def _escape_filter_path(path):
    """Escape a filesystem path for use inside an ffmpeg filter string."""
    text = str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return text


def _assemble(ffmpeg, record, manifest):
    """Run the real ffmpeg assembly.

    Returns ``(output_bytes, warnings, captions_burned, duration_seconds)``.
    The output lives inside a temp workspace that is removed afterwards, so
    the bytes (and their probed metadata) are read before the cleanup.
    """
    warnings = []
    clips = manifest["clips"]
    settings = manifest["settings"]
    width = int(settings.get("width") or 1920)
    height = int(settings.get("height") or 1080)
    fps = int(settings.get("fps") or 30)

    with tempfile.TemporaryDirectory(prefix="genesis-render-", dir=store.TMP_DIR) as workdir:
        inputs = []
        filters = []
        concat_pairs = []
        concat_audio = []
        probed = [_probe_clip(clip["path"]) for clip in clips]
        # ffmpeg input indices are positional. Silent lavfi inputs are added
        # between clip inputs whenever a clip has no audio track, so the index
        # of a clip's own (video/audio) streams is not necessarily its position
        # in the timeline. Track it explicitly instead of assuming index==clip.
        next_input = 0

        for index, clip in enumerate(clips):
            is_image = clip.get("kind") == "image"
            # A record may be mislabeled (e.g. an mp4 stored through an
            # image-upload path). Real still images have no container
            # duration; anything with a real duration is a video.
            if is_image and probed[index]["duration"]:
                is_image = False
            # Still images are looped for their scene duration (or a 4s
            # default); video clips play for their real probed length.
            display_duration = float(clip.get("duration_hint") or 0) if is_image else None
            if is_image and not display_duration:
                display_duration = 4.0
            if is_image:
                inputs += ["-loop", "1", "-t", "%.3f" % display_duration, "-i", clip["path"]]
            else:
                inputs += ["-i", clip["path"]]
            video_input = next_input
            next_input += 1
            filters.append(
                "[%d:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
                "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=%d,format=yuv420p[v%d]"
                % (video_input, width, height, width, height, fps, index)
            )
            concat_pairs.append("[v%d]" % index)
            if probed[index]["has_audio"]:
                filters.append(
                    "[%d:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[a%d]"
                    % (video_input, index)
                )
            else:
                # Silent input for clips without audio keeps concat a=1 valid.
                inputs += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
                silent_input = next_input
                next_input += 1
                segment = display_duration if is_image else max(0.5, probed[index]["duration"] or 1.0)
                filters.append(
                    "[%d:a]atrim=duration=%.3f,asetpts=PTS-STARTPTS,aresample=44100,"
                    "aformat=sample_fmts=fltp:channel_layouts=stereo[a%d]"
                    % (silent_input, segment, index)
                )
            concat_audio.append("[a%d]" % index)

        n = len(clips)
        # concat expects its inputs interleaved per segment: v0,a0,v1,a1,...
        concat_inputs = "".join(
            "%s%s" % (concat_pairs[i], concat_audio[i]) for i in range(n)
        )
        filters.append("%sconcat=n=%d:v=1:a=1[vcat][acat]" % (concat_inputs, n))

        final_video = "[vcat]"
        final_audio = "[acat]"

        voiceover = (manifest["audio_tracks"] or {}).get("voiceover_media_id")
        music = (manifest["audio_tracks"] or {}).get("music_media_id")
        if voiceover or music:
            parts = ["[acat]"]
            if voiceover:
                inputs += ["-i", voiceover["path"]]
                voiceover_input = next_input
                next_input += 1
                filters.append(
                    "[%d:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo[xvoice]"
                    % voiceover_input
                )
                parts.append("[xvoice]")
            if music:
                inputs += ["-i", music["path"]]
                music_input = next_input
                next_input += 1
                # Background music sits under the voiceover instead of
                # competing with it at full scale.
                filters.append(
                    "[%d:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                    "volume=0.25[xmusic]" % music_input
                )
                parts.append("[xmusic]")
            # duration=first pins the mix to the assembled timeline, so a
            # longer voiceover or music bed can never extend the video.
            filters.append(
                "%samix=inputs=%d:duration=first:dropout_transition=2[amix]"
                % ("".join(parts), len(parts))
            )
            final_audio = "[amix]"

        burned = False
        captions_srt = manifest.get("captions_srt")
        if captions_srt and not _subtitles_filter_available(ffmpeg):
            warnings.append("Captions could not be burned: this ffmpeg build has no subtitles filter.")
        elif captions_srt:
            srt_path = os.path.join(workdir, "captions.srt")
            with open(srt_path, "w", encoding="utf-8") as handle:
                handle.write(captions_srt)
            style = "FontSize=20,Outline=1,MarginV=30"
            filters.append(
                "[vcat]subtitles=filename='%s':force_style='%s'[vcap]"
                % (_escape_filter_path(srt_path), style)
            )
            final_video = "[vcap]"
            burned = True

        encoder = _h264_encoder(ffmpeg)
        output_path = os.path.join(workdir, "output.mp4")
        command = [ffmpeg, "-hide_banner", "-y"]
        command += inputs
        command += [
            "-filter_complex", ";".join(filters),
            "-map", final_video, "-map", final_audio,
            "-c:v", encoder,
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
        ]
        if encoder != "libx264":
            warnings.append("Encoded with %s (libx264 was not available in this ffmpeg build)." % encoder)
        command.append(output_path)

        _update(record["id"], detail="Running ffmpeg (%s, %d clip(s))." % (encoder, n))
        code, _, stderr = _run(command, timeout=FFMPEG_TIMEOUT)
        if code != 0:
            detail = (stderr or "").strip().splitlines()
            raise RenderError(
                "ffmpeg_failed",
                "ffmpeg exited with code %d: %s" % (code, detail[-1][:400] if detail else "no output"),
                500,
            )
        # Probe the real output, then read the bytes before the temp dir goes away.
        out_probe = probe(output_path)
        output_duration = None
        try:
            output_duration = float(out_probe.get("format", {}).get("duration") or 0) or None
        except (TypeError, ValueError):
            pass
        if not output_duration:
            output_duration = sum(info["duration"] for info in probed) or None
        with open(output_path, "rb") as handle:
            data = handle.read()
        return data, warnings, burned, output_duration
