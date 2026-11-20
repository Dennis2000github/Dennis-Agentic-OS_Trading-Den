"""HTTP dispatcher for the GENESIS production API.

``app.py`` forwards every ``/api/cosmos/*`` request to ``cosmos_api``, which
delegates the ``genesis`` subtree here — the same pure
``(method, path, query, body) → (status, payload)`` contract the other COSMOS
dispatchers use, so it is directly unit-testable without the server.
"""

from __future__ import annotations

import json

from . import cosmos_common as common
from . import genesis
from . import genesis_publish as publish_service
from . import genesis_render as render_service
from . import genesis_store as store
from . import studio_workspaces

PREFIX = "/api/cosmos/genesis"


def _fail(exc):
    if isinstance(exc, (common.CosmosError,)):
        payload = {"error": exc.message, "code": exc.code}
        for attr in ("execution_id", "recommendation_id", "project_id"):
            value = getattr(exc, attr, None)
            if value:
                payload[attr] = value
        return exc.status if 400 <= exc.status <= 599 else 400, payload
    if isinstance(exc, render_service.RenderError):
        return exc.status if 400 <= exc.status <= 599 else 400, {"error": exc.message, "code": exc.code}
    if isinstance(exc, publish_service.PublishError):
        return exc.status if 400 <= exc.status <= 599 else 400, {"error": exc.message, "code": exc.code}
    if isinstance(exc, studio_workspaces.WorkspaceError):
        return exc.status if 400 <= exc.status <= 599 else 400, {"error": exc.message, "code": exc.code}
    return 500, {"error": common.clean_text(str(exc), "Error", 500) or "genesis_error", "code": "genesis_error"}


def handle(method, path, query=None, body=None):
    if not path.startswith(PREFIX):
        return 404, {"error": "Not found.", "code": "not_found"}
    subpath = path[len(PREFIX):].strip("/")
    parts = [part for part in subpath.split("/") if part]
    query = query or {}
    try:
        if method == "GET":
            return _get(parts, query)
        if method == "POST":
            return _post(parts, body or {}, query)
        if method == "PATCH":
            return _patch(parts, body or {})
        if method == "DELETE":
            return _delete(parts)
        return 405, {"error": "Method not allowed.", "code": "method_not_allowed"}
    except Exception as exc:
        return _fail(exc)


def _query(query, name, default=None):
    try:
        values = query.get(name, [default])
        return values[0] if values else default
    except AttributeError:
        return default


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------
def _get(parts, query):
    if not parts or parts[0] == "overview":
        return 200, overview()

    if parts[0] == "queue":
        return 200, genesis.queue(
            division=_query(query, "division"),
            query=_query(query, "q"),
        )

    if parts[0] == "projects":
        if len(parts) == 1:
            return 200, {
                "projects": genesis.list_projects(
                    division=_query(query, "division"),
                    status=_query(query, "status"),
                    query=_query(query, "q"),
                    include_archived=_query(query, "include_archived") in ("1", "true"),
                    limit=int(_query(query, "limit") or 100),
                ),
            }
        if len(parts) == 2:
            project = store.get_project(parts[1], include_deleted=True)
            return 200, {
                "project": project,
                "renders": store.list_renders(project_id=project["id"], limit=100),
                "publications": store.list_publications(project_id=project["id"], limit=100),
                "media": genesis.project_media(project),
            }
        return 404, {"error": "Not found.", "code": "not_found"}

    if parts[0] == "media":
        ids = [part.strip() for part in str(_query(query, "ids") or "").split(",") if part.strip()]
        if not ids:
            return 400, {"error": "Provide one or more media ids.", "code": "invalid_field"}
        return 200, genesis.resolve_media(ids[:100])

    if parts[0] == "renders":
        if len(parts) == 1:
            return 200, {
                "renders": store.list_renders(
                    project_id=_query(query, "project_id"),
                    status=_query(query, "status"),
                    limit=int(_query(query, "limit") or 100),
                ),
            }
        return 200, {"render": store.get_render(parts[1])}

    if parts[0] == "render-engine":
        return 200, {"engine": render_service.availability()}

    if parts[0] == "publications":
        if len(parts) == 1:
            return 200, {
                "publications": store.list_publications(
                    project_id=_query(query, "project_id"),
                    status=_query(query, "status"),
                    limit=int(_query(query, "limit") or 100),
                ),
            }
        return 200, {"publication": store.get_publication(parts[1])}

    if parts[0] == "publishing" and len(parts) >= 2 and parts[1] == "destinations":
        return 200, publish_service.destinations()

    if parts[0] == "publishing" and len(parts) >= 3 and parts[1] == "youtube" and parts[2] == "callback":
        port = int(_query(query, "port") or 0) or None
        result = publish_service.complete_youtube_authorization(query, port)
        html = (
            "<!doctype html><html><head><meta charset='utf-8'><title>GENESIS</title></head>"
            "<body style='background:#05070c;color:#d7e2ef;font-family:sans-serif;"
            "display:grid;place-items:center;height:100vh;margin:0'>"
            "<div style='text-align:center'><h1>YouTube connected</h1>"
            "<p>Authorization complete. You can close this tab and return to GENESIS.</p></div>"
            "</body></html>"
        )
        return 200, {"__html__": html, "result": result}

    return 404, {"error": "Not found.", "code": "not_found"}


