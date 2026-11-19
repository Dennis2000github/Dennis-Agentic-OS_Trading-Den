"""Runway developer API client.

Documented surface used here (https://docs.dev.runwayml.com/api):

* ``POST /v1/text_to_video`` / ``POST /v1/image_to_video`` - returns a task id
* ``GET  /v1/tasks/{id}`` - ``status`` is PENDING / RUNNING / SUCCEEDED /
  FAILED, plus ``progress`` (0-1), ``output`` (list of URLs) and ``failure``
* ``DELETE /v1/tasks/{id}`` - cancels a task that has not finished
* ``GET  /v1/avatars?limit=1`` - cheap authenticated call for credential tests

Auth is ``Authorization: Bearer <secret>`` plus the required
``X-Runway-Version`` header. Inputs accept HTTPS URLs, Runway URIs or data
URIs, so uploads are encoded inline instead of using the ephemeral-upload
endpoint.
"""

from __future__ import annotations

from . import studio_http

API_BASE = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"
DEFAULT_TIMEOUT = 45

ProviderRequestError = studio_http.ProviderRequestError


def _clean_task_id(value):
    text = str(value or "").strip()
    if not text or "/" in text or "?" in text or " " in text or len(text) > 200:
        raise ProviderRequestError(0, {
            "code": "invalid_job_id",
            "message": "The provider returned an unexpected task id.",
            "retryable": False,
        })
    return text


class RunwayClient:
    def __init__(self, credentials, base_url=API_BASE, timeout=DEFAULT_TIMEOUT):
        if not isinstance(credentials, dict):
            raise ValueError("credentials must be a dict")
        self.api_key = (credentials.get("api_key") or "").strip()
        if not self.api_key:
            raise ValueError("api_key is required")
        self.base_url = (base_url or API_BASE).rstrip("/")
        self.timeout = timeout

    def _headers(self):
        return {
            "Authorization": "Bearer %s" % self.api_key,
            "X-Runway-Version": API_VERSION,
        }

    def _request(self, method, path, body=None, allow_empty=False):
        return studio_http.request_json(
            method,
            self.base_url + path,
            headers=self._headers(),
            body=body,
            timeout=self.timeout,
            allow_empty=allow_empty,
        )

    def submit(self, endpoint, params, idempotency_key=None):
        # Runway documents no idempotency header; de-duplication happens in the
        # Studio runner via the request hash before a task is created.
        payload = self._request("POST", endpoint, body=params or {})
        task_id = payload.get("id")
        if not isinstance(task_id, str) or not task_id:
            raise ProviderRequestError(0, {
                "code": "unexpected_response",
                "message": "Runway accepted the task but returned no id.",
                "retryable": False,
            })
        return payload

    def status(self, task_id):
        return self._request("GET", "/v1/tasks/%s" % _clean_task_id(task_id))

    def cancel(self, task_id):
        return self._request(
            "DELETE", "/v1/tasks/%s" % _clean_task_id(task_id), allow_empty=True
        )

    def upload(self, data, content_type, filename):
        return {"public_url": studio_http.data_url(data, content_type), "content_type": content_type}

    def test_credentials(self):
        payload = self._request("GET", "/v1/avatars?limit=1")
        return {
            "ok": True,
            "message": "Runway credentials accepted.",
            "details": {"avatars": len(payload.get("data") or [])},
        }
