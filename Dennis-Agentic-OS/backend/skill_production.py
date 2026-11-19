"""Production technology reconstruction, environment detection and dependency
intelligence.

Nothing in this module installs software.  It detects what is already present,
chooses the smallest viable stack for the reference video, classifies every
dependency and produces an approval-gated installation plan.
"""

import os
import platform
import shutil
import sys
import time

from . import config, skill_media

WORKSPACE = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent-skill-builder")
)
_ENV_CACHE = {"at": 0.0, "value": None}
_GPU_CACHE = {"at": 0.0, "value": None}

PROVIDER_KEYS = (
    "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "RUNWAY_API_KEY", "LUMA_API_KEY", "FAL_KEY", "HIGGSFIELD_API_KEY",
    "ELEVENLABS_API_KEY", "REPLICATE_API_TOKEN",
)

STACK_CATALOG = {
    "ffmpeg": {"name": "FFmpeg", "kind": "mandatory", "why": "Decode, cut, composite, encode and mux every deliverable."},
    "ffprobe": {"name": "FFprobe", "kind": "mandatory", "why": "Read authoritative technical metadata before planning."},
    "python": {"name": "Python", "kind": "mandatory", "why": "Runs the deterministic planning, validation and packaging scripts."},
    "node": {"name": "Node.js", "kind": "conditional", "why": "Required only for Remotion/Three.js or browser-driven rendering."},
    "remotion": {"name": "Remotion", "kind": "conditional", "why": "Programmatic React video for motion graphics, typography and data-driven scenes."},
    "three_js": {"name": "Three.js", "kind": "conditional", "why": "Real-time 3D scenes and camera moves without a full DCC pipeline."},
    "canvas_2d": {"name": "Canvas 2D", "kind": "conditional", "why": "Lightweight 2D animation, captions and shape-driven explainers."},
    "moviepy": {"name": "MoviePy", "kind": "optional", "why": "Python-first editing for simple cuts and overlays; unnecessary when FFmpeg suffices."},
    "blender": {"name": "Blender", "kind": "conditional", "why": "Offline 3D rendering, simulation and stop-motion-style asset work."},
    "headless_chrome": {"name": "Headless Chrome", "kind": "conditional", "why": "Frame-accurate capture of web/HTML scenes and UI demonstrations."},
    "image_provider": {"name": "Image generation provider", "kind": "external", "why": "Creates still assets or characters not present in stock footage."},
    "video_provider": {"name": "Video generation provider", "kind": "external", "why": "Generates shots that cannot be filmed or animated locally."},
    "audio_provider": {"name": "Voice / music provider", "kind": "external", "why": "Narration, music or SFX when no recorded audio exists."},
}

STYLE_STACKS = {
    "motion_graphics": ("node", "remotion", "canvas_2d"),
    "typography_animation": ("node", "remotion"),
    "2d_animation": ("canvas_2d",),
    "cartoon": ("canvas_2d",),
    "stickman": ("canvas_2d",),
    "3d_animation": ("node", "three_js"),
    "cgi": ("node", "three_js", "blender"),
    "claymation": ("blender",),
    "stop_motion": ("blender",),
    "ai_generated": ("image_provider", "video_provider", "audio_provider"),
    "hybrid": ("node", "canvas_2d", "image_provider"),
    "live_action": (),
    "talking_head": (),
    "ugc": (),
    "cinematic": (),
    "documentary": (),
}


def _version(argv, timeout=8):
    code, out, err = skill_media.run(argv, timeout=timeout)
    text = (out or err or "").strip().splitlines()
    return text[0].strip()[:160] if text else ""


def _ram_gb():
    try:
        if platform.system() == "Darwin":
            code, out, _ = skill_media.run(["sysctl", "-n", "hw.memsize"], timeout=5)
            if code == 0 and out.strip().isdigit():
                return round(int(out.strip()) / (1024 ** 3), 1)
        elif os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("MemTotal:"):
                        return round(int(line.split()[1]) / (1024 ** 2), 1)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _gpu():
    if _GPU_CACHE["value"] is not None and time.time() - _GPU_CACHE["at"] < 300:
        return _GPU_CACHE["value"]
    value = {"available": None, "name": ""}
    exe = shutil.which("nvidia-smi")
    if exe:
        code, out, _ = skill_media.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"], timeout=6)
        if code == 0 and out.strip():
            value = {"available": True, "name": out.strip().splitlines()[0][:120]}
    elif platform.system() == "Darwin":
        code, out, _ = skill_media.run(
            ["system_profiler", "SPDisplaysDataType"], timeout=8)
        if code == 0:
            names = [line.split(":", 1)[1].strip() for line in out.splitlines()
                     if "Chipset Model:" in line or "Chip:" in line]
            if names:
                value = {"available": True, "name": names[0][:120]}
    _GPU_CACHE.update({"at": time.time(), "value": value})
    return value