def overview():
    counts = genesis.overview_counts()
    projects = genesis.list_projects(limit=12)
    renders = store.list_renders(limit=12)
    publications = store.list_publications(limit=12)
    return {
        "counts": counts,
        "projects": projects,
        "renders": renders,
        "publications": publications,
        "engine": render_service.availability(),
        "destinations": publish_service.destinations().get("destinations"),
    }


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------
def _post(parts, body, query):
    if parts[0] == "projects":
        if len(parts) == 1:
            return 201, {"project": genesis.create_project(body)}
        if len(parts) == 3 and parts[2] == "render":
            project = store.get_project(parts[1])
            _guard_render(project)
            return 201, {"render": render_service.create_render(project)}
        if len(parts) == 3 and parts[2] == "quality-review":
            return 200, genesis.run_quality_review(parts[1])
        if len(parts) == 3 and parts[2] == "approval":
            return 200, genesis.approve_project(parts[1], body)
        if len(parts) == 3 and parts[2] == "publish":
            return 201, {"publication": publish_service.publish(parts[1], body)}
        if len(parts) == 3 and parts[2] == "archive":
            return 200, {"project": genesis.archive_project(parts[1])}
        if len(parts) == 3 and parts[2] == "reopen":
            return 200, {"project": genesis.reopen_project(parts[1])}
        return 404, {"error": "Not found.", "code": "not_found"}

    if parts[0] == "renders" and len(parts) == 3 and parts[2] == "cancel":
        return 200, {"render": render_service.cancel_render(parts[1])}

    if parts[0] == "publications" and len(parts) == 3:
        if parts[2] == "retry":
            return 200, {"publication": publish_service.retry_publication(parts[1])}
        if parts[2] == "cancel":
            return 200, {"publication": publish_service.cancel_publication(parts[1])}
        return 404, {"error": "Not found.", "code": "not_found"}

    if parts[0] == "publishing" and len(parts) >= 3 and parts[1] == "youtube":
        if parts[2] == "authorize":
            port = int(_query(query, "port") or 0) or None
            return 200, publish_service.start_youtube_authorization(port)
        if parts[2] == "disconnect":
            return 200, publish_service.disconnect_youtube()
        return 404, {"error": "Not found.", "code": "not_found"}

    return 404, {"error": "Not found.", "code": "not_found"}


def _guard_render(project):
    if project.get("archived"):
        raise common.CosmosError("project_archived", "Archived projects cannot render.", 409)
    if project.get("status") not in ("in_production", "failed"):
        raise common.CosmosError(
            "project_locked",
            "Project is %s — rendering is available while the project is in production." % project.get("status"),
            409,
        )


# ---------------------------------------------------------------------------
# PATCH / DELETE
# ---------------------------------------------------------------------------
def _patch(parts, body):
    if parts[0] == "projects" and len(parts) == 2:
        return 200, {"project": genesis.update_project(parts[1], body)}
    return 404, {"error": "Not found.", "code": "not_found"}


def _delete(parts):
    if parts[0] == "projects" and len(parts) == 2:
        return 200, {"deleted": genesis.delete_project(parts[1])["id"]}
    return 404, {"error": "Not found.", "code": "not_found"}
