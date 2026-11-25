"""HTTP dispatcher for the Shopee capability over shared integrations.

``integrations.api`` forwards the ``shopee/`` subtree here. Pure:
``(method, path, query, body) -> (status, payload)``.

* ``GET  /api/integrations/shopee``                 — credential/connection state
* ``GET  /api/integrations/shopee/auth/self-test``  — signing self-check (vector report)
* ``GET  /api/integrations/shopee/feeds``           — feed registry + sync status
* ``GET  /api/integrations/shopee/products``        — local normalized catalog
* ``POST /api/integrations/shopee/offers/search``   — live productOfferV2 (keyword)
* ``POST /api/integrations/shopee/offers/query``    — live productOfferV2 (full params)
* ``POST /api/integrations/shopee/feeds/list``      — listItemFeeds (FULL/DELTA)
* ``POST /api/integrations/shopee/feeds/sync``      — checkpointed feed ingestion
* ``POST /api/integrations/shopee/feeds/reconcile`` — validated FULL rebuild
"""

from __future__ import annotations

from .. import executions
from .. import store
from . import auth
from . import feeds
from . import products


def _fail(exc):
    if isinstance(exc, store.IntegrationError):
        return exc.status if 400 <= exc.status <= 599 else 400, {
            "error": exc.message, "code": exc.code}
    return 500, {"error": store.clean_text(str(exc), "Error", 500) or "shopee_error",
                 "code": "shopee_error"}


def handle(method, path, query=None, body=None):
    subpath = path[len("/api/integrations/shopee"):].strip("/")
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
        state = auth.public_state()
        return 200, {
            "capability": "shopee.affiliate",
            "credentials": {"configured": state["configured"], "source": state["source"]},
            "endpoint": state["endpoint"],
            "note": "Live calls require the AppId/secret; the GraphQL endpoint must be "
                    "confirmed against the supplied API documentation.",
            "self_test": auth.verify_test_vector(),
        }
    if parts[0] == "auth" and len(parts) == 2 and parts[1] == "self-test":
        return 200, auth.verify_test_vector()
    if parts[0] == "feeds":
        return 200, feeds.sync_status()
    if parts[0] == "products":
        return 200, {
            "products": products.list_products(
                limit=_first(query, "limit", 50), keyword=_first(query, "keyword")),
            "total": products._count(),
        }
    raise store.IntegrationError("not_found", "Unknown Shopee path.", 404)


def _caller(params):
    """Agent/module attribution supplied by an authorized COSMOS caller."""
    params = params if isinstance(params, dict) else {}
    agent = store.clean_text(params.get("agent"), "Agent", 60) or None
    module = store.clean_text(params.get("module"), "Module", 120) or "integrations.shopee"
    return agent, module


def _audit(action, status, params, *, target=None, error=None, detail=None,
           started_at=None, finished_at=None):
    agent, module = _caller(params)
    return executions.record(
        "shopee", action, status, domain="affiliate", tool=action, target=target,
        agent=agent, module=module, error=error, detail=detail,
        started_at=started_at, finished_at=finished_at,
    )


def _audited(action, params, target, fn):
    started = store.now()
    try:
        result = fn()
    except store.IntegrationError as exc:
        _audit(action, "failed", params, target=target, started_at=started,
               finished_at=store.now(),
               error={"code": exc.code, "message": exc.message})
        raise
    except Exception as exc:  # noqa: BLE001 - dispatcher still maps the error
        _audit(action, "failed", params, target=target, started_at=started,
               finished_at=store.now(),
               error={"code": "shopee_error", "message": str(exc)[:400]})
        raise
    row = _audit(action, "succeeded", params, target=target, started_at=started,
                 finished_at=store.now(),
                 detail={"result_keys": sorted(result.keys())[:20]}
                 if isinstance(result, dict) else None)
    if isinstance(result, dict):
        result = dict(result)
        result["audit_id"] = row.get("id")
    return result


def _post(parts, body):
    body = body or {}
    if parts == ["credentials"]:
        app_id = body.get("app_id") or body.get("appId")
        secret = body.get("secret")
        if not app_id or not secret:
            raise store.IntegrationError(
                "invalid_arguments", "app_id and secret are required.", 400)
        state = auth.save_credentials(app_id, secret, body.get("endpoint"))
        _audit("credentials.save", "succeeded", body,
               target={"source": state.get("source")},
               detail={"endpoint": state.get("endpoint") or auth.DEFAULT_ENDPOINT})
        # Live-independent validation: confirms the stored credentials can be
        # resolved by the shared registry. No Shopee network call is made here.
        from .. import registry
        validation = registry.validate("shopee").get("validation") or {}
        return 200, {
            "credentials": {
                "configured": True,
                "source": state.get("source"),
                "endpoint": state.get("endpoint") or auth.DEFAULT_ENDPOINT,
            },
            "validation": validation,
            "note": "Credentials are stored backend-side (0600). Confirm the endpoint before live use.",
        }
    if parts == ["offers", "search"]:
        def _search():
            result = products.search(
                store.clean_text(body.get("keyword"), "keyword", 200, required=True),
                page=body.get("page"), limit=body.get("limit"), sortType=body.get("sortType"))
            products.upsert_offers(result["offers"])
            return result
        return 200, _audited("productOffers.search", body,
                             {"keyword": body.get("keyword")}, _search)
    if parts == ["offers", "query"]:
        def _query():
            result = products.query_offers(body.get("params") or {})
            products.upsert_offers(result["offers"])
            return result
        return 200, _audited("productOffers.query", body,
                             {"params": body.get("params")}, _query)
    if parts == ["feeds", "list"]:
        return 200, _audited(
            "listItemFeeds", body, {"feed_mode": body.get("feed_mode") or body.get("feedMode")},
            lambda: feeds.list_feeds(body.get("feed_mode") or body.get("feedMode")))
    if parts == ["feeds", "sync"]:
        datafeed_id = body.get("datafeed_id") or body.get("datafeedId")
        return 200, _audited(
            "getItemFeedData.sync", body, {"datafeed_id": datafeed_id},
            lambda: feeds.sync_feed(
                datafeed_id,
                feed_mode=body.get("feed_mode") or body.get("feedMode"),
                max_pages=body.get("max_pages")))
    if parts == ["feeds", "reconcile"]:
        datafeed_id = body.get("datafeed_id") or body.get("datafeedId")
        return 200, _audited(
            "getItemFeedData.reconcile", body, {"datafeed_id": datafeed_id},
            lambda: feeds.reconcile(datafeed_id, expected_total=body.get("expected_total")))
    raise store.IntegrationError("not_found", "Unknown Shopee path.", 404)


def _first(query, name, default=None):
    try:
        values = query.get(name, [default])
        return values[0] if values else default
    except AttributeError:
        return default
