"""Read access to the agent registry and CLI capability metadata.

The registry file is the same ``data/agents.json`` used by the existing OS.
This module only reads/derives; it never launches processes.
"""

import os
import shutil
import subprocess
import threading
import time

from . import config
from . import freellmapi

DEFAULT_AGENTS = [
    {
        "id": "antigravity",
        "name": "Antigravity",
        "version": "v1.3.5",
        "binary_path": os.path.join(os.path.expanduser("~"), ".local", "bin", "agy"),
        "type": "AI Coding Assistant & OS Core",
        "provider": "Google DeepMind",
        "status": "Connected",
        "icon_char": "A",
        "color_theme": "cyan",
        "is_system": True,
    },
    {
        "id": "cursor",
        "name": "Cursor",
        "version": "Optional",
        "binary_path": "cursor-agent",
        "type": "Autonomous Coding Agent",
        "provider": "Cursor",
        "status": "Available when installed",
        "icon_char": "Cu",
        "color_theme": "violet",
        "is_system": False,
    },
    {
        "id": "openclaw",
        "name": "OpenClaw",
        "version": "Optional",
        "binary_path": "openclaw",
        "type": "Personal Agent & Automation",
        "provider": "OpenClaw",
        "status": "Available when installed",
        "icon_char": "OC",
        "color_theme": "orange",
        "is_system": False,
    },
]

# Agents that run their own native web UI inside the OS instead of a host CLI.
# They have no binary to probe: ``transport`` tells the workspace to mount the
# chat dashboard and the model list comes from the agent's own API.
BUILTIN_CHAT_AGENTS = [
    {
        "id": "deepseek-harness",
        "name": "DeepSeek Harness",
        "provider": "DeepSeek",
        "version": "Native chat",
        "type": "Native DeepSeek Chat",
        "icon_char": "D",
        "color_theme": "blue",
        "transport": "chat",
        "status": "Ready",
        "registered": True,
        "detected": True,
        "is_system": False,
    },
]

# How each CLI is driven. ``models_cmd`` is the non-interactive listing command
# (None means the CLI only exposes models through its interactive picker).
# ``model_flag`` is prepended with the model id. ``resume_flag`` is used to
# resume a stored CLI session id. ``continue_flag`` resumes the most recent.
CLI_PROFILES = {
    # No PTY profile: the chat dashboard owns this agent. The entry only marks
    # that a model switch applies to the next message without restarting a CLI.
    "deepseek-harness": {
        "transport": "chat",
        "models_cmd": None,
        "model_flag": [],
        "resume_flag": [],
        "continue_flag": [],
        "supports_live_model_switch": True,
        "interactive_model_picker": True,
    },
    "opencode": {
        # OpenCode v2's interactive root command (its default TUI) accepts no
        # --model/-m flag; passing one aborts the CLI at startup ("Unrecognized
        # flag: -m"), which is why sessions showed up as stopped. The model is
        # chosen with /models in the TUI, so we never inject a model flag.
        # Model SWITCHES are applied by backend/model_launch.py: a per-session
        # config file ({"model": ...}) plus --standalone, because the shared
        # background service ignores per-client env/config.
        "binary": "opencode",
        "models_cmd": ["models"],
        "model_flag": [],
        "resume_flag": ["-s"],
        "continue_flag": ["-c"],
        "supports_live_model_switch": False,
        "interactive_model_picker": True,
    },
    "cursor": {
        "binary": "cursor-agent",
        "binaries": ["cursor-agent", "agent"],
        "models_cmd": None,  # Cursor exposes model selection through /model.
        "model_flag": ["--model"],
        "resume_flag": [],
        "continue_flag": [],
        "supports_live_model_switch": False,
        "interactive_model_picker": True,
    },
    "openclaw": {
        "binary": "openclaw",
        "base_args": ["tui", "--local"],
        "models_cmd": ["models", "list", "--json"],
        "model_flag": [],  # The TUI changes models with Ctrl+L or /model.
        # The TUI takes the model inline: /model <id> after it has rendered.
        "model_input_steps": [
            {"at": 1.5, "text": "/model {model}\r"},
        ],
        "resume_flag": [],
        "continue_flag": [],
        "supports_live_model_switch": False,
        "interactive_model_picker": True,
    },
    "antigravity": {
        "binary": "agy",
        "models_cmd": ["models"],
        "model_flag": ["--model"],
        "resume_flag": ["--conversation"],
        "continue_flag": ["--continue"],
        "supports_live_model_switch": False,
    },
    "codex": {
        "binary": "codex",
        "models_cmd": ["debug", "models"],
        "model_flag": ["-m"],
        "resume_flag": ["resume"],
        "continue_flag": ["resume", "--last"],
        "supports_live_model_switch": False,
    },
    "codex-cli": {
        "binary": "codex",
        "models_cmd": ["debug", "models"],
        "model_flag": ["-m"],
        "resume_flag": ["resume"],
        "continue_flag": ["resume", "--last"],
        "supports_live_model_switch": False,
    },
    "claude-code": {
        "binary": "claude",
        "models_cmd": None,  # interactive /model picker only
        "model_flag": ["--model"],
        "resume_flag": ["--resume"],
        "continue_flag": ["--continue"],
        "supports_live_model_switch": False,
        "interactive_model_picker": True,
    },
    "gemini-cli": {
        "binary": "gemini",
        "models_cmd": None,
        "model_flag": ["--model"],
        "resume_flag": ["--resume"],
        "continue_flag": ["--continue"],
        "supports_live_model_switch": False,
    },
    "aider": {
        "binary": "aider",
        "models_cmd": ["--list-models"],
        "model_flag": ["--model"],
        "resume_flag": [],
        "continue_flag": [],
        "supports_live_model_switch": False,
    },
    "goose": {
        "binary": "goose",
        "models_cmd": None,
        "model_flag": [],
        "resume_flag": [],
        "continue_flag": [],
        "supports_live_model_switch": False,
    },
    "hermes-cli": {
        # Hermes Agent CLI (`hermes`). The id is deliberately not "hermes":
        # that name belongs to the in-OS orchestrator (backend/hermes.py).
        # Interactive sessions launch bare (`hermes` opens the TUI); one-shot
        # delegation uses `hermes -z "<prompt>"`.
        "binary": "hermes",
        "models_cmd": None,  # `hermes model` is an interactive picker
        "model_flag": ["-m"],
        "resume_flag": ["--resume"],
        "continue_flag": ["--continue"],
        "supports_live_model_switch": False,
    },
}

