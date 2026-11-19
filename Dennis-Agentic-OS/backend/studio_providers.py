"""Studio provider registry and adapters.

Two adapters share one contract:

* ``higgsfield`` - the official async HTTP API (default, synchronous writes).
* ``higgsfield_mcp`` - an optional local MCP server (``higgsfield-mcp-unified``
  by default), which adds cost estimates but is experimental and must be
  explicitly configured.

Adapters normalize every response to ``studio_contracts`` shapes. Provider
errors are raised as ``ProviderError`` so the HTTP layer can return a stable
JSON error without leaking credentials.
"""

from __future__ import annotations

import threading
import time

from . import studio_catalog as catalog
from . import studio_contracts as contracts
from . import studio_higgsfield
from . import studio_mcp
from . import studio_media
from . import studio_secrets

HIGGSFIELD_ID = "higgsfield"
MCP_ID = "higgsfield_mcp"


class ProviderError(Exception):
    def __init__(self, code, message, status=502, retryable=False):
        self.code = code
        self.message = contracts.redact_text(message)
        self.status = int(status or 502)
        self.retryable = bool(retryable)
        super().__init__(self.message)

    def as_error(self):
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


def _from_request_error(exc):
    error = exc.error or {}
    return ProviderError(
        error.get("code") or "provider_error",
        error.get("message") or str(exc),
        status=exc.status if 400 <= exc.status < 600 else 502,
        retryable=bool(error.get("retryable")),
    )


class BaseProvider:
    id = ""
    label = ""
    docs_url = ""
    experimental = False

    def credentials(self):
        return {"configured": False, "source": "none", "hint": None, "missing": []}

    def descriptor(self):
        creds = self.credentials()
        try:
            count = len(self.models())
            model_error = None
        except Exception as exc:  # keep /providers usable even if a catalog broke
            count = 0
            model_error = str(exc)
        return {
            "id": self.id,
            "label": self.label,
            "docs_url": self.docs_url,
            "experimental": self.experimental,
            "configured": bool(creds.get("configured")),
            "credentials": creds,
            "available": bool(creds.get("configured")),
            "model_count": count,
            "model_error": model_error,
            "capabilities": self.capabilities(),
            "notes": self.notes(),
        }

    def capabilities(self):
        return {
            "upload": True,
            "cancel": "queued_only",
            "progress": False,
            "estimate": False,
            "polling": True,
            "webhook": True,
        }

    def notes(self):
        return []

    def models(self, kind=None, mode=None, query=None):
        raise NotImplementedError

    def model_detail(self, model_id):
        raise NotImplementedError

    def model(self, model_id):
        raise NotImplementedError

    def submit(self, model, params, idempotency_key):
        raise NotImplementedError

    def status(self, job):
        raise NotImplementedError

    def cancel(self, job):
        raise NotImplementedError

    def upload(self, data, content_type, filename):
        raise NotImplementedError

    def test(self):
        raise NotImplementedError

    def connect(self):
        return self.descriptor()


