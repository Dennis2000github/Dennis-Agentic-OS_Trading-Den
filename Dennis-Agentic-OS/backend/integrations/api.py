"""HTTP dispatcher for the shared integrations API.

``app.py`` forwards every ``/api/integrations/*`` request here. The
dispatcher is pure — ``(method, path, query, body)`` returns
``(status, payload)`` — so it is directly unit-testable without the server.

Phase 1 surface (foundation; deliberately read-mostly):

* ``GET  /api/integrations``                      — registry overview
* ``GET  /api/integrations/providers/{id}``       — provider detail
* ``GET  /api/integrations/executions``           — execution audit (filters)
* ``POST /api/integrations/providers/{id}/validate`` — live re-validation
* ``POST /api/integrations/providers/{id}/enable``   — owner override
* ``POST /api/integrations/providers/{id}/disable``  — owner override

Provider action execution arrives with the Phase 2/3 adapters and will run
through the shared approval queue — never through ad-hoc module code.
"""

from __future__ import annotations

from . import executions
from . import links
from . import permissions
from . import registry
from . import store
from . import youtube_api
from .shopee import api as shopee_api

PREFIX = "/api/integrations"


def _query(query, name, default=None):
    try:
        values = query.get(name, [default])
        return values[0] if values else default
    except AttributeError:
        return default


def _fail(exc):
    if isinstance(exc, store.IntegrationError):
        return exc.status if 400 <= exc.status <= 599 else 400, {
            "error": exc.message,
            "code": exc.code,
        }
    return 500, {
        "error": store.clean_text(str(exc), "Error", 500) or "integration_error",
        "code": "integration_error",
    }


def handle(method, path, query=None, body=None):
    subpath = path[len(PREFIX):].strip("/")
    # The YouTube capability owns its subtree over the shared Composio adapter.
    if subpath == "youtube" or subpath.startswith("youtube/"):
        return youtube_api.handle(method, path, query, body)
    # The native Shopee connector owns its subtree.
    if subpath == "shopee" or subpath.startswith("shopee/"):
        return shopee_api.handle(method, path, query, body)
    # Cross-platform campaign links.
    if subpath == "links" or subpath.startswith("links/"):
        return _links(method, subpath, query, body)
    parts = [part for part in subpath.split("/") if part]
    try:
        if method == "GET":
            return _get(parts, query or {})
        if method == "POST":
            return _post(parts, body or {})
        return 405, {"error": "Method not allowed.", "code": "method_not_allowed"}
    except Exception as exc:  # noqa: BLE001 - dispatcher converts to JSON errors
        return _fail(exc)


def _get(parts, query):
    if not parts:
        return 200, registry.registry()
    if parts[0] == "providers":
        if len(parts) == 1:
            return 200, {"providers": [
                registry.provider_view(provider_id) for provider_id in registry.PROVIDERS
            ]}
        if len(parts) == 2:
            return 200, registry.provider_view(parts[1])
        raise store.IntegrationError("not_found", "Unknown integrations path.", 404)
    if parts[0] == "executions":
        return 200, {
            "executions": executions.list(
                limit=_query(query, "limit", 50),
                provider=_query(query, "provider"),
                status=_query(query, "status"),
                domain=_query(query, "domain"),
                module=_query(query, "module"),
            ),
            "summary": executions.summary(),
        }
    if parts[0] == "health":
        return 200, registry.health()
    if parts[0] == "permissions":
        return 200, {
            "hard_approval_classes": sorted(permissions.HARD_APPROVAL),
            "executors_registered": permissions.registered_executors(),
            "note": "Every mutating integration action requires explicit owner approval.",
        }
    raise store.IntegrationError("not_found", "Unknown integrations path.", 404)


def _post(parts, body):
    if len(parts) == 3 and parts[0] == "providers" and parts[2] == "validate":
        return 200, registry.validate(parts[1])
    if len(parts) == 3 and parts[0] == "providers" and parts[2] in ("enable", "disable"):
        result = registry.set_enabled(parts[1], parts[2] == "enable")
        return 200, result
    raise store.IntegrationError("not_found", "Unknown integrations path.", 404)


# ---------------------------------------------------------------------------
# Cross-platform links: /api/integrations/links
# ---------------------------------------------------------------------------
def _links(method, subpath, query, body):
    body = body or {}
    parts = [part for part in subpath.split("/") if part and part != "links"]
    try:
        if method == "GET":
            if not parts:
                return 200, {"links": links.list_links(
                    limit=_q(query, "limit", 50),
                    status=_q(query, "status"),
                    campaign=_q(query, "campaign"))}
            if len(parts) == 2 and parts[1] == "performance":
                row = links.get(parts[0])
                if not row:
                    raise store.IntegrationError("link_not_found", "Link record not found.", 404)
                return 200, links.build_performance_snapshot(row)
            if len(parts) == 1:
                row = links.get(parts[0])
                if not row:
                    raise store.IntegrationError("link_not_found", "Link record not found.", 404)
                return 200, row
            raise store.IntegrationError("not_found", "Unknown links path.", 404)
        if method == "POST":
            if not parts:
                return 200, links.create_link(
                    body.get("product"), campaign=body.get("campaign"),
                    notes=body.get("notes"), status=body.get("status") or "proposed")
            if len(parts) == 2 and parts[1] == "video":
                return 200, links.attach_video(parts[0], body.get("video"),
                                               status=body.get("status") or "in_production")
            if len(parts) == 2 and parts[1] == "performance":
                snapshot = links.build_performance_snapshot(links.get(parts[0]) or {})
                recorded = links.record_performance(parts[0], snapshot)
                return 200, recorded
            if len(parts) == 2 and parts[1] == "status":
                return 200, links.set_status(parts[0], body.get("status"))
            raise store.IntegrationError("not_found", "Unknown links path.", 404)
        return 405, {"error": "Method not allowed.", "code": "method_not_allowed"}
    except Exception as exc:  # noqa: BLE001 - dispatcher converts to JSON errors
        if isinstance(exc, store.IntegrationError):
            return exc.status if 400 <= exc.status <= 599 else 400, {
                "error": exc.message, "code": exc.code}
        return 500, {"error": store.clean_text(str(exc), "Error", 500), "code": "links_error"}


def _q(query, name, default=None):
    try:
        values = query.get(name, [default])
        return values[0] if values else default
    except AttributeError:
        return default
