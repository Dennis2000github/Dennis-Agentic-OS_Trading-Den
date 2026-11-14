"""Provider diagnostics for Studio generation.

One service, one normalized response contract. Every provider is checked
independently through staged, non-billable verifications so the owner can see
exactly which pipeline stage fails — configuration, authentication, API
reachability, model catalog, upload issuance or generation wiring.

Billable generation requests are never part of an ordinary diagnostic: the
``generation`` capability is reported as verified only when a documented,
free verification endpoint exists for the provider, otherwise the result says
explicitly that the capability has not been exercised.

A diagnostic never marks a provider operational merely because credentials
exist. ``CONFIGURED`` means "credentials present"; ``CONNECTED`` and above
always require a real authenticated round-trip to the provider.
"""

from __future__ import annotations

import threading
import time
import traceback

from . import studio_catalog as catalog
from . import studio_free as free
from . import studio_registry as registry
from . import studio_secrets as secrets

#: Explicit diagnostic states (stable contract for the frontend).
NOT_CONFIGURED = "NOT_CONFIGURED"
CONFIGURED = "CONFIGURED"
CHECKING = "CHECKING"
CONNECTED = "CONNECTED"
AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
NETWORK_ERROR = "NETWORK_ERROR"
TIMEOUT = "TIMEOUT"
RATE_LIMITED = "RATE_LIMITED"
INSUFFICIENT_CREDITS = "INSUFFICIENT_CREDITS"
PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
PARTIALLY_OPERATIONAL = "PARTIALLY_OPERATIONAL"
OPERATIONAL = "OPERATIONAL"
UNKNOWN_ERROR = "UNKNOWN_ERROR"

#: Pipeline stages, in the order they are verified.
STAGES = (
    "configuration",
    "authentication",
    "api_reachability",
    "model_catalog",
    "upload",
    "generation",
)

#: Providers whose generation adapter is wired into the engine registry.
_WIRED = frozenset(registry._ADAPTER_READY)  # noqa: SLF001 - same package

_LOCK = threading.Lock()
_RESULTS = {}  # provider_id -> last diagnostic result (in-memory history)
_HISTORY_LIMIT = 20

# Provider-specific non-billable verification probes.
_POLLINATIONS_HOST = "https://image.pollinations.ai"

_RECOMMENDATIONS = {
    NOT_CONFIGURED: "Open Studio Setup and save the credentials for this provider.",
    CONFIGURED: "Run “Check connection” to verify authentication with the provider.",
    AUTHENTICATION_FAILED: "The provider rejected the stored credentials. Re-save them in Studio Setup.",
    NETWORK_ERROR: "Check the network connection / DNS, then run the check again.",
    TIMEOUT: "The provider did not respond in time. Retry; if it persists the provider may be down.",
    RATE_LIMITED: "The provider is rate limiting this account. Wait and retry.",
    INSUFFICIENT_CREDITS: "The account has insufficient credits or quota with the provider.",
    PROVIDER_UNAVAILABLE: "The provider API returned a server error. Retry later.",
    MODEL_UNAVAILABLE: "The model catalog is unreachable. The provider API may have changed.",
    PARTIALLY_OPERATIONAL: "Authentication works but the generation adapter is not wired yet — credentials are safe to keep.",
    OPERATIONAL: "Ready to generate.",
    UNKNOWN_ERROR: "An unexpected error occurred. Retry the check; see the message for details.",
    CHECKING: "Diagnostic in progress.",
}


class DiagnosticCheck:
    """One verified stage inside a diagnostic run."""

    def __init__(self, stage, status, ok, message, response_time_ms=None,
                 error_code=None, http_status=None, retryable=False):
        self.stage = stage
        self.status = status
        self.ok = bool(ok)
        self.message = message
        self.response_time_ms = response_time_ms
        self.error_code = error_code
        self.http_status = http_status
        self.retryable = bool(retryable)

    def as_dict(self):
        return {
            "stage": self.stage,
            "status": self.status,
            "ok": self.ok,
            "message": self.message,
            "response_time_ms": self.response_time_ms,
            "error_code": self.error_code,
            "http_status": self.http_status,
            "retryable": self.retryable,
        }


def _classify_error(code, http_status, message):
    """Map a normalized error code / HTTP status to a diagnostic state."""
    code = str(code or "")
    lowered = str(message or "").lower()
    if "timed out" in lowered or "timeout" in lowered:
        return TIMEOUT
    if code == "credentials_missing" or code == "missing_key":
        return NOT_CONFIGURED
    if code in ("authentication_failed", "invalid_api_key", "unauthorized", "invalid_key"):
        return AUTHENTICATION_FAILED
    if code == "insufficient_credits":
        return INSUFFICIENT_CREDITS
    if code == "rate_limited" or http_status == 429:
        return RATE_LIMITED
    if code == "network_error":
        return NETWORK_ERROR
    if code == "model_unavailable" or http_status == 503:
        return MODEL_UNAVAILABLE
    if http_status and http_status >= 500:
        return PROVIDER_UNAVAILABLE
    if http_status == 404:
        return MODEL_UNAVAILABLE
    return UNKNOWN_ERROR