# Host CLIs the OS can detect. Availability merges the registry with every probe
# whose binary resolves, so an installed CLI is listed in the agent dropdowns
# even before it has been added to ``data/agents.json``. Each agent keeps its
# own CLI/session; the dropdown only chooses which one to drive.
HOST_AGENT_PROBES = [
    {
        "id": "cursor",
        "name": "Cursor",
        "binary": "cursor-agent",
        "binaries": ["cursor-agent", "agent"],
        "provider": "Cursor",
        "category": "Autonomous Coding Agent",
        "icon_char": "Cu",
        "color_theme": "violet",
    },
    {
        "id": "openclaw",
        "name": "OpenClaw",
        "binary": "openclaw",
        "provider": "OpenClaw",
        "category": "Personal Agent & Automation",
        "icon_char": "OC",
        "color_theme": "orange",
    },
    {
        "id": "antigravity",
        "name": "Antigravity",
        "binary": "agy",
        "provider": "Google DeepMind",
        "category": "Coding & Orchestration",
        "icon_char": "A",
        "color_theme": "cyan",
        "system": True,
    },
    {
        "id": "claude-code",
        "name": "Claude Code",
        "binary": "claude",
        "provider": "FreeLLMAPI",
        "category": "Autonomous Coding Agent",
        "icon_char": "C",
        "color_theme": "amber",
    },
    {
        "id": "opencode",
        "name": "OpenCode",
        "binary": "opencode",
        "provider": "OpenCode AI",
        "category": "Open-Source Coding Agent",
        "icon_char": "O",
        "color_theme": "blue",
    },
    {
        "id": "codex",
        "name": "Codex CLI",
        "binary": "codex",
        "provider": "OpenAI",
        "category": "CLI Agent & Code Assistant",
        "icon_char": "X",
        "color_theme": "emerald",
    },
    {
        "id": "gemini-cli",
        "name": "Gemini CLI",
        "binary": "gemini",
        "provider": "Google",
        "category": "CLI Multi-Modal Agent",
        "icon_char": "G",
        "color_theme": "indigo",
    },
    {
        "id": "aider",
        "name": "Aider",
        "binary": "aider",
        "provider": "Paul Gauthier",
        "category": "Git-Integrated Pair Programming",
        "icon_char": "Ai",
        "color_theme": "purple",
    },
    {
        "id": "goose",
        "name": "Goose",
        "binary": "goose",
        "provider": "Block",
        "category": "Autonomous Extensible Agent",
        "icon_char": "Gs",
        "color_theme": "cyan",
    },
    {
        # The installed Hermes Agent CLI, not the OS orchestrator. Keep the id
        # distinct so Hermes sub-agent routing does not exclude it.
        "id": "hermes-cli",
        "name": "Hermes Agent",
        "binary": "hermes",
        "provider": "Nous Research",
        "category": "Autonomous Agent & Orchestrator",
        "icon_char": "H",
        "color_theme": "amber",
    },
]


