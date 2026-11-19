"""Luma AI Dream Machine API client.

Documented surface (https://docs.lumalabs.ai/docs/video-generation):

* ``POST   /v1/generations`` - create a generation, returns ``id``/``state``
* ``GET    /v1/generations/{id}`` - ``state`` is queued / dreaming / completed
  / failed, ``assets.video`` holds the output URL, ``failure_reason`` the error
* ``DELETE /v1/generations/{id}`` - cancels/deletes a generation
* ``GET    /v1/generations?limit=1`` - cheap authenticated call

Auth is ``Authorization: Bearer <key>``. Luma only accepts public image URLs
for keyframes, so Studio uploads are not supported for this provider; the UI
asks for an https URL instead of pretending an upload worked.
"""

from __future__ import annotations

from . import studio_http

API_BASE = "https://api.lumalabs.ai/dream-machine/v1"
DEFAULT_TIMEOUT = 45

ProviderRequestError = studio_http.ProviderRequestError


def _clean_id(value):
    text = str(value or "").strip()
    if not text or "/" in text or "?" in text or " " in text or len(text) > 200:
        raise ProviderRequestError(0, {
            "code": "invalid_job_id",
            "message": "The provider returned an unexpected generation id.",
            "retryable": False,
        })
    return text


class LumaClient:
    def __init__(self, credentials, base_url=API_BASE, timeout=DEFAULT_TIMEOUT):
        if not isinstance(credentials, dict):
            raise ValueError("credentials must be a dict")
        self.api_key = (credentials.get("api_key") or "").strip()
        if not self.api_key:
            raise ValueError("api_key is required")
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.timeout = timeout

    def _headers(self):
        return {"Authorization": "Bearer %s" % self.api_key}

    def _request(self, method, path, body=None, allow_empty=False):
        return studio_http.request_json(
            method,
            self.base_url + path,
            headers=self._headers(),
            body=body,
            timeout=self.timeout,
            allow_empty=allow_empty,
        )

    def submit(self, params, idempotency_key=None):
        payload = self._request("POST", "/generations", body=params or {})
        generation_id = payload.get("id")
        if not isinstance(generation_id, str) or not generation_id:
            raise ProviderRequestError(0, {
                "code": "unexpected_response",
                "message": "Luma accepted the generation but returned no id.",
                "retryable": False,
            })
        return payload

    def status(self, generation_id):
        return self._request("GET", "/generations/%s" % _clean_id(generation_id))

    def cancel(self, generation_id):
        return self._request(
            "DELETE", "/generations/%s" % _clean_id(generation_id), allow_empty=True
        )

    def test_credentials(self):
        payload = self._request("GET", "/generations?limit=1")
        count = len(payload.get("generations") or []) if isinstance(payload, dict) else 0
        return {
            "ok": True,
            "message": "Luma credentials accepted.",
            "details": {"generations": count},
        }
