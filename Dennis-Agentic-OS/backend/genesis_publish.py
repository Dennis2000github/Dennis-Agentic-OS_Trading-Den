"""GENESIS publishing — destination adapters with real verification.

Publishing belongs to GENESIS, never to the individual Studio workspaces.
The adapter architecture is intentionally explicit about what is real:

* **YouTube** — a genuine OAuth 2.0 (installed-app) flow against Google's
  authorization/token endpoints and a genuine resumable upload through the
  YouTube Data API v3. Nothing is marked published unless Google's API
  returns a real video id for the upload request.
* **Other platforms** — listed with ``available: false`` and a plain reason.
  They are never shown as connected or operational.

Hard rules enforced here:

* Tokens are stored only on the backend (0600 file) and are never returned
  by any API response; only booleans and scope lists are exposed.
* A saved OAuth client configuration does **not** count as authorized —
  authorization requires a stored refresh token from a completed flow.
* Publication requires an approved quality review, an explicit owner
  approval flag on the request, and passing destination gates.
* Duplicate publication requests are rejected; retries are explicit.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from . import cosmos_common as common
from . import genesis
from . import genesis_store as store
from . import studio_media

OAUTH_CLIENT_FILE = os.path.join(common.COSMOS_DIR, "..", "studio", "youtube", "google-oauth.json")

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"

DEFAULT_PORT = int(os.environ.get("DENNIS_PORT") or 8080)
UPLOAD_TIMEOUT = 60 * 60

_status_lock = threading.RLock()
_scheduler_started = False
_pending_states = {}


class PublishError(Exception):
    """A user-facing publishing failure with a stable code."""

    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


# ---------------------------------------------------------------------------
# HTTP transport (isolated so tests can mock without network access)
# ---------------------------------------------------------------------------
def _http_json(method, url, headers=None, payload=None, data=None, timeout=30):
    """Perform one HTTP request. Returns (status, parsed_json_or_text, headers)."""
    body = None
    headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers.setdefault("Content-Type", "application/json; charset=UTF-8")
    elif data is not None:
        body = data
    request = urllib.request.Request(url, data=body, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            headers_out = dict(response.headers.items())
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        headers_out = dict(exc.headers.items())
        status = exc.code
    except (urllib.error.URLError, OSError) as exc:
        raise PublishError("network_error", "Could not reach the destination: %s" % exc, 502)
    text = raw.decode("utf-8", "replace")
    try:
        parsed = json.loads(text) if text.strip() else {}
    except ValueError:
        parsed = {"raw": text[:2000]}
    return status, parsed, headers_out


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------
IMPLEMENTED_DESTINATIONS = ("youtube",)


def destinations():
    return {
        "destinations": [
            _youtube_status(),
            {"id": "tiktok", "name": "TikTok", "available": False,
             "configured": False, "authorized": False,
             "reason": "No TikTok adapter is implemented yet. It will appear here when one is added."},
            {"id": "instagram", "name": "Instagram", "available": False,
             "configured": False, "authorized": False,
             "reason": "No Instagram adapter is implemented yet. It will appear here when one is added."},
            {"id": "facebook", "name": "Facebook Pages", "available": False,
             "configured": False, "authorized": False,
             "reason": "No Facebook adapter is implemented yet. It will appear here when one is added."},
        ]
    }


def _oauth_client():
    """Resolve the Google OAuth client the same way youtube_api already does:
    environment first, then the owner-managed client file. Values never leave
    the backend."""
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    source = "env"
    if not (client_id and client_secret):
        path = os.path.normpath(OAUTH_CLIENT_FILE)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                client_id = data.get("client_id")
                client_secret = data.get("client_secret")
                source = "file"
            except (OSError, ValueError):
                return None, None, "file_unreadable"
    if client_id and client_secret:
        return client_id, client_secret, source
    return None, None, "none"


def _tokens():
    data = common.read_json(store.TOKENS_FILE, {})
    return (data.get("youtube") or {}) if isinstance(data, dict) else {}


def _save_tokens(payload):
    common.write_json(store.TOKENS_FILE, {"version": 1, "youtube": payload, "updated_iso": common.iso()})


def _youtube_status():
    client_id, _, source = _oauth_client()
    tokens = _tokens()
    authorized = bool(tokens.get("refresh_token"))
    return {
        "id": "youtube",
        "name": "YouTube",
        "available": True,
        "configured": bool(client_id),
        "configured_source": source,
        "authorized": authorized,
        "scopes": SCOPES,
        "granted_iso": tokens.get("granted_iso"),
        "channel_title": tokens.get("channel_title"),
        "channel_id": tokens.get("channel_id"),
        "redirect_uri": redirect_uri(),
        "reason": None if (client_id and authorized) else _youtube_reason(bool(client_id), authorized, source),
    }


def _youtube_reason(configured, authorized, source):
    if not configured:
        return ("Google OAuth is not configured. Add a Google Cloud OAuth client "
                "(env GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET, or the client file) "
                "to enable YouTube publishing.")
    if not authorized:
        return ("OAuth client found (%s). Complete the Google authorization flow to grant "
                "youtube.upload access before publishing." % source)
    return None


def redirect_uri(port=None):
    port = port or DEFAULT_PORT
    return "http://localhost:%d/api/cosmos/genesis/publishing/youtube/callback" % port


# ---------------------------------------------------------------------------
# OAuth flow
# ---------------------------------------------------------------------------
def start_youtube_authorization(port=None):
    client_id, client_secret, source = _oauth_client()
    if not (client_id and client_secret):
        raise PublishError(
            "oauth_not_configured",
            "Google OAuth is not configured (%s). YouTube publishing cannot start." % source,
            409,
        )
    state = secrets.token_urlsafe(24)
    with _status_lock:
        _pending_states[state] = {"created": time.time(), "port": port or DEFAULT_PORT}
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri(port or DEFAULT_PORT),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return {
        "authorization_url": AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params),
        "redirect_uri": redirect_uri(port or DEFAULT_PORT),
        "scopes": SCOPES,
        "expires_in": 600,
    }


def _pop_state(state, port=None):
    with _status_lock:
        record = _pending_states.pop(str(state or ""), None)
    if not record:
        raise PublishError("oauth_state_invalid", "The authorization state is missing or expired. Restart the flow.", 400)
    if time.time() - record["created"] > 600:
        raise PublishError("oauth_state_expired", "The authorization state expired. Restart the flow.", 400)
    if port and record.get("port") and int(record["port"]) != int(port):
        raise PublishError("oauth_state_invalid", "The authorization callback arrived on an unexpected port.", 400)


def complete_youtube_authorization(query, port=None):
    """OAuth callback: validate state, exchange the code, store refresh token."""
    code = (query.get("code") or [None])[0]
    state = (query.get("state") or [None])[0]
    error = (query.get("error") or [None])[0]
    if error:
        raise PublishError("oauth_denied", "Google returned an authorization error: %s" % error, 400)
    if not code:
        raise PublishError("oauth_code_missing", "The authorization callback contained no code.", 400)
    _pop_state(state, port)
    client_id, client_secret, _ = _oauth_client()
    if not (client_id and client_secret):
        raise PublishError("oauth_not_configured", "Google OAuth is not configured.", 409)
    status, payload, _ = _http_json("POST", TOKEN_ENDPOINT, payload={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri(port or DEFAULT_PORT),
        "grant_type": "authorization_code",
    })
    if status != 200 or not payload.get("access_token"):
        raise PublishError(
            "oauth_exchange_failed",
            "Google rejected the token exchange (HTTP %s): %s" % (status, payload.get("error_description") or payload.get("error") or "unknown"),
            502,
        )
    tokens = {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token") or _tokens().get("refresh_token"),
        "expires_at": time.time() + int(payload.get("expires_in") or 3600),
        "scope": payload.get("scope") or " ".join(SCOPES),
        "granted_iso": common.iso(),
        "channel_title": None,
    }
    if not tokens["refresh_token"]:
        raise PublishError(
            "oauth_no_refresh_token",
            "Google did not grant a refresh token. Re-run the authorization with prompt=consent.",
            502,
        )
    _save_tokens(tokens)
    channel = _fetch_channel(tokens)
    if channel:
        tokens["channel_title"] = channel.get("title")
        tokens["channel_id"] = channel.get("id")
        _save_tokens(tokens)
    return {
        "authorized": True,
        "scopes": SCOPES,
        "channel_title": tokens.get("channel_title"),
        "channel_id": tokens.get("channel_id"),
    }


def disconnect_youtube():
    with store.acquire():
        common.write_json(store.TOKENS_FILE, {"version": 1, "updated_iso": common.iso()})
    return {"authorized": False}


def _access_token():
    """Return a valid access token, refreshing it when required."""
    tokens = _tokens()
    if not tokens.get("refresh_token"):
        raise PublishError(
            "not_authorized",
            "YouTube is not authorized. Complete the Google authorization flow in the Publishing Center first.",
            409,
        )
    if tokens.get("expires_at") and tokens["expires_at"] - 60 > time.time():
        return tokens["access_token"]
    client_id, client_secret, _ = _oauth_client()
    if not (client_id and client_secret):
        raise PublishError("oauth_not_configured", "Google OAuth is not configured.", 409)
    status, payload, _ = _http_json("POST", TOKEN_ENDPOINT, payload={
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token",
    })
    if status != 200 or not payload.get("access_token"):
        raise PublishError(
            "oauth_refresh_failed",
            "Google rejected the token refresh (HTTP %s): %s" % (status, payload.get("error_description") or "unknown"),
            502,
        )
    tokens = dict(tokens)
    tokens["access_token"] = payload["access_token"]
    tokens["expires_at"] = time.time() + int(payload.get("expires_in") or 3600)
    _save_tokens(tokens)
    return tokens["access_token"]


def _fetch_channel(tokens):
    try:
        status, payload, _ = _http_json(
            "GET",
            "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
            headers={"Authorization": "Bearer %s" % tokens["access_token"]},
        )
        if status == 200:
            items = payload.get("items") or []
            if items:
                snippet = items[0].get("snippet") or {}
                return {"id": items[0].get("id"), "title": snippet.get("title")}
    except PublishError:
        return None
    return None


# ---------------------------------------------------------------------------
# YouTube upload (resumable protocol)
# ---------------------------------------------------------------------------
def _rfc3339(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upload_youtube(project, metadata, video_path):
    """Upload one rendered video. Returns the real remote id + URL, or raises."""
    access = _access_token()
    title = metadata.get("title") or project.get("title")
    description = metadata.get("description") or ""
    disclosure = metadata.get("disclosure")
    if disclosure:
        description = (description + "\n\n" + disclosure).strip()
    snippet = {
        "title": title[:100],
        "description": description[:5000],
        "tags": (metadata.get("tags") or [])[:30],
        "categoryId": metadata.get("category_id") or "22",
    }
    status_block = {
        "privacyStatus": metadata.get("privacy_status") or "private",
        "selfDeclaredMadeForKids": False,
    }
    publish_at = metadata.get("publish_at")
    if publish_at:
        status_block["privacyStatus"] = "private"
        status_block["publishAt"] = publish_at

    size = os.path.getsize(video_path)
    init_status, init_payload, init_headers = _http_json(
        "POST",
        UPLOAD_ENDPOINT + "?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": "Bearer %s" % access,
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        payload={"snippet": snippet, "status": status_block},
    )
    if init_status != 200:
        message = init_payload.get("error", {}).get("message") if isinstance(init_payload.get("error"), dict) else init_payload
        raise PublishError(
            "upload_init_failed",
            "YouTube rejected the upload session (HTTP %s): %s" % (init_status, str(message)[:400]),
            502,
        )
    upload_url = None
    for key, value in (init_headers or {}).items():
        if key.lower() == "location":
            upload_url = value
    if not upload_url:
        raise PublishError("upload_init_failed", "YouTube did not return an upload session URL.", 502)

    with open(video_path, "rb") as handle:
        data = handle.read()
    up_status, up_payload, _ = _http_json(
        "PUT",
        upload_url,
        headers={
            "Authorization": "Bearer %s" % access,
            "Content-Type": "video/mp4",
            "Content-Length": str(size),
        },
        data=data,
        timeout=UPLOAD_TIMEOUT,
    )
    if up_status not in (200, 201):
        message = up_payload.get("error", {}).get("message") if isinstance(up_payload.get("error"), dict) else up_payload
        raise PublishError(
            "upload_failed",
            "YouTube upload failed (HTTP %s): %s" % (up_status, str(message)[:400]),
            502,
        )
    remote_id = up_payload.get("id")
    if not remote_id:
        raise PublishError("upload_unverified", "YouTube accepted the upload but returned no video id.", 502)
    return {
        "remote_id": remote_id,
        "remote_url": "https://youtu.be/%s" % remote_id,
        "remote_status": up_payload.get("status") or {},
    }


def upload_thumbnail(video_id, path):
    """Upload a real thumbnail for a confirmed video; raises on failure."""
    access = _access_token()
    content_type = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as handle:
        data = handle.read()
    url = ("https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
           "?videoId=%s&uploadType=media" % urllib.parse.quote(video_id))
    status, payload, _ = _http_json(
        "POST", url,
        headers={"Authorization": "Bearer %s" % access, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    if status not in (200, 201):
        message = payload.get("error", {}).get("message") if isinstance(payload.get("error"), dict) else payload
        raise PublishError(
            "thumbnail_upload_failed",
            "YouTube rejected the thumbnail (HTTP %s): %s" % (status, str(message)[:400]),
            502,
        )
    return {"uploaded": True}


# ---------------------------------------------------------------------------
# Publication records
# ---------------------------------------------------------------------------
PUBLISH_METADATA_FIELDS = (
    "title", "description", "tags", "disclosure", "sponsored",
    "affiliate_url", "privacy_status", "category_id", "thumbnail_media_id",
)


def _publication_metadata(project, override):
    """Merge and sanitize publish-time metadata overrides.

    Only known fields are accepted; everything is cleaned by the same domain
    validator the project editor uses, so a request cannot smuggle arbitrary
    keys into the stored publication record.
    """
    current = dict(project.get("publishing_metadata") or {})
    if isinstance(override, dict):
        current.update({key: override[key] for key in PUBLISH_METADATA_FIELDS if key in override})
    return genesis._clean_metadata({}, current)


def _destination_account():
    """A non-secret snapshot of the destination account used for provenance."""
    tokens = _tokens()
    return {
        "channel_title": tokens.get("channel_title"),
        "channel_id": tokens.get("channel_id"),
        "granted_iso": tokens.get("granted_iso"),
        "scopes": SCOPES,
    }


def publish(project_id, payload):
    """Create a publication and start (or schedule) the real upload."""
    body = payload if isinstance(payload, dict) else {}
    destination = common.clean_text(body.get("destination") or "youtube", "Destination", 40)
    if destination not in IMPLEMENTED_DESTINATIONS:
        raise PublishError(
            "destination_unavailable",
            "'%s' has no implemented adapter. Publishing cannot start." % destination,
            409,
        )
    if body.get("approved") is not True:
        raise PublishError(
            "approval_required",
            "Publishing requires the explicit owner approval flag on this request. Nothing is published automatically.",
            409,
        )

    schedule_at = body.get("schedule_at")
    publish_at_iso = None
    schedule_epoch = None
    if schedule_at:
        try:
            parsed = datetime.fromisoformat(str(schedule_at).replace("Z", "+00:00"))
        except ValueError:
            raise PublishError("invalid_schedule", "schedule_at must be an ISO-8601 timestamp.", 400)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed <= datetime.now(timezone.utc):
            raise PublishError("invalid_schedule", "The scheduled time is in the past.", 400)
        schedule_epoch = parsed.timestamp()
        publish_at_iso = _rfc3339(parsed.timestamp())

    with store.acquire():
        project = genesis.store.get_project(project_id)
        approval = project.get("approval") or {}
        if not approval.get("approved"):
            raise PublishError(
                "project_not_approved",
                "The project has no owner approval. Approve it in Quality Review first.",
                409,
            )
        review = project.get("quality_review") or {}
        if review.get("verdict") == "fail":
            raise PublishError("quality_review_failed", "The last quality review failed. Fix the issues and re-run it.", 409)
        if not project.get("media_id"):
            raise PublishError("no_render", "The project has no rendered output. Complete a render first.", 409)
        if review.get("media_id") != project.get("media_id"):
            raise PublishError(
                "quality_review_stale",
                "The quality review does not match the current render. Re-run it before publishing.",
                409,
            )

        destination_status = _youtube_status()
        if not destination_status.get("authorized"):
            raise PublishError(
                "not_authorized",
                "YouTube is not authorized. Complete the Google authorization flow in Publishing first.",
                409,
            )

        existing = store.find_publication(project_id, destination, statuses=("scheduled", "publishing"))
        if existing:
            raise PublishError(
                "publication_in_progress",
                "Publication %s to %s is already %s." % (existing["id"], destination, existing["status"]),
                409,
            )
        succeeded = store.find_publication(project_id, destination, statuses=("succeeded",))
        if succeeded:
            raise PublishError(
                "already_published",
                "This project is already published to %s (%s)." % (destination, succeeded.get("remote_url")),
                409,
            )
        failed = store.find_publication(project_id, destination, statuses=("failed",))
        if failed:
            raise PublishError(
                "retry_required",
                "Publication %s to %s failed. Retry that attempt instead of creating a duplicate."
                % (failed["id"], destination),
                409,
            )

        metadata = _publication_metadata(project, body.get("metadata"))
        if not metadata.get("title"):
            raise PublishError("metadata_title_required", "A publishing title is required.", 400)

        now = common.now()
        record = {
            "id": common.new_id("gpb_"),
            "project_id": project_id,
            "division": project.get("division"),
            "destination": destination,
            "destination_account": _destination_account(),
            "status": "scheduled" if schedule_epoch else "publishing",
            "metadata": {
                "title": metadata.get("title") or project.get("title"),
                "description": metadata.get("description") or "",
                "tags": metadata.get("tags") or [],
                "privacy_status": metadata.get("privacy_status") or "private",
                "publish_at": None,
                "disclosure": metadata.get("disclosure"),
                "sponsored": bool(metadata.get("sponsored")),
                "affiliate_url": metadata.get("affiliate_url"),
                "category_id": metadata.get("category_id") or "22",
                "thumbnail_media_id": metadata.get("thumbnail_media_id"),
            },
            "review": {
                "verdict": review.get("verdict"),
                "media_id": review.get("media_id"),
                "render_id": review.get("render_id"),
                "ran_iso": review.get("ran_iso"),
            },
            "approval": {
                "approved_by": "owner",
                "project_approved_iso": approval.get("at_iso"),
                "review_media_id": review.get("media_id"),
                "at_iso": common.iso(),
                "note": common.clean_text(body.get("approval_note"), "Approval note", 500),
            },
            "remote_id": None,
            "remote_url": None,
            "attempts": 0 if schedule_epoch else 1,
            "error": None,
            "warnings": [],
            "schedule_at": schedule_epoch,
            "scheduled_iso": publish_at_iso,
            "created_at": now,
            "created_iso": common.iso(now),
            "updated_iso": common.iso(now),
            "published_iso": None,
            "history": [],
        }
        record["history"].append({"iso": common.iso(), "event": "created",
                                  "note": "Scheduled for %s" % publish_at_iso if publish_at_iso else "Publishing now."})
        store.insert_publication(record)
        store.update_project(project_id, {
            "publication_ids": list(dict.fromkeys((project.get("publication_ids") or []) + [record["id"]])),
            "updated_at": common.now(),
            "updated_iso": common.iso(),
        })
        publication_id = record["id"]

    if not schedule_epoch:
        thread = threading.Thread(target=_attempt_publication, args=(publication_id,), daemon=True, name="genesis-publish")
        thread.start()
    return store.get_publication(publication_id)


def retry_publication(publication_id):
    record = store.get_publication(publication_id)
    if record.get("status") != "failed":
        raise PublishError("retry_not_allowed", "Only failed publications can be retried.", 409)
    return _start_publication(publication_id, "retry_requested")


def cancel_publication(publication_id):
    record = store.get_publication(publication_id)
    if record.get("status") != "scheduled":
        raise PublishError("cancel_not_allowed", "Only scheduled publications can be cancelled.", 409)
    store.update_publication(publication_id, {
        "status": "cancelled",
        "updated_iso": common.iso(),
        "history": (record.get("history") or []) + [{"iso": common.iso(), "event": "cancelled", "note": None}],
    })
    return store.get_publication(publication_id)


def _start_publication(publication_id, event):
    """Atomically move a scheduled/failed record into 'publishing' and start the upload."""
    with store.acquire():
        record = store.get_publication(publication_id)
        if record.get("status") not in ("scheduled", "failed"):
            raise PublishError("conflict", "Publication %s is %s." % (publication_id, record.get("status")), 409)
        store.update_publication(publication_id, {
            "status": "publishing",
            "attempts": int(record.get("attempts") or 0) + 1,
            "error": None,
            "updated_iso": common.iso(),
            "history": (record.get("history") or []) + [{"iso": common.iso(), "event": event, "note": None}],
        })
    thread = threading.Thread(target=_attempt_publication, args=(publication_id,), daemon=True, name="genesis-publish")
    thread.start()
    return store.get_publication(publication_id)


def _execution_media(project):
    """Resolve the current rendered media file, or raise a precise error."""
    media_id = project.get("media_id")
    if not media_id:
        raise PublishError("no_render", "The project has no rendered output. Complete a render first.", 409)
    media_record = studio_media.find(media_id)
    path = None
    if media_record:
        try:
            path, _ = studio_media.open_path(media_id)
        except studio_media.MediaError:
            path = None
    if media_record is None or not path or not os.path.isfile(path):
        raise PublishError(
            "render_media_missing",
            "The current rendered media for this project is missing from the library.",
            409,
        )
    return media_record, path


def _require_current_approval(project, record):
    """Re-check approval and quality review immediately before uploading."""
    approval = project.get("approval") or {}
    if not approval.get("approved"):
        raise PublishError(
            "approval_revoked",
            "The project's owner approval has been withdrawn. Publishing was stopped.",
            409,
        )
    review = project.get("quality_review") or {}
    if review.get("verdict") == "fail":
        raise PublishError(
            "quality_review_failed",
            "The current quality review failed. Publishing was stopped.",
            409,
        )
    if not project.get("media_id") or review.get("media_id") != project.get("media_id"):
        raise PublishError(
            "quality_review_stale",
            "The quality review no longer matches the current render. Re-run it before publishing.",
            409,
        )
    snapshot = record.get("review") or {}
    if snapshot.get("media_id") and snapshot.get("media_id") != project.get("media_id"):
        raise PublishError(
            "quality_review_stale",
            "The project render changed after this publication was created. Retry after a fresh review.",
            409,
        )


def _attempt_publication(publication_id):
    try:
        record = store.get_publication(publication_id)
        if record.get("status") != "publishing":
            return
        project = genesis.store.get_project(record["project_id"])
        _require_current_approval(project, record)
        media_record, path = _execution_media(project)
        result = upload_youtube(project, record.get("metadata") or {}, path)

        warnings = list(record.get("warnings") or [])
        thumbnail_id = (record.get("metadata") or {}).get("thumbnail_media_id")
        if thumbnail_id:
            thumbnail = studio_media.find(thumbnail_id)
            thumb_path = None
            if thumbnail and thumbnail.get("kind") == "image":
                try:
                    thumb_path, _ = studio_media.open_path(thumbnail_id)
                except studio_media.MediaError:
                    thumb_path = None
            if thumbnail is None or not thumb_path or not os.path.isfile(thumb_path):
                warnings.append("Thumbnail media %s is missing; the video was published without it." % thumbnail_id)
            else:
                try:
                    upload_thumbnail(result["remote_id"], thumb_path)
                except PublishError as exc:
                    warnings.append("Thumbnail upload failed: %s" % exc.message)

        store.update_publication(publication_id, {
            "status": "succeeded",
            "remote_id": result["remote_id"],
            "remote_url": result["remote_url"],
            "remote_status": result.get("remote_status"),
            "media_id": media_record.get("id"),
            "warnings": warnings,
            "published_iso": common.iso(),
            "updated_iso": common.iso(),
            "history": (record.get("history") or []) + [{
                "iso": common.iso(), "event": "succeeded",
                "note": "YouTube video %s confirmed by the API." % result["remote_id"]}],
        })
    except PublishError as exc:
        _publication_failed(publication_id, exc.code, exc.message)
        return
    except common.CosmosError as exc:
        _publication_failed(publication_id, exc.code, exc.message)
        return
    except Exception as exc:
        _publication_failed(publication_id, "publish_error", str(exc))
        return

    # The destination has already confirmed the upload. Any failure while
    # linking the result back into the project must not rewrite the successful
    # publication record as failed.
    try:
        genesis.mark_published(record["project_id"], store.get_publication(publication_id))
    except Exception as exc:
        try:
            current = store.get_publication(publication_id)
            store.update_publication(publication_id, {
                "history": (current.get("history") or []) + [{
                    "iso": common.iso(), "event": "project_link_failed",
                    "note": str(exc)[:300]}],
            })
        except common.CosmosError:
            pass


def _publication_failed(publication_id, code, message):
    try:
        record = store.get_publication(publication_id)
        store.update_publication(publication_id, {
            "status": "failed",
            "error": {"code": code, "message": message[:800]},
            "updated_iso": common.iso(),
            "history": (record.get("history") or []) + [{"iso": common.iso(), "event": "failed", "note": message[:300]}],
        })
    except common.CosmosError:
        pass


# ---------------------------------------------------------------------------
# Scheduler for scheduled publications
# ---------------------------------------------------------------------------
def _scheduler_loop():
    while True:
        time.sleep(20)
        try:
            now = time.time()
            for record in store.list_publications(status="scheduled", limit=200):
                due = record.get("schedule_at") or 0
                if due and due <= now:
                    try:
                        _start_publication(record["id"], "schedule_due")
                    except PublishError:
                        continue
        except Exception:
            continue


def start_scheduler():
    """Start the due-publication scheduler (idempotent; called by app.py)."""
    global _scheduler_started
    with _status_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="genesis-scheduler")
    thread.start()
