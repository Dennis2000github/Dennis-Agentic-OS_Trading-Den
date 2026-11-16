"""Universal Skill Adapter: how each agent consumes skills.

One declarative row per agent id (the same ids as
``backend/agent_registry.py``). Nothing here *claims* a runtime works; the
flags record the documented consumption mechanism, and
:meth:`check_agent_compatibility` returns the evidence it used. Rows default
to "no support" so a new agent is never silently treated as skill-capable.

Access methods:

* ``native``        the agent's own skill loader reads the skill directory
* ``instruction``   the SKILL.md text is injected into the task prompt
* ``mcp``           the skill surface is exposed as MCP tools
* ``script``        the agent runs local scripts (with OS-level file access)

Every agent that gets ``script`` also gets file access by construction; the
separate ``file_access`` flag marks *read* access to workspace files.
"""

from __future__ import annotations

# Agent id -> consumption profile. Keep ids aligned with agent_registry.
AGENT_SKILL_CAPS = {
    "claude-code": {
        "label": "Claude Code",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": True,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": True,
        "skill_dir": "~/.claude/skills",
        "formats": ("skill-md", "mcp"),
    },
    "cursor": {
        "label": "Cursor",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": True,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": True,
        "skill_dir": "~/.cursor/skills",
        "formats": ("skill-md", "mcp"),
    },
    "codex": {
        "label": "Codex CLI",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "~/.codex/skills",
        "formats": ("skill-md",),
    },
    "codex-cli": {
        "label": "Codex CLI",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "~/.codex/skills",
        "formats": ("skill-md",),
    },
    "gemini-cli": {
        "label": "Gemini CLI",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "~/.gemini/skills",
        "formats": ("skill-md",),
    },
    "opencode": {
        "label": "OpenCode",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": True,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "~/.config/opencode/skills",
        "formats": ("skill-md", "mcp"),
    },
    "hermes": {
        "label": "Hermes (orchestrator)",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "~/.hermes/skills",
        "formats": ("skill-md",),
    },
    "hermes-cli": {
        "label": "Hermes Agent CLI",
        "native_skills": True,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": True,
        "file_access": True,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "~/.hermes/skills",
        "formats": ("skill-md",),
    },
    "antigravity": {
        "label": "Antigravity",
        "native_skills": False,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": False,
        "file_access": False,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "",
        "formats": ("skill-md",),
    },
    "openclaw": {
        "label": "OpenClaw",
        "native_skills": False,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": False,
        "file_access": False,
        "image_input": False,
        "browser_preview": False,
        "skill_dir": "",
        "formats": ("skill-md",),
    },
    "deepseek-harness": {
        # Chat API only: no filesystem, no script execution. Instruction
        # injection is the sole consumption method, so skills can never be
        # claimed "executable" here.
        "label": "DeepSeek Harness",
        "native_skills": False,
        "instruction_injection": True,
        "mcp_tools": False,
        "script_exec": False,
        "file_access": False,
        "image_input": True,
        "browser_preview": False,
        "skill_dir": "",
        "formats": ("skill-md",),
    },
}

# Skill-side execution requirements, matched against agent caps by key.
REQUIREMENT_CAPS = {
    "script": "script_exec",
    "file_access": "file_access",
    "image_input": "image_input",
    "browser_preview": "browser_preview",
    "mcp": "mcp_tools",
    "native_skill": "native_skills",
}

CAP_LEVELS = ("none", "instructions", "executable")


def agent_ids():
    return sorted(AGENT_SKILL_CAPS)


def capability(agent_id):
    """Copy of one agent's row, or ``None`` when unknown."""
    row = AGENT_SKILL_CAPS.get(agent_id)
    return dict(row) if row else None


def matrix():
    """All rows, in id order, for UI display and tests."""
    return [dict(row, id=aid) for aid, row in sorted(AGENT_SKILL_CAPS.items())]


def access_methods(agent_id):
    """Consumption methods an agent supports (empty list when unknown)."""
    row = AGENT_SKILL_CAPS.get(agent_id)
    if not row:
        return []
    out = []
    if row.get("native_skills"):
        out.append("native")
    if row.get("instruction_injection"):
        out.append("instruction")
    if row.get("mcp_tools"):
        out.append("mcp")
    return out


def can_execute(agent_id):
    """True only when the agent can run the skill's scripts, not just read
    its instructions. An instruction-only agent is never reported as
    executable (directive: never silently claim execution)."""
    row = AGENT_SKILL_CAPS.get(agent_id)
    return bool(row and row.get("script_exec"))


def check_agent_compatibility(agent_id, requirements=None, image=False,
                              browser=False):
    """Evaluate one agent against a skill's execution requirements.

    Returns a diagnostic dict. ``level`` is one of ``CAP_LEVELS``:
    ``executable`` (all requirements met), ``instructions`` (only the text
    can be injected) or ``none`` (nothing works). Missing rows are hostile by
    default, never optimistic.
    """
    row = AGENT_SKILL_CAPS.get(agent_id)
    result = {
        "agent_id": agent_id,
        "known": row is not None,
        "level": "none",
        "access_methods": [],
        "requirements": [],
        "reasons": [],
    }
    if not row:
        result["reasons"].append(
            "Agent '%s' has no skill-capability profile; refusing to guess." % agent_id)
        return result

    result["access_methods"] = access_methods(agent_id)
    missing = []
    for req in requirements or []:
        cap = REQUIREMENT_CAPS.get(req)
        if not cap:
            continue  # unknown requirement names are ignored, not assumed
        if not row.get(cap):
            missing.append(req)
    if image and not row.get("image_input"):
        missing.append("image_input")
    if browser and not row.get("browser_preview"):
        missing.append("browser_preview")

    result["requirements"] = missing
    if missing:
        result["reasons"].append(
            "Missing: " + ", ".join(sorted(set(missing))) + ".")
        if row.get("instruction_injection"):
            result["level"] = "instructions"
            result["reasons"].append(
                "Instruction injection still available (SKILL.md text only).")
        return result

    if row.get("instruction_injection"):
        result["level"] = "executable"
    else:
        result["level"] = "instructions"
        result["reasons"].append("No instruction-injection profile recorded.")
    return result


def compatible_agents(requirements=None, image=False, browser=False,
                      min_level="executable"):
    """Agent ids meeting ``min_level`` for the given requirements."""
    wanted = CAP_LEVELS.index(min_level)
    out = []
    for agent_id in AGENT_SKILL_CAPS:
        diag = check_agent_compatibility(agent_id, requirements, image, browser)
        if CAP_LEVELS.index(diag["level"]) >= wanted:
            out.append(agent_id)
    return out
