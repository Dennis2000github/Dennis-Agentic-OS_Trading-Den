"""YouTube domain service over the shared Composio provider adapter.

Every authorized COSMOS module reaches YouTube through this module — never
through Composio directly and never through private OAuth code.

Design:

* **Dynamic discovery.** The op table maps capability operations to Composio
  tool slugs; live availability, schemas, scopes and versions are fetched
  from the REST catalog at call time. No action counts or schemas are
  hardcoded beyond the op map itself, and deprecated tools are flagged.
* **Connection reuse.** The user's existing project-level YouTube connection
  (ACTIVE, Composio-managed OAuth) is resolved per call — no re-auth, no
  duplicate connections.
* **Guarded autonomy.** Read ops execute autonomously and are audited.
  Mutating ops create an owner approval first (see ``permissions``); the
  registered executors run only after the owner approves, through the one
  shared approval queue.
* **Honest limits.** ``uploadLimitExceeded`` maps to the documented
  channel-level daily limit — never retried, never bypassed by switching
  credentials. Upload results are never claimed successful without a real
  tool response.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse

from .. import studio_contracts as contracts
from . import composio_adapter as adapter
from . import executions
from . import permissions
from . import store

TOOLKIT = "youtube"

# The connected channel is read-only identity data, not editorial channel
# configuration. A short cache keeps COSMOS overviews from issuing a live
# YouTube call on every render; `refresh=1` deliberately bypasses it.
CHANNEL_CACHE_TTL = 120.0
_CHANNEL_CACHE = {"payload": None, "expires": 0.0}

# op -> tool mapping. Classes mirror permissions.HARD_APPROVAL naming.
_O = [
    # --- channel management -------------------------------------------------
    ("list_channels", "YOUTUBE_LIST_CHANNELS", False, "", "List channels (mine=true for the connected channel)."),
    ("channel_statistics", "YOUTUBE_GET_CHANNEL_STATISTICS", False, "", "Subscriber/view/video statistics for a channel."),
    ("channel_activities", "YOUTUBE_GET_CHANNEL_ACTIVITIES", False, "", "Recent channel activities."),
    ("channel_id_by_handle", "YOUTUBE_GET_CHANNEL_ID_BY_HANDLE", False, "", "Resolve a channel id from a handle."),
    ("update_channel", "YOUTUBE_UPDATE_CHANNEL", True, "youtube.channel_settings", "Update channel branding/metadata."),
    ("list_channel_sections", "YOUTUBE_LIST_CHANNEL_SECTIONS", False, "", "List channel sections."),
    ("create_channel_section", "YOUTUBE_CREATE_CHANNEL_SECTION", True, "youtube.channel_settings", "Create a channel section."),
    ("update_channel_section", "YOUTUBE_UPDATE_CHANNEL_SECTION", True, "youtube.channel_settings", "Update a channel section."),
    ("delete_channel_section", "YOUTUBE_DELETE_CHANNEL_SECTION", True, "youtube.delete", "Delete a channel section."),
    ("upload_channel_banner", "YOUTUBE_UPLOAD_CHANNEL_BANNER", True, "youtube.channel_settings", "Upload a channel banner image."),
    # --- video management ---------------------------------------------------
    ("upload_video", "YOUTUBE_UPLOAD_VIDEO", True, "youtube.upload", "Upload a video (local videoFilePath; any privacy still needs approval)."),
    ("multipart_upload_video", "YOUTUBE_MULTIPART_UPLOAD_VIDEO", True, "youtube.upload", "Multipart video upload variant."),
    ("update_video", "YOUTUBE_UPDATE_VIDEO", True, "youtube.video_update", "Update video metadata (title, description, tags, category, privacy)."),
    ("delete_video", "YOUTUBE_DELETE_VIDEO", True, "youtube.delete", "Delete a video."),
    ("video_details", "YOUTUBE_GET_VIDEO_DETAILS_BATCH", False, "", "Fetch details for a batch of video ids."),
    ("list_channel_videos", "YOUTUBE_LIST_CHANNEL_VIDEOS", False, "", "List the channel's uploads."),
    ("most_popular_videos", "YOUTUBE_LIST_MOST_POPULAR_VIDEOS", False, "", "Chart/most-popular videos."),
    ("search_videos", "YOUTUBE_SEARCH_YOU_TUBE", False, "", "Search YouTube."),
    ("video_rating", "YOUTUBE_GET_VIDEO_RATING", False, "", "Get the authenticated user's rating of videos."),
    ("rate_video", "YOUTUBE_RATE_VIDEO", True, "youtube.comment", "Like/dislike a video."),
    ("update_thumbnail", "YOUTUBE_UPDATE_THUMBNAIL", True, "youtube.video_update", "Set a video thumbnail (local imageFilePath)."),
    # --- playlists ----------------------------------------------------------
    ("create_playlist", "YOUTUBE_CREATE_PLAYLIST", True, "youtube.playlist_write", "Create a playlist."),
    ("list_user_playlists", "YOUTUBE_LIST_USER_PLAYLISTS", False, "", "List the authenticated user's playlists."),
    ("playlists_by_id", "YOUTUBE_GET_PLAYLISTS_BY_ID", False, "", "Fetch playlists by id."),
    ("update_playlist", "YOUTUBE_UPDATE_PLAYLIST", True, "youtube.playlist_write", "Rename/re-describe a playlist."),
    ("delete_playlist", "YOUTUBE_DELETE_PLAYLIST", True, "youtube.delete", "Delete a playlist."),
    ("add_video_to_playlist", "YOUTUBE_ADD_VIDEO_TO_PLAYLIST", True, "youtube.playlist_write", "Add a video to a playlist."),
    ("list_playlist_items", "YOUTUBE_LIST_PLAYLIST_ITEMS", False, "", "List a playlist's items."),
    ("update_playlist_item", "YOUTUBE_UPDATE_PLAYLIST_ITEM", True, "youtube.playlist_write", "Change a playlist item position/privacy."),
    ("delete_playlist_item", "YOUTUBE_DELETE_PLAYLIST_ITEM", True, "youtube.delete", "Remove a video from a playlist."),
    ("list_playlist_images", "YOUTUBE_LIST_PLAYLIST_IMAGES", False, "", "List playlist images."),
    # --- comments / engagement ---------------------------------------------
    ("list_comments", "YOUTUBE_LIST_COMMENTS", False, "", "List replies to a comment."),
    ("list_comment_threads", "YOUTUBE_LIST_COMMENT_THREADS2", False, "", "List comment threads (current thread action)."),
    ("reply_comment", "YOUTUBE_CREATE_COMMENT_REPLY", True, "youtube.comment", "Reply to a comment (needs owner approval)."),
    ("post_comment", "YOUTUBE_POST_COMMENT", True, "youtube.comment", "Post a top-level comment (needs owner approval)."),
    ("update_comment", "YOUTUBE_UPDATE_COMMENT", True, "youtube.comment", "Update an existing comment."),
    ("delete_comment", "YOUTUBE_DELETE_COMMENT", True, "youtube.delete", "Delete a comment."),
    ("mark_comment_spam", "YOUTUBE_MARK_COMMENT_AS_SPAM", True, "youtube.moderation", "Mark a comment as spam (catalog-deprecated; prefer moderate_comment)."),
    ("moderate_comment", "YOUTUBE_SET_COMMENT_MODERATION_STATUS", True, "youtube.moderation", "Set moderation status for a comment."),
    # --- captions -----------------------------------------------------------
    ("list_caption_tracks", "YOUTUBE_LIST_CAPTION_TRACK", False, "", "List caption tracks for a video."),
    ("load_captions", "YOUTUBE_LOAD_CAPTIONS", False, "", "Download a caption track (force-ssl scope required)."),
    ("upload_caption_track", "YOUTUBE_UPLOAD_CAPTION_TRACK", True, "youtube.captions", "Upload a caption track."),
    ("upload_caption_media", "YOUTUBE_UPLOAD_CAPTION_MEDIA", True, "youtube.captions", "Upload caption media content."),
    ("update_caption", "YOUTUBE_UPDATE_CAPTION", True, "youtube.captions", "Update a caption track."),
    ("delete_caption_track", "YOUTUBE_DELETE_CAPTION_TRACK", True, "youtube.delete", "Delete a caption track."),
    # --- analytics & reporting ---------------------------------------------
    ("analytics", "YOUTUBE_QUERY_ANALYTICS", False, "", "Query YouTube Analytics (metrics, dimensions, date range)."),
    ("reporting_jobs", "YOUTUBE_LIST_REPORTING_JOBS", False, "", "List reporting jobs."),
    ("job_reports", "YOUTUBE_LIST_JOB_REPORTS", False, "", "List reports for a reporting job."),
    ("report_types", "YOUTUBE_LIST_REPORT_TYPES", False, "", "List available report types."),
    # --- live streaming -----------------------------------------------------
    ("create_live_broadcast", "YOUTUBE_CREATE_LIVE_BROADCAST", True, "youtube.live", "Create a live broadcast."),
    ("create_live_stream", "YOUTUBE_CREATE_LIVE_STREAM", True, "youtube.live", "Create a live stream."),
    ("bind_live_broadcast", "YOUTUBE_BIND_LIVE_BROADCAST", True, "youtube.live", "Bind a broadcast to a stream."),
    ("list_live_broadcasts", "YOUTUBE_LIST_LIVE_BROADCASTS", False, "", "List live broadcasts."),
    ("list_live_streams", "YOUTUBE_LIST_LIVE_STREAMS", False, "", "List live streams."),
    ("transition_live_broadcast", "YOUTUBE_TRANSITION_LIVE_BROADCAST", True, "youtube.live", "Transition broadcast state (testing/live/complete)."),
    ("update_live_broadcast", "YOUTUBE_UPDATE_LIVE_BROADCAST", True, "youtube.live", "Update a live broadcast."),
    ("delete_live_broadcast", "YOUTUBE_DELETE_LIVE_BROADCAST", True, "youtube.live", "Delete a live broadcast."),
    ("list_live_chat_messages", "YOUTUBE_LIST_LIVE_CHAT_MESSAGES", False, "", "List live chat messages."),
    # --- other capabilities -------------------------------------------------
    ("list_video_categories", "YOUTUBE_LIST_VIDEO_CATEGORIES", False, "", "List video categories."),
    ("list_i18n_languages", "YOUTUBE_LIST_I18N_LANGUAGES", False, "", "List supported application languages."),
    ("list_i18n_regions", "YOUTUBE_LIST_I18N_REGIONS", False, "", "List supported regions."),
    ("list_rated_videos", "YOUTUBE_LIST_RATED_VIDEOS", False, "", "Videos the user rated."),
    ("list_user_subscriptions", "YOUTUBE_LIST_USER_SUBSCRIPTIONS", False, "", "List the user's subscriptions."),
    ("subscribe_channel", "YOUTUBE_SUBSCRIBE_CHANNEL", True, "youtube.subscription", "Subscribe the account to a channel."),
    ("unsubscribe_channel", "YOUTUBE_UNSUBSCRIBE_CHANNEL", True, "youtube.subscription", "Unsubscribe from a channel."),
    ("list_super_chat_events", "YOUTUBE_LIST_SUPER_CHAT_EVENTS", False, "", "List Super Chat events."),
    ("list_video_abuse_reasons", "YOUTUBE_LIST_VIDEO_ABUSE_REPORT_REASONS", False, "", "List abuse report reasons."),
    ("report_video_abuse", "YOUTUBE_REPORT_VIDEO_ABUSE", True, "youtube.moderation", "Report a video for abuse."),
]

OPS = {
    op: {"slug": slug, "mutating": mutating, "action_class": action_class, "summary": summary}
    for op, slug, mutating, action_class, summary in _O
}
READ_OPS = frozenset(op for op, spec in OPS.items() if not spec["mutating"])
WRITE_OPS = frozenset(op for op, spec in OPS.items() if spec["mutating"])

# Upload-style ops that reference local files: validated before anything runs.
_FILE_ARGS = {
    "upload_video": ("videoFilePath",),
    "multipart_upload_video": ("videoFilePath",),
    "update_thumbnail": ("imageFilePath",),
    "upload_channel_banner": ("imageFilePath",),
}


class YoutubeError(store.IntegrationError):
    pass


def _audit(op, spec, status, *, target=None, error=None, agent=None, module=None,
           approval_id=None, detail=None, started_at=None, finished_at=None):
    return executions.record(
        "composio", op, status,
        domain=TOOLKIT,
        tool=spec["slug"],
        target=target,
        agent=agent,
        module=module,
        approval_id=approval_id,
        error=error,
        detail=detail,
        started_at=started_at,
        finished_at=finished_at,
    )


def _validate_local_files(op, arguments):
    """Upload paths must exist locally before approval or execution."""
    for key in _FILE_ARGS.get(op, ()):
        value = arguments.get(key)
        if not value:
            raise YoutubeError("missing_argument", "%s is required for %s." % (key, op))
        path = os.path.expanduser(str(value))
        if not os.path.isfile(path):
            raise YoutubeError(
                "file_not_found",
                "%s does not point to a readable local file: %s" % (key, path),
            )


def _normalize_op_args(op, arguments):
    """Validate arguments against the LIVE tool schema before executing."""
    spec = OPS[op]
    schema = adapter.tool_schema(spec["slug"])
    params = ((schema.get("input_parameters") or {}).get("properties")) or {}
    if not isinstance(arguments, dict):
        raise YoutubeError("invalid_arguments", "Arguments must be an object.")
    unknown = [k for k in arguments if params and k not in params]
    if unknown:
        raise YoutubeError(
            "unknown_argument",
            "%s does not accept: %s" % (spec["slug"], ", ".join(sorted(unknown))[:200]),
        )
    return schema


def discover():
    """Live availability of every op: slug, deprecated flag, schema presence."""
    tools = {}
    try:
        for tool in adapter.toolkit_tools(TOOLKIT, limit=100):
            # The live catalog nests a summary object under `deprecated`;
            # `is_deprecated` is the actual boolean flag.
            tools[tool.get("slug")] = {
                "deprecated": bool(tool.get("is_deprecated")),
                "version": tool.get("version"),
            }
    except adapter.ComposioAdapterError as exc:
        return {"ops": [], "available": 0, "warning": str(exc.message), "code": exc.code}
    rows = []
    for op, spec in OPS.items():
        info = tools.get(spec["slug"])
        rows.append({
            "op": op,
            "slug": spec["slug"],
            "mutating": spec["mutating"],
            "action_class": spec["action_class"],
            "summary": spec["summary"],
            "available": info is not None,
            "deprecated": bool(info.get("deprecated")) if info else None,
            "version": info.get("version") if info else None,
        })
    available = sum(1 for r in rows if r["available"])
    return {
        "ops": rows,
        "available": available,
        "total": len(rows),
        "warning": "" if available else "No YouTube tools are currently available in the live catalog.",
    }


def connection_state():
    """Project connection + session execution transport state, honestly."""
    state = {"connection": None, "session": None, "execution_available": False,
             "blocked_reason": ""}
    try:
        conn = adapter.resolve_connection(TOOLKIT)
        state["connection"] = {
            "id": conn.get("id"),
            "status": conn.get("status"),
            "created_at": conn.get("created_at"),
        }
    except adapter.ComposioAdapterError as exc:
        state["blocked_reason"] = "%s: %s" % (exc.code, exc.message)
        return state
    try:
        session = adapter.session_connection_state(TOOLKIT)
        state["session"] = session
        state["execution_available"] = session.get("status") == "active"
        if not state["execution_available"]:
            state["blocked_reason"] = (
                "The tool-router session does not see the project connection on the "
                "current Composio tier; the owner must open the session connect link once "
                "(POST /api/integrations/youtube/session/link) or upgrade the tier."
            )
    except adapter.ComposioAdapterError as exc:
        state["session"] = {"status": "unknown"}
        state["blocked_reason"] = "%s: %s" % (exc.code, exc.message)
    return state


def _identity_text(value, limit):
    try:
        return store.clean_text(value, "Channel identity", limit) or None
    except store.IntegrationError:
        return None


def _avatar_url(value):
    text = _identity_text(value, 500)
    if not text:
        return None
    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return None
    return text if parsed.scheme == "https" else None


def _channel_items(data):
    """Find the YouTube `items` array inside known response envelopes."""
    cursor = data
    for _ in range(4):
        if isinstance(cursor, str):
            try:
                cursor = json.loads(cursor)
            except ValueError:
                return []
        if not isinstance(cursor, dict):
            return []
        if isinstance(cursor.get("response"), (dict, str)):
            cursor = cursor["response"]
            continue
        if isinstance(cursor.get("items"), list):
            return cursor["items"]
        nested = cursor.get("data")
        if isinstance(nested, (dict, str)) and nested is not cursor:
            cursor = nested
            continue
        return []
    return []


def _parse_connected_channel(item):
    if not isinstance(item, dict):
        return {"id": None, "name": None, "handle": None, "avatar_url": None}
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    localized = snippet.get("localized") if isinstance(snippet.get("localized"), dict) else {}
    thumbnails = snippet.get("thumbnails") if isinstance(snippet.get("thumbnails"), dict) else {}
    avatar = None
    for key in ("high", "medium", "default"):
        thumbnail = thumbnails.get(key)
        if isinstance(thumbnail, dict):
            avatar = _avatar_url(thumbnail.get("url"))
            if avatar:
                break
    return {
        "id": _identity_text(item.get("id"), 80),
        "name": _identity_text(localized.get("title") or snippet.get("title"), 160),
        "handle": _identity_text(snippet.get("customUrl"), 80),
        "avatar_url": avatar,
    }


def _channel_payload(status, error=None, connection_id=None):
    return {
        "status": status,
        "id": None,
        "name": None,
        "handle": None,
        "avatar_url": None,
        "connection_id": connection_id,
        "checked_at": store.iso(),
        "source": "composio.youtube.list_channels",
        "error": _identity_text(error, 400),
    }


def connected_channel(refresh=False):
    """Resolve the actual channel behind the shared YouTube connection.

    Returns a small, honest projection for COSMOS. The local Studio channel
    profile is editorial data and is never used as the live identity source.
    """
    cached = _CHANNEL_CACHE.get("payload")
    if not refresh and cached and store.now() < _CHANNEL_CACHE.get("expires", 0):
        return dict(cached)

    payload = _channel_payload("unavailable")
    try:
        state = connection_state()
        connection_id = (state.get("connection") or {}).get("id")
        payload["connection_id"] = _identity_text(connection_id, 80)
        if not state.get("execution_available"):
            payload["status"] = "not_connected"
            payload["error"] = state.get("blocked_reason") or "YouTube is not connected."
        else:
            result = run(
                "list_channels",
                {"mine": True, "part": "snippet,statistics"},
                agent="cosmos",
                module="integrations.youtube.channel_identity",
            )
            result_status = result.get("status")
            if result_status == "execution_blocked":
                payload["status"] = "not_connected"
                payload["error"] = result.get("blocked_reason") or "YouTube is not connected."
                payload["connection_id"] = _identity_text(
                    (result.get("connection") or {}).get("id"), 80
                ) or payload["connection_id"]
            elif result_status != "ok":
                payload["error"] = result.get("error") or "The channel could not be read."
            else:
                items = _channel_items(result.get("data"))
                channel = _parse_connected_channel(items[0]) if items else {}
                if channel.get("id") and channel.get("name"):
                    payload.update({"status": "connected", "error": None, **channel})
                else:
                    payload["error"] = (
                        "The connected account returned no usable channel profile."
                    )
    except Exception as exc:  # noqa: BLE001 - identity must degrade honestly
        payload["status"] = "unavailable"
        payload["error"] = _identity_text(str(exc), 400) or "Channel identity unavailable."

    payload["checked_at"] = store.iso()
    with store.lock():
        _CHANNEL_CACHE["payload"] = dict(payload)
        _CHANNEL_CACHE["expires"] = store.now() + CHANNEL_CACHE_TTL
    return dict(payload)


def op_schema(op):
    spec = _spec(op)
    return adapter.tool_schema(spec["slug"])


def _spec(op):
    if op not in OPS:
        raise YoutubeError("op_unknown", "Unknown YouTube operation: %s" % op, 404)
    return OPS[op]


def run(op, arguments=None, *, agent=None, module=None):
    """Run one YouTube operation through the shared gate.

    Read ops execute immediately and are audited. Mutating ops never execute
    here: they create a pending owner approval and return it. After approval,
    the registered executor performs the audited execution.
    """
    spec = _spec(op)
    arguments = dict(arguments or {})
    decision = permissions.classify("composio", TOOLKIT, spec["action_class"] or op, spec["mutating"])
    if not decision["approval_required"]:
        return _execute_read(op, spec, arguments, agent=agent, module=module)
    _validate_local_files(op, arguments)
    _normalize_op_args(op, arguments)  # schema-checked BEFORE approval, fail early
    preview = {
        "action": op,
        "provider": "composio",
        "tool": spec["slug"],
        "arguments": contracts.redact(arguments),
        "reason": decision["reason"],
        "risk": "integration-action",
    }
    approval = permissions.request_approval(
        "integration.youtube." + op, arguments, preview,
        reason="YouTube %s requires owner approval." % op,
    )
    return {
        "status": "approval_required",
        "op": op,
        "tool": spec["slug"],
        "approval": approval,
        "message": "The owner must approve this action before it runs.",
    }


def _execute_read(op, spec, arguments, *, agent=None, module=None):
    state = connection_state()
    if not state["execution_available"]:
        return {
            "status": "execution_blocked",
            "op": op,
            "tool": spec["slug"],
            "blocked_reason": state["blocked_reason"],
            "connection": state["connection"],
            "session": state["session"],
        }
    schema = _normalize_op_args(op, arguments)
    started = store.now()
    try:
        data = adapter.execute(spec["slug"], arguments,
                               thought="COSMOS read-only YouTube operation: %s" % op)
    except adapter.ComposioAdapterError as exc:
        _audit(op, spec, "failed", target=_target(arguments), agent=agent, module=module,
               error={"code": exc.code, "message": exc.message},
               detail={"arguments": contracts.redact(arguments)},
               started_at=started, finished_at=store.now())
        raise YoutubeError(exc.code, exc.message, exc.status)
    row = _audit(op, spec, "succeeded", target=_target(arguments), agent=agent, module=module,
                 detail={"arguments": contracts.redact(arguments)},
                 started_at=started, finished_at=store.now())
    return {"status": "ok", "op": op, "data": data, "audit_id": row["id"],
            "schema_version": schema.get("version")}


def _execute_write(op, arguments, approval_id=None, *, agent=None, module=None):
    """Executor path: runs ONLY after owner approval (shared queue dispatch)."""
    spec = _spec(op)
    if not permissions.approval_decided(approval_id):
        raise YoutubeError("approval_required", "This action has not been approved.", 409)
    state = connection_state()
    if not state["execution_available"]:
        raise YoutubeError("execution_blocked", state["blocked_reason"], 409)
    _validate_local_files(op, arguments)
    _normalize_op_args(op, arguments)
    started = store.now()
    try:
        data = adapter.execute(spec["slug"], arguments,
                               thought="COSMOS approved YouTube operation: %s" % op)
    except adapter.ComposioAdapterError as exc:
        message = str(exc.message)
        if "uploadLimitExceeded" in message:
            # Documented channel-level daily upload limit: never retried, never
            # bypassed by switching credentials.
            code, status = "channel_upload_limit", 429
        else:
            code, status = exc.code, exc.status
        _audit(op, spec, "failed", target=_target(arguments), agent=agent, module=module,
               approval_id=approval_id, error={"code": code, "message": message},
               detail={"arguments": contracts.redact(arguments)},
               started_at=started, finished_at=store.now())
        raise YoutubeError(code, message, status)
    row = _audit(op, spec, "succeeded", target=_target(arguments), agent=agent, module=module,
                 approval_id=approval_id, detail={"arguments": contracts.redact(arguments),
                                                  "result": contracts.redact(data)},
                 started_at=started, finished_at=store.now())
    return {"status": "ok", "op": op, "data": contracts.redact(data), "audit_id": row["id"]}


def _target(arguments):
    """Small bounded target reference for audit rows."""
    for key in ("videoId", "playlistId", "channelId", "commentId", "captionId",
                "videoFilePath", "forHandle", "id"):
        if isinstance(arguments, dict) and arguments.get(key):
            value = str(arguments[key])
            return {key: value[:160]}
    return None


def register_executors():
    """Register every write op with the shared approval queue."""
    for op in WRITE_OPS:
        def _executor(raw_args, _op=op):
            args = dict(raw_args or {})
            approval_id = args.pop("__approval_id__", None)
            return _execute_write(_op, args, approval_id=approval_id)
        permissions.register_executor("integration.youtube." + op, _executor)


register_executors()
