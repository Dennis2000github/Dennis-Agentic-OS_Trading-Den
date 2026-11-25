"""Shared integration registry — the COSMOS integration router's foundation.

One declarative registry for every external provider the OS can reach.
Providers register here once; every authorized intelligence module (COSMOS
agents, GENESIS, Studio, future modules) consumes the same view instead of
owning private integrations.

Principles:

* **Honest status.** Connection status is derived live at read time and
  never fabricated; when a live check fails the warning is surfaced, not
  swallowed. An unconfigured provider says ``not_configured``.
* **Capabilities declare their effect class.** Each capability carries a
  permission class (read vs write) and, for writes, the approval gate it
  goes through (see ``permissions.py``).
* **No execution here.** Adapters execute provider actions; this module
  answers "what is connected, what may run, and how healthy is it".
* **User state is tiny.** ``data/integrations/registry.json`` only stores
  owner overrides (enabled/disabled, labels) and validation history —
  everything else is derived.
"""

from __future__ import annotations

import os

from .. import composio
from . import executions
from . import permissions
from . import store

OVERRIDES_FILE = os.path.join(store.INTEGRATIONS_DIR, "registry.json")

# Statuses a provider/domain can report. Derived, never stored as truth.
NOT_CONFIGURED = "not_configured"
CONNECTED = "connected"
ACTIVE = "active"
EXPIRED = "expired"
UNREACHABLE = "unreachable"
DISABLED = "disabled"

# Executor availability honesty: a capability is only ``ready`` once its
# adapter ships. Phases mark the implementation roadmap, not marketing.
PHASE_COMPOSIO_YOUTUBE = 2
PHASE_SHOPEE = 3


# ---------------------------------------------------------------------------
# Static provider skeletons
# ---------------------------------------------------------------------------
PROVIDERS = {
    "shopee": {
        "provider": "shopee",
        "label": "Shopee Affiliate",
        "kind": "native",
        "description": (
            "Native Shopee Philippines Affiliate Open API connector: product "
            "catalog, offers, feeds and affiliate links with Shopee's own "
            "SHA-256 request signing."
        ),
        "mechanism": "rest_graphql",
        "region": "ph",
        "credential_env": ("SHOPEE_APP_ID", "SHOPEE_AFFILIATE_SECRET"),
        "credential_file": "shopee/credentials.json",
        "domains": ("affiliate",),
        "capabilities": (
            {
                "id": "shopee.catalog",
                "label": "Product catalog & offers",
                "permission": permissions.READ,
                "phase": PHASE_SHOPEE,
                "summary": "Discover products, offers, commissions, sellers and prices.",
            },
            {
                "id": "shopee.feeds",
                "label": "Product feed synchronization",
                "permission": permissions.READ,
                "phase": PHASE_SHOPEE,
                "summary": "FULL initial download plus DELTA incremental sync (local stores only).",
            },
            {
                "id": "shopee.affiliate_links",
                "label": "Affiliate link retrieval",
                "permission": permissions.READ,
                "phase": PHASE_SHOPEE,
                "summary": "Retrieve affiliate offer links for validated products.",
            },
        ),
    },
    "composio": {
        "provider": "composio",
        "label": "Composio",
        "kind": "provider",
        "description": (
            "Shared tool-execution provider. Connected apps (toolkits) are "
            "project-scoped and reused by every agent; the user's existing "
            "YouTube connection is one registered toolkit, not a private one."
        ),
        "mechanism": "rest_v3_1",
        "credential_env": ("COMPOSIO_API_KEY", "COMPOSIO_CONSUMER_KEY"),
        "credential_file": None,
        "domains": ("youtube",),
        "capabilities": (
            {
                "id": "composio.youtube.read",
                "label": "YouTube read (channel, videos, playlists, analytics)",
                "permission": permissions.READ,
                "phase": PHASE_COMPOSIO_YOUTUBE,
                "summary": "Read-only retrieval through the connected YouTube account.",
            },
            {
                "id": "composio.youtube.write",
                "label": "YouTube write (uploads, playlists, comments, captions)",
                "permission": permissions.WRITE,
                "approval_required": True,
                "phase": PHASE_COMPOSIO_YOUTUBE,
                "summary": "Mutating actions — each one requires explicit owner approval.",
            },
        ),
    },
}


# ---------------------------------------------------------------------------
# Live status derivation (indirection points kept tiny for tests)
# ---------------------------------------------------------------------------
def _composio_keys():
    return composio.public_keys()


def _composio_connections():
    return composio.connections()


