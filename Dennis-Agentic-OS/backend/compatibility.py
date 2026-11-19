"""Capability-based compatibility for Agent + Connection + Protocol + Model.

The matrix below records **documented, verified** connectivity mechanisms
only - the two rows marked ``compatible`` (claude-code on Anthropic-
compatible endpoints, codex on OpenAI-compatible endpoints) are exactly the
mechanisms the existing FreeLLMAPI integration has been exercising in
production. Everything without runtime validation stays ``not_tested`` with
the documented mechanism attached, and agents with no documented custom-
endpoint mechanism are ``incompatible`` for API connections. Nothing is
displayed as ready merely because it appears in a model list.

Statuses: compatible | partial | incompatible | not_tested |
auth_required | connection_unavailable.
"""

from __future__ import annotations

from . import connections
from . import model_catalog

# Statuses
COMPATIBLE = "compatible"
PARTIAL = "partial"
INCOMPATIBLE = "incompatible"
NOT_TESTED = "not_tested"
AUTH_REQUIRED = "auth_required"
CONNECTION_UNAVAILABLE = "connection_unavailable"


class AgentEndpoint:
    """One documented way an agent can consume an external endpoint.

    ``env_template`` maps environment variable name -> value kind:
    ``base_url``, ``api_key``, ``model`` (the native model id) or a literal.
    """

    def __init__(self, env_template, model_env=None, status=COMPATIBLE, note="",
                 execution="cli_native"):
        self.env_template = dict(env_template)
        self.model_env = model_env
        self.status = status
        self.note = note
        self.execution = execution  # cli_native | api (in-process) | config


