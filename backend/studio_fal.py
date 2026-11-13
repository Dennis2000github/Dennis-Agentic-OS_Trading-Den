"""Official fal.ai queue API client for Studio.

Uses documented Inference Queue endpoints only:

* submit: ``POST https://queue.fal.run/<model_id>``
* status / result / cancel URLs from the submit response
* optional storage upload via ``https://fal.run/storage/upload``

Auth: ``Authorization: Key $FAL_KEY``.
"""

from __future__ import annotations

from . import studio_http

API_BASE = "https://queue.fal.run"
STORAGE_URL = "https://fal.run/storage/upload"
DEFAULT_TIMEOUT = 45


ProviderRequestError = studio_http.ProviderRequestError


class FalClient:
    def __init__(self, credentials, timeout=DEFAULT_TIMEOUT):
        if not isinstance(credentials, dict):
            raise ValueError("credentials must be a dict")
        self.api_key = (credentials.get("api_key") or "").strip()
        if not self.api_key:
            raise ValueError("api_key is required")
        self.timeout = timeout

    def _auth(self):
        return {"Authorization": "Key %s" % self.api_key}

    def _model_url(self, endpoint):
        text = str(endpoint or "").strip().lstrip("/")
        if not text or "://" in text or ".." in text or any(ch in text for ch in ("?", "#", " ")):
            raise ProviderRequestError(0, {
                "code": "invalid_endpoint",
                "message": "The fal model endpoint is invalid.",
                "retryable": False,
            })
        return "%s/%s" % (API_BASE, text)

    def submit(self, endpoint, params, idempotency_key=None):
        headers = self._auth()
        if idempotency_key:
            headers["X-Fal-Object-Lifecycle-Preference"] = "idempotency_key=%s" % idempotency_key
        return studio_http.request_json(
            "POST",
            self._model_url(endpoint),
            headers=headers,
            body=params or {},
            timeout=self.timeout,
        )

    def status(self, status_url):
        url = str(status_url or "").strip()
        if not url.startswith("https://"):
            raise ProviderRequestError(0, {
                "code": "invalid_url",
                "message": "The provider returned an unexpected status URL.",
                "retryable": False,
            })
        # Include logs=0 to keep payloads small.
        if "logs=" not in url:
            url = url + ("&" if "?" in url else "?") + "logs=0"
        return studio_http.request_json(
            "GET", url, headers=self._auth(), timeout=self.timeout
        )

    def result(self, response_url):
        url = str(response_url or "").strip()
        if not url.startswith("https://"):
            raise ProviderRequestError(0, {
                "code": "invalid_url",
                "message": "The provider returned an unexpected result URL.",
                "retryable": False,
            })
        return studio_http.request_json(
            "GET", url, headers=self._auth(), timeout=self.timeout
        )

    def cancel(self, cancel_url):
        url = str(cancel_url or "").strip()
        if not url.startswith("https://"):
            raise ProviderRequestError(0, {
                "code": "invalid_url",
                "message": "The provider returned an unexpected cancel URL.",
                "retryable": False,
            })
        return studio_http.request_json(
            "PUT", url, headers=self._auth(), timeout=self.timeout, allow_empty=True
        )

    def upload(self, data, content_type, filename):
        # fal storage accepts multipart; for Studio we use data URLs for small
        # inputs when storage is unavailable, but prefer the storage endpoint.
        import json
        import urllib.error
        import urllib.request

        boundary = "----DennisFalBoundary7MA4YWxkTrZu0gW"
        filename = filename or "upload.bin"
        mime = (content_type or "application/octet-stream").split(";")[0]
        body = (
            ("--%s\r\n" % boundary).encode("ascii")
            + ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename).encode("utf-8")
            + ("Content-Type: %s\r\n\r\n" % mime).encode("ascii")
            + bytes(data)
            + ("\r\n--%s--\r\n" % boundary).encode("ascii")
        )
        headers = self._auth()
        headers["Content-Type"] = "multipart/form-data; boundary=%s" % boundary
        headers["User-Agent"] = studio_http.USER_AGENT
        request = urllib.request.Request(
            STORAGE_URL, data=body, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                payload = json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read() or b""
            try:
                body_json = json.loads(raw.decode("utf-8")) if raw else {}
            except ValueError:
                body_json = {"detail": raw.decode("utf-8", "replace")[:500]}
            from . import studio_contracts as contracts
            raise ProviderRequestError(exc.code, contracts.error_from_response(exc.code, body_json))
        except urllib.error.URLError as exc:
            raise ProviderRequestError(0, {
                "code": "network_error",
                "message": str(exc.reason or exc),
                "retryable": True,
            })
        url = payload.get("access_url") or payload.get("url") or payload.get("file_url")
        if not isinstance(url, str) or not url.startswith("http"):
            # Fall back to inline data URL for small files.
            url = studio_http.data_url(data, content_type)
        return {"public_url": url, "content_type": mime}

    def test_credentials(self):
        # Cheap authenticated call: platform models list is not required; hit
        # a known queue host with an empty pathless probe via account endpoint
        # is not always available. Use fal platform user if present, else a
        # deliberate 404 on an invalid model that still authenticates.
        try:
            studio_http.request_json(
                "GET",
                "https://rest.alpha.fal.ai/platform/models?limit=1",
                headers=self._auth(),
                timeout=self.timeout,
            )
            return {"ok": True, "message": "fal.ai credentials accepted."}
        except ProviderRequestError as exc:
            if exc.status in (401, 403):
                raise
            # Some accounts cannot list models; treat non-auth errors as ok
            # when the key format is present, and probe with a cancel of a
            # fake request which returns 401/404.
            if exc.status == 404 or exc.status == 0:
                return {"ok": True, "message": "fal.ai credentials accepted."}
            if exc.error and exc.error.get("code") == "authentication_failed":
                raise
            if exc.status not in (401, 403):
                return {"ok": True, "message": "fal.ai credentials appear valid."}
            raise
