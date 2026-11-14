"""HTTP dispatcher for YouTube Studio editorial routes.

``app.py`` forwards every ``/api/studio/youtube/*`` request here with the
already-authenticated context. The dispatcher is pure: it takes
``(method, subpath, query, body)`` and returns ``(status, payload)`` so it is
directly unit-testable without the server.
"""

from __future__ import annotations

from . import studio_llm
from . import youtube_channels as channels
from . import youtube_common as common
from . import youtube_research as research
from . import youtube_scripts as scripts
from . import youtube_topics as topics

PREFIX = "/api/studio/youtube"


def _fail(exc):
    if isinstance(exc, common.YoutubeError):
        return exc.status, {"error": exc.message, "code": exc.code}
    return 500, {"error": common.clean_text(str(exc), "Error", 400) or "unexpected_error", "code": "youtube_error"}


def _query(query, name, default=None):
    try:
        values = query.get(name, [default])
        return values[0] if values else default
    except AttributeError:
        return default


def _active_channel_id(body=None, query=None):
    explicit = (body or {}).get("channel_id") if isinstance(body, dict) else None
    explicit = explicit or _query(query or {}, "channel_id")
    if explicit:
        channels.get_channel(explicit)
        return explicit
    return channels.active_channel()["id"]


def handle(method, path, query=None, body=None):
    """Route one YouTube Studio API call. Returns ``(status, payload)``."""
    subpath = path[len(PREFIX):].strip("/")
    parts = [part for part in subpath.split("/") if part]
    try:
        if method == "GET":
            return _get(parts, query or {})
        if method == "POST":
            return _post(parts, body or {})
        if method == "PATCH":
            return _patch(parts, body or {})
        if method == "DELETE":
            return _delete(parts)
        return 405, {"error": "Method not allowed.", "code": "method_not_allowed"}
    except common.YoutubeError as exc:
        return _fail(exc)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(exc)


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------
def _get(parts, query):
    if not parts or parts[0] == "overview":
        return _overview()
    head = parts[0]

    if head == "llm" and len(parts) == 2 and parts[1] == "status":
        return 200, studio_llm.status()

    if head == "channels":
        if len(parts) == 1:
            return 200, channels.list_channels(include_archived=_query(query, "include_archived") in ("1", "true"))
        channel_id = parts[1]
        if len(parts) == 2:
            return 200, {"channel": channels.get_channel(channel_id)}
        if parts[2] == "prompt":
            return 200, channels.current_prompt(channel_id)
        if parts[2] == "prompt-versions":
            return 200, channels.list_prompt_versions(channel_id)
        if parts[2] == "agent-prompts":
            return 200, channels.list_agent_prompts(channel_id)
        if parts[2] == "compose" and len(parts) == 4:
            context = _query(query, "context") or ""
            return 200, channels.compose_agent_prompt(channel_id, parts[3], context)

    if head == "research":
        if len(parts) == 1:
            return 200, research.list_validations(
                _active_channel_id(query=query),
                pillar=_query(query, "pillar"),
                verdict=_query(query, "verdict"),
                query=_query(query, "q"),
            )
        if len(parts) == 2:
            return 200, {"validation": research.get_validation(_active_channel_id(query=query), parts[1])}
        if len(parts) == 2 and parts[1] == "rules":
            return 200, research.validation_rules()

    if head == "competitors":
        if len(parts) == 1:
            return 200, research.list_competitors(query=_query(query, "q"))
        if len(parts) == 2:
            return 200, {"competitor": research.get_competitor(parts[1])}

    if head == "topics":
        if len(parts) == 1:
            return 200, topics.list_topics(
                _active_channel_id(query=query),
                status=_query(query, "status"),
                pillar=_query(query, "pillar"),
                priority=_query(query, "priority"),
                min_score=_query(query, "min_score"),
                query=_query(query, "q"),
            )
        if len(parts) == 2:
            return 200, {"topic": topics.get_topic(_active_channel_id(query=query), parts[1])}
        if len(parts) == 3 and parts[2] == "duplicates":
            title = _query(query, "title") or ""
            return 200, {"duplicates": topics.find_duplicates(_active_channel_id(query=query), title)}

    if head == "scripts":
        if len(parts) == 1:
            return 200, scripts.list_scripts(
                _active_channel_id(query=query),
                status=_query(query, "status"),
                topic_id=_query(query, "topic_id"),
                query=_query(query, "q"),
            )
        if len(parts) == 2:
            return 200, {"script": scripts.get_script(_active_channel_id(query=query), parts[1])}
        if len(parts) == 3 and parts[2] == "versions":
            return 200, scripts.list_versions(_active_channel_id(query=query), parts[1])

    return 404, {"error": "YouTube Studio endpoint not found.", "code": "not_found"}


