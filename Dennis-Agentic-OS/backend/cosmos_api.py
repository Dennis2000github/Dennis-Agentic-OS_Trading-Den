"""HTTP dispatcher for the COSMOS intelligence API.

``app.py`` forwards every ``/api/cosmos/*`` request here with the already
authenticated context. The dispatcher is pure: ``(method, path, query, body)``
returns ``(status, payload)`` so it is directly unit-testable without the
server.
"""

from __future__ import annotations

from . import ads_backlog
from . import cosmos_common as common
from . import cosmos_backlog
from . import cosmos_config
from . import cosmos_engine
from . import cosmos_llm
from . import cosmos_registry
from . import cosmos_store
from . import genesis
from . import genesis_api
from . import genesis_store
from . import studio_llm
from . import studio_workspaces
from . import youtube_channels
from . import youtube_common as youtube_common
from . import youtube_scripts
from . import youtube_topics

PREFIX = "/api/cosmos"


def _query(query, name, default=None):
    try:
        values = query.get(name, [default])
        return values[0] if values else default
    except AttributeError:
        return default


def _fail(exc):
    if isinstance(exc, common.CosmosError):
        payload = {"error": exc.message, "code": exc.code}
        execution_id = getattr(exc, "execution_id", None)
        recommendation_id = getattr(exc, "recommendation_id", None)
        if execution_id:
            payload["execution_id"] = execution_id
        if recommendation_id:
            payload["recommendation_id"] = recommendation_id
        return exc.status if 400 <= exc.status <= 599 else 400, payload
    return 500, {"error": common.clean_text(str(exc), "Error", 500) or "cosmos_error", "code": "cosmos_error"}


def handle(method, path, query=None, body=None):
    subpath = path[len(PREFIX):].strip("/")
    # The GENESIS production dashboard owns the /genesis subtree of COSMOS.
    # It is a system-level workflow (production, rendering, publishing), not
    # an intelligence agent, so it dispatches to its own pure handler.
    if subpath == "genesis" or subpath.startswith("genesis/"):
        return genesis_api.handle(method, path, query, body)
    parts = [part for part in subpath.split("/") if part]
    try:
        if method == "GET":
            return _get(parts, query or {})
        if method == "POST":
            return _post(parts, body or {}, query or {})
        if method == "PATCH":
            return _patch(parts, body or {})
        if method == "DELETE":
            return _delete(parts, query or {})
        return 405, {"error": "Method not allowed.", "code": "method_not_allowed"}
    except Exception as exc:
        return _fail(exc)


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------
def _llm_status():
    try:
        return studio_llm.status()
    except Exception as exc:
        return {"agent_id": None, "model": None, "configured": False, "error": str(exc)}


def _channels():
    try:
        return youtube_channels.list_channels().get("channels", [])
    except Exception:
        return []


def _youtube_summary():
    try:
        channel = youtube_channels.active_channel()
        topics = youtube_topics.list_topics(channel["id"])
        return {
            "channel_id": channel["id"],
            "channel_name": channel.get("name"),
            "counts": topics.get("counts") or {},
            "total": topics.get("total") or 0,
            "topics": [
                {
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "priority": item.get("priority"),
                    "created_iso": item.get("created_iso"),
                }
                for item in (topics.get("topics") or [])[:20]
            ],
        }
    except Exception as exc:
        return {"channel_id": None, "counts": {}, "total": 0, "topics": [], "error": str(exc)}


def _channel_id(query=None, body=None):
    """Resolve the channel for a COSMOS backlog call.

    Explicit ids are validated against the channel store; otherwise the
    active channel is used. A missing channel is a real configuration gap,
    not an empty backlog.
    """
    explicit = body.get("channel_id") if isinstance(body, dict) else None
    explicit = explicit or _query(query or {}, "channel_id")
    if explicit:
        try:
            youtube_channels.get_channel(explicit)
        except Exception:
            raise common.CosmosError("channel_not_found", "YouTube channel not found.", 404)
        return explicit
    try:
        return youtube_channels.active_channel()["id"]
    except Exception:
        raise common.CosmosError(
            "channel_required", "No active YouTube channel is configured.", 400
        )


