"""Text-generation adapter for YouTube Studio production agents.

Reuses the DeepSeek Harness credential storage (``data/deepseek/credentials.json``
or ``DEEPSEEK_API_KEY``) but performs plain, non-streaming Chat Completions
calls so background production jobs can request a full completion without
touching the interactive chat state.

Environment overrides allow any OpenAI-compatible endpoint:

* ``DENNIS_YT_LLM_BASE_URL`` (default: DeepSeek base URL)
* ``DENNIS_YT_LLM_API_KEY`` (default: DeepSeek key)
* ``DENNIS_YT_LLM_MODEL``   (default: DeepSeek default model)

Honesty rules: an unconfigured key is reported as such, upstream errors are
surfaced verbatim (shortened, redacted) and no output is ever invented.
"""

from __future__ import annotations

import json
import os

from . import deepseek_harness
from . import studio_http
from . import youtube_common as common

DEFAULT_TIMEOUT = 240
MAX_OUTPUT_TOKENS = 8000
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 120_000


class LLMError(common.YoutubeError):
    pass


def _base_url():
    override = (os.environ.get("DENNIS_YT_LLM_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    return deepseek_harness.base_url()


def _api_key():
    override = (os.environ.get("DENNIS_YT_LLM_API_KEY") or "").strip()
    if override:
        return override, "env"
    key, source = deepseek_harness.api_key()
    return key, source


def _model():
    override = (os.environ.get("DENNIS_YT_LLM_MODEL") or "").strip()
    if override:
        return override
    return deepseek_harness.default_model()


def status():
    key, source = _api_key()
    result = {
        "provider": "openai_compatible",
        "base_url": _base_url(),
        "model": _model(),
        "configured": bool(key),
        "source": source if key else "none",
        "hint": ("..." + key[-4:]) if key and len(key) >= 4 else None,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    if not key:
        result["hint_setup"] = (
            "Add a DeepSeek API key in the Workspace (DeepSeek Harness) or set "
            "DENNIS_YT_LLM_API_KEY / DENNIS_YT_LLM_BASE_URL / DENNIS_YT_LLM_MODEL."
        )
    return result


def _validate_messages(messages):
    if not isinstance(messages, list) or not messages:
        raise LLMError("invalid_messages", "messages must be a non-empty list.")
    if len(messages) > MAX_MESSAGES:
        raise LLMError("invalid_messages", "Too many messages (limit %d)." % MAX_MESSAGES)
    cleaned = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise LLMError("invalid_messages", "Each message must be an object.")
        role = str(message.get("role") or "").strip().lower()
        if role not in ("system", "user", "assistant"):
            raise LLMError("invalid_messages", "Message %d has an unsupported role." % index)
        content = common.clean_text(message.get("content"), "Message %d content" % index, MAX_MESSAGE_CHARS, required=True)
        cleaned.append({"role": role, "content": content})
    return cleaned


def complete(messages, model=None, temperature=0.6, max_tokens=MAX_OUTPUT_TOKENS, timeout=DEFAULT_TIMEOUT):
    """Run one Chat Completions request and return the assistant text."""
    key, _source = _api_key()
    if not key:
        raise LLMError(
            "llm_not_configured",
            "No text-generation API key is configured. Add a DeepSeek API key or "
            "set DENNIS_YT_LLM_* environment variables.",
            409,
        )
    cleaned = _validate_messages(messages)
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        temperature = 0.6
    temperature = max(0.0, min(2.0, temperature))
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        max_tokens = MAX_OUTPUT_TOKENS
    max_tokens = max(256, min(16000, max_tokens))

    payload = {
        "model": common.clean_text(model, "Model", 120) or _model(),
        "messages": cleaned,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        response = studio_http.request_json(
            "POST",
            _base_url() + "/chat/completions",
            headers={"Authorization": "Bearer %s" % key},
            body=payload,
            timeout=max(30, min(600, int(timeout))),
            retries=0,
        )
    except studio_http.ProviderRequestError as exc:
        error = exc.error or {}
        message = str(error.get("message") or "")
        # DeepSeek-style bodies nest the real message under {"error": {...}};
        # surface the human message instead of the raw JSON envelope.
        if message.lstrip().startswith("{"):
            try:
                parsed = json.loads(message)
                nested = parsed.get("error") if isinstance(parsed, dict) else None
                if isinstance(nested, dict) and nested.get("message"):
                    message = str(nested["message"])
                elif isinstance(parsed, dict) and parsed.get("message"):
                    message = str(parsed["message"])
            except (ValueError, TypeError):
                pass
        raise LLMError(
            "llm_request_failed",
            message or "The text-generation request failed.",
            502,
        )
    choices = response.get("choices") if isinstance(response, dict) else None
    text = ""
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            text = str(message.get("content") or "")
    if not text.strip():
        raise LLMError(
            "llm_empty_response",
            "The model returned no content. Check the configured model name and try again.",
            502,
        )
    usage = response.get("usage") if isinstance(response, dict) else {}
    return {
        "content": text,
        "model": payload["model"],
        "usage": usage if isinstance(usage, dict) else {},
    }
