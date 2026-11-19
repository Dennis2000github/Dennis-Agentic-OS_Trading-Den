"""Source adapters for the Agent Skill Builder.

Every adapter returns the same normalized shape so the skill lifecycle
(analysis -> generation -> review -> publishing) stays source agnostic::

    {
      "kind": "youtube" | "platform" | "repository" | "video",
      "platform": "tiktok",       # platform sources only
      "url": "...",
      "id": "...",
      "title": "...",
      "metadata": {...},
      "text": "...",              # normalized source material
      "transcript": "...",        # yt-dlp sources (may be "")
      "timed_transcript": "...",  # yt-dlp sources (may be "")
      "visual": "...",            # optional, added by the pipeline
      "documents": [...],         # repository only
      "warnings": [...],
      "fetched_at": 1690000000,
      "content_hash": "sha256",
    }

Design notes
------------
* **One yt-dlp extraction per YouTube URL.** The previous pipeline called
  yt-dlp four times *per* URL (metadata, id, title, subtitles), which took
  ~160 s on a caption-less video.  A single ``extract_info`` call yields both
  metadata and caption URLs.
* **Targeted repository retrieval.** Only README/docs/manifests are downloaded
  (bounded count and bytes), never the whole tree.  Code is never executed.
* **Caching is source-identity + content based.** Successful results are
  cached; failures are not.  Repository caches revalidate ``pushed_at`` so a
  changed repository is refetched.
* **Bounded network work.** Timeouts, retries with backoff, and a small
  thread pool for independent raw-file fetches.
"""

import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import DIRECTORY

WORKSPACE = os.path.abspath(
    os.environ.get("DENNIS_SKILL_BUILDER_DIR", os.path.join(DIRECTORY, "agent-skill-builder"))
)
CACHE_DIR = os.path.abspath(
    os.environ.get("DENNIS_SB_CACHE_DIR", os.path.join(WORKSPACE, ".cache", "sources"))
)
CACHE_TTL = int(os.environ.get("DENNIS_SB_CACHE_TTL", "21600"))  # 6 hours
REPO_REVALIDATE_TTL = int(os.environ.get("DENNIS_SB_REPO_REVALIDATE_TTL", "21600"))

MAX_FILES = int(os.environ.get("DENNIS_SB_REPO_MAX_FILES", "12"))
MAX_FILE_BYTES = int(os.environ.get("DENNIS_SB_REPO_MAX_FILE_BYTES", "24000"))
MAX_TOTAL_BYTES = int(os.environ.get("DENNIS_SB_REPO_MAX_TOTAL_BYTES", "96000"))
MAX_CAPTION_BYTES = int(os.environ.get("DENNIS_SB_MAX_CAPTION_BYTES", "2000000"))
MAX_VIDEO_BYTES = int(os.environ.get("DENNIS_SB_MAX_VIDEO_BYTES", str(250 * 1024 * 1024)))

USER_AGENT = "DENNIS-Agentic-OS/1.0 (+local skill builder)"
YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}
# Social/video platforms that yt-dlp can resolve even though they serve HTML
# pages rather than direct media. Kept explicit so arbitrary pages still fail
# classification instead of being handed to yt-dlp.
PLATFORM_HOSTS = {
    "tiktok.com": "tiktok",
    "www.tiktok.com": "tiktok",
    "m.tiktok.com": "tiktok",
    "vm.tiktok.com": "tiktok",
    "vt.tiktok.com": "tiktok",
    "instagram.com": "instagram",
    "www.instagram.com": "instagram",
    "instagr.am": "instagram",
    "twitter.com": "x",
    "www.twitter.com": "x",
    "mobile.twitter.com": "x",
    "x.com": "x",
    "www.x.com": "x",
    "t.co": "x",
    "facebook.com": "facebook",
    "www.facebook.com": "facebook",
    "m.facebook.com": "facebook",
    "fb.watch": "facebook",
    "vimeo.com": "vimeo",
    "www.vimeo.com": "vimeo",
    "player.vimeo.com": "vimeo",
    "dailymotion.com": "dailymotion",
    "www.dailymotion.com": "dailymotion",
    "dai.ly": "dailymotion",
    "twitch.tv": "twitch",
    "www.twitch.tv": "twitch",
    "clips.twitch.tv": "twitch",
    "reddit.com": "reddit",
    "www.reddit.com": "reddit",
    "v.redd.it": "reddit",
    "pinterest.com": "pinterest",
    "www.pinterest.com": "pinterest",
    "snapchat.com": "snapchat",
    "www.snapchat.com": "snapchat",
    "linkedin.com": "linkedin",
    "www.linkedin.com": "linkedin",
    "rumble.com": "rumble",
    "www.rumble.com": "rumble",
    "bilibili.com": "bilibili",
    "www.bilibili.com": "bilibili",
    "threads.net": "threads",
    "www.threads.net": "threads",
    "kwai.com": "kwai",
    "www.kwai.com": "kwai",
    "likee.video": "likee",
    "www.likee.video": "likee",
    "weibo.com": "weibo",
    "www.weibo.com": "weibo",
}
GITHUB_HOSTS = {"github.com", "www.github.com"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi", ".mpeg", ".mpg",
              ".wmv", ".flv", ".3gp"}