def _http_probe(url, headers=None, timeout=10):
    """Minimal reachability probe. Returns (state, message, ms, http_status).

    Any HTTP response — including 404/405 — proves DNS + TLS + host
    reachability without generating anything.
    """
    import socket
    import urllib.error
    import urllib.request

    started = time.time()
    request = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "DENNIS-Agentic-OS-Studio/1.0",
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(64)
            return CONNECTED, "Host reachable.", _ms(started), int(response.status)
    except urllib.error.HTTPError as exc:
        try:
            exc.read(64)
        except Exception:
            pass
        return CONNECTED, "Host reachable (HTTP %s)." % exc.code, _ms(started), int(exc.code)
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
            return TIMEOUT, "The host did not respond in time.", _ms(started), None
        return NETWORK_ERROR, "Cannot reach the host: %s" % reason, _ms(started), None
    except (socket.timeout, TimeoutError):
        return TIMEOUT, "The host did not respond in time.", _ms(started), None
    except OSError as exc:
        return NETWORK_ERROR, "Network error: %s" % exc, _ms(started), None


def _ms(started):
    return int(round((time.time() - started) * 1000))


# ---------------------------------------------------------------------------
# Per-provider verification (all non-billable)
# ---------------------------------------------------------------------------
def _verify_higgsfield():
    from . import studio_providers as providers

    checks = []
    descriptor = providers.REGISTRY.get("higgsfield").descriptor()
    if not descriptor.get("configured"):
        checks.append(DiagnosticCheck(
            "configuration", NOT_CONFIGURED, False,
            "No Higgsfield credentials are stored."))
        return checks
    checks.append(DiagnosticCheck(
        "configuration", CONNECTED, True,
        "Credentials configured (source: %s)." % descriptor["credentials"].get("source")))
    try:
        result = providers.REGISTRY.get("higgsfield").test()
    except providers.ProviderError as exc:
        state = _classify_error(exc.code, exc.status, exc.message)
        checks.append(DiagnosticCheck(
            "authentication", state, False, exc.message,
            error_code=exc.code, http_status=exc.status if exc.status >= 400 else None,
            retryable=exc.retryable))
        return checks
    except Exception as exc:  # pragma: no cover - defensive
        checks.append(DiagnosticCheck(
            "authentication", UNKNOWN_ERROR, False, str(exc), error_code="unexpected_error"))
        return checks
    checks.append(DiagnosticCheck(
        "authentication", CONNECTED, True,
        str(result.get("message") or "Credentials accepted by the provider.")))
    # The presigned-URL probe *is* the documented upload handshake, so upload
    # issuance is verified as a side effect without generating anything.
    checks.append(DiagnosticCheck(
        "upload", CONNECTED, True,
        "Upload URL issuance verified (non-billable presign request)."))
    checks.append(DiagnosticCheck(
        "generation", CONFIGURED, True,
        "Not exercised — generation is billable. Submit a job to verify."))
    return checks


def _verify_google():
    checks = []
    if not free.google_available():
        checks.append(DiagnosticCheck(
            "configuration", NOT_CONFIGURED, False,
            "No Google AI key is stored."))
        return checks
    checks.append(DiagnosticCheck(
        "configuration", CONNECTED, True, "Gemini API key configured."))
    try:
        result = free.test_google_key()
    except free.FreeError as exc:
        state = _classify_error(exc.code, exc.status if exc.status != 502 else None, exc.message)
        checks.append(DiagnosticCheck(
            "authentication", state, False, str(exc.message),
            error_code=exc.code,
            retryable=exc.code == "network_error"))
        return checks
    except Exception as exc:  # pragma: no cover - defensive
        checks.append(DiagnosticCheck(
            "authentication", UNKNOWN_ERROR, False, str(exc), error_code="unexpected_error"))
        return checks
    if result.get("ok"):
        checks.append(DiagnosticCheck(
            "authentication", CONNECTED, True,
            str(result.get("message") or "Google AI key accepted.")))
        checks.append(DiagnosticCheck(
            "model_catalog", CONNECTED, True,
            "Model list retrieved from the Gemini API."))
        checks.append(DiagnosticCheck(
            "generation", CONFIGURED, True,
            "Not exercised — Veo generation is billable. Submit a job to verify."))
    else:
        state = _classify_error(result.get("code"), result.get("status"), result.get("message"))
        checks.append(DiagnosticCheck(
            "authentication", state, False, str(result.get("message") or "Verification failed."),
            error_code=result.get("code"), http_status=result.get("status"),
            retryable=bool(result.get("retryable"))))
    return checks