def _shopee_credential_state():
    """Resolve Shopee credentials (env > 0600 store). Returns presence only."""
    env_app = (os.environ.get("SHOPEE_APP_ID") or "").strip()
    env_secret = (os.environ.get("SHOPEE_AFFILIATE_SECRET") or "").strip()
    if env_app and env_secret:
        return {"configured": True, "source": "env"}
    cred_file = os.path.join(store.INTEGRATIONS_DIR, "shopee", "credentials.json")
    if os.path.isfile(cred_file):
        data = store.read_json(cred_file, {}) or {}
        if isinstance(data, dict) and data.get("app_id") and data.get("secret"):
            return {"configured": True, "source": "store"}
    return {"configured": False, "source": "none"}


def _composio_status():
    """Derive Composio provider status: keys, then the YouTube connection."""
    try:
        key_state = _composio_keys()
    except Exception as exc:  # noqa: BLE001 - honest degradation
        return {"status": UNREACHABLE, "warning": str(exc), "youtube": None, "keys": {}}
    if not key_state.get("api_key_set"):
        return {"status": NOT_CONFIGURED, "warning": "", "youtube": None, "keys": key_state}
    try:
        conns = _composio_connections()
    except Exception as exc:  # noqa: BLE001
        return {"status": UNREACHABLE, "warning": str(exc), "youtube": None, "keys": key_state}
    warning = conns.get("warning") or ""
    youtube = _domain_connection(conns.get("connections") or [], "youtube")
    status = ACTIVE if youtube and youtube.get("status") == "ACTIVE" else (
        EXPIRED if youtube else NOT_CONFIGURED
    )
    if warning and status != ACTIVE:
        status = UNREACHABLE
    return {"status": status, "warning": warning, "youtube": youtube, "keys": key_state}


def _domain_connection(connections, toolkit):
    """Pick the most relevant connected account for one toolkit."""
    matches = [
        conn
        for conn in connections
        if str(conn.get("toolkit") or "").lower() == toolkit
    ]
    if not matches:
        return None
    for conn in matches:  # active wins over expired
        if str(conn.get("status") or "").upper() == "ACTIVE":
            return conn
    return matches[0]


def _shopee_status():
    state = _shopee_credential_state()
    return {
        "status": CONNECTED if state.get("configured") else NOT_CONFIGURED,
        "warning": "" if state.get("configured") else "Add the Shopee Affiliate AppId and secret to configure this provider.",
        "credentials": {"configured": state.get("configured", False), "source": state.get("source")},
    }


def _youtube_execution_state():
    """Live execution-transport state for the composio→youtube capability.

    Kept as a tiny indirection so views can degrade gracefully and tests can
    patch it.
    """
    try:
        from . import youtube

        return youtube.connection_state()
    except Exception as exc:  # noqa: BLE001 - honest degradation
        return {"connection": None, "session": None, "execution_available": False,
                "blocked_reason": str(exc)}


# ---------------------------------------------------------------------------
# Overrides (owner state only — never a source of truth for status)
# ---------------------------------------------------------------------------
def _load_overrides():
    data = store.read_json(OVERRIDES_FILE, {})
    if not isinstance(data, dict):
        return {}
    return data.get("overrides") if isinstance(data.get("overrides"), dict) else {}


def _save_overrides(overrides):
    store.write_json(
        OVERRIDES_FILE,
        {"version": store.SCHEMA_VERSION, "overrides": overrides,
         "updated_at": store.now(), "updated_iso": store.iso()},
    )


def set_enabled(provider_id, enabled):
    provider_id = store.clean_text(provider_id, "Provider", 60, required=True)
    if provider_id not in PROVIDERS:
        raise store.IntegrationError("provider_unknown", "Unknown provider: %s" % provider_id, 404)
    with store.lock():
        overrides = _load_overrides()
        entry = dict(overrides.get(provider_id) or {})
        entry["enabled"] = bool(enabled)
        overrides[provider_id] = entry
        _save_overrides(overrides)
    return provider_view(provider_id)


