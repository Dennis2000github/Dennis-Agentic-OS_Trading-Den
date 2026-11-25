"""Shopee Affiliate GraphQL client (stdlib only).

One transport for the whole connector: sign the exact serialized body, POST
it, map documented error codes to stable exceptions, and retry only
transient failures. Permanent errors (bad signature, access denied, frozen
or blacklisted accounts, parameter errors) are never retried.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .. import store
from . import auth

REQUEST_TIMEOUT = 30
MAX_TRANSIENT_RETRIES = 2
TRANSIENT_BACKOFF_SECONDS = 2.0


class ShopeeError(store.IntegrationError):
    """A Shopee API failure with the documented code attached."""

    def __init__(self, code, message, status=400, shopee_code=None):
        super().__init__(code, message, status)
        self.shopee_code = shopee_code


# Documented Shopee Affiliate error codes -> stable local codes / HTTP status.
DOCUMENTED_ERRORS = {
    "11000": ("business_error", 400),
    "11001": ("params_error", 400),
    "11002": ("bind_account_error", 403),
    "10020": ("authentication_error", 401),  # multiple auth-related problems
    "10030": ("rate_limited", 429),
    "10031": ("access_denied", 403),
    "10032": ("invalid_affiliate_id", 403),
    "10033": ("account_frozen", 403),
    "10034": ("affiliate_id_blacklisted", 403),
    "10035": ("no_api_access", 403),
}

PERMANENT_CODES = {"10020", "10031", "10032", "10033", "10034", "10035", "11001", "11002"}


def endpoint():
    """Effective GraphQL endpoint (env > credentials store > PH default)."""
    return auth.public_state()["endpoint"]


def graphql(query, variables=None, *, retries=MAX_TRANSIENT_RETRIES):
    """POST one GraphQL request with documented signing and error mapping.

    The body is serialized exactly once; those bytes are signed and sent.
    """
    creds = auth.resolve_credentials()
    body_object = {"query": str(query or "")}
    if variables:
        body_object["variables"] = variables
    body_text = json.dumps(body_object, separators=(",", ":"), ensure_ascii=False)
    url = creds.get("endpoint") or auth.DEFAULT_ENDPOINT

    attempt = 0
    while True:
        attempt += 1
        # A retry is a new request: generate a fresh timestamp and signature
        # over the same exact serialized body.
        headers, timestamp = auth.sign_request(body_text, creds)
        try:
            req = urllib.request.Request(url, data=body_text.encode("utf-8"),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
                server_time_ok = auth.validate_server_time(resp.headers.get("Date"), timestamp)
            if server_time_ok is False:
                raise ShopeeError(
                    "clock_skew",
                    "Shopee server time is outside the allowed 10-minute signing window.",
                    401,
                )
            return _parse_graphql(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            mapped = _map_http_error(exc.code, detail)
            if mapped is not None:
                raise mapped
            if attempt <= retries:
                time.sleep(TRANSIENT_BACKOFF_SECONDS)
                continue
            raise ShopeeError("http_error", "Shopee HTTP %s: %s" % (exc.code, detail), 502)
        except urllib.error.URLError as exc:
            if attempt <= retries:
                time.sleep(TRANSIENT_BACKOFF_SECONDS)
                continue
            raise ShopeeError("unreachable", "Shopee API unreachable: %s" % exc.reason, 502)
        except ShopeeError:
            raise
        except Exception as exc:  # noqa: BLE001 - defensive transport guard
            raise ShopeeError("bad_response", "Unexpected Shopee response: %s" % exc, 502)


def _parse_graphql(raw):
    try:
        payload = json.loads(raw)
    except ValueError:
        raise ShopeeError("bad_response", "Shopee returned non-JSON output.", 502)
    errors = payload.get("errors") or []
    if errors:
        first = errors[0] if isinstance(errors, list) else errors
        code = str((first.get("extensions") or {}).get("code") or "") if isinstance(first, dict) else ""
        message = str(first.get("message") or "GraphQL error")[:400] if isinstance(first, dict) else str(first)[:400]
        if code in DOCUMENTED_ERRORS:
            local_code, status = DOCUMENTED_ERRORS[code]
            raise ShopeeError(local_code, "%s (%s)" % (message, code), status, shopee_code=code)
        raise ShopeeError("graphql_error", message, 502)
    if "data" not in payload:
        raise ShopeeError("bad_response", "Shopee response missing data.", 502)
    return payload["data"]


def _map_http_error(http_status, detail):
    """Map non-200 HTTP responses to documented codes where identifiable."""
    for shopee_code, (local_code, status) in DOCUMENTED_ERRORS.items():
        if shopee_code in detail:
            raise ShopeeError(local_code, "%s (%s)" % (detail[:300], shopee_code), status,
                              shopee_code=shopee_code)
    if http_status == 429:
        raise ShopeeError("rate_limited", "Shopee rate limit exceeded.", 429, shopee_code="10030")
    if http_status in (401, 403):
        raise ShopeeError("authentication_error", "Shopee rejected the request: %s" % detail[:300],
                          401 if http_status == 401 else 403)
    return None  # transient -> caller may retry


def is_permanent(shopee_code):
    return str(shopee_code or "") in PERMANENT_CODES


def clean_query(query):
    return store.clean_text(query, "GraphQL query", 20000, required=True)
