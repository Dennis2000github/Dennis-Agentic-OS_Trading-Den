"""Agent-agnostic plugin manager.

A *plugin* is a portable folder built on the open Agent Skills standard:

    my-plugin/
      plugin.json            universal manifest (optional but recommended)
      skills/<skill>/SKILL.md
      agents/                optional agent definitions
      mcp/                   optional MCP server configs (reported, not run)
      hooks/                 optional lifecycle hooks
      README.md

The same plugin can be installed into any agent that reads ``SKILL.md``
(Claude Code, Cursor, Codex, Gemini CLI, OpenCode, Hermes, or the generic
``~/.agents/skills``). This module never assumes Claude: targets are just
directories, and the state file records which agents were selected.

No third-party dependencies; everything is file based under
``DENNIS_PLUGINS_DIR`` (default ``~/.agents/plugins``).

Catalog entries carry a ``tier``:
  * ``personal`` - packs shipped with the OS / authored locally
  * ``public``   - community packs under ``public-plugins/`` (e.g. API Mega List)

The Plugins tab uses that field to split the discovery rail into Personal and
Public without hard-coding plugin names in the browser.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import config

PLUGINS_ROOT = os.path.abspath(
    os.environ.get("DENNIS_PLUGINS_DIR", os.path.join(os.path.expanduser("~"), ".agents", "plugins"))
)
STATE_FILE = os.path.join(config.DATA_DIR, "plugins_state.json")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)

# Where each agent looks for skills. Keys are stable ids used by the UI/state.
AGENT_SKILL_DIRS = {
    "generic": os.path.join(os.path.expanduser("~"), ".agents", "skills"),
    "claude-code": os.path.join(os.path.expanduser("~"), ".claude", "skills"),
    "cursor": os.path.join(os.path.expanduser("~"), ".cursor", "skills"),
    "codex": os.path.join(os.path.expanduser("~"), ".codex", "skills"),
    "gemini-cli": os.path.join(os.path.expanduser("~"), ".gemini", "skills"),
    "opencode": os.path.join(os.path.expanduser("~"), ".config", "opencode", "skills"),
    "hermes": os.path.join(os.path.expanduser("~"), ".hermes", "skills"),
}

AGENT_LABELS = {
    "generic": "Generic (~/.agents)",
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "codex": "Codex",
    "gemini-cli": "Gemini CLI",
    "opencode": "OpenCode",
    "hermes": "Hermes",
}


class PluginError(Exception):
    pass


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _safe_name(name):
    name = (name or "").strip().lower()
    if not NAME_RE.match(name):
        raise PluginError("Invalid plugin name: %r" % name)
    return name


def _load_state():
    data = config.read_json(STATE_FILE, default=None)
    if not isinstance(data, dict):
        data = {}
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    return {"plugins": plugins}


def _save_state(state):
    config.write_json(STATE_FILE, state)


def _state_for(state, name):
    rec = state["plugins"].get(name)
    if not isinstance(rec, dict):
        rec = {"enabled": True, "agents": []}
    rec.setdefault("enabled", True)
    agents = rec.get("agents")
    rec["agents"] = [a for a in agents if a in AGENT_SKILL_DIRS] if isinstance(agents, list) else []
    return rec


def _tier_for(name, rec):
    """Personal vs Public. State wins; catalog membership is the fallback."""
    tier = rec.get("tier") if isinstance(rec, dict) else None
    if tier in ("personal", "public"):
        return tier
    for entry in _CATALOG:
        if entry["id"] == name:
            return entry.get("tier", "personal")
    return "personal"


def _plugin_dir(name):
    return os.path.join(PLUGINS_ROOT, _safe_name(name))


# Short tags used by the Installed Plugins rail. Manifests can override the tag
# with ``tag`` or ``x-dennis.tag``; this map covers packs that predate tags and
# the built-in apps that never carried a plugin manifest.
KNOWN_TAGS = {
    "composio": "MCP",
    "composio-cli": "CLI",
    "rea": "REV",
    "heygen": "VID",
    "api-mega-list": "API",
    "free-llm-apis": "LLM",
    "ui-ux-pro-max": "UX",
    "yt2skill": "SKL",
}


def _short_tag(name, manifest=None):
    manifest = manifest if isinstance(manifest, dict) else {}
    meta = manifest.get("x-dennis")
    meta = meta if isinstance(meta, dict) else {}
    raw = meta.get("tag") or meta.get("short") or manifest.get("tag") or ""
    if not raw:
        raw = KNOWN_TAGS.get(name, "")
    if not raw:
        raw = name
    tag = re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()
    return (tag[:4] or "PLG")


def _read_manifest(plugin_dir):
    paths = [
        os.path.join(plugin_dir, "plugin.json"),
        os.path.join(plugin_dir, ".claude-plugin", "plugin.json"),
    ]
    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return {}


def _skill_meta(path):
    title, desc = "", ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return title, desc
    m = FRONTMATTER_RE.match(text)
    if m:
        fm = m.group(1)
        nm = re.search(r"^name\s*:\s*(.+)$", fm, re.MULTILINE)
        dm = re.search(r"^description\s*:\s*(.+)$", fm, re.MULTILINE)
        title = nm.group(1).strip() if nm else ""
        desc = dm.group(1).strip() if dm else ""
    return title, desc


def _within(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def _manifest_skills(plugin_dir, manifest):
    """Return [(rel_dir, abs_dir)] for skills declared or discovered."""
    found, seen = [], set()

    def add(adapter):
        if not _within(plugin_dir, adapter):
            return
        if os.path.isfile(os.path.join(adapter, "SKILL.md")) and adapter not in seen:
            seen.add(adapter)
            found.append((os.path.relpath(adapter, plugin_dir), adapter))

    rels = manifest.get("skills")
    # `skills` may be a list of folders, a single folder ("" or "." for the
    # plugin root), or the Claude-style "./.claude/skills/" directory.
    if isinstance(rels, str):
        rels = [rels]
    if isinstance(rels, list):
        for rel in rels:
            if not isinstance(rel, str):
                continue
            adapter = os.path.join(plugin_dir, rel)
            if os.path.isdir(adapter):
                add(adapter)
                if rel not in ("", "."):
                    try:
                        for entry in sorted(os.listdir(adapter)):
                            add(os.path.join(adapter, entry))
                    except OSError:
                        pass
            else:
                add(adapter)
    # Discover skills/ (and Claude's .claude/skills/) when the manifest is
    # silent, plus a bare top-level SKILL.md.
    if not found:
        for skills_root in (os.path.join(plugin_dir, "skills"),
                            os.path.join(plugin_dir, ".claude", "skills")):
            if not os.path.isdir(skills_root):
                continue
            try:
                entries = sorted(os.listdir(skills_root))
            except OSError:
                continue
            for entry in entries:
                add(os.path.join(skills_root, entry))
        add(plugin_dir)
    return found


def _plugin_summary(plugin_dir, state):
    name = os.path.basename(plugin_dir)
    manifest = _read_manifest(plugin_dir)
    rec = _state_for(state, name)
    skills = []
    for rel, abspath in _manifest_skills(plugin_dir, manifest):
        title, desc = _skill_meta(os.path.join(abspath, "SKILL.md"))
        skills.append({
            "name": os.path.basename(abspath) if abspath != plugin_dir else name,
            "rel": rel.replace(os.sep, "/"),
            "title": title or os.path.basename(abspath),
            "description": desc,
            "installed": _installed_agents(abspath),
        })
    mcp = manifest.get("mcp")
    mcp = mcp if isinstance(mcp, list) else []
    compat = manifest.get("compatibility")
    compat_agents = []
    if isinstance(compat, dict) and isinstance(compat.get("agents"), list):
        compat_agents = [a for a in compat["agents"] if isinstance(a, str)]
    readme = ""
    rp = os.path.join(plugin_dir, "README.md")
    if os.path.isfile(rp):
        try:
            with open(rp, encoding="utf-8", errors="replace") as fh:
                readme = fh.read()[:4000]
        except OSError:
            readme = ""
    collections = manifest.get("collections")
    collections = [
        {
            "id": str(c.get("id") or "")[:64],
            "name": str(c.get("name") or "")[:80],
            "count": c.get("count") if isinstance(c.get("count"), int) else 0,
            "reference": str(c.get("reference") or "")[:160],
        }
        for c in collections
        if isinstance(c, dict)
    ] if isinstance(collections, list) else []
    meta = manifest.get("x-dennis")
    meta = meta if isinstance(meta, dict) else {}
    return {
        "name": name,
        "title": str(manifest.get("title") or meta.get("title") or manifest.get("name") or name),
        "tag": _short_tag(name, manifest),
        "icon": str(manifest.get("icon") or meta.get("icon") or "puzzle"),
        "category": str(manifest.get("category") or meta.get("category") or "Plugin"),
        "version": str(manifest.get("version") or ""),
        "description": str(manifest.get("description") or ""),
        "author": str(manifest.get("author") or ""),
        "license": str(manifest.get("license") or ""),
        "path": plugin_dir,
        "enabled": bool(rec.get("enabled", True)),
        "tier": _tier_for(name, rec),
        "agents": rec.get("agents", []),
        "compatibility": compat_agents,
        "skills": skills,
        "skill_count": len(skills),
        "mcp": [m for m in mcp if isinstance(m, str)],
        "collections": collections,
        "repository": str(manifest.get("repository") or manifest.get("homepage") or ""),
        "homepage": str(manifest.get("homepage") or ""),
        "source_url": str(rec.get("source_url") or ""),
        "installed_at": str(rec.get("installed_at") or ""),
        "meta": {
            "api_count": meta.get("api_count") if isinstance(meta.get("api_count"), int) else 0,
            "category_count": meta.get("category_count") if isinstance(meta.get("category_count"), int) else 0,
            "source_repo": str(meta.get("source_repo") or ""),
            "generated_at": str(meta.get("generated_at") or ""),
        },
        "has_agents_dir": os.path.isdir(os.path.join(plugin_dir, "agents")),
        "has_hooks_dir": os.path.isdir(os.path.join(plugin_dir, "hooks")),
        "readme": readme,
    }


def _installed_agents(skill_dir):
    """Agents whose skills dir has a link/copy pointing at this skill."""
    agents = []
    for agent, root in AGENT_SKILL_DIRS.items():
        target = os.path.join(root, os.path.basename(skill_dir))
        try:
            if os.path.islink(target):
                if os.path.realpath(target) == os.path.realpath(skill_dir):
                    agents.append(agent)
            elif os.path.isdir(target):
                agents.append(agent)
        except OSError:
            continue
    return agents


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def agents():
    out = []
    for agent, root in AGENT_SKILL_DIRS.items():
        out.append({
            "id": agent,
            "label": AGENT_LABELS.get(agent, agent),
            "dir": root,
            "present": os.path.isdir(os.path.dirname(root)) or os.path.isdir(root),
        })
    return out


def status():
    root = PLUGINS_ROOT
    count = len(_plugin_dirs())
    return {
        "root": root,
        "root_exists": os.path.isdir(root),
        "count": count,
        "agents": agents(),
    }


def _plugin_dirs():
    if not os.path.isdir(PLUGINS_ROOT):
        return []
    out = []
    for entry in sorted(os.listdir(PLUGINS_ROOT)):
        if entry.startswith("."):
            continue
        path = os.path.join(PLUGINS_ROOT, entry)
        if not os.path.isdir(path):
            continue
        if (os.path.isfile(os.path.join(path, "plugin.json"))
                or os.path.isfile(os.path.join(path, ".claude-plugin", "plugin.json"))
                or os.path.isdir(os.path.join(path, "skills"))
                or os.path.isdir(os.path.join(path, ".claude", "skills"))
                or os.path.isfile(os.path.join(path, "SKILL.md"))):
            out.append(path)
    return out


def list_plugins():
    state = _load_state()
    items = [_plugin_summary(p, state) for p in _plugin_dirs()]
    return {
        "plugins": items,
        "root": PLUGINS_ROOT,
        "root_exists": os.path.isdir(PLUGINS_ROOT),
        "agents": agents(),
    }


def read_plugin(name):
    state = _load_state()
    path = _plugin_dir(name)
    if not os.path.isdir(path):
        raise PluginError("Plugin '%s' is not installed." % name)
    return _plugin_summary(path, state)


def install_plugin(source, name=None, force=False, tier="personal", source_url=""):
    source = (source or "").strip()
    if not source:
        raise PluginError("A local folder or git URL is required.")
    os.makedirs(PLUGINS_ROOT, exist_ok=True)
    tmp = None
    src_dir = None
    try:
        if os.path.isdir(os.path.expanduser(source)):
            src_dir = os.path.abspath(os.path.expanduser(source))
        elif re.match(r"^(https?://|git@|ssh://|git://)", source) or source.endswith(".git"):
            tmp = tempfile.mkdtemp(prefix="dennis-plugin-")
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", source, tmp],
                capture_output=True, text=True, timeout=300,
            )
            if proc.returncode != 0:
                raise PluginError("git clone failed: %s" % ((proc.stderr or proc.stdout).strip()[:300]))
            src_dir = tmp
        else:
            raise PluginError("Not a local folder or git URL: %s" % source)

        if not os.path.isdir(src_dir):
            raise PluginError("Source is not a directory: %s" % source)

        manifest = _read_manifest(src_dir)
        resolved = (name or manifest.get("name") or os.path.basename(src_dir)).strip().lower()
        safe = _safe_name(resolved)
        dest = os.path.join(PLUGINS_ROOT, safe)

        if os.path.isdir(dest) and not force:
            raise PluginError("Plugin '%s' is already installed (remove it first)." % safe)
        if os.path.isdir(dest) and force:
            shutil.rmtree(dest)

        staging = dest + ".installing"
        if os.path.isdir(staging):
            shutil.rmtree(staging)
        try:
            shutil.copytree(src_dir, staging, symlinks=False,
                            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
            os.rename(staging, dest)
        except OSError as e:
            shutil.rmtree(staging, ignore_errors=True)
            raise PluginError("Could not install plugin: %s" % e)
    except subprocess.TimeoutExpired:
        raise PluginError("git clone timed out.")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    state = _load_state()
    rec = _state_for(state, safe)
    rec["enabled"] = True
    rec["tier"] = tier if tier in ("personal", "public") else "personal"
    if source_url:
        rec["source_url"] = str(source_url)[:500]
    rec["installed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    state["plugins"][safe] = rec
    _save_state(state)
    return read_plugin(safe)


def set_enabled(name, enabled, selected_agents=None):
    safe = _safe_name(name)
    path = _plugin_dir(safe)
    if not os.path.isdir(path):
        raise PluginError("Plugin '%s' is not installed." % safe)
    state = _load_state()
    rec = _state_for(state, safe)
    rec["enabled"] = bool(enabled)
    if selected_agents is not None:
        rec["agents"] = [a for a in selected_agents if a in AGENT_SKILL_DIRS]
    state["plugins"][safe] = rec
    _save_state(state)
    return read_plugin(safe)


def install_skills(name, selected_agents=None, force=False):
    """Symlink a plugin's skills into the chosen agents' skill directories."""
    safe = _safe_name(name)
    plugin_path = _plugin_dir(safe)
    if not os.path.isdir(plugin_path):
        raise PluginError("Plugin '%s' is not installed." % safe)
    manifest = _read_manifest(plugin_path)

    chosen = selected_agents or [a for a in AGENT_SKILL_DIRS if a != "generic"]
    chosen = [a for a in chosen if a in AGENT_SKILL_DIRS]
    if not chosen:
        raise PluginError("Select at least one target agent.")

    results = []
    for rel, skill_dir in _manifest_skills(plugin_path, manifest):
        skill_name = os.path.basename(skill_dir) if skill_dir != plugin_path else safe
        for agent in chosen:
            root = AGENT_SKILL_DIRS[agent]
            target = os.path.join(root, skill_name)
            entry = {"skill": skill_name, "agent": agent, "target": target, "ok": False, "reason": ""}
            try:
                os.makedirs(root, exist_ok=True)
                if os.path.islink(target) or os.path.isfile(target):
                    if os.path.realpath(target) == os.path.realpath(skill_dir):
                        entry["ok"] = True
                        entry["reason"] = "already linked"
                        results.append(entry)
                        continue
                    if not force:
                        entry["reason"] = "a different skill already exists here"
                        results.append(entry)
                        continue
                    os.unlink(target)
                elif os.path.isdir(target):
                    if not force:
                        entry["reason"] = "a different skill folder already exists here"
                        results.append(entry)
                        continue
                    shutil.rmtree(target)
                try:
                    os.symlink(skill_dir, target)
                except OSError:
                    shutil.copytree(skill_dir, target, symlinks=False,
                                    ignore=shutil.ignore_patterns(".git", "__pycache__"))
                entry["ok"] = True
            except OSError as e:
                entry["reason"] = str(e)[:200]
            results.append(entry)

    state = _load_state()
    rec = _state_for(state, safe)
    merged = set(rec.get("agents", [])) | set(chosen)
    rec["agents"] = sorted(merged)
    state["plugins"][safe] = rec
    _save_state(state)
    return {"name": safe, "results": results, "plugin": read_plugin(safe)}


def uninstall_plugin(name, keep_skills=False):
    safe = _safe_name(name)
    path = _plugin_dir(safe)
    if not os.path.isdir(path):
        raise PluginError("Plugin '%s' is not installed." % safe)
    removed_links = []
    if not keep_skills:
        manifest = _read_manifest(path)
        for _rel, skill_dir in _manifest_skills(path, manifest):
            skill_name = os.path.basename(skill_dir) if skill_dir != path else safe
            for agent, root in AGENT_SKILL_DIRS.items():
                target = os.path.join(root, skill_name)
                try:
                    if os.path.islink(target) and os.path.realpath(target) == os.path.realpath(skill_dir):
                        os.unlink(target)
                        removed_links.append(target)
                except OSError:
                    continue
    shutil.rmtree(path)
    state = _load_state()
    state["plugins"].pop(safe, None)
    _save_state(state)
    return {"name": safe, "removed": True, "removed_links": removed_links}


def convert_to_claude(name):
    """Add Claude Code plugin metadata alongside the universal manifest."""
    safe = _safe_name(name)
    path = _plugin_dir(safe)
    if not os.path.isdir(path):
        raise PluginError("Plugin '%s' is not installed." % safe)
    manifest = _read_manifest(path)
    claude_dir = os.path.join(path, ".claude-plugin")
    os.makedirs(claude_dir, exist_ok=True)
    meta = {
        "name": safe,
        "version": str(manifest.get("version") or "1.0.0"),
        "description": str(manifest.get("description") or ""),
    }
    if manifest.get("author"):
        meta["author"] = manifest.get("author")
    target = os.path.join(claude_dir, "plugin.json")
    config.write_json(target, meta)
    return {"name": safe, "path": target}


def scaffold(name, description=""):
    """Create an empty universal plugin skeleton so users can start authoring."""
    safe = _safe_name(name)
    dest = os.path.join(PLUGINS_ROOT, safe)
    if os.path.isdir(dest):
        raise PluginError("Plugin '%s' already exists." % safe)
    skill_dir = os.path.join(dest, "skills", "%s-expert" % safe)
    os.makedirs(skill_dir, exist_ok=True)
    manifest = {
        "name": safe,
        "version": "0.1.0",
        "description": description or ("%s plugin" % safe),
        "author": "",
        "skills": ["skills/%s-expert" % safe],
        "mcp": [],
        "compatibility": {"agents": list(AGENT_SKILL_DIRS)},
    }
    config.write_json(os.path.join(dest, "plugin.json"), manifest)
    with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "---\nname: %s-expert\ndescription: Use when the task matches %s.\n---\n\n"
            "# %s Expert\n\nDescribe the method here.\n" % (safe, safe, safe.title())
        )
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8") as fh:
        fh.write("# %s\n\n%s\n" % (safe, manifest["description"]))
    state = _load_state()
    state["plugins"][safe] = {"enabled": True, "agents": [], "tier": "personal"}
    _save_state(state)
    return read_plugin(safe)


# ---------------------------------------------------------------------------
# Catalog + skills discovery (the "discover -> add -> installed" pipeline)
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Bundled, actually-installable packs. `rel` points at a folder containing
# SKILL.md (or skills/<name>/SKILL.md). These are copies, never live links.
#
# `tier` drives how a pack is surfaced:
#   personal - packs authored locally
#   public   - community packs anyone can add (see public-plugins/)
#
# The Plugins tab is now the API Mega List console; this catalog remains the
# install/link surface used by the console's "Install skill" action.
# The Plugins tab also hosts the Composio integration panel, which installs the
# bundled public pack through this same pipeline.
_CATALOG = [
    {
        "id": "composio",
        "name": "Composio",
        "title": "Composio",
        "tag": "MCP",
        "icon": "plug-zap",
        "category": "Integrations",
        "tier": "public",
        "kind": "plugin",
        "repository": "https://github.com/ComposioHQ/composio",
        "homepage": "https://composio.dev",
        "rel": os.path.join("public-plugins", "composio"),
        "blurb": "One MCP endpoint for 1,600+ app toolkits: Gmail, Slack, GitHub, "
                 "Notion, Linear, HubSpot, Google Sheets and more.",
    },
    {
        "id": "rea",
        "name": "REA",
        "title": "REA",
        "tag": "REV",
        "icon": "binary",
        "category": "Developer tools",
        "tier": "public",
        "kind": "plugin",
        "repository": "https://github.com/morluto/rea",
        "homepage": "https://rea.tools",
        "rel": os.path.join("public-plugins", "rea"),
        "blurb": "Reverse engineer anything: one local MCP for native binaries, "
                 "JavaScript/Electron apps, .NET assemblies, websites, Android "
                 "APKs and more, with evidence-backed findings.",
    },
    {
        "id": "ui-ux-pro-max",
        "name": "UI UX Pro Max",
        "title": "UI UX Pro Max",
        "tag": "UX",
        "icon": "palette",
        "category": "Design intelligence",
        "tier": "public",
        "kind": "plugin",
        "repository": "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill",
        "homepage": "https://uupm.cc",
        "rel": os.path.join("public-plugins", "ui-ux-pro-max"),
        "blurb": "UI/UX design intelligence for agents: 80 searchable styles, "
                 "192 product palettes, 74 font pairings, 120 UX guidelines, "
                 "25 chart types and 22 stack guides.",
    },
]

# Apps that ship with the OS but are not agent-skill packs. The Installed
# Plugins view lists them next to real plugins so one rail covers every
# installed app and GitHub repo.
_BUILTIN_APPS = [
    {
        "id": "api-mega-list",
        "name": "API Mega List",
        "title": "API Mega List",
        "tag": "API",
        "icon": "database-zap",
        "category": "API directory",
        "tier": "public",
        "kind": "app",
        "status": "core",
        "installable": False,
        "view": "plugins",
        "repository": "https://github.com/cporter202/API-mega-list",
        "rel": os.path.join("public-plugins", "api-mega-list"),
        "blurb": "Upstream repository workspace with 23 core categories, the "
                 "featured collection, sponsored partners and its README pipeline.",
    },
    {
        "id": "free-llm-apis",
        "name": "FreeLLMAPI",
        "title": "FreeLLMAPI",
        "tag": "LLM",
        "icon": "zap",
        "category": "Free model catalog",
        "tier": "public",
        "kind": "app",
        "status": "core",
        "installable": False,
        "view": "freellm",
        "repository": "https://github.com/open-free-llm-api/awesome-freellm-apis",
        "homepage": "https://freellm.net",
        "rel": os.path.join("public-plugins", "free-llm-apis"),
        "blurb": "Free model catalog with 505+ models from 30 providers, search, "
                 "tier filters and per-agent connections.",
    },
]


def _catalog_entry(entry, installed):
    src = os.path.join(REPO_ROOT, entry["rel"])
    title, desc = ("", "")
    skill_md = os.path.join(src, "SKILL.md")
    if os.path.isfile(skill_md):
        title, desc = _skill_meta(skill_md)
    if not title and os.path.isdir(src):
        # Packs keep their skills under skills/<name>/; use the first one so the
        # catalog can describe a pack that has no root SKILL.md.
        for _rel, adapter in _manifest_skills(src, _read_manifest(src)):
            skill_title, skill_desc = _skill_meta(os.path.join(adapter, "SKILL.md"))
            title = title or skill_title
            desc = desc or skill_desc
            if title:
                break
    return {
        "id": entry["id"],
        "name": entry["name"],
        "title": entry.get("title") or entry["name"],
        "tag": entry.get("tag") or _short_tag(entry["id"]),
        "icon": entry["icon"],
        "category": entry["category"],
        "tier": entry.get("tier", "personal"),
        "kind": entry.get("kind", "plugin"),
        "repository": entry.get("repository", ""),
        "homepage": entry.get("homepage", ""),
        "description": entry.get("blurb") or desc,
        "skill_title": title,
        "source": src,
        "available": os.path.isdir(src),
        "installed": entry["id"] in installed,
    }


def catalog():
    plugins = list_plugins()
    installed = set(p["name"] for p in plugins["plugins"])
    items = [_catalog_entry(e, installed) for e in _CATALOG]
    return {
        "items": items,
        "installed": sorted(installed),
        "counts": {
            "personal": len([i for i in items if i["tier"] == "personal"]),
            "public": len([i for i in items if i["tier"] == "public"]),
        },
    }


def _installed_item(base, rec):
    item = dict(base)
    if rec:
        # The installed summary is the source of truth for live fields, but it
        # must not erase curated catalog metadata (tag, title, repository).
        curated = dict(item)
        item.update(rec)
        for key in ("id", "title", "tag", "icon", "category", "repository",
                    "homepage", "description", "kind", "tier", "view"):
            if curated.get(key) and (key == "title" or not item.get(key)):
                item[key] = curated[key]
        item["status"] = "installed" if rec.get("enabled", True) else "disabled"
        item["installed"] = True
        item["installable"] = bool(curated.get("installable", True))
    else:
        item["installed"] = False
        item["enabled"] = False
        item["agents"] = []
        item["skills"] = []
        item["skill_count"] = 0
        item["installable"] = bool(base.get("installable", True))
        item.setdefault("status",
                        "bundled" if os.path.isdir(os.path.join(REPO_ROOT, base.get("rel", "")))
                        else "available")
    item["name"] = item.get("name") or item.get("id")
    item["title"] = item.get("title") or item["name"]
    item["tag"] = _short_tag(item.get("id") or item["name"], {"tag": item.get("tag")})
    item["icon"] = item.get("icon") or "puzzle"
    item["category"] = item.get("category") or "Plugin"
    item["kind"] = item.get("kind") or "plugin"
    item["view"] = item.get("view") or ""
    item["repository"] = item.get("repository") or ""
    item["homepage"] = item.get("homepage") or ""
    item["description"] = item.get("description") or ""
    return item


def installed_apps():
    """One rail for every installed plugin, bundled pack, and built-in app.

    Installed plugin folders are merged with the bundled catalog and the apps
    shipped with the OS. Each item carries a short ``tag`` for the sidebar / card
    badge, a lifecycle ``status`` and the skills already linked into agents.
    """
    kit = list_plugins()
    installed = {p["name"]: p for p in kit["plugins"]}
    items, seen = [], set()

    for entry in _CATALOG:
        rec = installed.pop(entry["id"], None)
        base = {
            "id": entry["id"],
            "name": entry["name"],
            "title": entry.get("title") or entry["name"],
            "tag": entry.get("tag", ""),
            "icon": entry.get("icon", "puzzle"),
            "category": entry.get("category", "Plugin"),
            "tier": entry.get("tier", "public"),
            "kind": entry.get("kind", "plugin"),
            "description": entry.get("blurb", ""),
            "repository": entry.get("repository", ""),
            "homepage": entry.get("homepage", ""),
            "rel": entry.get("rel", ""),
            "installable": True,
            "view": "",
            "status": "bundled",
        }
        items.append(_installed_item(base, rec))
        seen.add(entry["id"])

    for entry in _BUILTIN_APPS:
        if entry["id"] in seen:
            continue
        rec = installed.pop(entry["id"], None)
        base = dict(entry)
        base["description"] = entry.get("description") or entry.get("blurb", "")
        base["installable"] = False
        base["rel"] = entry.get("rel", "")
        base.setdefault("status", "core")
        items.append(_installed_item(base, rec))
        seen.add(entry["id"])

    for name, rec in installed.items():
        base = {
            "id": name,
            "name": name,
            "title": rec.get("title") or name,
            "tag": rec.get("tag", ""),
            "icon": rec.get("icon", "puzzle"),
            "category": rec.get("category", "Plugin"),
            "tier": rec.get("tier", "personal"),
            "kind": "plugin",
            "description": rec.get("description", ""),
            "repository": rec.get("repository", ""),
            "homepage": rec.get("homepage", ""),
            "rel": "",
            "installable": True,
            "view": "",
            "status": "installed",
        }
        items.append(_installed_item(base, rec))

    order = {"installed": 0, "disabled": 1, "core": 2, "bundled": 3, "available": 4}
    items.sort(key=lambda item: (order.get(item.get("status"), 9),
                                 str(item.get("title") or "").lower()))

    counts = {
        "total": len(items),
        "installed": len([i for i in items if i["status"] == "installed"]),
        "disabled": len([i for i in items if i["status"] == "disabled"]),
        "core": len([i for i in items if i["status"] == "core"]),
        "bundled": len([i for i in items if i["status"] == "bundled"]),
        "available": len([i for i in items if i["status"] == "available"]),
        "skills": sum(i.get("skill_count") or len(i.get("skills") or []) for i in items),
        "agents": len(kit.get("agents") or []),
    }
    return {
        "items": items,
        "counts": counts,
        "agents": kit.get("agents") or [],
        "root": kit.get("root") or PLUGINS_ROOT,
        "root_exists": kit.get("root_exists", False),
    }



def install_catalog(catalog_id, force=False):
    entry = None
    for item in _CATALOG:
        if item["id"] == catalog_id:
            entry = item
            break
    if entry is None:
        raise PluginError("Unknown catalog plugin: %r" % catalog_id)
    src = os.path.join(REPO_ROOT, entry["rel"])
    if not os.path.isdir(src):
        raise PluginError("Bundled source is missing: %s" % src)
    return install_plugin(
        src,
        name=entry["id"],
        force=force,
        tier=entry.get("tier", "personal"),
        source_url=os.path.join(REPO_ROOT, entry["rel"]),
    )


def list_skills():
    """Every SKILL.md visible to any supported agent, deduped by name."""
    found = {}
    order = []
    for agent, root in AGENT_SKILL_DIRS.items():
        if not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for entry in entries:
            if entry.startswith("."):
                continue
            path = os.path.join(root, entry)
            skill_md = os.path.join(path, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            if entry not in found:
                title, desc = _skill_meta(skill_md)
                found[entry] = {
                    "name": entry,
                    "title": title or entry,
                    "description": desc,
                    "path": os.path.realpath(path),
                    "agents": [],
                    "plugin": entry,
                }
                order.append(entry)
            if agent not in found[entry]["agents"]:
                found[entry]["agents"].append(agent)
    return {
        "skills": [found[n] for n in order],
        "agents": agents(),
        "count": len(order),
    }