def validate(provider_id):
    """Run a live validation for one provider and record the result.

    Validation never changes provider behavior — it refreshes the recorded
    health picture so modules and dashboards can trust one shared answer.
    """
    provider_id = store.clean_text(provider_id, "Provider", 60, required=True)
    if provider_id not in PROVIDERS:
        raise store.IntegrationError("provider_unknown", "Unknown provider: %s" % provider_id, 404)
    if provider_id == "shopee":
        status = _shopee_status()
        result = {
            "ok": status["status"] == CONNECTED,
            "status": status["status"],
            "checked": "credentials",
            "warning": status.get("warning") or "",
        }
    else:
        status = _composio_status()
        result = {
            "ok": status["status"] in (ACTIVE, CONNECTED),
            "status": status["status"],
            "checked": "connected_accounts",
            "warning": status.get("warning") or "",
            "youtube": status.get("youtube"),
        }
    executions.record(
        provider_id,
        "validate",
        "succeeded" if result["ok"] else "failed",
        domain="",
        tool="",
        target={"checked": result.get("checked")},
        module="integrations.registry",
        error=None if result["ok"] else {"code": result["status"], "message": result.get("warning") or result["status"]},
    )
    with store.lock():
        overrides = _load_overrides()
        entry = dict(overrides.get(provider_id) or {})
        entry["last_validated_at"] = store.now()
        entry["last_validated_iso"] = store.iso()
        entry["last_validation"] = result
        overrides[provider_id] = entry
        _save_overrides(overrides)
    return {"provider": provider_id, "validation": result}


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
def _capability_rows(provider_id, spec, status_view, enabled):
    # Readiness is derived, never declared: an adapter is ready only when its
    # transport actually works right now.
    youtube_state = None
    if provider_id == "composio":
        youtube_state = _youtube_execution_state()
    transport_ready = bool(youtube_state and youtube_state.get("execution_available"))
    shopee_ready = bool(status_view and status_view.get("status") == CONNECTED)
    rows = []
    for cap in spec.get("capabilities") or ():
        approval = permissions.classify(
            provider_id,
            provider_id,
            cap.get("id", "").split(".", 1)[-1],
            cap.get("permission") == permissions.WRITE,
        )
        if provider_id == "composio":
            ready = enabled and transport_ready
        else:
            ready = enabled and shopee_ready
        rows.append(
            {
                "id": cap.get("id"),
                "provider": provider_id,
                "label": cap.get("label"),
                "permission": cap.get("permission"),
                "approval_required": approval.get("approval_required", False),
                "hard_gate": approval.get("hard_gate", False),
                "ready": ready,
                "phase": cap.get("phase"),
                "summary": cap.get("summary"),
                "status": status_view.get("status") if status_view else NOT_CONFIGURED,
            }
        )
    return rows


def provider_view(provider_id):
    provider_id = store.clean_text(provider_id, "Provider", 60, required=True)
    spec = PROVIDERS.get(provider_id)
    if not spec:
        raise store.IntegrationError("provider_unknown", "Unknown provider: %s" % provider_id, 404)
    overrides = _load_overrides()
    entry = overrides.get(provider_id) or {}
    enabled = bool(entry.get("enabled", True))
    if not enabled:
        status_view = {"status": DISABLED, "warning": "Provider disabled by the owner."}
    elif provider_id == "shopee":
        status_view = _shopee_status()
    else:
        status_view = _composio_status()
    detail = {k: v for k, v in status_view.items() if k not in ("status", "warning")}
    if provider_id == "composio":
        # Execution transport truth: discovery works over REST today; tool
        # execution runs through the session tool router whose connection
        # binding is tier-dependent. Report both honestly.
        yt_state = _youtube_execution_state()
        detail["youtube_execution"] = {
            "available": bool(yt_state.get("execution_available")),
            "blocked_reason": yt_state.get("blocked_reason") or "",
            "connection": yt_state.get("connection"),
            "session": yt_state.get("session"),
        }
    view = {
        "provider": provider_id,
        "label": spec.get("label"),
        "kind": spec.get("kind"),
        "description": spec.get("description"),
        "mechanism": spec.get("mechanism"),
        "region": spec.get("region"),
        "credential_env": spec.get("credential_env"),
        "enabled": enabled,
        "status": status_view.get("status"),
        "warning": status_view.get("warning") or "",
        "detail": detail,
        "last_validated_at": entry.get("last_validated_at"),
        "last_validated_iso": entry.get("last_validated_iso"),
        "last_validation": entry.get("last_validation"),
        "capabilities": _capability_rows(provider_id, spec, status_view, enabled),
    }
    return view


def registry():
    """Aggregate view: providers, capabilities, health. One shared answer."""
    providers = []
    capabilities = []
    for provider_id in PROVIDERS:
        view = provider_view(provider_id)
        providers.append(view)
        capabilities.extend(view["capabilities"])
    return {
        "providers": providers,
        "capabilities": capabilities,
        "health": health(),
        "executors_registered": permissions.registered_executors(),
        "checked_at": store.iso(),
    }


def health():
    """Shared status view: provider availability plus execution audit rollup."""
    providers = {}
    for provider_id in PROVIDERS:
        view = provider_view(provider_id)
        providers[provider_id] = {
            "status": view["status"],
            "enabled": view["enabled"],
            "warning": view["warning"],
            "last_validated_iso": view.get("last_validated_iso"),
        }
    audit = executions.summary()
    return {
        "providers": providers,
        "executions": {
            "total": audit["total"],
            "by_status": audit["by_status"],
            "last_success": audit["last_success"],
            "last_failure": audit["last_failure"],
        },
        "checked_at": store.iso(),
    }
