"""Text-generation adapter for YouTube Studio production agents.

Two selectable provider profiles (default: GLM-5.3-Flash through the local
OpenCode CLI, per the YouTube Studio master specification):

* ``opencode`` — runs the installed OpenCode CLI headless
  (``opencode run -m <model> <prompt>``). Default model
  ``nvidia/z-ai/glm-5.3-flash``. No API key is needed from this module: the
  CLI uses the owner's own configured providers.
* ``openai_compatible`` — plain, non-streaming Chat Completions against any
  OpenAI-compatible endpoint. Reuses the DeepSeek Harness credential storage
  (``data/deepseek/credentials.json`` or ``DEEPSEEK_API_KEY``) by default.

Selection order (first match wins):

1. the YouTube workspace settings (``Settings → Generation`` in the UI,
   persisted via :func:`configure`);
2. environment overrides (``DENNIS_YT_LLM_PROVIDER`` and, for the
   openai_compatible profile, ``DENNIS_YT_LLM_BASE_URL`` / ``_API_KEY`` /
   ``_MODEL``; ``DENNIS_YT_LLM_OPENCODE_MODEL`` for the CLI profile);
3. built-in defaults (opencode + GLM-5.3-Flash).

Honesty rules: an unconfigured or missing backend is reported as such, upstream
errors are surfaced verbatim (shortened, redacted) and no output is ever
invented.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from . import deepseek_harness
from . import studio_http
from . import studio_workspaces
from . import youtube_common as common

DEFAULT_TIMEOUT = 240
MAX_OUTPUT_TOKENS = 8000
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 120_000

PROFILES = ("opencode", "openai_compatible")
DEFAULT_PROFILE = "opencode"
DEFAULT_OPENCODE_MODEL = "nvidia/z-ai/glm-5.3-flash"
MAX_PROMPT_CHARS = 400_000

PROFILE_LABELS = {
    "opencode": "OpenCode CLI (GLM-5.3-Flash by default)",
    "openai_compatible": "OpenAI-compatible API (DeepSeek by default)",
}


class LLMError(common.YoutubeError):
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _settings_llm():
    try:
        settings = studio_workspaces.get_settings("youtube")
    except Exception:  # pragma: no cover - settings store always readable
        return {}
    block = settings.get("llm") if isinstance(settings, dict) else None
    return block if isinstance(block, dict) else {}


def profile():
    """Resolve the active provider profile id."""
    stored = str(_settings_llm().get("provider") or "").strip().lower()
    if stored in PROFILES:
        return stored
    override = (os.environ.get("DENNIS_YT_LLM_PROVIDER") or "").strip().lower()
    if override in PROFILES:
        return override
    return DEFAULT_PROFILE


def _opencode_binary():
    override = (os.environ.get("DENNIS_OPENCODE_BIN") or "").strip()
    if override:
        return override
    home_bin = os.path.expanduser("~/.opencode/bin")
    path = os.environ.get("PATH", "")
    if home_bin and home_bin not in path:
        path = path + os.pathsep + home_bin if path else home_bin
    return shutil.which("opencode", path=path)


def _base_url():
    override = (os.environ.get("DENNIS_YT_LLM_BASE_URL") or "").strip()
    if override:
        return override.rstrip("/")
    return deepseek_harness.base_url()


def _api_key():
    override = (os.environ.get("DENNIS_YT_LLM_API_KEY") or "").strip()
    if override:
        return override, "env"
    return deepseek_harness.api_key()


def _openai_model():
    override = (os.environ.get("DENNIS_YT_LLM_MODEL") or "").strip()
    if override:
        return override
    return deepseek_harness.default_model()


def model():
    """Resolve the model id for the active profile."""
    stored = str(_settings_llm().get("model") or "").strip()
    if stored:
        return stored
    active = profile()
    if active == "opencode":
        override = (os.environ.get("DENNIS_YT_LLM_OPENCODE_MODEL") or "").strip()
        return override or DEFAULT_OPENCODE_MODEL
    return _openai_model()


def status():
    active = profile()
    result = {
        "provider": active,
        "provider_label": PROFILE_LABELS.get(active, active),
        "model": model(),
        "base_url": _base_url() if active == "openai_compatible" else None,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "profiles": [
            {"id": item, "label": PROFILE_LABELS.get(item, item), "default_model": DEFAULT_OPENCODE_MODEL if item == "opencode" else _openai_model()}
            for item in PROFILES
        ],
    }
    if active == "opencode":
        binary = _opencode_binary()
        result["configured"] = bool(binary)
        result["source"] = "cli" if binary else "none"
        result["binary"] = binary
        result["hint"] = binary
        if not binary:
            result["hint_setup"] = (
                "The OpenCode CLI was not found on this machine. Install it or "
                "point DENNIS_OPENCODE_BIN at the binary, or switch the profile "
                "to an OpenAI-compatible API."
            )
        return result
    key, source = _api_key()
    result["configured"] = bool(key)
    result["source"] = source if key else "none"
    result["hint"] = ("..." + key[-4:]) if key and len(key) >= 4 else None
    if not key:
        result["hint_setup"] = (
            "Add a DeepSeek API key in the Workspace (DeepSeek Harness) or set "
            "DENNIS_YT_LLM_API_KEY / DENNIS_YT_LLM_BASE_URL / DENNIS_YT_LLM_MODEL."
        )
    return result


def configure(body):
    """Persist the profile selection in the YouTube workspace settings."""
    data = body if isinstance(body, dict) else {}
    provider = str(data.get("provider") or "").strip().lower()
    if provider not in PROFILES:
        raise LLMError(
            "invalid_provider",
            "provider must be one of: %s." % ", ".join(PROFILES),
            400,
        )
    raw_model = str(data.get("model") or "").strip()
    model_value = common.clean_text(raw_model, "Model", 120) if raw_model else ""
    # The settings store deep-merges patches: always send an explicit model key
    # so switching profiles cannot inherit the previous profile's model.
    block = {"provider": provider, "model": model_value or None}
    studio_workspaces.update_settings("youtube", {"llm": block})
    return status()


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------
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


def _flatten_messages(cleaned):
    """Turn a chat transcript into one prompt for CLI profiles."""
    parts = []
    for message in cleaned:
        role = message["role"]
        content = message["content"]
        if role == "system":
            parts.append(content)
        elif role == "assistant":
            parts.append("ASSISTANT:\n" + content)
        else:
            parts.append("USER:\n" + content)
    text = "\n\n".join(parts).strip()
    if len(text) > MAX_PROMPT_CHARS:
        raise LLMError(
            "prompt_too_long",
            "The composed prompt exceeds the %d character limit for the CLI profile." % MAX_PROMPT_CHARS,
            400,
        )
    return text


def _complete_opencode(cleaned, model_id, timeout):
    binary = _opencode_binary()
    if not binary:
        raise LLMError(
            "llm_not_configured",
            "The OpenCode CLI was not found. Install it, set DENNIS_OPENCODE_BIN, "
            "or switch the profile to an OpenAI-compatible API.",
            409,
        )
    prompt = _flatten_messages(cleaned)
    argv = [binary, "run"]
    if model_id:
        argv += ["-m", model_id]
    argv.append(prompt)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=max(30, min(600, int(timeout))),
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMError("llm_timeout", "The OpenCode CLI did not answer in time.", 504)
    except OSError as exc:
        raise LLMError("llm_request_failed", "Could not run the OpenCode CLI: %s" % exc, 502)
    text = (result.stdout or "").strip()
    if result.returncode != 0 and not text:
        detail = (result.stderr or result.stdout or "").strip()
        raise LLMError(
            "llm_request_failed",
            common.clean_text(detail, "OpenCode error", 400) or "The OpenCode CLI failed.",
            502,
        )
    if not text.strip():
        raise LLMError(
            "llm_empty_response",
            "The model returned no content. Check the configured model name and try again.",
            502,
        )
    return {"content": text, "model": model_id, "usage": {}}


def _complete_openai_compatible(cleaned, model_id, temperature, max_tokens, timeout, key):
    if not key:
        raise LLMError(
            "llm_not_configured",
            "No text-generation API key is configured. Add a DeepSeek API key or "
            "set DENNIS_YT_LLM_API_KEY / DENNIS_YT_LLM_BASE_URL / DENNIS_YT_LLM_MODEL.",
            409,
        )
    payload = {
        "model": model_id,
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
    return {"content": text, "model": model_id, "usage": usage if isinstance(usage, dict) else {}}


def complete(messages, model_id=None, temperature=0.6, max_tokens=MAX_OUTPUT_TOKENS, timeout=DEFAULT_TIMEOUT):
    """Run one completion with the active profile and return the assistant text."""
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
    resolved_model = common.clean_text(model_id, "Model", 120) or model()
    if profile() == "opencode":
        return _complete_opencode(cleaned, resolved_model, timeout)
    key, _source = _api_key()
    return _complete_openai_compatible(cleaned, resolved_model, temperature, max_tokens, timeout, key)