def _script_summary(channel_id, topic):
    script_id = topic.get("script_id")
    if not script_id:
        return None
    try:
        script = youtube_scripts.get_script(channel_id, script_id)
    except Exception:
        return {"id": script_id, "missing": True, "title": None, "status": None, "sections": 0}
    return {
        "id": script.get("id"),
        "title": script.get("title"),
        "status": script.get("status"),
        "sections": len(script.get("sections") or []),
        "word_count": script.get("word_count"),
        "missing": False,
    }


def _topic_row(channel_id, topic, active_index=None):
    """Add the cross-store context the COSMOS backlog UI needs.

    The topic record stays authoritative; script, Studio project and GENESIS
    production data are read from their own stores and reported honestly
    (``None``/``missing`` when the linked record no longer exists).
    """
    row = dict(topic)
    script = _script_summary(channel_id, topic)
    row["script"] = script
    row["script_title"] = (script or {}).get("title")
    row["script_status"] = (script or {}).get("status")
    row["script_sections"] = (script or {}).get("sections") or 0

    studio_project = None
    if topic.get("project_id"):
        try:
            studio_project = studio_workspaces.get_project("youtube", topic["project_id"])
        except Exception:
            studio_project = None
    row["studio_project"] = (
        {
            "id": studio_project.get("id"),
            "title": studio_project.get("title"),
            "status": studio_project.get("status"),
            "stage": studio_project.get("stage"),
        }
        if studio_project
        else None
    )

    active = (active_index or {}).get(topic.get("id"))
    row["genesis_project_id"] = active.get("id") if active else None
    row["genesis_project_status"] = active.get("status") if active else None
    row["has_active_production"] = bool(active)
    return row


def _youtube_backlog(query):
    channel_id = _channel_id(query=query)
    listed = youtube_topics.list_topics(
        channel_id,
        status=_query(query, "status"),
        pillar=_query(query, "pillar"),
        priority=_query(query, "priority"),
        min_score=_query(query, "min_score"),
        query=_query(query, "q"),
    )
    active = genesis.active_source_index("youtube_topic")
    topics = [_topic_row(channel_id, topic, active) for topic in listed.get("topics") or []]
    try:
        requested = int(_query(query, "limit", 200) or 200)
    except (TypeError, ValueError):
        requested = 200
    requested = max(1, min(requested, 500))
    channel = None
    try:
        channel = youtube_channels.get_channel(channel_id)
    except Exception:
        channel = None
    return {
        "channel_id": channel_id,
        "channel_name": (channel or {}).get("name"),
        "topics": topics[:requested],
        "total": listed.get("total", len(topics)),
        "counts": listed.get("counts") or {},
        "statuses": listed.get("statuses") or list(youtube_topics.STATUSES),
        "backlog_target": listed.get("backlog_target", 50),
    }


def _youtube_topic_detail(topic_id, query):
    channel_id = _channel_id(query=query)
    topic = youtube_topics.get_topic(channel_id, topic_id)
    script = None
    if topic.get("script_id"):
        try:
            script = youtube_scripts.get_script(channel_id, topic["script_id"])
        except Exception:
            script = None
    studio_project = None
    if topic.get("project_id"):
        try:
            studio_project = studio_workspaces.get_project("youtube", topic["project_id"])
        except Exception:
            studio_project = None
    projects = genesis.source_projects("youtube_topic", topic["id"])
    publications = []
    for project in projects:
        try:
            publications.extend(genesis_store.list_publications(project_id=project["id"], limit=20))
        except Exception:
            continue
    active = genesis.active_source_index("youtube_topic").get(topic["id"])
    return {
        "topic": _topic_row(channel_id, topic, {topic["id"]: active} if active else {}),
        "script": script,
        "studio_project": studio_project,
        "genesis_projects": projects,
        "publications": publications,
        "duplicates": youtube_topics.find_duplicates(channel_id, topic.get("title"), exclude_id=topic["id"]),
        "queue_eligible": (
            topic.get("status") == genesis.YOUTUBE_ELIGIBLE_STATUS
            and bool(topic.get("script_id"))
            and not active
        ),
    }