def _read_env_keys():
    keys = {}
    for name in PROVIDER_KEYS:
        if os.environ.get(name):
            keys[name] = True
    path = os.path.join(WORKSPACE, ".env")
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                name = name.strip()
                if name in PROVIDER_KEYS and value.strip().strip('"\''):
                    keys[name] = True
    except OSError:
        pass
    return keys


def detect_environment(force=False):
    """Detect the local runtime without installing or calling a provider."""
    if not force and _ENV_CACHE["value"] and time.time() - _ENV_CACHE["at"] < 60:
        return dict(_ENV_CACHE["value"])
    media = skill_media.available()
    python = sys.executable or "python3"
    node = shutil.which("node")
    chrome = None
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if os.path.isfile(candidate):
            chrome = candidate
            break
    chrome = chrome or shutil.which("google-chrome") or shutil.which("chromium")
    try:
        usage = shutil.disk_usage(config.DIRECTORY)
        free_gb = round(usage.free / (1024 ** 3), 1)
    except OSError:
        free_gb = 0.0
    managers = []
    for name, argv in (
        ("pip", [python, "-m", "pip", "--version"]),
        ("brew", ["brew", "--version"]),
        ("npm", ["npm", "--version"]),
        ("pnpm", ["pnpm", "--version"]),
        ("yarn", ["yarn", "--version"]),
        ("uv", ["uv", "--version"]),
    ):
        if shutil.which(argv[0]) or (argv[0] == python and os.path.isfile(python)):
            managers.append(name)
    venvs = []
    for name in (".venv", "venv", "env"):
        path = os.path.join(config.DIRECTORY, name)
        if os.path.isdir(path):
            venvs.append(path)
    models = []
    for path in (
        os.path.join(config.DIRECTORY, "tts_service", "models"),
        os.path.expanduser("~/.cache/huggingface"),
        os.path.expanduser("~/.ollama/models"),
    ):
        if os.path.isdir(path):
            models.append(path)
    env = {
        "os": {"name": platform.system(), "version": platform.release(),
               "arch": platform.machine()},
        "cpu": {"cores": os.cpu_count() or 0},
        "ram_gb": _ram_gb(),
        "storage": {"free_gb": free_gb},
        "gpu": _gpu(),
        "python": {"path": python, "version": platform.python_version()},
        "node": {"path": node or "", "version": _version([node, "--version"]) if node else ""},
        "ffmpeg": {"path": media.get("ffmpeg") or "",
                   "version": _version([media["ffmpeg"], "-version"]) if media.get("ffmpeg") else ""},
        "ffprobe": {"path": media.get("ffprobe") or "",
                    "version": _version([media["ffprobe"], "-version"]) if media.get("ffprobe") else ""},
        "chrome": {"path": chrome or "", "version": ""},
        "package_managers": managers,
        "venvs": venvs,
        "models": models,
        "network": {"online": None},
        "providers": _read_env_keys(),
    }
    _ENV_CACHE.update({"at": time.time(), "value": env})
    return dict(env)


def _installed(entry_id, env):
    if entry_id == "ffmpeg":
        return bool(env.get("ffmpeg", {}).get("path"))
    if entry_id == "ffprobe":
        return bool(env.get("ffprobe", {}).get("path"))
    if entry_id == "python":
        return bool(env.get("python", {}).get("path"))
    if entry_id == "node":
        return bool(env.get("node", {}).get("path"))
    if entry_id == "headless_chrome":
        return bool(env.get("chrome", {}).get("path"))
    if entry_id == "image_provider":
        return bool(env.get("providers", {}).get("FAL_KEY") or
                    env.get("providers", {}).get("OPENAI_API_KEY"))
    if entry_id == "video_provider":
        return bool(env.get("providers", {}).get("RUNWAY_API_KEY") or
                    env.get("providers", {}).get("LUMA_API_KEY") or
                    env.get("providers", {}).get("FAL_KEY"))
    if entry_id == "audio_provider":
        return bool(env.get("providers", {}).get("ELEVENLABS_API_KEY"))
    if entry_id in ("remotion", "three_js", "canvas_2d", "moviepy", "blender"):
        return False if entry_id != "blender" else bool(shutil.which("blender"))
    return False


