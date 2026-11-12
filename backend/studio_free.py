"""Free generation engines for Ads Studio (no paid provider required).

Ads Studio runs on free model credits instead of the Higgsfield paid API:

* **Pollinations** (https://pollinations.ai) — keyless image generation.
  Text-to-image always works; image editing works when the reference images
  are publicly reachable URLs.
* **Google AI** (Gemini API, free tier key from https://aistudio.google.com) —
  ``gemini-2.5-flash-image`` edits images with inline base64 references (no
  public upload needed), and the Veo models generate video from text or an
  image. This is the API behind Google Flow.

Jobs run on background threads with a small in-memory registry and the same
public shape as Studio jobs (``state`` / ``outputs`` / ``terminal``), so the
existing frontend polling works unchanged. Every output is stored in the
local Studio media library and served from ``/api/studio/media/<id>``.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from . import studio_media as media
from . import studio_secrets as secrets

USER_AGENT = "DENNIS-Agentic-OS/1.0 (Ads Studio free engines)"

POLLINATIONS_IMAGE = "https://image.pollinations.ai/prompt/"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

#: Google image model (free tier): Gemini 2.5 Flash Image ("nano banana").
GEMINI_IMAGE_MODELS = ("gemini-2.5-flash-image", "gemini-2.5-flash-image-preview")

#: Veo video models (Google Flow's engine) — fast first.
VIDEO_ENGINES = (
    {
        "id": "veo-3.0-fast-generate-001",
        "label": "Veo 3 Fast (Google AI)",
        "mode_label": "text/image → video with audio",
        "durations": [4, 6, 8],
        "resolutions": ["720p", "1080p"],
        "aspect_ratios": ["16:9", "9:16"],
        "accepts_image": True,
    },
    {
        "id": "veo-3.0-generate-001",
        "label": "Veo 3 Quality (Google AI)",
        "mode_label": "text/image → video with audio",
        "durations": [8],
        "resolutions": ["720p", "1080p"],
        "aspect_ratios": ["16:9", "9:16"],
        "accepts_image": True,
    },
    {
        "id": "veo-2.0-generate-001",
        "label": "Veo 2 (Google AI)",
        "mode_label": "text/image → video",
        "durations": [5, 6, 8],
        "resolutions": ["720p"],
        "aspect_ratios": ["16:9", "9:16"],
        "accepts_image": True,
    },
)

IMAGE_ENGINES = (
    {
        "id": "auto",
        "label": "Auto (recommended)",
        "mode_label": "Google when a key exists, Pollinations otherwise",
        "needs_key": False,
        "accepts_references": True,
    },
    {
        "id": "google-gemini-image",
        "label": "Gemini 2.5 Flash Image (Google AI)",
        "mode_label": "text → image + image edit with references (free-tier key)",
        "needs_key": True,
        "accepts_references": True,
    },
    {
        "id": "pollinations-flux",
        "label": "Pollinations Flux (free, no key)",
        "mode_label": "text → image; edits need public reference URLs",
        "needs_key": False,
        "accepts_references": False,
    },
    {
        "id": "pollinations-turbo",
        "label": "Pollinations Turbo (free, fast, no key)",
        "mode_label": "text → image, fastest draft quality",
        "needs_key": False,
        "accepts_references": False,
    },
)

#: Pollinations edit model when every reference is a public URL.
POLLINATIONS_EDIT_MODEL = "kontext"

#: Videos: never add music (Ads Studio Prompt 4 rule) and never burn in text.
VIDEO_NEGATIVE_PROMPT = (
    "background music, soundtrack, instrumental music, singing, humming, "
    "subtitles, captions, text overlay, watermark"
)

_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_MAX_VIDEO_BYTES = 200 * 1024 * 1024
_HTTP_TIMEOUT = 180

_lock = threading.RLock()
_JOBS: dict = {}
_WORKERS: dict = {}


class FreeError(Exception):
    """A free-engine problem that is safe to show to the owner."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ---------------------------------------------------------------------------
# Credentials (Google AI key — free tier from aistudio.google.com)
# ---------------------------------------------------------------------------
def google_api_key():
    values, _source = secrets.get("google_veo")
    return (values or {}).get("api_key") or ""


def google_available():
    return bool(google_api_key())


def save_google_key(api_key):
    key = str(api_key or "").strip()
    if not key:
        raise FreeError("missing_key", "Paste your Google AI API key first.")
    return secrets.set_values("google_veo", {"api_key": key})