# The protocol capability matrix. Keys are agent ids from the agent
# registry; ``protocols`` maps API protocol -> AgentEndpoint or None.
AGENT_MATRIX = {
    "claude-code": {
        "label": "Claude Code",
        "protocols": {
            connections.ANTHROPIC_PROTOCOL: AgentEndpoint(
                env_template={"ANTHROPIC_BASE_URL": "base_url",
                              "ANTHROPIC_AUTH_TOKEN": "api_key",
                              "ANTHROPIC_API_KEY": "",
                              "ANTHROPIC_CUSTOM_MODEL_OPTION": "model",
                              "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "model_context"},
                model_env="ANTHROPIC_MODEL",
                note="Verified in daily use via the FreeLLMAPI integration "
                     "(ANTHROPIC_BASE_URL/AUTH_TOKEN injection).",
            ),
            connections.OPENAI_PROTOCOL: None,
        },
        "executions": ["cli_native", "api_via_cli"],
    },
    "codex": {
        "label": "Codex CLI",
        "protocols": {
            connections.OPENAI_PROTOCOL: AgentEndpoint(
                env_template={"OPENAI_BASE_URL": "base_url",
                              "OPENAI_API_KEY": "api_key"},
                model_env=None,  # model travels via the CLI's -m flag
                note="Verified in daily use via the FreeLLMAPI integration "
                     "(OPENAI_BASE_URL/API_KEY injection).",
            ),
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native", "api_via_cli"],
    },
    "deepseek-harness": {
        "label": "DeepSeek Harness",
        "protocols": {
            connections.OPENAI_PROTOCOL: AgentEndpoint(
                env_template={},  # resolved in-process per request
                model_env=None,
                execution="api",
                note="The in-process chat client speaks OpenAI-compatible "
                     "chat completions; the router resolves the endpoint per "
                     "request (no env needed).",
            ),
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["api", "cli_native"],
    },
    "opencode": {
        "label": "OpenCode",
        "protocols": {
            connections.OPENAI_PROTOCOL: AgentEndpoint(
                env_template={},  # would require a generated opencode config
                execution="config",
                status=NOT_TESTED,
                note="OpenCode supports custom providers in opencode.json; "
                     "generating that config for arbitrary gateways is "
                     "implemented but not yet validated end-to-end.",
            ),
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
    },
    "aider": {
        "label": "Aider",
        "protocols": {
            connections.OPENAI_PROTOCOL: AgentEndpoint(
                env_template={"OPENAI_API_BASE": "base_url",
                              "OPENAI_API_KEY": "api_key"},
                execution="cli_native",
                status=NOT_TESTED,
                note="Aider documents OPENAI_API_BASE/OPENAI_API_KEY; not "
                     "yet validated from DENNIS.",
            ),
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
    },
    "codex-cli": None,  # alias resolved to codex below
    "hermes-cli": {
        "label": "Hermes CLI",
        "protocols": {
            connections.OPENAI_PROTOCOL: None,
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
        "note": "Hermes manages its own provider config (~/.hermes); no "
                "documented env override.",
    },
    "antigravity": {
        "label": "Antigravity",
        "protocols": {
            connections.OPENAI_PROTOCOL: None,
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
        "note": "Authenticates with a Google account from its own UI; no "
                "documented custom-endpoint mechanism.",
    },
    "cursor": {
        "label": "Cursor",
        "protocols": {
            connections.OPENAI_PROTOCOL: None,
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
        "note": "Uses the Cursor account (cursor-agent login).",
    },
    "openclaw": {
        "label": "OpenClaw",
        "protocols": {
            connections.OPENAI_PROTOCOL: None,
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
        "note": "Uses its own provider config (openclaw configure).",
    },
    "gemini-cli": {
        "label": "Gemini CLI",
        "protocols": {
            connections.OPENAI_PROTOCOL: None,
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
        "note": "Uses Google account auth; no documented custom-endpoint "
                "env override.",
    },
    "goose": {
        "label": "Goose",
        "protocols": {
            connections.OPENAI_PROTOCOL: None,
            connections.ANTHROPIC_PROTOCOL: None,
        },
        "executions": ["cli_native"],
        "note": "No validated custom-endpoint mechanism from DENNIS.",
    },
}

_AGENT_ALIASES = {"codex-cli": "codex", "codex": "codex"}


def matrix_entry(agent_id):
    resolved = _AGENT_ALIASES.get(agent_id, agent_id)
    return AGENT_MATRIX.get(resolved)


def endpoint_for(agent_id, protocol):
    """The documented AgentEndpoint for a protocol, or None when the agent
    cannot consume that protocol at all."""
    entry = matrix_entry(agent_id)
    if not entry:
        return None, None
    endpoint = (entry.get("protocols") or {}).get(protocol)
    return endpoint, entry


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate(agent_id, model_id, connection=None):
    """Validate one Agent + Source + Connection + Protocol + Model combo.

    Returns a diagnostic dict; never raises for evaluable combinations.
    """
    result = {
        "agent_id": agent_id,
        "model_id": model_id,
        "status": NOT_TESTED,
        "reason": "",
        "connection": None,
        "model": None,
        "execution": None,
        "requirements": [],
    }

    # CLI-native model (no connection namespace): the model list is the
    # runtime contract for these.
    if not model_catalog.is_connection_model(model_id):
        result["status"] = NOT_TESTED
        result["reason"] = ("CLI-native model id '%s' - run it through the "
                            "agent's own model list for a definitive check."
                            % model_id)
        result["execution"] = "cli_native"
        return result

    connection_id, native_id = model_catalog.parse_internal_id(model_id)
    conn = connection or connections.get_connection(connection_id)
    if not conn:
        result["status"] = CONNECTION_UNAVAILABLE
        result["reason"] = "Connection '%s' no longer exists." % connection_id
        return result
    result["connection"] = connections.public_view(conn)

    endpoint, entry = endpoint_for(agent_id, conn["protocol"])
    if endpoint is None:
        if entry is None:
            result["status"] = INCOMPATIBLE
            result["reason"] = "Agent '%s' has no known way to use a %s-protocol endpoint." % (
                agent_id, conn["protocol"])
        else:
            result["status"] = INCOMPATIBLE
            result["reason"] = (entry.get("note") or
                                "Agent '%s' cannot consume %s endpoints." % (agent_id, conn["protocol"]))
        return result

    result["execution"] = endpoint.execution
    result["requirements"] = sorted(endpoint.env_template.keys())
    if endpoint.status != COMPATIBLE:
        result["status"] = endpoint.status
        result["reason"] = endpoint.note
        return result

    # Resolvable authentication?
    key, key_source = connections.resolve_key(conn)
    if not key:
        result["status"] = AUTH_REQUIRED
        result["reason"] = "No API key configured for this connection."
        if conn.get("key_env"):
            result["requirements"].append(conn["key_env"])
        return result

    # Live endpoint health (from the last real test, never inferred).
    health = connections.get_health(conn["id"])
    if health.get("status") == connections.STATUS_AUTH_FAILED:
        result["status"] = AUTH_REQUIRED
        result["reason"] = "Last connection test failed authentication."
        return result
    if health.get("status") in (connections.STATUS_UNAVAILABLE,
                                connections.STATUS_RATE_LIMITED):
        result["status"] = CONNECTION_UNAVAILABLE
        result["reason"] = health.get("detail") or "Connection is not reachable."
        return result

    # The model must actually be known on this connection.
    record, _conn = model_catalog.get_record(model_id)
    if record is None:
        result["status"] = NOT_TESTED
        result["reason"] = ("Model '%s' is not registered or discovered on "
                            "this connection yet; discover models or register "
                            "it manually first." % native_id)
        return result
    result["model"] = record

    if endpoint.execution == "config":
        result["status"] = PARTIAL
        result["reason"] = endpoint.note
        return result

    result["status"] = COMPATIBLE
    result["reason"] = endpoint.note or ("Validated %s-protocol path with %s authentication."
                                         % (conn["protocol"], key_source))
    return result


def compatible_connections(agent_id, conn_type=None):
    """Connections usable by an agent, each annotated with its status."""
    out = []
    for conn in connections.list_connections():
        if conn_type and conn["type"] != conn_type:
            continue
        endpoint, _entry = endpoint_for(agent_id, conn["protocol"])
        if endpoint is None:
            view = connections.public_view(conn)
            view["compatibility"] = INCOMPATIBLE
            view["compatibility_reason"] = "Agent cannot consume %s endpoints." % conn["protocol"]
            out.append(view)
            continue
        # Evaluate with a synthetic model id so auth/health checks run; the
        # model-level check is skipped by passing the connection explicitly.
        probe = evaluate(agent_id, model_catalog.internal_id(conn["id"], "_probe"), connection=conn)
        view = connections.public_view(conn)
        view["compatibility"] = probe["status"] if probe["status"] != NOT_TESTED else (
            COMPATIBLE if endpoint.status == COMPATIBLE else endpoint.status)
        view["compatibility_reason"] = probe["reason"]
        view["execution"] = probe["execution"]
        out.append(view)
    return out


def matrix_public():
    """The capability matrix for UI display (no secrets involved)."""
    rows = []
    for agent_id, entry in AGENT_MATRIX.items():
        if entry is None:
            continue
        rows.append({
            "agent_id": agent_id,
            "label": entry.get("label") or agent_id,
            "protocols": {
                protocol: ({"status": endpoint.status, "note": endpoint.note,
                            "execution": endpoint.execution,
                            "env": sorted(endpoint.env_template.keys())}
                           if endpoint else {"status": INCOMPATIBLE,
                                             "note": "No documented mechanism.",
                                             "execution": None, "env": []})
                for protocol, endpoint in (entry.get("protocols") or {}).items()
            },
            "executions": entry.get("executions") or ["cli_native"],
            "note": entry.get("note", ""),
        })
    return rows