def overview():
    config = cosmos_config.config_public()
    return {
        "agents": cosmos_registry.list_agents(),
        "config": {
            "active_ads_template_id": config.get("active_ads_template_id"),
            "active_youtube_template_id": config.get("active_youtube_template_id"),
            "active_youtube_channel_id": config.get("active_youtube_channel_id"),
            "ads_templates": config.get("ads_templates") or [],
            "youtube_templates": config.get("youtube_templates") or [],
        },
        "channels": _channels(),
        "llm": _llm_status(),
        "llm_selections": cosmos_llm.public_selections(),
        "counts": {
            "ads_backlog": ads_backlog.counts(),
            "youtube_backlog": _youtube_summary().get("counts") or {},
            "recommendations": cosmos_store.recommendation_counts(),
            "genesis": genesis.overview_counts(),
        },
    }


def _agent_detail(agent_id):
    agent = cosmos_registry.get_agent(agent_id)
    detail = cosmos_registry.public_agent(agent)
    detail["activity"] = cosmos_store.agent_activity(agent_id)
    detail["recommendation_counts"] = cosmos_store.recommendation_counts(agent_id=agent_id)
    return detail


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------
def _get(parts, query):
    if not parts or parts[0] == "overview":
        return 200, overview()

    if parts[0] == "agents":
        if len(parts) == 1:
            return 200, {"agents": cosmos_registry.list_agents()}
        agent_id = parts[1]
        if len(parts) == 2:
            return 200, {"agent": _agent_detail(agent_id)}
        if len(parts) == 3 and parts[2] == "executions":
            data = cosmos_store.list_executions(
                agent_id=agent_id,
                division=_query(query, "division"),
                status=_query(query, "status"),
                limit=_query(query, "limit", 20),
            )
            return 200, data
        if len(parts) == 3 and parts[2] == "recommendations":
            data = cosmos_store.list_recommendations(
                agent_id=agent_id,
                division=_query(query, "division"),
                status=_query(query, "status"),
                limit=_query(query, "limit", 100),
            )
            return 200, data
        if len(parts) == 3 and parts[2] == "activity":
            return 200, cosmos_store.agent_activity(agent_id, limit=_query(query, "limit", 8))
        return 404, {"error": "Agent endpoint not found.", "code": "not_found"}

    if parts[0] == "executions" and len(parts) == 2:
        return 200, {"execution": cosmos_store.get_execution(parts[1])}

    if parts[0] == "recommendations":
        if len(parts) == 1:
            data = cosmos_store.list_recommendations(
                agent_id=_query(query, "agent_id"),
                division=_query(query, "division"),
                status=_query(query, "status"),
                limit=_query(query, "limit", 100),
            )
            return 200, data
        if len(parts) == 2:
            recommendation = cosmos_store.get_recommendation(parts[1])
            execution = None
            try:
                execution = cosmos_store.get_execution(recommendation.get("execution_id"))
            except common.CosmosError:
                execution = None
            return 200, {"recommendation": recommendation, "execution": execution}
        if len(parts) == 3 and parts[2] == "execution":
            return 200, cosmos_store.get_execution(
                cosmos_store.get_recommendation(parts[1]).get("execution_id")
            )
        return 404, {"error": "Recommendation endpoint not found.", "code": "not_found"}

    if parts[0] == "llm":
        if len(parts) == 1:
            return 200, {
                "default": _llm_status(),
                "selections": cosmos_llm.public_selections(),
            }
        if len(parts) == 3:
            stored = cosmos_llm.get(parts[1], parts[2])
            fallback = studio_llm.selection()
            return 200, {
                "selection": stored or {
                    "agent_id": fallback["agent_id"],
                    "model": fallback["model"],
                },
                "inherited": not bool(stored),
                "default": _llm_status(),
            }

    if parts[0] == "config":
        config = cosmos_config.config_public()
        return 200, {
            "config": config,
            "channels": _channels(),
            "llm": _llm_status(),
            "llm_selections": cosmos_llm.public_selections(),
        }

    if parts[0] == "backlog" and len(parts) >= 2:
        if parts[1] == "ads":
            if len(parts) == 2:
                return 200, ads_backlog.list_products(
                    status=_query(query, "status"),
                    query=_query(query, "q"),
                    limit=_query(query, "limit", 200),
                    include_archived=_query(query, "include_archived") in ("1", "true"),
                )
            if len(parts) == 3:
                return 200, {"product": ads_backlog.get_product(parts[2])}
        if parts[1] == "youtube":
            if len(parts) == 2:
                return 200, _youtube_backlog(query)
            if len(parts) == 3:
                return 200, _youtube_topic_detail(parts[2], query)
    return 404, {"error": "COSMOS endpoint not found.", "code": "not_found"}


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------
def _post(parts, body, query):
    if parts and parts[0] == "agents" and len(parts) == 3 and parts[2] == "execute":
        division = body.get("division")
        if not division:
            raise common.CosmosError("division_required", "division is required (ads or youtube).", 400)
        execution = cosmos_engine.run(
            parts[1],
            division,
            body.get("task") or "",
            source_notes=body.get("source_notes") or "",
            template_id=body.get("template_id") or None,
            channel_id=body.get("channel_id") or None,
        )
        return 200, {"execution": execution, "agent_id": parts[1], "division": division}

    if parts and parts[0] == "recommendations" and len(parts) == 3:
        recommendation_id = parts[1]
        action = parts[2]
        if action == "draft":
            return 200, cosmos_backlog.draft(recommendation_id)
        if action == "reject":
            return 200, cosmos_backlog.reject(recommendation_id, body.get("note"))
        if action == "refine":
            return 200, cosmos_backlog.refine(recommendation_id, body.get("instruction"))
        if action == "validate-affiliate":
            return 200, cosmos_backlog.validate_affiliate(recommendation_id, body)
        return 404, {"error": "Unknown recommendation action.", "code": "not_found"}

    if parts and parts[0] == "llm" and len(parts) == 3:
        selection = cosmos_llm.configure(
            parts[1], parts[2], body.get("agent_id"), body.get("model")
        )
        return 200, {"selection": selection}

    if parts and parts[0] == "config" and len(parts) == 2 and parts[1] == "active":
        domain = body.get("domain")
        if body.get("channel_id"):
            return 200, cosmos_config.set_active_channel(body.get("channel_id"))
        if not domain:
            raise common.CosmosError("domain_required", "domain is required (ads or youtube).", 400)
        return 200, cosmos_config.set_active_template(domain, body.get("template_id"))

    if parts and parts[0] == "backlog" and len(parts) >= 2 and parts[1] == "ads":
        if len(parts) == 2:
            product = ads_backlog.create_product(
                body,
                origin={"agent_id": "owner", "division": "ads", "template_id": body.get("template_id")},
            )
            return 200, {"product": product}
        if len(parts) == 4 and parts[3] == "status":
            product = ads_backlog.set_status(parts[2], body.get("status"), note=body.get("note"))
            return 200, {"product": product}

    if parts and parts[0] == "backlog" and len(parts) >= 2 and parts[1] == "youtube":
        channel_id = _channel_id(query=query, body=body)
        if len(parts) == 2:
            topic = youtube_topics.create_topic(
                channel_id, body, force=youtube_common.clean_bool(body.get("force"))
            )
            return 200, {"topic": _topic_row(channel_id, topic)}
        if len(parts) == 4 and parts[3] == "status":
            topic = youtube_topics.set_status(
                channel_id, parts[2], body.get("status"), body.get("reason") or False
            )
            return 200, {"topic": _topic_row(channel_id, topic)}

    return 404, {"error": "COSMOS endpoint not found.", "code": "not_found"}


