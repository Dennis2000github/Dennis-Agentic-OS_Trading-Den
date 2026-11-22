"""Text-generation adapter for Studio and COSMOS production agents.

There are no hard-coded provider profiles. The selected *agent* comes from the
same registry the Workspace uses (``/api/agents/availability``) and the
selected *model* comes from that agent's own catalog
(``/api/agents/<agent_id>/models``, including compatible connection models).

Selection is stored server-side in the workspace settings::

    {"llm": {"agent_id": "opencode", "model": "provider/model-id"}}

An empty model means "the agent CLI's own default". Resolution order:

1. the explicit ``agent_id``/``model_id`` passed to :func:`complete`;
2. the stored workspace selection;
3. ``DEFAULT_AGENT_ID`` (OpenCode CLI) with the CLI's own default model.

Execution follows the normal agent routing rules: CLI agents run a one-shot
print-mode command through their own binary with connection environment
injection; native chat agents use the in-process chat client, optionally
routed through a compatible connection.

Honesty rules: an unregistered or unavailable agent is reported as such,
upstream errors are surfaced verbatim (shortened, redacted) and no output is
ever invented.
"""

from __future__ import annotations

import json
import subprocess
from contextlib import nullcontext

from . import agent_registry
from . import deepseek_harness
from . import execution_router
from . import model_catalog
from . import studio_http
from . import studio_workspaces
from . import youtube_common as common

DEFAULT_TIMEOUT = 240
MAX_OUTPUT_TOKENS = 8000
MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 120_000
MAX_PROMPT_CHARS = 400_000

DEFAULT_AGENT_ID = "opencode"


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


def selection():
    """Return ``{"agent_id", "model"}`` for the active workspace selection."""
    block = _settings_llm()
    agent_id = common.clean_text(block.get("agent_id"), "Agent", 120).strip().lower()
    model_id = common.clean_text(block.get("model"), "Model", 120)
    return {
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "model": model_id,
    }


def agent():
    """Resolve the selected agent from the shared registry."""
    selected = selection()["agent_id"]
    return agent_registry.get_agent(selected)


def model():
    """The stored model id (empty means the agent CLI's own default)."""
    return selection()["model"]


def status():
    """Describe the active agent/model selection for the Studio dropdowns."""
    selected = selection()
    agent_id = selected["agent_id"]
    found = agent_registry.get_agent(agent_id)
    result = {
        "agent_id": agent_id,
        "agent_name": (found or {}).get("name") or agent_id,
        "model": selected["model"],
        "configured": False,
        "source": "none",
    }
    if not found:
        result["hint_setup"] = (
            "The selected agent is no longer registered. Pick an available agent "
            "from the Agent menu."
        )
        return result
    if found.get("transport") == "chat":
        key, source = deepseek_harness.api_key()
        connected = model_catalog.is_connection_model(selected["model"])
        result["configured"] = bool(key or connected)
        result["source"] = "connection" if connected else (source if key else "none")
        result["base_url"] = deepseek_harness.base_url()
        if not result["configured"]:
            result["hint_setup"] = (
                "Add a DeepSeek API key in the Workspace (DeepSeek Harness), or "
                "choose a model from a compatible connection."
            )
        return result
    available, reason = agent_registry.availability(found)
    result["configured"] = available
    result["source"] = "cli" if available else "none"
    result["binary"] = agent_registry.resolve_binary(found)
    if not available:
        result["hint_setup"] = (
            "%s for %s. Pick another agent from the Agent menu or install its CLI."
            % (reason, found.get("name") or agent_id)
        )
    return result


def configure(body):
    """Persist the selected agent + model in the workspace settings."""
    data = body if isinstance(body, dict) else {}
    agent_id = common.clean_text(data.get("agent_id"), "Agent", 120).strip().lower()
    if not agent_id or not agent_registry.get_agent(agent_id):
        raise LLMError(
            "invalid_agent",
            "agent_id must be an agent from /api/agents/availability.",
            400,
        )
    raw_model = common.clean_text(data.get("model"), "Model", 120)
    # The settings store deep-merges patches: always send an explicit model key
    # so switching agents cannot inherit the previous agent's model.
    block = {"agent_id": agent_id, "model": raw_model or None}
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
    """Turn a chat transcript into one prompt for CLI agents."""
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
            "The composed prompt exceeds the %d character limit for CLI agents." % MAX_PROMPT_CHARS,
            400,
        )
    return text