def delete_google_key():
    return secrets.delete("google_veo")


def test_google_key():
    key = google_api_key()
    if not key:
        raise FreeError("missing_key", "No Google AI key stored yet.")
    request = urllib.request.Request(
        GEMINI_BASE + "/models?pageSize=1",
        headers={"x-goog-api-key": key, "User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if 200 <= getattr(response, "status", 200) < 300:
                return {"ok": True, "message": "Google AI key accepted — image models are free to use."}
            raise FreeError("test_failed", "Google AI returned HTTP %s." % getattr(response, "status", "?"), 502)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message", "")
        except Exception:
            pass
        raise FreeError(
            "invalid_key",
            "Google AI rejected the key (%s). Create a free key at aistudio.google.com." % (detail or exc.code),
            502,
        )
    except (urllib.error.URLError, OSError) as exc:
        raise FreeError("network_error", "Could not reach Google AI: %s" % exc, 502)


def capabilities():
    return {
        "google": google_available(),
        "image_engines": [dict(engine) for engine in IMAGE_ENGINES],
        "video_engines": [dict(engine) for engine in VIDEO_ENGINES],
        "limits": {"image_bytes": _MAX_IMAGE_BYTES, "video_bytes": _MAX_VIDEO_BYTES},
    }


# ---------------------------------------------------------------------------
# Upload dedup cache — re-uploading identical bytes returns the stored media
# instantly instead of writing another copy.
# ---------------------------------------------------------------------------
_UPLOAD_HASH_CACHE: dict = {}
_UPLOAD_CACHE_LOCK = threading.Lock()
_UPLOAD_CACHE_MAX = 128


def upload_once(data, content_type, filename):
    """Store an uploaded image once; identical bytes reuse the stored record.

    Returns the ``studio_media`` record. The cache is keyed on the SHA-256 of
    the bytes, so re-attaching the same screenshot from any step is instant.
    """
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    with _UPLOAD_CACHE_LOCK:
        cached = _UPLOAD_HASH_CACHE.get(digest)
    if cached:
        try:
            media.open_path(cached["id"])  # still on disk?
            return cached
        except Exception:
            with _UPLOAD_CACHE_LOCK:
                _UPLOAD_HASH_CACHE.pop(digest, None)
    record = media.add_bytes(
        data, content_type, kind="image", filename=filename,
        meta={"provider": "free", "role": "input", "title": filename,
              "sha256": digest},
    )
    with _UPLOAD_CACHE_LOCK:
        if len(_UPLOAD_HASH_CACHE) >= _UPLOAD_CACHE_MAX:
            _UPLOAD_HASH_CACHE.pop(next(iter(_UPLOAD_HASH_CACHE)))
        _UPLOAD_HASH_CACHE[digest] = record
    return record


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _open(url, data=None, headers=None, method="GET", timeout=_HTTP_TIMEOUT):
    request = urllib.request.Request(
        url, data=data, headers=dict({"User-Agent": USER_AGENT}, **(headers or {})), method=method
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), (response.headers.get("Content-Type") or "application/octet-stream")


def _fetch_bytes(url, max_bytes):
    data, content_type = _open(url)
    if len(data) > max_bytes:
        raise FreeError("output_too_large", "The engine returned %d MB; the limit is %d MB."
                        % (len(data) // (1024 * 1024), max_bytes // (1024 * 1024)), 502)
    return bytes(data), content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"


def _store(data, content_type, kind, filename, job_id):
    record = media.add_bytes(
        data, content_type, kind=kind, filename=filename,
        meta={"provider": "free", "job_id": job_id, "title": filename},
    )
    return {
        "media_id": record.get("id"),
        "url": "/api/studio/media/" + str(record.get("id")),
        "kind": kind,
    }


def _public_url(url):
    """True when Pollinations' servers can fetch the reference themselves."""
    parsed = urllib.parse.urlparse(str(url or ""))
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _read_reference(url, max_bytes=_MAX_IMAGE_BYTES):
    """Reference images may be local media ids, absolute media paths or URLs."""
    text = str(url or "").strip()
    if text.startswith("/api/studio/media/"):
        media_id = text.rsplit("/", 1)[-1]
        path, record = media.open_path(media_id)
        with open(path, "rb") as handle:
            data = handle.read()
        return bytes(data), (record or {}).get("content_type") or "image/png"
    if _public_url(text):
        return _fetch_bytes(text, max_bytes)
    raise FreeError("invalid_reference", "Reference images must be uploads or public http(s) URLs.")


# ---------------------------------------------------------------------------
# Pollinations (keyless)
# ---------------------------------------------------------------------------
def _pollinations_size(aspect):
    text = str(aspect or "")
    if text.startswith("9:16") or text.startswith("3:4"):
        return 768, 1344
    if text.startswith("16:9") or text.startswith("4:3"):
        return 1344, 768
    return 1024, 1024


def pollinations_url(prompt, aspect="", model="flux", ref_urls=(), seed=None):
    width, height = _pollinations_size(aspect)
    seed = seed if seed is not None else int(time.time()) % 1000000
    query = {
        "width": width,
        "height": height,
        "model": model,
        "seed": seed,
        "nologo": "true",
    }
    if ref_urls:
        query["image"] = ",".join(ref_urls)
    return POLLINATIONS_IMAGE + urllib.parse.quote(prompt[:1800], safe="") + "?" + urllib.parse.urlencode(query)


# ---------------------------------------------------------------------------
# Google AI (Gemini + Veo)
# ---------------------------------------------------------------------------
def _gemini_request(path, body, key=None):
    key = key or google_api_key()
    if not key:
        raise FreeError("missing_key",
                        "Add your free Google AI key in Provider Setup to use this engine.")
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    try:
        raw, _type = _open(GEMINI_BASE + path, data=data, headers=headers, method="POST")
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message", "")
        except Exception:
            pass
        if exc.code in (401, 403):
            raise FreeError("invalid_key",
                            "Google AI rejected the key (%s). Check it in Provider Setup." % (detail or exc.code), 502)
        if exc.code == 429:
            raise FreeError("rate_limited",
                            "Free-tier quota reached (%s). Try again in a minute." % (detail or "rate limit"), 502)
        raise FreeError("provider_error", "Google AI error: %s" % (detail or exc.code), 502)
    except (urllib.error.URLError, OSError) as exc:
        raise FreeError("network_error", "Could not reach Google AI: %s" % exc, 502)


def gemini_image(prompt, ref_images=()):
    """Generate/edit an image with Gemini 2.5 Flash Image (inline base64)."""
    parts = [{"text": str(prompt or "").strip()}]
    for url in ref_images or []:
        data, content_type = _read_reference(url)
        parts.append({
            "inlineData": {
                "mimeType": content_type if content_type.startswith("image/") else "image/png",
                "data": base64.b64encode(data).decode("ascii"),
            }
        })
    body = {"contents": [{"role": "user", "parts": parts}]}
    last = None
    for model in GEMINI_IMAGE_MODELS:
        try:
            parsed = _gemini_request("/models/%s:generateContent" % model, body)
            break
        except FreeError as exc:
            last = exc
            if exc.code == "provider_error" and "not found" in exc.message.lower():
                continue  # try the next documented model name
            raise
    else:
        raise last or FreeError("provider_error", "No Google image model is available.", 502)

    for candidate in parsed.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"]), inline.get("mimeType") or "image/png"
    raise FreeError("no_output", "The Google image model returned no image.", 502)


def veo_submit(engine_id, prompt, image=None, aspect="16:9", duration=None,
               negative_prompt=VIDEO_NEGATIVE_PROMPT):
    """Start a Veo long-running video generation; returns the operation name."""
    instance = {"prompt": str(prompt or "").strip()}
    if image:
        data, content_type = _read_reference(image, _MAX_IMAGE_BYTES)
        instance["image"] = {
            "bytesBase64Encoded": base64.b64encode(data).decode("ascii"),
            "mimeType": content_type if content_type.startswith("image/") else "image/png",
        }
    parameters = {"aspectRatio": aspect if aspect in ("16:9", "9:16") else "16:9"}
    if negative_prompt:
        parameters["negativePrompt"] = negative_prompt[:500]
    if duration:
        parameters["durationSeconds"] = int(duration)
    parsed = _gemini_request("/models/%s:predictLongRunning" % engine_id,
                             {"instances": [instance], "parameters": parameters})
    name = (parsed or {}).get("name")
    if not name:
        raise FreeError("provider_error", "Veo did not return an operation name.", 502)
    return name


def veo_poll(operation_name):
    """Poll one Veo operation. Returns (done, video_uri_or_None, error_or_None)."""
    try:
        raw, _type = _open(GEMINI_BASE + "/" + str(operation_name).lstrip("/"),
                           headers={"x-goog-api-key": google_api_key()}, timeout=60)
        parsed = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return True, None, "Veo operation failed (HTTP %s)." % exc.code
    except (urllib.error.URLError, OSError, ValueError):
        return False, None, None  # transient — keep polling

    if parsed.get("error"):
        return True, None, str((parsed.get("error") or {}).get("message") or "Veo operation failed.")
    if not parsed.get("done"):
        return False, None, None

    response = parsed.get("response") or {}
    generate = response.get("generateVideoResponse") or {}
    videos = response.get("videos") or generate.get("generatedSamples") or []
    for video in videos:
        uri = (video.get("video") or {}).get("uri") or video.get("uri")
        if uri:
            return True, uri, None
    filtered = response.get("raiMediaFilteredReasons")
    if filtered:
        return True, None, "Veo filtered this prompt: %s" % "; ".join(str(f) for f in filtered)
    return True, None, "Veo finished but returned no video."


def veo_download(uri):
    key = google_api_key()
    separator = "&" if "?" in uri else "?"
    data, content_type = _open(uri + separator + "key=" + urllib.parse.quote(key),
                               headers={"x-goog-api-key": key})
    return bytes(data), (content_type if content_type.startswith("video/") else "video/mp4")


# ---------------------------------------------------------------------------
# In-memory job registry (same public shape as Studio jobs)
# ---------------------------------------------------------------------------
def _job_public(job):
    with _lock:
        snapshot = dict(job)
    outputs = []
    for output in snapshot.get("outputs") or []:
        public = dict(output)
        outputs.append(public)
    elapsed = round(max(0.0, time.time() - snapshot.get("started_at", time.time())), 1)
    return {
        "id": snapshot["id"],
        "kind": snapshot["kind"],
        "engine": snapshot["engine"],
        "engine_label": snapshot.get("engine_label", snapshot["engine"]),
        "prompt": snapshot.get("prompt", ""),
        "state": snapshot["state"],
        "terminal": snapshot["state"] in ("succeeded", "failed"),
        "outputs": outputs,
        "error": snapshot.get("error"),
        "elapsed_seconds": elapsed,
        "created_at": snapshot.get("created_at"),
        "model_label": snapshot.get("engine_label", snapshot["engine"]),
    }


def job_status(job_id):
    with _lock:
        job = _JOBS.get(str(job_id))
    if not job:
        raise FreeError("job_not_found", "This free job no longer exists.", 404)
    return _job_public(job)


def _set_state(job_id, state, error=None):
    with _lock:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["state"] = state
        job["error"] = error


def _worker(job_id, plan):
    try:
        _set_state(job_id, "running")
        if plan["kind"] == "image":
            outputs = _run_image(plan, job_id)
        else:
            outputs = _run_video(plan, job_id)
        with _lock:
            job = _JOBS.get(job_id)
            if job:
                job["outputs"] = outputs
                job["state"] = "succeeded"
    except FreeError as exc:
        _set_state(job_id, "failed", {"code": exc.code, "message": exc.message})
    except Exception as exc:  # pragma: no cover — defensive worker guard
        _set_state(job_id, "failed", {"code": "engine_error", "message": str(exc)[:300]})


def _run_image(plan, job_id):
    prompt = plan.get("prompt") or ""
    refs = [url for url in (plan.get("image_urls") or []) if url]
    engine = str(plan.get("engine") or "auto").strip().lower()

    if engine in ("google", "google-gemini-image"):
        if not google_available():
            raise FreeError(
                "missing_key",
                "Gemini Image needs a free Google AI key (Provider Setup) — "
                "or switch the image engine to Pollinations.",
            )
        data, content_type = gemini_image(prompt, refs)
        output = _store(data, content_type, "image", "ads-image.png", job_id)
        return [output]

    # Pollinations family (keyless), or auto without a Google key.
    public_refs = [url for url in refs if _public_url(url)
                   and not str(url).startswith("/api/studio/media/")]
    if refs and len(public_refs) != len(refs):
        raise FreeError(
            "references_need_key",
            "Editing uploaded images needs a free Google AI key (Provider Setup). "
            "Pollinations can only edit images that are already on the public internet.",
        )
    model = "turbo" if engine == "pollinations-turbo" else "flux"
    if public_refs:
        model = POLLINATIONS_EDIT_MODEL  # edits always use the edit-capable model
    url = pollinations_url(prompt, plan.get("aspect_ratio"), model, public_refs)
    data, content_type = _fetch_bytes(url, _MAX_IMAGE_BYTES)
    if not content_type.startswith("image/"):
        raise FreeError("no_output", "Pollinations returned no image.", 502)
    output = _store(data, content_type, "image", "ads-image.jpg", job_id)
    return [output]


def _run_video(plan, job_id):
    if not google_available():
        raise FreeError(
            "missing_key",
            "Free video generation runs on Google Veo — add your free Google AI key "
            "in Provider Setup (aistudio.google.com → Get API key).",
        )
    engine_id = plan.get("engine") or VIDEO_ENGINES[0]["id"]
    if not any(engine["id"] == engine_id for engine in VIDEO_ENGINES):
        engine_id = VIDEO_ENGINES[0]["id"]
    refs = [url for url in (plan.get("image_urls") or []) if url]
    operation = veo_submit(engine_id, plan.get("prompt") or "",
                           image=refs[0] if refs else None,
                           aspect=plan.get("aspect_ratio") or "16:9",
                           duration=plan.get("duration"))
    deadline = time.time() + 15 * 60
    while time.time() < deadline:
        time.sleep(8)
        done, video_uri, error = veo_poll(operation)
        if error:
            raise FreeError("provider_error", error, 502)
        if done and video_uri:
            data, content_type = veo_download(video_uri)
            if len(data) > _MAX_VIDEO_BYTES:
                raise FreeError("output_too_large", "The video exceeds 200 MB.", 502)
            output = _store(data, content_type, "video", "ads-video.mp4", job_id)
            return [output]
        if done:
            raise FreeError("no_output", "Veo finished without a video.", 502)
    raise FreeError("timeout", "Veo took longer than 15 minutes.", 504)


def submit_job(plan):
    """Start a free generation job; returns the public job snapshot."""
    if not isinstance(plan, dict):
        raise FreeError("invalid_request", "The request body must be an object.")
    kind = plan.get("kind")
    if kind not in ("image", "video"):
        raise FreeError("invalid_kind", "kind must be 'image' or 'video'.")
    prompt = str(plan.get("prompt") or "").strip()
    if not prompt:
        raise FreeError("missing_prompt", "Describe what to generate in the prompt.")
    engine = plan.get("engine") or ""
    if kind == "video":
        engine = engine if any(e["id"] == engine for e in VIDEO_ENGINES) else VIDEO_ENGINES[0]["id"]
        engine_label = next(e["label"] for e in VIDEO_ENGINES if e["id"] == engine)
    else:
        image_ids = {e["id"] for e in IMAGE_ENGINES} | {"google", "pollinations"}
        if engine not in image_ids:
            engine = "auto"
        legacy_labels = {"google": "Google Gemini Image", "pollinations": "Pollinations Flux"}
        engine_label = next((e["label"] for e in IMAGE_ENGINES if e["id"] == engine),
                            legacy_labels.get(engine, "Auto (free engines)"))
    refs = plan.get("image_urls") or []
    if not isinstance(refs, list):
        raise FreeError("invalid_images", "image_urls must be a list.")
    job_id = "free_" + uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "kind": kind,
        "engine": engine,
        "engine_label": engine_label,
        "prompt": prompt[:2000],
        "image_urls": [str(url) for url in refs][:8],
        "aspect_ratio": str(plan.get("aspect_ratio") or ""),
        "duration": plan.get("duration"),
        "state": "queued",
        "outputs": [],
        "error": None,
        "started_at": time.time(),
        "created_at": time.time(),
    }
    with _lock:
        # Keep the registry small: the frontend only needs recent jobs.
        for old in [j for j in _JOBS if time.time() - _JOBS[j]["created_at"] > 6 * 3600]:
            _JOBS.pop(old, None)
            _WORKERS.pop(old, None)
        _JOBS[job_id] = job
    worker = threading.Thread(target=_worker, args=(job_id, dict(job)), daemon=True)
    _WORKERS[job_id] = worker
    worker.start()
    return _job_public(job)


def list_video_engines():
    return [dict(engine) for engine in VIDEO_ENGINES]


def is_safe_prompt_text(text):
    """Cheap sanity check so prompt text can never smuggle a URL fetch."""
    return not re.search(r"[<>]", str(text or ""))