def resolve_stack(style=None, fmt=None, env=None, wants=None):
    """Pick the smallest viable technology stack and justify each choice."""
    env = env or detect_environment()
    style_id = (style or {}).get("style_id") or "hybrid"
    format_id = (fmt or {}).get("format_id") or "explainer"
    wants = set(wants or [])
    chosen = ["ffmpeg", "ffprobe", "python"]
    for item in STYLE_STACKS.get(style_id, ()):
        chosen.append(item)
    if format_id in ("tutorial", "how_to", "product_demo") and "headless_chrome" not in chosen:
        chosen.append("headless_chrome")
    if "screen" in wants and "headless_chrome" not in chosen:
        chosen.append("headless_chrome")
    if not env.get("node", {}).get("path"):
        # Node is required by the node-based candidates; keep it visible.
        if any(item in chosen for item in ("remotion", "three_js")):
            chosen.append("node")
    stack = []
    seen = set()
    for item in chosen:
        if item in seen or item not in STACK_CATALOG:
            continue
        seen.add(item)
        meta = STACK_CATALOG[item]
        stack.append({
            "id": item,
            "name": meta["name"],
            "kind": meta["kind"],
            "why": meta["why"],
            "selected": True,
            "detected": _installed(item, env),
            "version": _detected_version(item, env),
        })
    # Optional candidates are listed but not selected.
    for item, meta in STACK_CATALOG.items():
        if item in seen:
            continue
        if meta["kind"] in ("optional", "external"):
            stack.append({
                "id": item, "name": meta["name"], "kind": meta["kind"],
                "why": meta["why"], "selected": False,
                "detected": _installed(item, env),
                "version": _detected_version(item, env),
            })
    return stack


def _detected_version(entry_id, env):
    if entry_id == "python":
        return env.get("python", {}).get("version") or ""
    if entry_id in ("ffmpeg", "ffprobe", "node", "chrome"):
        return (env.get(entry_id) or {}).get("version") or ""
    return ""


def build_production(style, fmt, env=None, wants=None):
    env = env or detect_environment()
    stack = resolve_stack(style, fmt, env, wants=wants)
    selected = [item for item in stack if item["selected"]]
    style_id = (style or {}).get("style_id") or "hybrid"
    render = ["Script/plan generated from STRATEGY.md and FORMAT.md",
              "Assets resolved or generated per scene",
              "Scenes composed with %s" % (", ".join(
                  item["name"] for item in selected if item["id"] in
                  ("ffmpeg", "remotion", "canvas_2d", "three_js", "blender")) or "FFmpeg filters"),
              "Audio bed, narration and SFX mixed",
              "Encode to the primary output configuration",
              "Verify duration, fps, loudness and caption sync"]
    return {
        "stack": stack,
        "rendering_pipeline": render,
        "asset_generation": (
            "Use the selected external image provider for missing stills; "
            "never claim an asset exists until it is returned."
            if any(item["id"] == "image_provider" and item["selected"] for item in stack)
            else "Reuse supplied footage/stills; generate only when a provider is connected."),
        "animation_pipeline": (
            "Keyframe and ease animation in the selected renderer; keep the "
            "style consistency rules from STYLE.md."
            if style_id in ("2d_animation", "cartoon", "stickman", "motion_graphics",
                            "typography_animation", "3d_animation", "cgi")
            else "Cut-driven editing with motion limited to camera moves, zooms and transitions."),
        "audio_pipeline": "Narration -> music bed -> SFX -> loudness normalization (-14 LUFS unless specified).",
        "compositing": "Scene -> overlay -> captions -> colour grade -> final composite.",
        "encoding": "H.264/AAC MP4 for delivery; keep a high-bitrate master.",
        "quality_control": [
            "Duration within 2% of the plan",
            "No missing scene or asset reference",
            "Captions match narration timing",
            "Audio peaks below -1 dBFS; integrated loudness checked",
            "Aspect/resolution/fps match FORMAT.md",
        ],
        "environment": {
            "os": env.get("os"), "cpu": env.get("cpu"),
            "ram_gb": env.get("ram_gb"), "storage": env.get("storage"),
            "gpu": env.get("gpu"),
        },
        "estimated_cost": "Local compute only unless an external provider is selected; provider pricing must be confirmed before use.",
        "limitations": [
            "No provider is assumed to be connected.",
            "Stack selection is a plan, not proof that a render succeeded.",
        ],
    }