class HiggsfieldOfficialProvider(BaseProvider):
    id = HIGGSFIELD_ID
    label = "Higgsfield API"
    docs_url = "https://docs.higgsfield.ai"
    experimental = False

    def __init__(self):
        self._client_cache = None
        self._client_key = None

    def credentials(self):
        return studio_secrets.status(HIGGSFIELD_ID)

    def _credentials(self):
        values, source = studio_secrets.get(HIGGSFIELD_ID)
        if not values:
            raise ProviderError(
                "credentials_missing",
                "Add a Higgsfield API key and secret in Studio setup first.",
                status=400,
            )
        return values

    def _client(self):
        values = self._credentials()
        key = (values.get("api_key"), values.get("secret"))
        if self._client_cache is None or self._client_key != key:
            self._client_cache = studio_higgsfield.HiggsfieldClient(values)
            self._client_key = key
        return self._client_cache

    def models(self, kind=None, mode=None, query=None):
        # Browsing is public catalog data; credentials are only required to
        # submit, upload or verify.
        return catalog.list_models(HIGGSFIELD_ID, kind=kind, mode=mode, query=query)

    def model_detail(self, model_id):
        return catalog.model_detail(model_id, HIGGSFIELD_ID)

    def model(self, model_id):
        return catalog.get_model(model_id, HIGGSFIELD_ID)

    def submit(self, model, params, idempotency_key):
        try:
            payload = self._client().submit(model["endpoint"], params, idempotency_key)
        except studio_higgsfield.ProviderRequestError as exc:
            raise _from_request_error(exc)
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ProviderError(
                "unexpected_response",
                "The provider accepted the request but returned no request id.",
            )
        return {
            "provider_request_id": request_id,
            "status_url": payload.get("status_url") or studio_higgsfield.status_path(request_id),
            "cancel_url": payload.get("cancel_url") or studio_higgsfield.cancel_path(request_id),
            "raw_state": payload.get("status") or "queued",
        }

    def _status_url(self, job):
        if job.get("status_url"):
            return job["status_url"]
        return studio_higgsfield.status_path(job.get("provider_request_id"))

    def _cancel_url(self, job):
        if job.get("cancel_url"):
            return job["cancel_url"]
        return studio_higgsfield.cancel_path(job.get("provider_request_id"))

    def status(self, job):
        try:
            payload = self._client().status(self._status_url(job))
        except studio_higgsfield.ProviderRequestError as exc:
            # A transient poll failure must not fail the job; the runner uses
            # the retryable flag to keep polling with backoff.
            raise _from_request_error(exc)
        state, error = contracts.map_state(
            HIGGSFIELD_ID, payload.get("status"), payload.get("error")
        )
        return {
            "state": state,
            "raw_state": payload.get("status"),
            "error": error,
            "outputs": contracts.normalize_outputs(payload, job.get("kind") or "video")
            if state == "succeeded"
            else [],
        }

    def cancel(self, job):
        if job.get("state") != "queued":
            raise ProviderError(
                "cancel_not_allowed",
                "Only requests that are still queued can be cancelled.",
                status=409,
            )
        try:
            self._client().cancel(self._cancel_url(job))
        except studio_higgsfield.ProviderRequestError as exc:
            raise _from_request_error(exc)
        return {"state": "cancelled"}

    def upload(self, data, content_type, filename):
        try:
            result = self._client().upload(data, content_type, filename)
        except studio_higgsfield.ProviderRequestError as exc:
            raise _from_request_error(exc)
        media = None
        try:
            media = studio_media.add_bytes(
                data,
                content_type,
                filename=filename,
                meta={"role": "input", "provider": self.id, "title": filename},
            )
        except studio_media.MediaError:
            media = None
        return {
            "public_url": result["public_url"],
            "content_type": result["content_type"],
            "media": media,
        }

    def test(self):
        try:
            return self._client().test_credentials()
        except studio_higgsfield.ProviderRequestError as exc:
            raise _from_request_error(exc)

    def notes(self):
        notes = [
            "Official API. Output URLs are retained by Higgsfield for at least 7 days.",
            "Cancel is available only while a request is still queued.",
        ]
        if not self.credentials().get("configured"):
            notes.insert(0, "Credentials are not configured.")
        return notes


_MCP_LOCK = threading.RLock()
_MCP_SESSION = None
_MCP_TOOLS = None
_MCP_MODELS = None
_MCP_MODELS_AT = 0
_MCP_ERROR = None
_MCP_INFO = None
MCP_MODEL_TTL = 120


def _mcp_tools(session):
    tools = session.list_tools()
    return {tool.get("name"): tool for tool in tools if isinstance(tool, dict)}


def _mcp_schema_from_tool(tool):
    schema = tool.get("inputSchema")
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        # Matches the official params object: {model_id, ..., extra_params}
        props = dict(schema["properties"])
        props.pop("model_id", None)
        props.pop("extra_params", None)
        required = [x for x in schema.get("required") or [] if x in props]
        return {"type": "object", "properties": props, "required": required}
    return None


def _infer_schema(name):
    """Conservative schema for models the MCP registry knows but we do not."""
    if name.endswith("_urls") or name in ("image_urls", "video_urls", "audio_urls"):
        return {"type": "array", "items": {"type": "string", "format": "uri"}}
    if name.endswith("_url") or name in ("image_url", "video_url", "audio_url"):
        return {"type": "string", "format": "uri"}
    if name in ("generate_audio", "sound", "prompt_extend", "enable_thinking"):
        return {"type": "boolean"}
    if name in ("duration", "seed", "batch_size", "fps"):
        return {"type": "integer"}
    return {"type": "string"}


