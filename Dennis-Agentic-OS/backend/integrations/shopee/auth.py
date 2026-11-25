"""Shopee Affiliate authentication: credentials and SHA-256 request signing.

Documented contract (source of truth: the project's Shopee Affiliate Open
API documentation):

* ``Authorization: SHA256 Credential={AppId}, Timestamp={Timestamp}, Signature={signature}``
* ``Signature = SHA256(AppId + Timestamp + Payload + Secret)``
* the timestamp must be within 10 minutes of Shopee server time;
* the signature is a 64-character lowercase hex digest;
* the exact serialized request body is both signed and transmitted — the
  same bytes, never re-serialized;
* the Secret is treated as a password: backend-only, redacted from logs,
  never exposed to the frontend.

Credentials resolve env-first (``SHOPEE_APP_ID`` / ``SHOPEE_AFFILIATE_SECRET``),
then the 0600 store ``data/integrations/shopee/credentials.json``. Secrets
are never returned in public views — only presence and source.

Documented test vector (AppId 123456 / Secret demo / Timestamp 1577836800 /
expected digest ``dc88d72f…45412``): the vector's example Payload value was
not supplied, so :func:`verify_test_vector` computes and reports the match
honestly instead of pretending. With the documented formula the vector is
reproducible the moment the example payload is provided.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import time
from email.utils import parsedate_to_datetime

from .. import store

CREDENTIALS_FILE = os.path.join(store.INTEGRATIONS_DIR, "shopee", "credentials.json")
APP_ID_ENV = "SHOPEE_APP_ID"
SECRET_ENV = "SHOPEE_AFFILIATE_SECRET"
ENDPOINT_ENV = "SHOPEE_API_ENDPOINT"

# Endpoint is configurable: the supplied documentation did not pin the URL.
# Default points at the documented Philippines affiliate host — confirm
# against the API docs before first live use.
DEFAULT_ENDPOINT = "https://open-api.affiliate.shopee.ph/graphql"

MAX_CLOCK_DRIFT_SECONDS = 10 * 60

DOCUMENTED_VECTOR = {
    "app_id": "123456",
    "secret": "demo",
    "timestamp": "1577836800",
    "payload": "",  # not supplied in the documentation we have
    "expected": "dc88d72feea70c80c52c3399751a7d34966763f51a7f056aa070a5e9df645412",
}


class ShopeeAuthError(store.IntegrationError):
    pass


def resolve_credentials():
    """Resolve Shopee credentials (env wins over the 0600 store)."""
    env_app = (os.environ.get(APP_ID_ENV) or "").strip()
    env_secret = (os.environ.get(SECRET_ENV) or "").strip()
    if env_app and env_secret:
        return {
            "app_id": env_app,
            "secret": env_secret,
            "endpoint": (os.environ.get(ENDPOINT_ENV) or "").strip() or None,
            "source": "env",
        }
    data = store.read_json(CREDENTIALS_FILE, {})
    if isinstance(data, dict) and data.get("app_id") and data.get("secret"):
        return {
            "app_id": str(data["app_id"]),
            "secret": str(data["secret"]),
            "endpoint": (os.environ.get(ENDPOINT_ENV) or "").strip()
            or (str(data.get("endpoint")) if data.get("endpoint") else None),
            "source": "store",
        }
    raise ShopeeAuthError(
        "not_configured",
        "Shopee Affiliate credentials are not configured. Set %s and %s (or write "
        "data/integrations/shopee/credentials.json with app_id and secret)."
        % (APP_ID_ENV, SECRET_ENV),
        409,
    )


def save_credentials(app_id, secret, endpoint=None):
    """Persist credentials to the 0600 store (used by an owner-gated flow)."""
    app_id = store.clean_text(app_id, "AppId", 120, required=True)
    secret = store.clean_text(secret, "Secret", 200, required=True)
    endpoint_value = store.clean_text(endpoint, "Endpoint", 300) if endpoint else None
    store.write_json(CREDENTIALS_FILE, {
        "app_id": app_id,
        "secret": secret,
        "endpoint": endpoint_value,
        "updated_at": store.now(),
        "updated_iso": store.iso(),
    })
    return {"configured": True, "source": "store", "endpoint": endpoint_value}


def signature(app_id, timestamp, payload, secret):
    """SHA256(AppId + Timestamp + Payload + Secret) — lowercase hex digest.

    ``payload`` is the EXACT serialized request body that will be transmitted.
    """
    material = "%s%s%s%s" % (app_id, timestamp, payload, secret)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def authorization_header(app_id, timestamp, payload, secret):
    """Build the documented Authorization header for a signed payload."""
    sig = signature(app_id, timestamp, payload, secret)
    return "SHA256 Credential=%s, Timestamp=%s, Signature=%s" % (app_id, timestamp, sig)


def generate_timestamp():
    """Fresh Unix timestamp per request (never cached across calls)."""
    return str(int(time.time()))


def validate_timestamp(timestamp, now_epoch=None):
    """True when the timestamp is within the documented 10-minute window."""
    try:
        value = int(str(timestamp))
    except (TypeError, ValueError):
        return False
    current = int(now_epoch if now_epoch is not None else time.time())
    return abs(current - value) <= MAX_CLOCK_DRIFT_SECONDS


def validate_server_time(date_header, timestamp=None):
    """Check the signed timestamp against the server's HTTP ``Date`` header.

    Returns ``True``/``False`` when the header is parseable, or ``None`` when no
    usable server time was supplied. A signature timestamp outside the
    documented window must fail before retries.
    """
    if not date_header:
        return None
    try:
        parsed = parsedate_to_datetime(str(date_header))
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            # HTTP dates are GMT by definition; make naive values comparable.
            import datetime
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        server_epoch = int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    try:
        signed_epoch = int(str(timestamp)) if timestamp is not None else int(time.time())
    except (TypeError, ValueError):
        return False
    return abs(server_epoch - signed_epoch) <= MAX_CLOCK_DRIFT_SECONDS


def verify_test_vector(payload=None):
    """Recompute the documented test vector and report the match honestly.

    The documentation we hold specifies the formula and the expected digest
    but not the example payload, so the default recomputation uses an empty
    payload and will honestly report a mismatch until the example payload is
    provided. This function is the hook that proves correctness the moment
    the payload example arrives.
    """
    payload_value = DOCUMENTED_VECTOR["payload"] if payload is None else payload
    computed = signature(
        DOCUMENTED_VECTOR["app_id"],
        DOCUMENTED_VECTOR["timestamp"],
        payload_value,
        DOCUMENTED_VECTOR["secret"],
    )
    return {
        "computed": computed,
        "expected": DOCUMENTED_VECTOR["expected"],
        "match": _hmac.compare_digest(computed, DOCUMENTED_VECTOR["expected"]),
        "payload_used": payload_value,
        "note": "The documentation did not include the vector's example payload; "
                "pass it here to confirm the signing function.",
    }


def sign_request(body_text, credentials=None):
    """Sign one serialized request body and return (headers, timestamp).

    ``body_text`` must be the exact string transmitted — sign the bytes you
    send, never a re-serialization.
    """
    creds = credentials or resolve_credentials()
    timestamp = generate_timestamp()
    if not validate_timestamp(timestamp):
        raise ShopeeAuthError("clock_skew", "Local clock is outside the allowed window.", 500)
    header = authorization_header(creds["app_id"], timestamp, body_text, creds["secret"])
    return {"Authorization": header, "Content-Type": "application/json"}, timestamp


def public_state():
    """Credential presence for views — never the values."""
    try:
        creds = resolve_credentials()
        return {"configured": True, "source": creds["source"],
                "endpoint": creds.get("endpoint") or DEFAULT_ENDPOINT}
    except ShopeeAuthError:
        return {"configured": False, "source": "none", "endpoint": DEFAULT_ENDPOINT}


def redact_credentials(payload):
    """Redact credential-looking values before anything reaches a log/store."""
    if isinstance(payload, dict):
        safe = {}
        for key, value in payload.items():
            if str(key).lower() in ("secret", "app_id", "authorization", "password"):
                safe[key] = "[redacted]"
            else:
                safe[key] = redact_credentials(value)
        return safe
    if isinstance(payload, list):
        return [redact_credentials(item) for item in payload[:200]]
    return payload


def _unused_json_guard():  # pragma: no cover - documents intent, keeps json import
    return json.dumps({"signed_with": "exact-serialized-body"})