def dependency_manifest(production, env=None):
    env = env or detect_environment()
    buckets = {"mandatory": [], "conditional": [], "optional": [], "external": []}
    for item in (production or {}).get("stack") or []:
        bucket = item.get("kind") if item.get("kind") in buckets else "optional"
        buckets[bucket].append({
            "id": item.get("id"), "name": item.get("name"),
            "selected": bool(item.get("selected")),
            "detected": bool(item.get("detected")),
            "version": item.get("version") or "",
            "why": item.get("why") or "",
        })
    manifest = {
        "version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "environment": env,
        "dependencies": buckets,
        "policy": {
            "reuse_existing": True,
            "pin_versions": True,
            "isolated_environments": True,
            "system_wide_changes": False,
            "admin_approval_required": True,
            "large_download_approval_mb": 100,
            "credentials_in_packages": False,
            "auto_install": False,
        },
    }
    manifest["install_plan"] = install_plan(manifest)
    return manifest


def install_plan(manifest):
    """Approval-gated plan. Commands are data; they are never executed here."""
    steps = []
    env = (manifest or {}).get("environment") or {}
    os_name = (env.get("os") or {}).get("name") or ""
    for item in ((manifest or {}).get("dependencies") or {}).get("mandatory", []):
        if not item.get("detected"):
            steps.append(_step(item, "Install %s" % item.get("name"),
                               _suggest_command(item["id"], os_name),
                               approval=False, size_mb=0, cost="free"))
    for item in ((manifest or {}).get("dependencies") or {}).get("conditional", []):
        if item.get("selected") and not item.get("detected"):
            steps.append(_step(item, "Install %s" % item.get("name"),
                               _suggest_command(item["id"], os_name),
                               approval=True, size_mb=_size_hint(item["id"]),
                               cost="free"))
    for item in ((manifest or {}).get("dependencies") or {}).get("external", []):
        if item.get("selected") and not item.get("detected"):
            steps.append(_step(item, "Connect %s" % item.get("name"), "",
                               approval=True, size_mb=0,
                               cost="paid API usage; confirm pricing before use"))
    return steps


def _step(item, title, command, approval, size_mb, cost):
    return {
        "dependency": item.get("id"), "title": title,
        "command": command, "requires_approval": bool(approval),
        "estimated_size_mb": int(size_mb or 0),
        "estimated_cost": cost,
        "automatic": False,
        "reversible": True,
    }


def _size_hint(entry_id):
    return {"blender": 400, "three_js": 5, "remotion": 250, "moviepy": 40,
            "headless_chrome": 350}.get(entry_id, 20)


def _suggest_command(entry_id, os_name):
    if entry_id == "ffmpeg":
        return "brew install ffmpeg" if os_name == "Darwin" else "sudo apt install ffmpeg"
    if entry_id == "ffprobe":
        return "brew install ffmpeg" if os_name == "Darwin" else "sudo apt install ffmpeg"
    if entry_id == "node":
        return "brew install node" if os_name == "Darwin" else "sudo apt install nodejs npm"
    if entry_id == "remotion":
        return "npx create-video@latest"
    if entry_id == "three_js":
        return "npm install three"
    if entry_id == "moviepy":
        return "pip install moviepy"
    if entry_id == "blender":
        return "brew install --cask blender" if os_name == "Darwin" else "sudo apt install blender"
    if entry_id == "headless_chrome":
        return "Install Google Chrome"
    return ""