def _mcp_entry_model(entry, provider_id):
    """Build a normalized model from a raw ``list_models`` entry."""
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    kind = entry.get("kind") if entry.get("kind") in contracts.KINDS else "video"
    constraints = entry.get("constraints")
    schema = None
    if isinstance(constraints, dict) and isinstance(constraints.get("properties"), dict):
        schema = constraints
    else:
        supports = entry.get("supports") or []
        if isinstance(supports, list):
            props = {}
            for name in supports:
                if isinstance(name, str):
                    props[name] = _infer_schema(name)
            if props:
                schema = {"type": "object", "properties": props}
    if not schema:
        return None
    mode = entry.get("mode") or ("text_to_video" if kind == "video" else "text_to_image")
    model = {
        "id": model_id,
        "label": entry.get("name") or entry.get("label") or model_id,
        "kind": kind,
        "mode": mode,
        "schema": schema,
        "provider": provider_id,
        "generation": True,
    }
    public = {
        "id": model_id,
        "label": model["label"],
        "kind": kind,
        "mode": mode,
        "mode_label": contracts.MODE_LABELS.get(mode, mode),
        "provider": provider_id,
        "doc_path": None,
        "summary": contracts.derive_summary(model),
        "capabilities": dict(contracts.capabilities_for(model, provider_id), estimate=True),
        "tags": ["MCP"],
    }
    return {"model": model, "public": public}


