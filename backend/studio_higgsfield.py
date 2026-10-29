"""Official Higgsfield API adapter.

Only the documented public API is used:

* submit:  ``POST https://api.higgsfield.ai/<model endpoint>``
* status:  ``GET  <status_url>`` (or ``/requests/<id>/status``)
* cancel:  ``POST <cancel_url>`` (or ``/requests/<id>/cancel``)
* upload:  ``POST /files/generate-upload-url`` then ``PUT`` to the presigned URL

Authentication is ``Authorization: Key KEY_ID:SECRET``. The adapter never
stores credentials, never logs headers and never sends provider credentials to
the presigned storage URL. Failures are normalized with
``studio_contracts.error_from_response``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import studio_contracts as contracts

API_BASE = "https://api.higgsfield.ai"
USER_AGENT = "DENNIS-Agentic-OS-Studio/1.0"
DEFAULT_TIMEOUT = 45
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class ProviderRequestError(Exception):
    def __init__(self, status, error):
        self.status = int(status or 0)
        self.error = error or {
            "code": "provider_error",
            "message": "The provider request failed.",
            "retryable": self.status >= 500,
        }
        super().__init__(self.error.get("message") or "Provider request failed")


def _clean_endpoint(endpoint):
    text = str(endpoint or "").strip().lstrip("/")
    if not text or len(text) > 300:
        raise ProviderRequestError(0, {
            "code": "invalid_endpoint",
            "message": "The model endpoint is invalid.",
            "retryable": False,
        })
    if any(ch in text for ch in ("?", "#", "\\", " ")) or "://" in text or ".." in text.split("/"):
        raise ProviderRequestError(0, {
            "code": "invalid_endpoint",
            "message": "The model endpoint contains unsupported characters.",
            "retryable": False,
        })
    if not all(part for part in text.split("/")):
        raise ProviderRequestError(0, {
            "code": "invalid_endpoint",
            "message": "The model endpoint is malformed.",
            "retryable": False,
        })
    return text


class HiggsfieldClient:
    def __init__(self, credentials, base_url=API_BASE, timeout=DEFAULT_TIMEOUT):
        if not isinstance(credentials, dict):
            raise ValueError("credentials must be a dict")
        self.api_key = (credentials.get("api_key") or "").strip()
        self.secret = (credentials.get("secret") or "").strip()
        if not self.api_key or not self.secret:
            raise ValueError("api_key and secret are required")
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.timeout = timeout

    # -- transport ---------------------------------------------------------
    def _auth(self):
        return "Key %s:%s" % (self.api_key, self.secret)

    def _url(self, path_or_url):
        text = str(path_or_url or "").strip()
        if text.startswith("http://") or text.startswith("https://"):
            if not text.startswith(self.base_url + "/"):
                raise ProviderRequestError(0, {
                    "code": "invalid_url",
                    "message": "The provider returned an unexpected URL.",
                    "retryable": False,
                })
            return text
        return self.base_url + "/" + text.lstrip("/")

    def _request(self, method, path_or_url, body=None, idempotency_key=None,
                 retries=0, extra_headers=None):
        url = self._url(path_or_url)
        payload = None
        headers = {
            "Authorization": self._auth(),
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        if extra_headers:
            headers.update(extra_headers)

        attempt = 0
        while True:
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    raw = response.read()
                    if status == 204 or not raw:
                        return {}
                    try:
                        return json.loads(raw.decode("utf-8"))
                    except ValueError:
                        return {"raw": raw.decode("utf-8", "replace")[:1000]}
            except urllib.error.HTTPError as exc:
                raw = b""
                try:
                    raw = exc.read()
                except Exception:
                    pass
                finally:
                    try:
                        exc.close()
                    except Exception:
                        pass
                parsed = None
                if raw:
                    try:
                        parsed = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        parsed = {"detail": raw.decode("utf-8", "replace")[:500]}
                error = contracts.error_from_response(exc.code, parsed)
                if method == "GET" and contracts.is_retryable_status(exc.code) and attempt < retries:
                    attempt += 1
                    time.sleep(0.25 * attempt)
                    continue
                raise ProviderRequestError(exc.code, error)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < retries:
                    attempt += 1
                    time.sleep(0.25 * attempt)
                    continue
                raise ProviderRequestError(0, {
                    "code": "network_error",
                    "message": contracts.redact_text(
                        "Could not reach the provider: %s" % getattr(exc, "reason", exc)
                    ),
                    "retryable": True,
                })

    # -- documented operations --------------------------------------------
    def submit(self, endpoint, params, idempotency_key):
        endpoint = _clean_endpoint(endpoint)
        return self._request(
            "POST", endpoint, body=params, idempotency_key=idempotency_key, retries=2
        )

    def status(self, status_url):
        return self._request("GET", status_url, retries=2)

    def cancel(self, cancel_url):
        return self._request("POST", cancel_url)

    def create_upload_url(self, content_type):
        return self._request(
            "POST", "files/generate-upload-url", body={"content_type": content_type}
        )

    def upload(self, data, content_type, filename="upload"):
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ProviderRequestError(0, {
                "code": "invalid_upload",
                "message": "The uploaded file is empty.",
                "retryable": False,
            })
        if len(data) > MAX_UPLOAD_BYTES:
            raise ProviderRequestError(0, {
                "code": "upload_too_large",
                "message": "Uploads are limited to 100 MB.",
                "retryable": False,
            })
        if content_type not in (
            "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
            "audio/wav", "audio/x-wav", "video/mp4",
        ):
            raise ProviderRequestError(0, {
                "code": "unsupported_media_type",
                "message": "That file type cannot be uploaded.",
                "retryable": False,
            })

        ticket = self.create_upload_url(content_type)
        upload_url = ticket.get("upload_url")
        public_url = ticket.get("public_url")
        if not isinstance(upload_url, str) or not isinstance(public_url, str):
            raise ProviderRequestError(0, {
                "code": "invalid_upload_ticket",
                "message": "The provider did not return an upload URL.",
                "retryable": False,
            })
        if not upload_url.startswith("https://") and not (
            upload_url.startswith("http://") and self.base_url.startswith("http://")
        ):
            raise ProviderRequestError(0, {
                "code": "invalid_upload_ticket",
                "message": "The provider returned an insecure upload URL.",
                "retryable": False,
            })

        headers = {}
        ticket_headers = ticket.get("upload_headers")
        if isinstance(ticket_headers, dict):
            for key, value in ticket_headers.items():
                if isinstance(key, str) and isinstance(value, str):
                    headers[key] = value
        headers.setdefault("Content-Type", content_type)
        # Provider credentials must never travel to the storage host.
        headers.pop("Authorization", None)

        request = urllib.request.Request(
            upload_url, data=bytes(data), headers=headers, method="PUT"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = getattr(response, "status", 200)
                if status < 200 or status >= 300:
                    raise ProviderRequestError(status, contracts.error_from_response(status, None))
        except urllib.error.HTTPError as exc:
            raise ProviderRequestError(
                exc.code, contracts.error_from_response(exc.code, None)
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderRequestError(0, {
                "code": "upload_failed",
                "message": contracts.redact_text("Upload failed: %s" % exc),
                "retryable": True,
            })
        return {"public_url": public_url, "content_type": content_type}

    def test_credentials(self):
        """Validate credentials with a documented, side-effect-light call."""
        ticket = self.create_upload_url("image/jpeg")
        if not isinstance(ticket.get("public_url"), str):
            raise ProviderRequestError(0, {
                "code": "unexpected_response",
                "message": "The provider accepted the credentials but returned no upload URL.",
                "retryable": False,
            })
        return {
            "ok": True,
            "message": "Credentials accepted. A presigned upload URL was issued.",
        }


def status_path(request_id):
    if not isinstance(request_id, str) or not request_id.strip():
        raise ProviderRequestError(0, {
            "code": "invalid_request_id",
            "message": "A provider request id is required.",
            "retryable": False,
        })
    return "requests/%s/status" % urllib.parse.quote(request_id.strip(), safe="")


def cancel_path(request_id):
    if not isinstance(request_id, str) or not request_id.strip():
        raise ProviderRequestError(0, {
            "code": "invalid_request_id",
            "message": "A provider request id is required.",
            "retryable": False,
        })
    return "requests/%s/cancel" % urllib.parse.quote(request_id.strip(), safe="")