def _verify_pollinations():
    checks = [DiagnosticCheck(
        "configuration", CONNECTED, True,
        "Keyless free engine — no credentials required.")]
    state, message, ms, status = _http_probe(_POLLINATIONS_HOST)
    checks.append(DiagnosticCheck(
        "api_reachability", state, state == CONNECTED, message,
        response_time_ms=ms, http_status=status,
        retryable=state in (TIMEOUT, NETWORK_ERROR)))
    checks.append(DiagnosticCheck(
        "generation", CONFIGURED, True,
        "Not exercised — image generation runs on the free host on demand."))
    return checks


def _verify_free_registry(provider_id):
    """``auto`` routes to Google when a key exists, Pollinations otherwise."""
    if provider_id == "auto" and free.google_available():
        return _verify_google()
    return _verify_pollinations()


def _verify_client_provider(provider_id):
    """fal / runway / luma: real auth verification, honest about the adapter."""
    checks = []
    secrets_id = registry._SECRETS_ID.get(provider_id)
    values, source = secrets.get(secrets_id)
    if not values:
        checks.append(DiagnosticCheck(
            "configuration", NOT_CONFIGURED, False,
            "No %s credentials are stored." % provider_id))
        return checks
    checks.append(DiagnosticCheck(
        "configuration", CONNECTED, True,
        "Credentials configured (source: %s)." % source))
    try:
        if provider_id == "fal":
            from . import studio_fal
            client = studio_fal.FalClient(values)
            result = client.test_credentials()
        elif provider_id == "runway":
            from . import studio_runway
            client = studio_runway.RunwayClient(values)
            result = client.test_credentials()
        elif provider_id == "luma":
            from . import studio_luma
            client = studio_luma.LumaClient(values)
            result = client.test_credentials()
        else:  # pragma: no cover - defensive
            raise ValueError("No verification client for %s" % provider_id)
    except Exception as exc:
        error = getattr(exc, "error", None) or {}
        status = int(getattr(exc, "status", 0) or 0)
        state = _classify_error(error.get("code"), status, str(exc))
        checks.append(DiagnosticCheck(
            "authentication", state, False,
            contracts_redact(str(exc)),
            error_code=error.get("code") or "authentication_failed",
            http_status=status if status >= 400 else None,
            retryable=bool(error.get("retryable"))))
        return checks
    checks.append(DiagnosticCheck(
        "authentication", CONNECTED, True,
        str(result.get("message") or "Credentials accepted by the provider.")))
    checks.append(DiagnosticCheck(
        "generation", PARTIALLY_OPERATIONAL, True,
        "Authentication verified, but the %s generation adapter is not wired "
        "into Studio jobs yet." % provider_id))
    return checks


def _verify_presence_only(provider_id):
    """minimax / replicate: no verification client exists — say so honestly."""
    checks = []
    secrets_id = registry._SECRETS_ID.get(provider_id)
    values, source = secrets.get(secrets_id)
    if not values:
        checks.append(DiagnosticCheck(
            "configuration", NOT_CONFIGURED, False,
            "No %s credentials are stored." % provider_id))
        return checks
    checks.append(DiagnosticCheck(
        "configuration", CONFIGURED, True,
        "Credentials configured (source: %s)." % source))
    checks.append(DiagnosticCheck(
        "authentication", CONFIGURED, False,
        "Not verified — no safe, non-billable verification endpoint is "
        "integrated for %s yet." % provider_id))
    checks.append(DiagnosticCheck(
        "generation", PARTIALLY_OPERATIONAL, False,
        "The %s generation adapter is not wired into Studio jobs yet." % provider_id))
    return checks