def production_text(production):
    lines = ["# Production Technology", ""]
    if (production or {}).get("stack"):
        lines += ["## Selected stack", "",
                  "| Technology | Kind | Why | Detected | Version |",
                  "|---|---|---|---|---|"]
        for item in production["stack"]:
            if not item.get("selected"):
                continue
            lines.append("| %s | %s | %s | %s | %s |" % (
                item.get("name"), item.get("kind"),
                (item.get("why") or "").replace("|", "/"),
                "yes" if item.get("detected") else "no",
                item.get("version") or "-"))
        unselected = [item for item in production["stack"] if not item.get("selected")]
        if unselected:
            lines += ["", "## Considered but not selected", ""]
            lines += ["- **%s** — %s" % (item.get("name"), item.get("why"))
                      for item in unselected]
    if (production or {}).get("rendering_pipeline"):
        lines += ["", "## Rendering pipeline"]
        lines += ["%d. %s" % (i + 1, step)
                  for i, step in enumerate(production["rendering_pipeline"])]
    for key, label in (
        ("asset_generation", "Asset generation"),
        ("animation_pipeline", "Animation pipeline"),
        ("audio_pipeline", "Audio pipeline"),
        ("compositing", "Compositing"),
        ("encoding", "Encoding"),
        ("estimated_cost", "Cost"),
    ):
        if (production or {}).get(key):
            lines += ["", "## %s" % label, production[key]]
    if (production or {}).get("quality_control"):
        lines += ["", "## Quality control"]
        lines += ["- " + item for item in production["quality_control"]]
    if (production or {}).get("limitations"):
        lines += ["", "## Limitations"]
        lines += ["- " + item for item in production["limitations"]]
    return "\n".join(lines).strip() + "\n"


def dependencies_text(manifest):
    lines = ["# Dependencies", "",
             "Generated: %s" % (manifest or {}).get("generated_at"),
             "",
             "This file is a plan. Nothing is installed automatically and no "
             "credentials are stored inside the skill package.", ""]
    env = (manifest or {}).get("environment") or {}
    lines += ["## Detected environment", "",
              "- OS: %s %s (%s)" % (
                  (env.get("os") or {}).get("name"),
                  (env.get("os") or {}).get("version"),
                  (env.get("os") or {}).get("arch")),
              "- CPU cores: %s · RAM: %s GB · Free disk: %s GB" % (
                  (env.get("cpu") or {}).get("cores"), env.get("ram_gb"),
                  (env.get("storage") or {}).get("free_gb")),
              "- Python: %s (%s)" % ((env.get("python") or {}).get("version"),
                                     (env.get("python") or {}).get("path")),
              "- Node: %s" % ((env.get("node") or {}).get("version") or "not found"),
              "- FFmpeg: %s" % ((env.get("ffmpeg") or {}).get("version") or "not found"),
              "- GPU: %s" % ((env.get("gpu") or {}).get("name") or "unknown"),
              "- Providers configured: %s" % (
                  ", ".join(sorted((env.get("providers") or {}).keys())) or "none"),
              ""]
    labels = {"mandatory": "Mandatory", "conditional": "Conditional",
              "optional": "Optional", "external": "External / provider"}
    for bucket in ("mandatory", "conditional", "optional", "external"):
        items = ((manifest or {}).get("dependencies") or {}).get(bucket) or []
        if not items:
            continue
        lines += ["## %s" % labels[bucket], "",
                  "| Dependency | Selected | Detected | Version | Why |",
                  "|---|---|---|---|---|"]
        for item in items:
            lines.append("| %s | %s | %s | %s | %s |" % (
                item.get("name"), "yes" if item.get("selected") else "no",
                "yes" if item.get("detected") else "no",
                item.get("version") or "-",
                (item.get("why") or "").replace("|", "/")))
        lines.append("")
    plan = (manifest or {}).get("install_plan") or []
    lines += ["## Installation plan", ""]
    if not plan:
        lines.append("Nothing needs to be installed for this plan.")
    else:
        lines += ["| Step | Command | Approval | Size (MB) | Cost |",
                  "|---|---|---|---|---|"]
        for step in plan:
            lines.append("| %s | `%s` | %s | %s | %s |" % (
                step.get("title"), step.get("command") or "-",
                "required" if step.get("requires_approval") else "not required",
                step.get("estimated_size_mb"), step.get("estimated_cost")))
    lines += ["", "## Installation policy", "",
              "- Reuse compatible software already installed; pin tested versions.",
              "- Prefer isolated environments; never make system-wide changes by default.",
              "- Administrator access and downloads over %s MB require approval."
              % ((manifest or {}).get("policy") or {}).get("large_download_approval_mb"),
              "- External API usage may cost money and must be disclosed before running.",
              "- Credentials stay in the OS environment, never in the skill package.",
              "- Setup is idempotent and every step is reversible."]
    return "\n".join(lines).strip() + "\n"