YOUTUBE_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|shorts/|embed/|live/)([A-Za-z0-9_-]{11})"
)
GITHUB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)"
    r"(?:/tree/([^/?#]+)(?:/(.*?))?)?/?(?:[?#].*)?$"
)

_DOC_PRIORITY = (
    "readme", "agents.md", "claude.md", "contributing", "architecture",
    "usage", "install", "quickstart", "getting-started", "workflow",
)


class SourceError(Exception):
    """User-facing source acquisition error (invalid URL, timeout, ...)."""


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------
def _is_public_host(host):
    """Reject localhost / private addresses for arbitrary media URLs (SSRF)."""
    host = (host or "").strip().lower()
    if not host or host in ("localhost", "localhost.localdomain"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False
    return True


def validate_url(url):
    """Validate and normalize an http(s) source URL."""
    raw = (url or "").strip()
    if not raw:
        raise SourceError("A source URL is required.")
    if len(raw) > 2048:
        raise SourceError("That URL is too long.")
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise SourceError("Only http(s) URLs are supported.")
    if not parsed.hostname:
        raise SourceError("That URL has no host.")
    return raw


def validate_public_url(url):
    """Validate a URL and require a publicly routable host (SSRF guard)."""
    raw = validate_url(url)
    host = urllib.parse.urlparse(raw).hostname
    if not _is_public_host(host):
        raise SourceError("That host is not publicly reachable.")
    return raw


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect so a public URL cannot pivot to localhost."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validate_public_url(newurl)
        except SourceError as exc:
            raise SourceError("Unsafe redirect: %s" % exc)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirectHandler())


def open_url(url, timeout=30, headers=None):
    """Open a validated public URL (redirects are revalidated)."""
    hdrs = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if headers:
        hdrs.update(headers)
    return _OPENER.open(urllib.request.Request(url, headers=hdrs), timeout=timeout)


def _platform_id(url):
    """Stable, filename-safe id from a social-platform URL."""
    parsed = urllib.parse.urlparse(url)
    path = (parsed.path or "").strip("/")
    token = ""
    if path:
        token = urllib.parse.unquote(path.split("/")[-1])
    token = re.sub(r"[^A-Za-z0-9._-]+", "-", token).strip("-._")
    if not token:
        token = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return token[:80]


def classify_url(url):
    """Return ``{kind, url, id, ...}`` or raise SourceError."""
    url = validate_url(url)
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in YOUTUBE_HOSTS:
        vid = _youtube_id(url)
        if not vid:
            raise SourceError("Could not find a YouTube video id in that URL.")
        if not _is_public_host(host):
            raise SourceError("That host is not reachable from this machine.")
        return {"kind": "youtube", "url": url, "id": vid}

    if host in PLATFORM_HOSTS:
        if not _is_public_host(host):
            raise SourceError("That host is not reachable from this machine.")
        return {
            "kind": "platform",
            "platform": PLATFORM_HOSTS[host],
            "url": url,
            "id": _platform_id(url),
        }

    if host in GITHUB_HOSTS:
        m = GITHUB_RE.match(url)
        if not m:
            raise SourceError("Use a repository URL like https://github.com/owner/repo.")
        owner, repo, ref, subpath = m.group(1), m.group(2), m.group(3), m.group(4)
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not owner or not repo:
            raise SourceError("Use a repository URL like https://github.com/owner/repo.")
        if not _is_public_host(host):
            raise SourceError("That host is not reachable from this machine.")
        return {
            "kind": "repository",
            "url": url,
            "id": "%s/%s" % (owner, repo),
            "owner": owner,
            "repo": repo,
            "ref": urllib.parse.unquote(ref or ""),
            "subpath": urllib.parse.unquote(subpath or "").strip("/"),
        }

    ext = os.path.splitext(parsed.path.lower())[1]
    if ext in VIDEO_EXTS:
        if not _is_public_host(host):
            raise SourceError("That video host is not publicly reachable.")
        return {"kind": "video", "url": url, "id": os.path.basename(parsed.path)}

    raise SourceError(
        "Unsupported source. Use a YouTube, TikTok, Instagram, X/Twitter, "
        "Facebook, Vimeo, Twitch, Reddit or Bilibili URL, a GitHub repository "
        "URL, or a direct video file URL (mp4/webm/mov)."
    )