def contracts_redact(text):
    from . import studio_contracts as contracts
    return contracts.redact_text(text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _aggregate(provider_id, checks, elapsed_ms):
    registry_entry = registry.registry()
    labels = {p["id"]: p for p in registry_entry["providers"]}
    entry = labels.get(provider_id) or {}
    label = entry.get("label") or provider_id

    failures = [c for c in checks if not c.ok]
    blocking = [c for c in failures if c.status != CONFIGURED]
    states = [c.status for c in checks]

    if not checks:
        status = UNKNOWN_ERROR
        stage = "configuration"
        message = "No checks were run."
    elif blocking:
        worst = blocking[0]
        status = worst.status
        stage = worst.stage
        message = worst.message
    elif "generation" in [c.stage for c in checks] and PARTIALLY_OPERATIONAL in states:
        status = PARTIALLY_OPERATIONAL
        stage = "generation"
        message = "Provider reachable and credentials verified; generation is not wired for this provider yet."
    elif all(c.ok for c in checks):
        status = OPERATIONAL if provider_id in _WIRED else CONNECTED
        stage = "generation"
        message = "All non-billable checks passed."
    else:
        status = CONFIGURED
        stage = checks[-1].stage
        message = checks[-1].message

    verified = {
        "configuration": any(c.ok and c.stage == "configuration" for c in checks),
        "authentication": any(c.ok and c.stage == "authentication" and c.status == CONNECTED for c in checks),
        "api_reachability": any(c.ok and c.stage == "api_reachability" for c in checks),
        "model_catalog": any(c.ok and c.stage == "model_catalog" for c in checks),
        "upload": any(c.ok and c.stage == "upload" for c in checks),
        "generation": False,  # never claimed without a real submission
    }
    failure = blocking[0] if blocking else (failures[0] if failures else None)
    return {
        "provider": provider_id,
        "label": label,
        "status": status,
        "stage": stage,
        "message": message,
        "error_code": failure.error_code if failure else None,
        "http_status": failure.http_status if failure else None,
        "retryable": bool(failure.retryable) if failure else False,
        "checked_at": _iso(),
        "response_time_ms": elapsed_ms,
        "recommended_action": _RECOMMENDATIONS.get(status, ""),
        "adapter_wired": provider_id in _WIRED,
        "verified": verified,
        "checks": [c.as_dict() for c in checks],
    }


def _iso(ts=None):
    import datetime
    return datetime.datetime.fromtimestamp(
        ts if ts is not None else time.time(),
        datetime.timezone.utc,
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_checks(provider_id):
    if provider_id == "higgsfield":
        return _verify_higgsfield()
    if provider_id == "google":
        return _verify_google()
    if provider_id in ("auto", "pollinations"):
        return _verify_free_registry(provider_id)
    if provider_id in ("fal", "runway", "luma"):
        return _verify_client_provider(provider_id)
    return _verify_presence_only(provider_id)


def check(provider_id):
    """Run a full non-billable diagnostic for one provider."""
    started = time.time()
    try:
        checks = _run_checks(provider_id)
    except Exception as exc:  # a broken provider must never break the endpoint
        checks = [DiagnosticCheck(
            "configuration", UNKNOWN_ERROR, False,
            "Diagnostic failed: %s" % contracts_redact(str(exc)),
            error_code="unexpected_error")]
    result = _aggregate(provider_id, checks, _ms(started))
    with _LOCK:
        _RESULTS[provider_id] = result
    return result


def check_all():
    """Check every registry provider; one failure never blocks the others."""
    results = []
    for provider_id in ("auto", "pollinations", "google", "higgsfield",
                        "fal", "runway", "luma", "minimax", "replicate"):
        try:
            results.append(check(provider_id))
        except Exception:  # pragma: no cover - defensive
            results.append({
                "provider": provider_id,
                "status": UNKNOWN_ERROR,
                "message": "Diagnostic crashed.",
                "error_code": "unexpected_error",
                "checked_at": _iso(),
            })
    return {"diagnostics": results, "checked_at": _iso()}


def summary():
    """Static snapshot (no network): configuration + catalog state per provider."""
    out = []
    for provider_id in ("auto", "pollinations", "google", "higgsfield",
                        "fal", "runway", "luma", "minimax", "replicate"):
        entry = dict(_RESULTS.get(provider_id) or {})
        last = entry.get("checked_at")
        static = {
            "provider": provider_id,
            "label": (registry.registry_providers().get(provider_id) or {}).get("label", provider_id),
            "adapter_wired": provider_id in _WIRED,
        }
        if provider_id == "higgsfield":
            static["configured"] = bool(secrets.status("higgsfield").get("configured"))
            static["model_count"] = _catalog_count("higgsfield")
        elif provider_id in ("fal", "runway", "luma", "minimax", "replicate"):
            secrets_id = registry._SECRETS_ID.get(provider_id)
            try:
                static["configured"] = bool(secrets.status(secrets_id).get("configured"))
            except Exception:
                static["configured"] = False
            static["model_count"] = 0
        elif provider_id == "google":
            static["configured"] = free.google_available()
            static["model_count"] = 2
        else:
            static["configured"] = True
            static["model_count"] = 0 if provider_id == "auto" else 2
        static["last_check"] = last
        static["last_status"] = entry.get("status")
        out.append(static)
    return {"diagnostics": out, "generated_at": _iso()}


def _catalog_count(provider):
    try:
        return len(catalog.list_models(provider))
    except Exception:
        return 0
