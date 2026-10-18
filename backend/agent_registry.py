"""Read access to the agent registry and CLI capability metadata.

The registry file is the same ``data/agents.json`` used by the existing OS.
This module only reads/derives; it never launches processes.
"""

import os
import shutil
import subprocess

from . import config

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
    }
]

# How each CLI is driven. ``models_cmd`` is the non-interactive listing command
# (None means the CLI only exposes models through its interactive picker).
# ``model_flag`` is prepended with the model id. ``resume_flag`` is used to
# resume a stored CLI session id. ``continue_flag`` resumes the most recent.
CLI_PROFILES = {
    "opencode": {
        "binary": "opencode",
        "models_cmd": ["models"],
        "model_flag": ["-m"],
        "resume_flag": ["-s"],
        "continue_flag": ["-c"],
        "supports_live_model_switch": False,
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
}


def get_augmented_env():
    env = os.environ.copy()
    user_home = os.path.expanduser("~")
    extra_paths = [
        os.path.join(config.DIRECTORY, ".venv", "bin"),
        os.path.join(user_home, ".local", "bin"),
        os.path.join(user_home, ".opencode", "bin"),
        os.path.join(user_home, ".nvm", "versions", "node", "v24.21.0", "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/usr/bin",
        "/bin",
    ]
    env["PATH"] = ":".join(extra_paths) + ":" + env.get("PATH", "")
    return env


def load_agents():
    data = config.read_json(config.AGENTS_FILE, default=None)
    if not isinstance(data, list) or not data:
        config.write_json(config.AGENTS_FILE, DEFAULT_AGENTS)
        return list(DEFAULT_AGENTS)
    if not any(a.get("id") == "antigravity" for a in data):
        data.insert(0, DEFAULT_AGENTS[0])
    return data


def get_agent(agent_id):
    return next((a for a in load_agents() if a.get("id") == agent_id), None)


def profile(agent_id):
    return CLI_PROFILES.get(agent_id, {})


def resolve_binary(agent):
    env = get_augmented_env()
    binary = (agent.get("binary_path") or "").strip()
    if binary and os.path.isabs(binary) and os.path.exists(binary) and os.access(binary, os.X_OK):
        return binary
    prof = profile(agent.get("id"))
    names = [binary, agent.get("binary"), prof.get("binary"), agent.get("id")]
    for name in names:
        if not name:
            continue
        resolved = shutil.which(name, path=env["PATH"])
        if resolved:
            return resolved
    return None


def availability(agent):
    """Return (available, reason). ``reason`` is a short human string."""
    if not agent:
        return False, "Agent not found"
    binary = resolve_binary(agent)
    if not binary:
        return False, "CLI not installed"
    prof = profile(agent.get("id"))
    if not prof:
        return False, "Unsupported agent type"
    if not prof.get("models_cmd") and not prof.get("interactive_model_picker"):
        return False, "Model listing unsupported"
    return True, "Ready"


def available_agents():
    result = []
    for agent in load_agents():
        ok, reason = availability(agent)
        result.append({
            "id": agent.get("id"),
            "name": agent.get("name"),
            "provider": agent.get("provider"),
            "version": agent.get("version"),
            "icon_char": agent.get("icon_char"),
            "color_theme": agent.get("color_theme"),
            "binary_path": agent.get("binary_path"),
            "available": ok,
            "reason": reason,
        })
    return result


def version_of(binary, timeout=3):
    try:
        res = subprocess.run([binary, "--version"], capture_output=True, text=True,
                             timeout=timeout, env=get_augmented_env())
        out = (res.stdout.strip() or res.stderr.strip()).splitlines()
        return out[0].strip() if out else ""
    except Exception:
        return ""