def _complete_cli(cleaned, found, model_id, timeout):
    """Run one non-interactive CLI call using the Workspace agent routing."""
    binary = agent_registry.resolve_binary(found)
    if not binary:
        raise LLMError(
            "llm_not_configured",
            "%s is not installed. Pick another agent from the Agent menu."
            % (found.get("name") or found.get("id") or "The selected agent"),
            409,
        )
    prompt = _flatten_messages(cleaned)
    runtime_model = execution_router.native_model_id(model_id) if model_id else ""
    argv = agent_registry.one_shot_argv(found, prompt, binary=binary, model_id=runtime_model)
    if not argv:
        raise LLMError(
            "llm_not_configured",
            "This agent cannot be used for one-shot text generation. Pick another agent.",
            409,
        )
    try:
        env = agent_registry.get_augmented_env(found.get("id"), model_id)
    except execution_router.RoutingError as exc:
        raise LLMError("llm_not_configured", exc.message or str(exc), 409)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env=env,
            timeout=max(30, min(600, int(timeout))),
        )
    except subprocess.TimeoutExpired:
        raise LLMError("llm_timeout", "The agent did not answer in time.", 504)
    except OSError as exc:
        raise LLMError("llm_request_failed", "Could not run the agent: %s" % exc, 502)
    text = (result.stdout or "").strip()
    if result.returncode != 0 and not text:
        detail = (result.stderr or result.stdout or "").strip()
        raise LLMError(
            "llm_request_failed",
            common.clean_text(detail, "Agent error", 400) or "The agent failed.",
            502,
        )
    if not text:
        raise LLMError(
            "llm_empty_response",
            "The model returned no content. Check the selected model and try again.",
            502,
        )
    return {"content": text, "model": model_id, "agent_id": found.get("id"), "usage": {}}


def _complete_openai_compatible(cleaned, model_id, temperature, max_tokens, timeout, key, base_url):
    if not key:
        raise LLMError(
            "llm_not_configured",
            "No text-generation API key is configured. Add a DeepSeek API key in "
            "the Workspace or choose a model from a compatible connection.",
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
            base_url.rstrip("/") + "/chat/completions",
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
            "The model returned no content. Check the selected model and try again.",
            502,
        )
    usage = response.get("usage") if isinstance(response, dict) else {}
    return {"content": text, "model": model_id, "usage": usage if isinstance(usage, dict) else {}}


def _complete_chat(cleaned, model_id, temperature, max_tokens, timeout):
    """Native chat agent, with connection routing when a conn: model is picked."""
    override_ctx = (
        execution_router.api_endpoint_override(model_id)
        if model_catalog.is_connection_model(model_id)
        else nullcontext(None)
    )
    with override_ctx as override:
        if override:
            base_url = override["base_url"]
            key = override["api_key"]
            call_model = override["model"]
        else:
            base_url = deepseek_harness.base_url()
            key, _source = deepseek_harness.api_key()
            call_model = (
                execution_router.native_model_id(model_id)
                if model_id else deepseek_harness.default_model()
            )
        result = _complete_openai_compatible(
            cleaned, call_model, temperature, max_tokens, timeout, key, base_url
        )
    result["model"] = model_id or call_model
    result["agent_id"] = "deepseek-harness"
    return result


def complete(messages, model_id=None, agent_id=None, temperature=0.6,
             max_tokens=MAX_OUTPUT_TOKENS, timeout=DEFAULT_TIMEOUT):
    """Run one completion with the selected agent + model.

    ``agent_id``/``model_id`` override the stored workspace selection so a
    COSMOS division can run with its own choice without changing the Studio.
    """
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

    selected = selection()
    resolved_agent_id = (
        common.clean_text(agent_id, "Agent", 120).strip().lower()
        if agent_id else selected["agent_id"]
    )
    found = agent_registry.get_agent(resolved_agent_id)
    if not found:
        raise LLMError(
            "llm_not_configured",
            "No text-generation agent is selected. Choose one from the Agent menu.",
            409,
        )
    resolved_model = common.clean_text(model_id, "Model", 120) if model_id else selected["model"]
    if found.get("transport") == "chat":
        return _complete_chat(cleaned, resolved_model, temperature, max_tokens, timeout)
    return _complete_cli(cleaned, found, resolved_model, timeout)