def _overview():
    channel = channels.active_channel()
    research_data = research.list_validations(channel["id"])
    topics_data = topics.list_topics(channel["id"])
    scripts_data = scripts.list_scripts(channel["id"])
    return 200, {
        "channel": channel,
        "llm": studio_llm.status(),
        "research": {
            "total": research_data["total"],
            "pass": sum(1 for v in research_data["validations"] if v.get("verdict") == "pass"),
            "review": sum(1 for v in research_data["validations"] if v.get("verdict") == "review"),
            "fail": sum(1 for v in research_data["validations"] if v.get("verdict") == "fail"),
        },
        "topics": topics_data["counts"],
        "scripts": scripts_data["counts"],
        "rules": {
            "validation": research.validation_rules(),
            "statuses": topics_data["statuses"],
            "backlog_target": topics_data["backlog_target"],
        },
    }


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------
def _post(parts, body):
    if not parts:
        return 404, {"error": "YouTube Studio endpoint not found.", "code": "not_found"}
    head = parts[0]

    if head == "channels":
        if len(parts) == 1:
            return 200, {"channel": channels.create_channel(body)}
        channel_id = parts[1]
        if len(parts) == 2 and body.get("action") == "activate":
            return 200, channels.activate_channel(channel_id)
        if len(parts) == 3 and parts[2] == "prompt":
            return 200, channels.add_prompt_version(channel_id, body)
        if len(parts) == 4 and parts[2] == "prompt" and parts[3] == "restore":
            return 200, channels.restore_prompt_version(channel_id, body.get("version_id"), body.get("note"))
        if len(parts) == 4 and parts[2] == "prompt" and parts[3] == "duplicate":
            return 200, channels.duplicate_prompt_version(
                channel_id, body.get("version_id"), body.get("to_channel_id"), body.get("note")
            )
        if len(parts) == 4 and parts[2] == "prompt" and parts[3] == "set-default":
            return 200, channels.set_default_prompt_version(channel_id, body.get("version_id"))
        if len(parts) == 3 and parts[2] == "agent-prompts":
            return 200, channels.update_agent_prompt(channel_id, body.get("role"), body.get("body"))

    if head == "research":
        if len(parts) == 1:
            return 200, {"validation": research.create_validation(_active_channel_id(body), body)}
        if len(parts) == 3 and parts[2] == "to-topic":
            validation = research.get_validation(_active_channel_id(body), parts[1])
            overrides = body.get("topic") if isinstance(body.get("topic"), dict) else {}
            payload = {
                "title": overrides.get("title") or validation.get("subject"),
                "angle": overrides.get("angle"),
                "audience_question": overrides.get("audience_question"),
                "pillar": overrides.get("pillar") or validation.get("pillar"),
                "cluster": overrides.get("cluster"),
                "priority": overrides.get("priority"),
                "validation_id": validation["id"],
                "notes": overrides.get("notes"),
            }
            topic = topics.create_topic(_active_channel_id(body), payload, force=common.clean_bool(body.get("force")))
            return 200, {"topic": topic}

    if head == "competitors":
        if len(parts) == 1:
            return 200, {"competitor": research.create_competitor(body)}
        if len(parts) == 3 and parts[2] == "observations":
            return 200, {"competitor": research.add_observation(parts[1], body)}
        if len(parts) == 4 and parts[2] == "observations" and parts[3] == "delete":
            return 200, {"competitor": research.delete_observation(parts[1], body.get("observation_id"))}

    if head == "topics":
        if len(parts) == 1:
            topic = topics.create_topic(
                _active_channel_id(body), body, force=common.clean_bool(body.get("force"))
            )
            return 200, {"topic": topic}
        if len(parts) == 3 and parts[2] == "status":
            return 200, {"topic": topics.set_status(
                _active_channel_id(body), parts[1], body.get("status"), body.get("reason") or False
            )}

    if head == "scripts":
        if len(parts) == 2 and parts[1] == "generate":
            return 200, {"script": scripts.generate_script(_active_channel_id(body), body)}
        if len(parts) == 1:
            return 200, {"script": scripts.create_script(_active_channel_id(body), body)}
        if len(parts) == 3 and parts[2] == "status":
            return 200, {"script": scripts.set_status(_active_channel_id(body), parts[1], body.get("status"))}
        if len(parts) == 4 and parts[2] == "versions" and parts[3] == "restore":
            return 200, {"script": scripts.restore_version(_active_channel_id(body), parts[1], body.get("version_id"))}

    return 404, {"error": "YouTube Studio endpoint not found.", "code": "not_found"}


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------
def _patch(parts, body):
    if not parts:
        return 404, {"error": "YouTube Studio endpoint not found.", "code": "not_found"}
    head = parts[0]
    if head == "channels" and len(parts) == 2:
        return 200, {"channel": channels.update_channel(parts[1], body)}
    if head == "research" and len(parts) == 2:
        return 200, {"validation": research.update_validation(_active_channel_id(body), parts[1], body)}
    if head == "competitors" and len(parts) == 2:
        return 200, {"competitor": research.update_competitor(parts[1], body)}
    if head == "topics" and len(parts) == 2:
        return 200, {"topic": topics.update_topic(_active_channel_id(body), parts[1], body)}
    if head == "scripts" and len(parts) == 2:
        return 200, {"script": scripts.update_script(
            _active_channel_id(body), parts[1], body, note=(body or {}).get("revision_note")
        )}
    return 404, {"error": "YouTube Studio endpoint not found.", "code": "not_found"}


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------
def _delete(parts):
    if not parts:
        return 404, {"error": "YouTube Studio endpoint not found.", "code": "not_found"}
    head = parts[0]
    if head == "channels" and len(parts) == 2:
        return 200, channels.delete_channel(parts[1])
    if head == "research" and len(parts) == 2:
        return 200, research.delete_validation(_active_channel_id(), parts[1])
    if head == "competitors" and len(parts) == 2:
        return 200, research.delete_competitor(parts[1])
    if head == "topics" and len(parts) == 2:
        return 200, topics.delete_topic(_active_channel_id(), parts[1])
    if head == "scripts" and len(parts) == 2:
        return 200, scripts.delete_script(_active_channel_id(), parts[1])
    return 404, {"error": "YouTube Studio endpoint not found.", "code": "not_found"}