class HiggsfieldMcpProvider(BaseProvider):
    id = MCP_ID
    label = "Higgsfield MCP"
    docs_url = "https://github.com/Hikhakk/higgsfield-mcp-unified"
    experimental = True

    def credentials(self):
        config = studio_mcp.public_config()
        return {
            "configured": bool(config.get("configured")),
            "source": "mcp_config" if config.get("configured") else "none",
            "hint": config.get("command"),
            "missing": [] if config.get("configured") else ["command"],
            "env_override": False,
        }

    def capabilities(self):
        return {
            "upload": True,
            "cancel": "queued_only",
            "progress": False,
            "estimate": True,
            "polling": True,
            "webhook": True,
        }

    def notes(self):
        notes = ["Experimental: runs a local MCP process and is not the primary path."]
        if not self.credentials().get("configured"):
            notes.append("No MCP server is configured.")
        elif _MCP_ERROR:
            notes.append("Last connection error: %s" % _MCP_ERROR)
        return notes

    def descriptor(self):
        creds = self.credentials()
        return {
            "id": self.id,
            "label": self.label,
            "docs_url": self.docs_url,
            "experimental": self.experimental,
            "configured": bool(creds.get("configured")),
            "credentials": creds,
            "available": bool(_MCP_SESSION is not None and _MCP_ERROR is None),
            "connected": bool(_MCP_SESSION is not None and _MCP_ERROR is None),
            "model_count": len(_MCP_MODELS or []),
            "model_error": _MCP_ERROR,
            "capabilities": self.capabilities(),
            "notes": self.notes(),
            "mcp": studio_mcp.public_config(),
            "tools": sorted((_MCP_TOOLS or {}).keys()),
        }

    # -- connection --------------------------------------------------------
    def connect(self):
        global _MCP_SESSION, _MCP_TOOLS, _MCP_MODELS, _MCP_MODELS_AT, _MCP_ERROR, _MCP_INFO
        with _MCP_LOCK:
            if _MCP_SESSION is not None:
                return self.descriptor()
            if not self.credentials().get("configured"):
                raise ProviderError("mcp_not_configured", "Configure an MCP server first.", 400)
            session = studio_mcp.client_from_config(timeout=45)
            try:
                session.start()
                _MCP_INFO = session.initialize()
                _MCP_TOOLS = _mcp_tools(session)
            except studio_mcp.McpError as exc:
                session.close()
                _MCP_ERROR = str(exc)
                raise ProviderError("mcp_connect_failed", str(exc), 502, retryable=True)
            except Exception as exc:  # pragma: no cover - defensive
                session.close()
                _MCP_ERROR = str(exc)
                raise ProviderError("mcp_connect_failed", str(exc), 502)
            _MCP_SESSION = session
            _MCP_MODELS = None
            _MCP_MODELS_AT = 0
            _MCP_ERROR = None
            return self.descriptor()

    def disconnect(self):
        global _MCP_SESSION, _MCP_TOOLS, _MCP_MODELS, _MCP_ERROR, _MCP_INFO
        with _MCP_LOCK:
            if _MCP_SESSION is not None:
                _MCP_SESSION.close()
            _MCP_SESSION = None
            _MCP_TOOLS = None
            _MCP_MODELS = None
            _MCP_MODELS_AT = 0
            _MCP_INFO = None
            _MCP_ERROR = None
        return self.descriptor()

    def _session(self):
        if _MCP_SESSION is None or _MCP_ERROR:
            self.connect()
        return _MCP_SESSION

    def _tool(self, *names):
        tools = _MCP_TOOLS or {}
        for name in names:
            if name in tools:
                return name
        raise ProviderError(
            "mcp_tool_missing",
            "The MCP server does not expose %s." % " / ".join(names),
            502,
        )

    def _models_from_session(self):
        global _MCP_MODELS, _MCP_MODELS_AT, _MCP_ERROR
        session = self._session()
        now = time.time()
        if _MCP_MODELS is not None and now - _MCP_MODELS_AT < MCP_MODEL_TTL:
            return _MCP_MODELS
        entries = []
        if "list_models" in (_MCP_TOOLS or {}):
            try:
                result = session.call_tool("list_models", {})
                payload = studio_mcp.McpClient.payload(result)
                raw = payload.get("models") if isinstance(payload.get("models"), list) else payload.get("items")
                if isinstance(raw, list):
                    entries = raw
            except studio_mcp.McpError as exc:
                _MCP_ERROR = str(exc)
                raise ProviderError("mcp_request_failed", str(exc), 502, retryable=True)
        _MCP_MODELS = [x for x in entries if isinstance(x, dict) and x.get("id")]
        _MCP_MODELS_AT = now
        return _MCP_MODELS

    def models(self, kind=None, mode=None, query=None):
        if not self.credentials().get("configured"):
            return []
        official = catalog.list_models(HIGGSFIELD_ID)
        try:
            entries = self._models_from_session()
        except ProviderError:
            return []
        if not entries:
            models = official
        else:
            by_id = {m["id"]: m for m in official}
            models = []
            for entry in entries:
                model_id = entry.get("id")
                base = by_id.get(model_id)
                if base:
                    model = dict(base)
                    model["provider"] = self.id
                    model["capabilities"] = dict(base["capabilities"], estimate=True)
                    models.append(model)
                    continue
                built = _mcp_entry_model(entry, self.id)
                if built:
                    models.append(built["public"])
        if kind:
            models = [m for m in models if m.get("kind") == kind]
        if mode:
            models = [m for m in models if m.get("mode") == mode]
        if query:
            terms = str(query).lower().split()
            models = [
                m for m in models
                if all(t in ("%s %s %s" % (m["label"], m["id"], m["mode"])).lower() for t in terms)
            ]
        return models

    def model(self, model_id):
        try:
            model = catalog.get_model(model_id, HIGGSFIELD_ID)
            return dict(model, provider=self.id)
        except catalog.CatalogError:
            pass
        try:
            entries = self._models_from_session()
        except ProviderError as exc:
            raise ProviderError("unknown_model", str(exc), 404)
        for entry in entries:
            built = _mcp_entry_model(entry, self.id)
            if built and built["model"]["id"] == model_id:
                return built["model"]
        raise ProviderError("unknown_model", "Unknown model: %s" % model_id, 404)

    def model_detail(self, model_id):
        try:
            detail = catalog.model_detail(model_id, HIGGSFIELD_ID)
            detail["provider"] = self.id
            detail["capabilities"] = dict(detail["capabilities"], estimate=True)
            return detail
        except catalog.CatalogError:
            pass
        model = self.model(model_id)
        detail = {
            "id": model_id,
            "label": model.get("label") or model_id,
            "kind": model.get("kind") or "video",
            "mode": model.get("mode") or "text_to_video",
            "mode_label": contracts.MODE_LABELS.get(model.get("mode"), model.get("mode")),
            "provider": self.id,
            "schema": model["schema"],
            "summary": contracts.derive_summary(model),
            "capabilities": dict(contracts.capabilities_for(model, self.id), estimate=True),
            "tags": ["MCP"],
            "generation": True,
        }
        detail["fields"] = contracts.derive_fields(model)
        return detail

    def _tool_name(self, kind):
        if kind == "image":
            return self._tool("generate_image", "generate_image_tool")
        return self._tool("generate_video", "generate_video_tool")

    def submit(self, model, params, idempotency_key):
        session = self._session()
        kind = model.get("kind") or "video"
        tool = self._tool_name(kind)
        args = {"model_id": model["id"]}
        extra = {}
        for key, value in dict(params).items():
            if key in ("prompt", "image_url", "video_url", "audio_url", "webhook_url"):
                args[key] = value
            else:
                extra[key] = value
        if extra:
            args["extra_params"] = extra
        try:
            result = session.call_tool(tool, args, timeout=120)
            payload = studio_mcp.McpClient.payload(result)
        except studio_mcp.McpError as exc:
            raise ProviderError("mcp_request_failed", str(exc), 502, retryable=exc.retryable)
        handle = None
        for key in ("job_handle", "request_id", "job_id", "id"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                handle = value
                break
        if not handle:
            raise ProviderError(
                "unexpected_response",
                "The MCP server accepted the request but returned no job handle.",
            )
        return {
            "provider_request_id": handle,
            "status_url": None,
            "cancel_url": None,
            "raw_state": payload.get("status") or "queued",
        }

    def status(self, job):
        session = self._session()
        try:
            result = session.call_tool(
                self._tool("get_status", "get_generation_status_tool"),
                {"job_handle": job.get("provider_request_id")},
                timeout=120,
            )
            payload = studio_mcp.McpClient.payload(result)
        except studio_mcp.McpError as exc:
            raise ProviderError("mcp_request_failed", str(exc), 502, retryable=exc.retryable)
        state, error = contracts.map_state(self.id, payload.get("status") or payload.get("state"),
                                           payload.get("error"))
        outputs = []
        if state == "succeeded":
            for key in ("images", "image_urls"):
                for item in payload.get(key) or []:
                    url = item.get("url") if isinstance(item, dict) else item
                    if isinstance(url, str):
                        outputs.append({"url": url, "kind": "image", "content_type": None})
            for key in ("video_url", "audio_url"):
                url = payload.get(key)
                if isinstance(url, str):
                    outputs.append({
                        "url": url,
                        "kind": "video" if "video" in key else "audio",
                        "content_type": None,
                    })
            for item in payload.get("audio_urls") or []:
                url = item.get("url") if isinstance(item, dict) else item
                if isinstance(url, str):
                    outputs.append({"url": url, "kind": "audio", "content_type": None})
            outputs.extend(contracts.normalize_outputs(payload, job.get("kind") or "video"))
            seen = set()
            outputs = [x for x in outputs if not (x["url"] in seen or seen.add(x["url"]))]
        return {
            "state": state,
            "raw_state": payload.get("status") or payload.get("state"),
            "error": error,
            "outputs": outputs,
        }

    def cancel(self, job):
        if job.get("state") != "queued":
            raise ProviderError(
                "cancel_not_allowed",
                "Only requests that are still queued can be cancelled.",
                status=409,
            )
        session = self._session()
        try:
            session.call_tool(
                self._tool("cancel_job", "cancel_generation_tool"),
                {"job_handle": job.get("provider_request_id")},
            )
        except studio_mcp.McpError as exc:
            raise ProviderError("mcp_request_failed", str(exc), 502, retryable=exc.retryable)
        return {"state": "cancelled"}

    def upload(self, data, content_type, filename):
        import base64

        session = self._session()
        try:
            result = session.call_tool(
                self._tool("upload_image"),
                {
                    "data_base64": base64.b64encode(data).decode("ascii"),
                    "mime": content_type,
                },
                timeout=120,
            )
            payload = studio_mcp.McpClient.payload(result)
        except studio_mcp.McpError as exc:
            raise ProviderError("mcp_request_failed", str(exc), 502, retryable=exc.retryable)
        public_url = payload.get("url") or payload.get("public_url")
        if not isinstance(public_url, str):
            raise ProviderError("unexpected_response", "The MCP server returned no upload URL.")
        media = None
        try:
            media = studio_media.add_bytes(
                data, content_type, filename=filename,
                meta={"role": "input", "provider": self.id, "title": filename},
            )
        except studio_media.MediaError:
            media = None
        return {"public_url": public_url, "content_type": content_type, "media": media}

    def test(self):
        session = self._session()
        if "preflight_check" not in (_MCP_TOOLS or {}):
            return {"ok": True, "message": "MCP server connected."}
        try:
            result = session.call_tool("preflight_check", {})
            payload = studio_mcp.McpClient.payload(result)
        except studio_mcp.McpError as exc:
            raise ProviderError("mcp_request_failed", str(exc), 502, retryable=exc.retryable)
        return {
            "ok": True,
            "message": payload.get("message") or "MCP server connected.",
            "details": payload,
        }


class ProviderRegistry:
    def __init__(self):
        self._providers = {
            HIGGSFIELD_ID: HiggsfieldOfficialProvider(),
            MCP_ID: HiggsfieldMcpProvider(),
        }

    def get(self, provider_id):
        provider = self._providers.get(provider_id)
        if not provider:
            raise ProviderError("unknown_provider", "Unknown provider: %s" % provider_id, 404)
        return provider

    def list(self):
        return [self._providers[key].descriptor() for key in (HIGGSFIELD_ID, MCP_ID)]

    def reset_mcp(self):
        self._providers[MCP_ID].disconnect()


REGISTRY = ProviderRegistry()