def _youtube_id(url):
    m = YOUTUBE_ID_RE.search(url or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
def _http_get(url, headers=None, timeout=30, max_bytes=None):
    """GET bytes with a browser-ish UA, bounded size and error mapping."""
    try:
        with open_url(url, timeout=timeout, headers=headers) as resp:
            chunks = []
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if max_bytes and total > max_bytes:
                    raise SourceError("Source response is larger than the allowed limit.")
                chunks.append(chunk)
            return resp.status, dict(resp.headers), b"".join(chunks)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(4096)
        except OSError:
            pass
        return exc.code, dict(exc.headers or {}), body
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise SourceError("Network error while fetching the source: %s" % exc)


def _json_or_error(url, headers=None, timeout=30):
    status, hdrs, raw = _http_get(url, headers=headers, timeout=timeout)
    if status == 404:
        return None, status, hdrs, raw
    if status >= 400:
        detail = ""
        try:
            detail = json.loads(raw.decode("utf-8", "replace")).get("message", "")
        except Exception:
            detail = raw[:200].decode("utf-8", "replace")
        raise SourceError("Source request failed (HTTP %s)%s" % (
            status, (": " + detail) if detail else ""))
    try:
        return json.loads(raw.decode("utf-8")), status, hdrs, raw
    except ValueError:
        raise SourceError("The source returned an unexpected response.")


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
def _cache_path(key):
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return os.path.join(CACHE_DIR, digest + ".json")


def _cache_read(key):
    path = _cache_path(key)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("cache_key") != key:
        return None
    return data


def _cache_write(key, payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    data = dict(payload)
    data["cache_key"] = key
    data["cached_at"] = int(time.time())
    tmp = _cache_path(key) + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, _cache_path(key))


def _cache_delete(key):
    try:
        os.remove(_cache_path(key))
    except OSError:
        pass


def _fresh(record, ttl=None):
    if not record:
        return False
    age = time.time() - float(record.get("cached_at") or 0)
    return age <= (CACHE_TTL if ttl is None else ttl)


def cache_stats():
    files = 0
    size = 0
    try:
        for name in os.listdir(CACHE_DIR):
            if name.endswith(".json"):
                files += 1
                try:
                    size += os.path.getsize(os.path.join(CACHE_DIR, name))
                except OSError:
                    pass
    except OSError:
        pass
    return {"dir": CACHE_DIR, "entries": files, "bytes": size, "ttl_seconds": CACHE_TTL}


def clear_cache():
    removed = 0
    try:
        for name in os.listdir(CACHE_DIR):
            if name.endswith(".json") or ".tmp-" in name:
                try:
                    os.remove(os.path.join(CACHE_DIR, name))
                    removed += 1
                except OSError:
                    pass
    except OSError:
        pass
    return {"removed": removed}


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_BADGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_SPACE_RE = re.compile(r"[ \t]{2,}")


def normalize_text(text, limit=None):
    """Strip HTML, badges, duplicate lines and excess whitespace."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _SCRIPT_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _BADGE_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text)
    lines = []
    seen = set()
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        key = stripped.lower()
        if key in seen and len(stripped) > 24:
            continue
        seen.add(key)
        lines.append(stripped)
    text = _MULTI_NL_RE.sub("\n\n", "\n".join(lines)).strip()
    if limit and len(text) > limit:
        text = text[:limit].rsplit("\n", 1)[0] + "\n\n[truncated]"
    return text


def _hash_text(*parts):
    h = hashlib.sha256()
    for part in parts:
        h.update((part or "").encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------
def _ytdlp_binary():
    """Find a yt-dlp console script when the active interpreter lacks the module."""
    from shutil import which
    import sysconfig
    candidates = []
    exe = which("yt-dlp")
    if exe:
        candidates.append(exe)
    try:
        user_bin = sysconfig.get_path("scripts", scheme="posix_user") or ""
    except Exception:
        user_bin = ""
    if user_bin:
        candidates.append(os.path.join(user_bin, "yt-dlp"))
    home = os.path.expanduser("~")
    candidates.extend([
        os.path.join(home, ".local", "bin", "yt-dlp"),
        os.path.join(home, "Library", "Python", "3.14", "bin", "yt-dlp"),
        os.path.join(home, "Library", "Python", "3.13", "bin", "yt-dlp"),
        os.path.join(home, "Library", "Python", "3.12", "bin", "yt-dlp"),
    ])
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def _ytdlp_info_subprocess(url, binary):
    import subprocess
    argv = [binary, "--no-warnings", "--skip-download", "--no-playlist",
            "--socket-timeout", "20", "--retries", "2", "-J", url]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120,
                              env=os.environ.copy())
    except subprocess.TimeoutExpired:
        raise SourceError("Video metadata request timed out.")
    if proc.returncode != 0 or not proc.stdout.strip():
        msg = (proc.stderr or proc.stdout or "unknown error").strip().splitlines()
        raise SourceError("Could not read that video: %s" % (msg[-1] if msg else "no output")[:300])
    try:
        info = json.loads(proc.stdout)
    except ValueError:
        raise SourceError("That platform returned an unexpected response.")
    if not isinstance(info, dict):
        raise SourceError("That platform returned an unexpected response.")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
        if not entries:
            raise SourceError("That playlist has no playable videos.")
        info = entries[0]
    return info


def _ytdlp_info(url):
    """Single yt-dlp extraction (metadata + caption URLs, no media download)."""
    try:
        import yt_dlp  # local import keeps app startup light
    except ImportError:
        binary = _ytdlp_binary()
        if not binary:
            raise SourceError("yt-dlp is not installed. Run Setup or pip install -U yt-dlp.")
        return _ytdlp_info_subprocess(url, binary)
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 2,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:  # yt-dlp raises many exception types
        msg = str(exc).strip().splitlines()[-1] if str(exc).strip() else "unknown error"
        raise SourceError("Could not read that video: %s" % msg[:300])
    if not isinstance(info, dict):
        raise SourceError("That platform returned an unexpected response.")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if isinstance(e, dict)]
        if not entries:
            raise SourceError("That playlist has no playable videos.")
        info = entries[0]
    return info


def _caption_candidates(info):
    """Ordered (lang, fmt, url) candidates, English first."""
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    langs = []

    def add(mapping, source):
        for lang, formats in mapping.items():
            if not isinstance(formats, list):
                continue
            for fmt in formats:
                if not isinstance(fmt, dict) or not fmt.get("url"):
                    continue
                langs.append({
                    "lang": lang,
                    "source": source,
                    "ext": (fmt.get("ext") or "").lower(),
                    "url": fmt["url"],
                })

    # Manual English captions win over auto-generated ones.
    add(subs, "manual")
    add(autos, "auto")

    def rank(c):
        lang = c["lang"].lower()
        english = 0 if lang in ("en", "en-us", "en-gb", "en-orig") else (
            1 if lang.startswith("en") else 2)
        fmt = c["ext"]
        fmt_rank = {"json3": 0, "srv3": 1, "vtt": 2, "srt": 3}.get(fmt, 4)
        return (english, fmt_rank, c["source"] != "manual")

    langs.sort(key=rank)
    # Keep at most one candidate per language/format to bound work.
    out, seen = [], set()
    for c in langs:
        key = (c["lang"], c["ext"])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _caption_to_text(raw, ext):
    """Convert json3 / vtt / srt / srv3 / ttml into (plain, timed)."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    if not text.strip():
        return "", ""
    if ext == "json3" or text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            plain, timed = [], []
            for event in data.get("events") or []:
                segs = event.get("segs") or []
                line = "".join(seg.get("utf8", "") for seg in segs).strip()
                if not line:
                    continue
                line = re.sub(r"\s+", " ", line)
                timed.append("[%s] %s" % (_mmss((event.get("tStartMs") or 0) / 1000), line))
                plain.append(line)
            return _dedupe_caption(plain), _dedupe_caption(timed, timed=True)

    # VTT / SRT
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    plain, timed = [], []
    for block in blocks:
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        stamp = next((ln for ln in lines if "-->" in ln or re.match(r"^\d+:\d+", ln)), "")
        body = " ".join(ln for ln in lines
                        if "-->" not in ln and not re.match(r"^\d+$", ln.strip())
                        and not ln.strip().upper().startswith("WEBVTT"))
        body = re.sub(r"<[^>]+>", "", html.unescape(body))
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        plain.append(body)
        if stamp:
            start = re.split(r"\s*-->\s*", stamp)[0].strip()
            secs = _parse_timestamp(start)
            timed.append("[%s] %s" % (_mmss(secs), body) if secs is not None else body)
        else:
            timed.append(body)
    return _dedupe_caption(plain), _dedupe_caption(timed, timed=True)


def _dedupe_caption(lines, timed=False):
    out, prev = [], ""
    for line in lines:
        body = line.split("] ", 1)[1] if timed and "] " in line else line
        if body == prev:
            continue
        prev = body
        out.append(line)
    return "\n".join(out)


def _parse_timestamp(value):
    m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$", (value or "").strip())
    if not m:
        return None
    h = int(m.group(1) or 0)
    return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + (int(m.group(4) or 0) / 1000.0)


def _mmss(seconds):
    try:
        seconds = int(max(0, float(seconds)))
    except (TypeError, ValueError):
        seconds = 0
    return "%02d:%02d" % (seconds // 60, seconds % 60)


def _fetch_transcript(info):
    """Download the first usable caption track. Returns (plain, timed, error)."""
    errors = []
    for candidate in _caption_candidates(info)[:6]:
        try:
            status, hdrs, raw = _http_get(
                candidate["url"], timeout=25, max_bytes=MAX_CAPTION_BYTES)
        except SourceError as exc:
            errors.append(str(exc))
            continue
        if status >= 400:
            errors.append("HTTP %s for %s captions" % (status, candidate["lang"]))
            continue
        plain, timed = _caption_to_text(raw, candidate["ext"])
        if plain.strip():
            return plain, timed, ""
        errors.append("empty %s captions" % candidate["lang"])
    return "", "", (errors[0] if errors else "No captions are available for that video.")


def _media_payload(info, url, kind="youtube", platform="", fallback_id=None):
    """Normalized yt-dlp payload for YouTube and other video platforms."""
    vid = info.get("id") or _youtube_id(url) or fallback_id or "video"
    metadata = {
        "id": vid,
        "title": info.get("title") or "",
        "channel": info.get("uploader") or info.get("channel") or "",
        "url": info.get("webpage_url") or url,
        "upload_date": info.get("upload_date"),
        "duration_seconds": info.get("duration"),
        "view_count": info.get("view_count"),
        "description": normalize_text(info.get("description") or "", 4000),
        "chapters": [
            {"start": c.get("start_time"), "title": c.get("title")}
            for c in (info.get("chapters") or []) if isinstance(c, dict)
        ],
        "tags": (info.get("tags") or [])[:30],
        "thumbnail": info.get("thumbnail"),
        "width": info.get("width"),
        "height": info.get("height"),
        "fps": info.get("fps"),
    }
    if platform:
        metadata["platform"] = platform
    transcript, timed, error = _fetch_transcript(info)
    warnings = []
    if error:
        warnings.append("Captions unavailable: %s" % error)
    body = normalize_text(
        (metadata.get("description") or "") + "\n\n" + transcript, 60000)
    payload = {
        "kind": kind,
        "url": metadata["url"],
        "id": vid,
        "title": metadata["title"] or vid,
        "metadata": metadata,
        "transcript": transcript,
        "timed_transcript": timed,
        "text": body,
        "documents": [],
        "warnings": warnings,
        "transcript_error": error,
        "provider": "yt-dlp",
        "fetched_at": int(time.time()),
        "content_hash": _hash_text(vid, metadata.get("title") or "",
                                  metadata.get("upload_date") or "", transcript),
    }
    if platform:
        payload["platform"] = platform
    return payload


def _youtube_payload(info, url):
    return _media_payload(info, url, kind="youtube")


def extract_youtube(url, refresh=False):
    """Fetch one YouTube video: metadata + captions in a single extraction."""
    info_url = classify_url(url)
    if info_url["kind"] != "youtube":
        raise SourceError("That is not a YouTube URL.")
    key = "youtube:%s" % info_url["id"]
    if not refresh:
        cached = _cache_read(key)
        if _fresh(cached):
            cached.pop("cache_key", None)
            cached["cached"] = True
            return cached
    info = _ytdlp_info(url)
    payload = _youtube_payload(info, url)
    # Do not cache a run whose only purpose was to record a transient caption
    # failure; a later attempt may succeed.
    if payload.get("transcript"):
        _cache_write(key, payload)
    payload["cached"] = False
    return payload


def extract_platform_video(url, refresh=False):
    """Fetch metadata/captions from TikTok, Instagram, X and similar pages."""
    spec = classify_url(url)
    if spec["kind"] != "platform":
        raise SourceError("That is not a supported video platform URL.")
    platform = spec.get("platform") or "video"
    key = "platform:%s:%s" % (platform, spec["id"])
    if not refresh:
        cached = _cache_read(key)
        if _fresh(cached):
            cached.pop("cache_key", None)
            cached["cached"] = True
            return cached
    info = _ytdlp_info(url)
    payload = _media_payload(
        info, url, kind="platform", platform=platform, fallback_id=spec["id"])
    if payload.get("transcript") or payload.get("text"):
        _cache_write(key, payload)
    payload["cached"] = False
    return payload


def download_platform_video(url, max_bytes=None):
    """Download the best playable stream from a supported platform.

    Returns ``(path, content_type)`` for a temporary file. The caller owns the
    file and must remove it. Used when Gemini needs a local video upload
    because social-platform pages are not direct media URLs.
    """
    import tempfile

    limit = int(max_bytes or MAX_VIDEO_BYTES)
    info = _ytdlp_info(url)
    candidates = []
    if info.get("url"):
        candidates.append(info)
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict) or not fmt.get("url"):
            continue
        if (fmt.get("vcodec") or "none") == "none":
            continue
        candidates.append(fmt)
    if not candidates:
        raise SourceError("No downloadable video stream was found for that URL.")

    def rank(item):
        has_audio = 1 if (item.get("acodec") or "none") != "none" else 0
        ext = (item.get("ext") or "").lower()
        return (has_audio, item.get("height") or 0, 1 if ext == "mp4" else 0)

    chosen = None
    for candidate in sorted(candidates, key=rank, reverse=True):
        size = candidate.get("filesize") or candidate.get("filesize_approx")
        if size and int(size) > limit:
            continue
        chosen = candidate
        break
    if not chosen:
        raise SourceError(
            "The video is larger than the %d MB analysis limit."
            % (limit // (1024 * 1024)))

    ext = (chosen.get("ext") or "mp4").lower()
    if ext not in ("mp4", "webm", "mov", "m4v"):
        ext = "mp4"
    mime = {"mp4": "video/mp4", "m4v": "video/mp4",
            "webm": "video/webm", "mov": "video/quicktime"}.get(ext, "video/mp4")
    headers = {"User-Agent": USER_AGENT}
    headers.update(chosen.get("http_headers") or {})
    tmp = tempfile.NamedTemporaryFile(
        prefix="dennis-platform-", suffix="." + ext, delete=False)
    total = 0
    try:
        req = urllib.request.Request(chosen["url"], headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp, tmp:
            ctype = (resp.headers.get("Content-Type") or mime).split(";")[0]
            if ctype.startswith("video/"):
                mime = ctype
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise SourceError(
                        "The video is larger than the %d MB analysis limit."
                        % (limit // (1024 * 1024)))
                tmp.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        raise SourceError("Could not download that platform video: %s" % exc)
    finally:
        try:
            tmp.close()
        except Exception:
            pass
    if total == 0:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
        raise SourceError("The platform video download was empty.")
    return tmp.name, mime


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------
def _github_headers():
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        # Reuse the workspace .env (the app already treats it as local config).
        try:
            with open(os.path.join(WORKSPACE, ".env"), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith("GITHUB_TOKEN=") or line.startswith("GH_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


def _repo_info(owner, repo):
    data, status, hdrs, raw = _json_or_error(
        "https://api.github.com/repos/%s/%s" % (owner, repo),
        headers=_github_headers(), timeout=25)
    if data is None:
        raise SourceError("Repository not found: %s/%s" % (owner, repo))
    return data


def _repo_tree(owner, repo, ref):
    data, status, hdrs, raw = _json_or_error(
        "https://api.github.com/repos/%s/%s/git/trees/%s?recursive=1" % (
            owner, repo, urllib.parse.quote(ref, safe="")),
        headers=_github_headers(), timeout=30)
    if data is None:
        raise SourceError("Could not read the repository tree.")
    return data.get("tree") or []


def _select_docs(tree, subpath="", limit=None):
    """Pick documentation/manifests by relevance without downloading the repo."""
    limit = limit or MAX_FILES
    prefix = (subpath.strip("/") + "/") if subpath else ""
    picked = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if not rel or rel.startswith("."):
            continue
        low = rel.lower()
        if any(part in low for part in (
                "node_modules/", "vendor/", "dist/", "build/", ".git/",
                "__pycache__/", "test/", "tests/", "fixtures/", "examples/")):
            continue
        name = os.path.basename(low)
        ext = os.path.splitext(name)[1]
        score = None
        if ext == ".md":
            score = 10
            for i, key in enumerate(_DOC_PRIORITY):
                if key in name or (i < 4 and key in low):
                    score = 100 - i
                    break
            if low.startswith("docs/"):
                score += 5
            if low.count("/") > 2:
                score -= 5
        elif ext == "" and name.startswith(("readme", "agents", "claude")):
            score = 90
        elif name in ("package.json", "pyproject.toml", "cargo.toml",
                      "go.mod", "requirements.txt", "dockerfile"):
            score = 30
        if score is None or (node.get("size") or 0) > MAX_FILE_BYTES:
            continue
        picked.append((score, path, node.get("size") or 0))
    picked.sort(key=lambda item: (-item[0], item[1]))
    return picked[:limit]


def _fetch_repo_file(owner, repo, ref, path):
    url = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
        owner, repo, urllib.parse.quote(ref, safe=""),
        urllib.parse.quote(path, safe="/"))
    status, hdrs, raw = _http_get(url, timeout=20, max_bytes=MAX_FILE_BYTES)
    if status >= 400:
        return path, None
    return path, raw.decode("utf-8", "replace")


def _repo_payload(info, url, spec):
    owner, repo = spec["owner"], spec["repo"]
    ref = spec.get("ref") or info.get("default_branch") or "main"
    tree = _repo_tree(owner, repo, ref)
    docs = _select_docs(tree, spec.get("subpath") or "")
    if not docs:
        raise SourceError(
            "No documentation found in %s/%s. Add a README.md or docs/*.md." % (owner, repo))

    fetched = {}
    errors = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_fetch_repo_file, owner, repo, ref, path): path
            for _score, path, _size in docs
        }
        for future in as_completed(futures):
            try:
                path, content = future.result()
            except Exception as exc:
                errors.append(str(exc)[:120])
                continue
            if content:
                fetched[path] = content

    total = 0
    chunks = []
    documents = []
    prefix = (spec.get("subpath") or "").strip("/")
    for _score, path, _size in docs:
        content = fetched.get(path)
        if not content:
            continue
        content = normalize_text(content, MAX_FILE_BYTES)
        if total + len(content) > MAX_TOTAL_BYTES:
            break
        total += len(content)
        display = path[len(prefix) + 1:] if prefix and path.startswith(prefix + "/") else path
        chunks.append("## File: %s\n\n%s" % (display, content))
        documents.append({"path": display, "chars": len(content)})

    if not chunks:
        raise SourceError("Could not download documentation from %s/%s." % (owner, repo))
    body = normalize_text("\n\n".join(chunks), MAX_TOTAL_BYTES)
    metadata = {
        "id": "%s/%s" % (owner, repo),
        "title": info.get("full_name") or "%s/%s" % (owner, repo),
        "description": normalize_text(info.get("description") or "", 1200),
        "url": info.get("html_url") or url,
        "owner": owner,
        "repo": repo,
        "default_branch": info.get("default_branch"),
        "ref": ref,
        "pushed_at": info.get("pushed_at"),
        "stars": info.get("stargazers_count"),
        "topics": (info.get("topics") or [])[:20],
        "language": info.get("language"),
        "documents": documents,
    }
    return {
        "kind": "repository",
        "url": metadata["url"],
        "id": metadata["id"],
        "title": metadata["title"],
        "metadata": metadata,
        "transcript": "",
        "timed_transcript": "",
        "text": body,
        "documents": documents,
        "warnings": (["Some files could not be read: %s" % errors[0]] if errors else []),
        "provider": "github",
        "fetched_at": int(time.time()),
        "content_hash": _hash_text(metadata["id"], ref, metadata.get("pushed_at") or "", body),
    }


def extract_repository(url, refresh=False):
    """Fetch repository docs/manifests only (bounded, concurrent, cached)."""
    spec = classify_url(url)
    if spec["kind"] != "repository":
        raise SourceError("That is not a GitHub repository URL.")
    key = "repo:%s:%s:%s" % (spec["id"], spec.get("ref") or "", spec.get("subpath") or "")
    info = _repo_info(spec["owner"], spec["repo"])
    cached = _cache_read(key)
    if not refresh and _fresh(cached):
        # Revalidate changed repositories cheaply via pushed_at.
        if not cached.get("pushed_at") or cached.get("pushed_at") == info.get("pushed_at"):
            cached.pop("cache_key", None)
            cached["cached"] = True
            return cached
        _cache_delete(key)

    payload = _repo_payload(info, url, spec)
    _cache_write(key, payload)
    payload["cached"] = False
    return payload


# ---------------------------------------------------------------------------
# Direct video
# ---------------------------------------------------------------------------
def extract_direct_video(url, refresh=False):
    """Validate a direct video URL and read metadata (no download yet)."""
    spec = classify_url(url)
    if spec["kind"] != "video":
        raise SourceError("That is not a direct video URL.")
    key = "video:%s" % url
    if not refresh:
        cached = _cache_read(key)
        if _fresh(cached):
            cached.pop("cache_key", None)
            cached["cached"] = True
            return cached

    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = resp.status
            hdrs = dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raise SourceError("Video URL returned HTTP %s." % exc.code)
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise SourceError("Could not reach the video URL: %s" % exc)
    if status >= 400:
        raise SourceError("Video URL returned HTTP %s." % status)
    ctype = (hdrs.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype and not ctype.startswith("video/") and ctype not in (
            "application/octet-stream", "binary/octet-stream"):
        raise SourceError("That URL does not serve a video (Content-Type: %s)." % ctype)
    length = hdrs.get("Content-Length")
    try:
        length = int(length) if length else None
    except ValueError:
        length = None
    if length and length > MAX_VIDEO_BYTES:
        raise SourceError("That video is larger than the %d MB limit." % (MAX_VIDEO_BYTES // (1024 * 1024)))
    payload = {
        "kind": "video",
        "url": url,
        "id": spec["id"],
        "title": spec["id"],
        "metadata": {
            "id": spec["id"],
            "title": spec["id"],
            "url": url,
            "content_type": ctype,
            "content_length": length,
        },
        "text": "",
        "transcript": "",
        "timed_transcript": "",
        "documents": [],
        "warnings": ["Direct video: visual analysis uploads the media to Gemini Files API."],
        "provider": "direct",
        "fetched_at": int(time.time()),
        "content_hash": _hash_text(url, str(length or ""), ctype),
    }
    _cache_write(key, payload)
    payload["cached"] = False
    return payload


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
def extract_source(url, refresh=False):
    """Classify and fetch one source, returning the normalized source dict."""
    spec = classify_url(url)
    if spec["kind"] == "youtube":
        return extract_youtube(url, refresh=refresh)
    if spec["kind"] == "platform":
        return extract_platform_video(url, refresh=refresh)
    if spec["kind"] == "repository":
        return extract_repository(url, refresh=refresh)
    return extract_direct_video(url, refresh=refresh)


def source_version(source):
    """Freshness signal used by the draft cache and UI."""
    meta = source.get("metadata") or {}
    return {
        "content_hash": source.get("content_hash"),
        "fetched_at": source.get("fetched_at"),
        "pushed_at": meta.get("pushed_at"),
        "upload_date": meta.get("upload_date"),
        "cached": bool(source.get("cached")),
    }
