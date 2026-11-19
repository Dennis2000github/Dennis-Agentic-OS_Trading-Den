"""Preflight readiness check (Docker, Node, build tools, agent images).

Exposed two ways:

* ``GET /api/system/readiness`` (authenticated) for the UI.
* ``scripts/preflight.py`` for the terminal / operator.

The result is a plain dict; no secrets, no raw Docker output. Every check has
``ok`` plus a short actionable ``message`` when it fails.
"""

import os
import shutil
import subprocess

from . import agent_registry, docker_runtime

_BUILD_TOOLS = ("git", "python3", "make", "gcc")


def _which(binary, env=None):
    return shutil.which(binary, path=(env or {}).get("PATH"))


def check_node(env=None):
    node = _which("node", env)
    if not node:
        return {"ok": False, "version": None,
                "message": "Node.js not found. Install Node.js 20+ and retry."}
    try:
        res = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=5)
        version = (res.stdout or res.stderr).strip()
    except Exception:
        version = None
    return {"ok": True, "path": node, "version": version, "message": "Node.js ready."}


def check_build_tools(env=None):
    found = {tool: bool(_which(tool, env)) for tool in _BUILD_TOOLS}
    missing = [tool for tool, ok in found.items() if not ok]
    if missing:
        return {"ok": False, "tools": found,
                "message": "Missing build tools: " + ", ".join(missing) + "."}
    return {"ok": True, "tools": found, "message": "Build tools ready."}


def check_agents(env=None):
    env = env or agent_registry.get_augmented_env()
    agents = []
    for agent in agent_registry.load_agents():
        binary = agent_registry.resolve_binary(agent)
        agents.append({
            "id": agent.get("id"),
            "name": agent.get("name"),
            "binary_found": bool(binary),
            "binary_path": binary,
        })
    return agents


def check_images(agent_ids, timeout=12):
    return docker_runtime.image_status(agent_ids, timeout=timeout)


def run(session_backend=None):
    """Full readiness report. ``ready`` means the agent screen can start a CLI."""
    env = agent_registry.get_augmented_env()
    agents = check_agents(env)
    docker = docker_runtime.status()
    node = check_node(env)
    tools = check_build_tools(env)
    images = check_images([a["id"] for a in agents])
    images_ready = all(i["present"] for i in images) if images else False
    any_agent = any(a["binary_found"] for a in agents)

    if docker["code"] == docker_runtime.READY and images_ready:
        backend = "docker"
        ready = True
        message = "Docker and all agent images are ready."
    elif any_agent:
        backend = "local"
        ready = True
        message = ("Docker is not ready; agents will run directly on this machine. "
                   + docker["message"])
    else:
        backend = "none"
        ready = False
        message = "No agent CLI and no Docker image is available."

    return {
        "ready": ready,
        "backend": session_backend or backend,
        "message": message,
        "docker": docker,
        "node": node,
        "build_tools": tools,
        "agents": agents,
        "images": images,
    }


def main():
    import json
    report = run()
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
