"""Shared HTTP helpers for Studio provider clients.

Thin urllib wrappers so each adapter stays dependency-free. Credentials are
never logged; response bodies are redacted through studio_contracts.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from . import studio_contracts as contracts

USER_AGENT = "DENNIS-Agentic-OS-Studio/1.0"
DEFAULT_TIMEOUT = 45
MAX_DATA_URL_BYTES = 8 * 1024 * 1024


class ProviderRequestError(Exception):
    def __init__(self, status, error):
        self.status = int(status or 0)
        self.error = error or {
            "code": "provider_error",
            "message": "The provider request failed.",
            "retryable": self.status >= 500,
        }
        super().__init__(self.error.get("message") or "Provider request failed")


def data_url(data, content_type):
    """Encode small binary payloads as data URLs for providers without upload."""
    if not isinstance(data, (bytes, bytearray)):
        raise ProviderRequestError(0, {
            "code": "invalid_file",
            "message": "Upload data must be bytes.",
            "retryable": False,
        })
    if len(data) > MAX_DATA_URL_BYTES:
        raise ProviderRequestError(0, {
            "code": "file_too_large",
            "message": "Inline uploads are limited to 8 MB for this provider.",
            "retryable": False,
        })
    mime = (content_type or "application/octet-stream").split(";")[0].strip() or "application/octet-stream"
    encoded = base64.b64encode(bytes(data)).decode("ascii")
    return "data:%s;base64,%s" % (mime, encoded)


def request_json(
    method,
    url,
    *,
    headers=None,
    body=None,
    timeout=DEFAULT_TIMEOUT,
    retries=1,
    allow_empty=False,
):
    """Perform an HTTP JSON request and return the decoded body (or {})."""
    payload = None
    req_headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if headers:
        req_headers.update(headers)
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    attempt = 0
    while True:
        request = urllib.request.Request(
            url, data=payload, headers=req_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                raw = response.read()
                if status == 204 or (allow_empty and not raw):
                    return {}
                if not raw:
                    return {}
                try:
                    return json.loads(raw.decode("utf-8"))
                except ValueError:
                    raise ProviderRequestError(status, {
                        "code": "unexpected_response",
                        "message": "The provider returned a non-JSON response.",
                        "retryable": False,
                    })
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read() or b""
            except Exception:
                pass
            body_json = None
            if raw:
                try:
                    body_json = json.loads(raw.decode("utf-8"))
                except ValueError:
                    body_json = {"detail": raw.decode("utf-8", "replace")[:500]}
            error = contracts.error_from_response(exc.code, body_json)
            if contracts.is_retryable_status(exc.code) and attempt < retries:
                attempt += 1
                continue
            raise ProviderRequestError(exc.code, error)
        except urllib.error.URLError as exc:
            if attempt < retries:
                attempt += 1
                continue
            raise ProviderRequestError(0, {
                "code": "network_error",
                "message": contracts.redact_text(str(exc.reason or exc)),
                "retryable": True,
            })