def get_augmented_env(agent_id=None, model_id=None):
    env = os.environ.copy()
    user_home = os.path.expanduser("~")
    try:
        import sysconfig
        user_scripts = sysconfig.get_path("scripts", scheme="posix_user") or ""
    except Exception:
        user_scripts = ""
    extra_paths = [
        os.path.join(config.DIRECTORY, ".venv", "bin"),
        user_scripts,
        os.path.join(user_home, ".local", "bin"),
        os.path.join(user_home, ".opencode", "bin"),
        os.path.join(user_home, ".nvm", "versions", "node", "v24.21.0", "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
    ]
    extra_paths = [p for p in extra_paths if p]
    env["PATH"] = ":".join(extra_paths) + ":" + env.get("PATH", "")
    return freellmapi.apply_agent_env(env, agent_id, model_id) if agent_id else env


def load_agents():
    data = config.read_json(config.AGENTS_FILE, default=None)
    if not isinstance(data, list) or not data:
        config.write_json(config.AGENTS_FILE, DEFAULT_AGENTS)
        return list(DEFAULT_AGENTS)
    known = {a.get("id") for a in data}
    for default in DEFAULT_AGENTS:
        if default.get("id") not in known:
            # Keep optional built-ins near the top of the Agent menu instead
            # of placing them below a long user registry where they are easy
            # to miss. Antigravity remains the first/system entry.
            data.insert(1, dict(default))
            known.add(default.get("id"))
    return data


def host_agent_probes():
    """Detectable host CLIs (copied so callers cannot mutate the table)."""
    return [dict(p) for p in HOST_AGENT_PROBES]


def _detected_agent(probe, binary_path=None, version=None):
    """Registry-shaped record for a host CLI that is not in agents.json."""
    return {
        "id": probe.get("id"),
        "name": probe.get("name") or probe.get("id"),
        "version": version or "Installed",
        "binary_path": binary_path or "",
        "type": probe.get("category") or "Coding CLI",
        "provider": probe.get("provider"),
        "status": "Detected",
        "icon_char": probe.get("icon_char"),
        "color_theme": probe.get("color_theme"),
        "detected": True,
        "registered": False,
        "is_system": bool(probe.get("system")),
    }


_DETECT_TTL = 15.0
_detect_cache = {"ts": 0.0, "agents": [], "versions": False}
_detect_lock = threading.Lock()

_VERSION_TTL = 300.0
_version_cache = {}
_version_lock = threading.Lock()


def clear_detection_cache():
    with _detect_lock:
        _detect_cache.update({"ts": 0.0, "agents": [], "versions": False})
    with _version_lock:
        _version_cache.clear()


def _known_version(path):
    with _version_lock:
        entry = _version_cache.get(path)
    return entry[1] if entry else ""


def _probe_version(path, refresh=False):
    now = time.time()
    if not refresh:
        known = _known_version(path)
        if known:
            return known
    version = version_of(path, timeout=2) or ""
    with _version_lock:
        _version_cache[path] = (now, version)
    return version


def detect_installed(refresh=False, with_version=False):
    """Return probe metadata for every host CLI whose binary resolves.

    Dropdowns call this on every open, so the binary scan is cached briefly and
    ``--version`` is only probed when explicitly requested (``with_version``);
    otherwise each entry reports ``Installed`` until a probe has run.
    """
    now = time.time()
    if not refresh:
        with _detect_lock:
            fresh = (now - _detect_cache["ts"]) < _DETECT_TTL
            if _detect_cache["agents"] and fresh and (not with_version or _detect_cache["versions"]):
                return [dict(a) for a in _detect_cache["agents"]]

    env = get_augmented_env()
    found = []
    for probe in HOST_AGENT_PROBES:
        binaries = [probe.get("binary")] + list(probe.get("binaries") or [])
        binaries = [str(binary).strip() for binary in binaries if str(binary or "").strip()]
        if not binaries:
            continue
        path = next((shutil.which(binary, path=env["PATH"]) for binary in binaries), None)
        if not path:
            continue
        probe_version = with_version and probe.get("version_probe", True)
        version = (_probe_version(path, refresh) if probe_version
                   else _known_version(path)) or "Installed"
        found.append(_detected_agent(probe, path, version))

    with _detect_lock:
        _detect_cache["ts"] = time.time()
        _detect_cache["agents"] = [dict(a) for a in found]
        _detect_cache["versions"] = bool(with_version)
    return found


def get_agent(agent_id):
    """Look up a registered agent, falling back to a built-in or detected one.

    The fallback keeps unregistered-but-installed CLIs launchable (their own
    session/CLI) without requiring the user to add them to agents.json first.
    Built-in chat agents have no binary; they are always resolvable so the
    workspace can mount their own dashboard.
    """
    agent = next((a for a in load_agents() if a.get("id") == agent_id), None)
    if agent:
        return agent
    builtin = next((a for a in BUILTIN_CHAT_AGENTS if a.get("id") == agent_id), None)
    if builtin:
        return dict(builtin)
    return next((a for a in detect_installed() if a.get("id") == agent_id), None)


def profile(agent_id):
    return CLI_PROFILES.get(agent_id, {})


# Where each CLI's provider credentials live. Most CLIs keep their own accounts;
# Claude Code and Codex also offer the app-managed FreeLLMAPI connector below.
PROVIDER_SETUP = {
    "opencode": {
        "label": "Add or switch an OpenCode provider",
        "command": "opencode auth login",
        "hint": "Store credentials for OpenAI, Anthropic, Google, local or any "
                "other provider, then press Refresh in this menu.",
    },
    "cursor": {
        "label": "Cursor account",
        "command": "cursor-agent login",
        "hint": "Sign in with `cursor-agent login`, then press Refresh in this menu.",
    },
    "openclaw": {
        "label": "OpenClaw provider setup",
        "command": "openclaw configure",
        "hint": "Configure providers in OpenClaw, then press Refresh in this menu.",
    },
    "hermes-cli": {
        "label": "Switch the Hermes provider",
        "command": "hermes model --refresh",
        "hint": "Picks the active provider and refreshes Hermes's model cache, "
                "then press Refresh in this menu.",
    },
    "claude-code": {
        "label": "FreeLLMAPI account",
        "hint": "Choose an Anthropic-compatible provider from the FreeLLMAPI catalog. Enter its base URL, model ID, and API key; the key stays local.",
        "form": "freellmapi",
    },
    "codex": {
        "label": "FreeLLMAPI account",
        "hint": "Choose an OpenAI-compatible provider from the FreeLLMAPI catalog. Enter its base URL, model ID, and API key; the key stays local.",
        "form": "freellmapi",
    },
    "codex-cli": {
        "label": "FreeLLMAPI account",
        "hint": "Choose an OpenAI-compatible provider from the FreeLLMAPI catalog. Enter its base URL, model ID, and API key; the key stays local.",
        "form": "freellmapi",
    },
    "antigravity": {
        "label": "Google account",
        "command": "agy",
        "hint": "Sign in from Antigravity's own UI, then press Refresh here.",
    },
    "deepseek-harness": {
        "label": "DeepSeek account",
        "command": None,
        "hint": "Connect with a DeepSeek API key. It is stored locally with "
                "owner-only permissions and used by the built-in chat. "
                "DEEPSEEK_BASE_URL switches to another compatible endpoint.",
        "form": "api_key",
    },
}


def provider_setup(agent_id):
    """CLI-native provider setup hint for an agent (never credentials)."""
    return dict(PROVIDER_SETUP.get(agent_id, {}))



def resolve_binary(agent):
    env = get_augmented_env()
    binary = (agent.get("binary_path") or "").strip()
    if binary and os.path.isabs(binary) and os.path.exists(binary) and os.access(binary, os.X_OK):
        return binary
    prof = profile(agent.get("id"))
    names = [binary, agent.get("binary"), prof.get("binary"), agent.get("id")]
    names += list(prof.get("binaries") or [])
    for name in names:
        if not name:
            continue
        resolved = shutil.which(name, path=env["PATH"])
        if resolved:
            return resolved
    return None


def availability(agent):
    """Return (available, reason). ``reason`` is a short human string.

    Availability only means the agent can be opened. A missing model-list
    command is not fatal: the dropdown falls back to the CLI's own default.
    Native chat agents need no binary; their dashboard handles key setup.
    """
    if not agent:
        return False, "Agent not found"
    if agent.get("transport") == "chat":
        return True, "Ready"
    if not resolve_binary(agent):
        return False, "CLI not installed"
    return True, "Ready"


def available_agents():
    """Registered agents plus every installed host CLI, for all dropdowns.

    Each entry maps to one agent CLI; the dropdown switches between them
    without stopping the other agents' own sessions.
    """
    result = []
    seen_ids = set()
    seen_bins = set()
    for agent in load_agents():
        ok, reason = availability(agent)
        resolved = resolve_binary(agent)
        aid = agent.get("id")
        seen_ids.add(aid)
        for candidate in (agent.get("binary_path"), resolved):
            if candidate:
                seen_bins.add(os.path.basename(str(candidate)).lower())
        result.append({
            "id": aid,
            "name": agent.get("name"),
            "provider": agent.get("provider"),
            "version": agent.get("version"),
            "icon_char": agent.get("icon_char"),
            "color_theme": agent.get("color_theme"),
            "binary_path": agent.get("binary_path"),
            "transport": agent.get("transport") or "cli",
            "available": ok,
            "reason": reason,
            "registered": True,
            "detected": bool(resolved),
        })

    # Native chat agents (no host CLI) always appear: the dashboard can be
    # opened before an API key is configured and guides the setup itself.
    for chat in BUILTIN_CHAT_AGENTS:
        aid = chat.get("id")
        if aid in seen_ids:
            continue
        result.append({
            "id": aid,
            "name": chat.get("name"),
            "provider": chat.get("provider"),
            "version": chat.get("version"),
            "icon_char": chat.get("icon_char"),
            "color_theme": chat.get("color_theme"),
            "binary_path": "",
            "transport": "chat",
            "available": True,
            "reason": "Ready",
            "registered": True,
            "detected": True,
        })
        seen_ids.add(aid)

    for detected in detect_installed():
        aid = detected.get("id")
        binary = os.path.basename(detected.get("binary_path") or "").lower()
        # Already represented by a registered agent (possibly under an alias
        # such as codex-cli/codex) - keep the registry entry only.
        if aid in seen_ids or (binary and binary in seen_bins):
            continue
        result.append({
            "id": aid,
            "name": detected.get("name"),
            "provider": detected.get("provider"),
            "version": detected.get("version"),
            "icon_char": detected.get("icon_char"),
            "color_theme": detected.get("color_theme"),
            "binary_path": detected.get("binary_path"),
            "transport": "cli",
            "available": True,
            "reason": "Ready (detected)",
            "registered": False,
            "detected": True,
        })
        seen_ids.add(aid)
        if binary:
            seen_bins.add(binary)
    return result


def one_shot_argv(agent, prompt, binary=None, model_id=""):
    """Build a non-interactive ("print mode") argv for one agent CLI.

    Mirrors how the CLIs are invoked from a shell so Hermes can delegate a
    single task and capture the answer. Returns ``None`` when the agent type
    is unknown. ``binary`` overrides the resolved executable path.
    """
    if not agent:
        return None
    aid = agent.get("id", "")
    exe = binary or resolve_binary(agent)
    if not exe:
        return None
    model_id = model_id or ""
    if aid == "antigravity":
        argv = [exe, "--print", prompt]
        if model_id:
            argv += ["--model", model_id]
    elif aid == "claude-code":
        argv = [exe, "-p", prompt]
        if model_id:
            argv += ["--model", model_id]
    elif aid == "opencode":
        argv = [exe, "run"]
        if model_id:
            argv += ["-m", model_id]
        argv.append(prompt)
    elif aid == "cursor":
        argv = [exe, "-p", prompt]
        if model_id:
            argv += ["--model", model_id]
    elif aid == "openclaw":
        argv = [exe, "agent", "exec"]
        if model_id:
            argv += ["--model", model_id]
        argv.append(prompt)
    elif aid in ("codex", "codex-cli"):
        argv = [exe, "exec"]
        if model_id:
            argv += ["-m", model_id]
        argv.append(prompt)
    elif aid == "gemini-cli":
        argv = [exe, "-p", prompt]
        if model_id:
            argv += ["--model", model_id]
    elif aid == "aider":
        argv = [exe, "--yes", "--message", prompt]
        if model_id:
            argv += ["--model", model_id]
    elif aid == "goose":
        argv = [exe, "run", "-t", prompt]
    elif aid == "hermes-cli":
        # Top-level one-shot: `hermes [-m MODEL] -z PROMPT`.
        argv = [exe]
        if model_id:
            argv += ["-m", model_id]
        argv += ["-z", prompt]
    else:
        argv = [exe, prompt]
    return argv


def version_of(binary, timeout=3):
    try:
        res = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=timeout, env=get_augmented_env())
        out = (res.stdout.strip() or res.stderr.strip()).splitlines()
        return out[0].strip() if out else ""
    except Exception:
        return ""