# ---------------------------------------------------------------------------
# PATCH / DELETE
# ---------------------------------------------------------------------------
def _patch(parts, body):
    if len(parts) == 3 and parts[0] == "templates" and parts[1] in ("ads", "youtube"):
        template = cosmos_config.update_template(parts[1], parts[2], body)
        return 200, {"template": template}
    if len(parts) == 3 and parts[0] == "backlog" and parts[1] == "ads":
        product = ads_backlog.update_product(parts[2], body)
        return 200, {"product": product}
    if len(parts) == 3 and parts[0] == "backlog" and parts[1] == "youtube":
        channel_id = _channel_id(body=body)
        topic = youtube_topics.update_topic(channel_id, parts[2], body)
        return 200, {"topic": _topic_row(channel_id, topic)}
    return 404, {"error": "COSMOS endpoint not found.", "code": "not_found"}


def _delete(parts, query=None):
    if len(parts) == 3 and parts[0] == "backlog" and parts[1] == "ads":
        return 200, ads_backlog.delete_product(parts[2])
    if len(parts) == 3 and parts[0] == "backlog" and parts[1] == "youtube":
        channel_id = _channel_id(query=query)
        return 200, youtube_topics.delete_topic(channel_id, parts[2])
    return 404, {"error": "COSMOS endpoint not found.", "code": "not_found"}
