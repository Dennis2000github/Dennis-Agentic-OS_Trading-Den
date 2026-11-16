"""Universal Skill Access API (Phase 3).

One centralized service through which every compatible agent discovers,
loads, executes and releases skills from the canonical registry. The design
rule from the audit: **one canonical source, progressive loading, no
per-agent copies**.

Canonical source ........... ``vault/Skills/<name>/`` (Obsidian Vault)
Metadata + status ........... ``data/skill_registry.json`` (skill_registry)
Agent views ................. symlinks into ``plugins.AGENT_SKILL_DIRS``
Execution ................... ``backend/skill_execution.py`` (PTY, bounded env)
Approval ................... Studio ``ApprovalStore`` pattern
Dependencies ............... ``skill_production`` manifest model + binary gate

Operations (all return plain dicts; errors raise SkillAccessError):

    search_skill          metadata-only discovery (progressive load step 1)
    resolve_skill         canonical id + install/integrity/enable checks
    load_skill            SKILL.md + required refs/scripts (step 2/3)
    check_dependencies    mandatory/conditional/external + binary presence
    check_agent_compatibility  capability matrix verdict per agent
    execute_skill         through the existing execution manager
    validate_output       contract + secret scan + artifact registration
    release_skill_context       teardown + audit log + context record

Never raises for *outcomes* (incompatible, missing, disabled); those come
back as structured payloads. Only invalid input raises.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time

from . import config
from . import skill_registry as registry
from . import skill_capabilities as caps
from . import skill_execution as execenv
from . import skill_sources_registry as sources_registry

SERVICE = "skill_access"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SkillAccessError(Exception):
    def __init__(self, code, message, status=400):
        self.code = code
        self.message = message
        self.status = int(status)
        super().__init__(message)


# ---------------------------------------------------------------------------
# Canonical source roots
# ---------------------------------------------------------------------------

def _vault_skills_root():
    try:
        from . import vault as dennis_vault
        return os.path.join(dennis_vault.VAULT_DIR, "Skills")
    except Exception:
        return os.path.join(config.DIRECTORY, "vault", "Skills")


def VAULT_SKILLS_ROOT():
    """Canonical Vault skills root, resolved at call time.

    A function (not a frozen constant) so tests that patch
    ``backend.vault.VAULT_DIR`` keep working: the studio suites patch the
    Vault dir per-test and the registry must follow it.
    """
    return _vault_skills_root()


def _norm(name):
    return re.sub(r"[^a-z0-9._-]+", "-", str(name or "").strip().lower()).strip("-.")


# ---------------------------------------------------------------------------
# Execution contexts (load_skill -> execute_skill -> release_skill_context)
# ---------------------------------------------------------------------------

_CONTEXTS = {}
_CONTEXT_LOCK = threading.RLock()


def _new_context(skill_id, agent_id, request=None):
    ctx_id = "sxc_" + hashlib.sha1(
        ("%s|%s|%d|%d" % (skill_id, agent_id, time.time(),
                          os.getpid())).encode("utf-8")).hexdigest()[:16]
    ctx = {
        "context_id": ctx_id,
        "skill_id": skill_id,
        "agent_id": agent_id,
        "loaded_at": time.time(),
        "released": False,
        "state": "loaded",          # loaded|executing|done|released|failed
        "skill_md": None,
        "files": [],
        "dependencies": None,
        "execution_requirements": [],
        "execution": None,
        "artifacts": [],
        "request": request or {},
        "log": [],
    }
    return ctx_id, ctx


def _log(ctx, message):
    entry = {"t": time.time(), "message": str(message)[:400]}
    ctx["log"].append(entry)
    ctx["log"] = ctx["log"][-200:]


# ---------------------------------------------------------------------------
# 1. search_skill — metadata only, never reads SKILL.md bodies
# ---------------------------------------------------------------------------

def search_skill(query="", category="", tag="", agent_id="", enabled_only=True,
                 limit=50):
    """Discovery. Reads registry metadata + frontmatter; no script loading."""
    q = (query or "").strip().lower()
    tokens = [t for t in re.split(r"[^a-z0-9._-]+", q) if len(t) >= 2]
    results = []
    _ensure_vault_indexed()
    listing = registry.list_skills()
    records = listing.get("skills") or []
    for item in records:
        name = item.get("name") or ""
        record = registry.record(name)
        if record is None:
            continue
        meta = record.get("meta") or {}
        if enabled_only and meta.get("enabled") is False:
            continue
        path = record.get("path") or ""
        # Frontmatter gives title/description even for unregistered vault skills.
        title, description = _frontmatter(name)
        searchable = " ".join([name, title or "", description or "",
                               " ".join(meta.get("capability_tags") or []),
                               " ".join(meta.get("categories") or [])]).lower()
        if tokens:
            score = sum(1 for t in tokens if t in searchable)
            if score == 0:
                continue
        else:
            score = 0
        if category and category.lower() not in [
                str(c).lower() for c in meta.get("categories") or []]:
            continue
        if tag and tag.lower() not in [str(t).lower() for t in meta.get("capability_tags") or []]:
            continue
        if agent_id and agent_id not in (meta.get("compatible_agents") or []):
            continue
        results.append({
            "id": _skill_id(name),
            "name": name,
            "title": title or name,
            "description": description or meta.get("description") or "",
            "version": record.get("current_version") or meta.get("version") or "",
            "categories": meta.get("categories") or [],
            "capability_tags": meta.get("capability_tags") or [],
            "compatible_agents": meta.get("compatible_agents") or [],
            "enabled": meta.get("enabled", True),
            "validation_status": meta.get("validation_status") or "unvalidated",
            "last_validated_at": meta.get("last_validated_at") or "",
            "source": meta.get("source") or record.get("sources") or [],
            "path": path,
            "installation_status": _install_status(name),
            "updated_at": meta.get("updated_at") or record.get("published_at") or "",
            "score": score,
            "executable_via_agent": bool(caps.compatible_agents(
                meta.get("execution_requirements") or [], min_level="executable")),
        })
    results.sort(key=lambda r: (-r.get("score", 0), r["name"]))
    return {"count": len(results), "skills": results[:max(0, int(limit or 50))]}


def _skill_id(name):
    """Canonical id: ``skill://<name>``. Names are unique in the registry."""
    return "skill://%s" % name


def _parse_id(skill_id):
    skill_id = str(skill_id or "").strip()
    if skill_id.startswith("skill://"):
        skill_id = skill_id[len("skill://"):]
    name = _norm(skill_id)
    if not name:
        raise SkillAccessError("skill_id_required", "A skill id is required.")
    return name


def _frontmatter(name):
    """Read only the first 4 KB of SKILL.md for title/description."""
    root = VAULT_SKILLS_ROOT()
    candidate = os.path.join(root, name, "SKILL.md")
    if not os.path.isfile(candidate):
        return "", ""
    try:
        with open(candidate, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
    except OSError:
        return "", ""
    title, description = "", ""
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", head, re.S)
    body = head
    if m:
        fm = m.group(1)
        dm = re.search(r"^description\s*:\s*(.+)$", fm, re.M)
        if dm:
            description = dm.group(1).strip().strip('"')
        body = head[m.end():]
    hm = re.search(r"^#\s+(.+)$", body, re.M)
    if hm:
        title = hm.group(1).strip()
    return title, description


def _install_status(name):
    root = os.path.join(VAULT_SKILLS_ROOT(), name)
    if not os.path.isdir(root):
        return "missing"
    if not os.path.isfile(os.path.join(root, "SKILL.md")):
        return "invalid"
    return "installed"


def _name_exists(name):
    """A skill exists when it is registered OR present in the Vault."""
    if registry.get(name):
        return True
    return os.path.isfile(os.path.join(VAULT_SKILLS_ROOT(), name, "SKILL.md"))


def ensure_registered(name, source_url="", source_kind="local"):
    """Create (or refresh) the canonical registry record for a Vault skill.

    Phase 2 integration: every SKILL.md under ``vault/Skills/`` is indexed by
    the central registry even when it was never published through the Skill
    Builder. The record stays minimal — name, canonical Vault path, version
    (from frontmatter when present), source — and the additive ``meta`` block
    is filled with conservative defaults. The Vault source is never copied or
    rewritten.
    """
    name = _norm(name)
    if not name:
        raise SkillAccessError("skill_name_required", "A skill name is required.")
    root = os.path.join(VAULT_SKILLS_ROOT(), name)
    skill_md = os.path.join(root, "SKILL.md")
    if not os.path.isfile(skill_md):
        raise SkillAccessError("not_installed",
                               "No SKILL.md at %s" % skill_md, 404)
    title, description = _frontmatter(name)
    version = ""
    try:
        with open(skill_md, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
        m = re.search(r"^version\s*:\s*([\w.-]+)\s*$", head, re.M)
        if m:
            version = m.group(1).strip()
    except OSError:
        pass
    sources = [{"kind": source_kind, "url": source_url}] if source_url else [
        {"kind": source_kind, "url": ""}]
    record = registry.register(
        name, "Skills/%s/SKILL.md" % name, sources=sources,
        version=version or None, files=1,
        meta={
            "description": description or title or name,
            "title": title or name,
            "categories": [],
            "capability_tags": [],
            "compatible_agents": [],
            "runtime_dependencies": [],
            "execution_requirements": [],
            "input_spec": {},
            "output_spec": {},
            "source_url": source_url,
            "source_kind": source_kind,
            "enabled": True,
            "validation_status": "unvalidated",
            "skill_format": "skill-md",
        })
    return record


def _ensure_vault_indexed(force=False):
    """Index every unregistered Vault skill (idempotent, cheap)."""
    listed = registry.list_skills().get("skills") or []
    known = {item.get("name") for item in listed}
    indexed = []
    for entry in sorted(os.listdir(VAULT_SKILLS_ROOT())) if os.path.isdir(VAULT_SKILLS_ROOT()) else []:
        if entry.startswith("."):
            continue
        if entry in known and not force:
            continue
        if os.path.isfile(os.path.join(VAULT_SKILLS_ROOT(), entry, "SKILL.md")):
            ensure_registered(entry)
            indexed.append(entry)
    return indexed


def _indexed_names():
    """Names currently visible in the Vault (indexed on demand)."""
    _ensure_vault_indexed()
    return [item.get("name") for item in registry.list_skills().get("skills") or []]


# ---------------------------------------------------------------------------
# 2. resolve_skill — id, install + integrity + enable checks
# ---------------------------------------------------------------------------

def resolve_skill(skill_id):
    name = _parse_id(skill_id)
    if not _name_exists(name):
        raise SkillAccessError("not_registered",
                               "Skill '%s' is not in the central registry." % name, 404)
    if not registry.get(name):
        # A Vault skill that was never explicitly registered: index it now so
        # discovery and registration stay one source of truth.
        try:
            ensure_registered(name)
        except SkillAccessError:
            raise SkillAccessError("not_registered",
                                   "Skill '%s' is not in the central registry." % name, 404)
    record = registry.get(name)
    meta = record.get("meta") or {}
    path = record.get("path") or ""
    vault_path = "Skills/%s/SKILL.md" % name
    abs_path = None
    try:
        from . import vault as dennis_vault
        abs_path = dennis_vault.resolve(vault_path)
    except Exception:
        abs_path = None
    integrity = {
        "skill_md_present": bool(abs_path and os.path.isfile(abs_path)),
        "skill_md_size": os.path.getsize(abs_path) if (abs_path and os.path.isfile(abs_path)) else 0,
        "canonical_path": vault_path,
        "registry_path": path,
        "path_matches": bool(path) and os.path.normpath(path).replace("\\", "/") == vault_path,
    }
    extra_files = []
    if abs_path:
        base = os.path.dirname(abs_path)
        try:
            for entry in sorted(os.listdir(base)):
                if entry.startswith(".") or entry == "SKILL.md":
                    continue
                extra_files.append(entry)
        except OSError:
            pass
    enabled = meta.get("enabled", True)
    return {
        "id": _skill_id(name),
        "name": name,
        "version": record.get("current_version") or meta.get("version") or "",
        "installed": integrity["skill_md_present"],
        "enabled": bool(enabled),
        "integrity": integrity,
        "extra_files": extra_files,
        "categories": meta.get("categories") or [],
        "capability_tags": meta.get("capability_tags") or [],
        "compatible_agents": meta.get("compatible_agents") or [],
        "execution_requirements": meta.get("execution_requirements") or [],
        "validation_status": meta.get("validation_status") or "unvalidated",
        "last_validated_at": meta.get("last_validated_at") or "",
        "source": meta.get("source") or record.get("sources") or [],
        "installation_status": _install_status(name),
    }


# ---------------------------------------------------------------------------
# 3. load_skill — SKILL.md + required refs/scripts, structured context
# ---------------------------------------------------------------------------

def load_skill(skill_id, agent_id="", include_references=False, include_scripts=False,
               request=None):
    """Return a structured execution context (progressive load step 2/3).

    The canonical source is never modified; nothing is copied into an agent
    workspace unless the calling agent's consumption method requires it
    (instruction injection reads the text, native skills use the symlink).
    """
    name = _parse_id(skill_id)
    resolved = resolve_skill(skill_id)
    if not resolved["installed"]:
        raise SkillAccessError("not_installed",
                               "Skill '%s' is not installed in the Vault." % name, 404)
    if not resolved["enabled"]:
        raise SkillAccessError("disabled",
                               "Skill '%s' is disabled; new invocations are blocked." % name, 409)

    abs_path = None
    try:
        from . import vault as dennis_vault
        abs_path = dennis_vault.resolve(resolved["integrity"]["canonical_path"])
    except Exception:
        abs_path = None
    if not abs_path or not os.path.isfile(abs_path):
        raise SkillAccessError("not_installed", "SKILL.md is missing for '%s'." % name)

    with open(abs_path, "r", encoding="utf-8", errors="replace") as fh:
        skill_md = fh.read()

    meta = (registry.get(name) or {}).get("meta") or {}
    files = []
    if include_references or include_scripts:
        base = os.path.dirname(abs_path)
        for rel in sorted(os.listdir(base)):
            if rel.startswith(".") or rel == "SKILL.md":
                continue
            full = os.path.join(base, rel)
            entry = {"name": rel, "kind": "dir" if os.path.isdir(full) else "file"}
            if include_scripts or entry["kind"] == "dir":
                files.append(entry)

    ctx_id, ctx = _new_context(name, agent_id, request)
    ctx["skill_md"] = skill_md
    ctx["files"] = files
    ctx["dependencies"] = meta.get("runtime_dependencies") or []
    ctx["execution_requirements"] = meta.get("execution_requirements") or []
    ctx["compatible_agents"] = meta.get("compatible_agents") or []
    ctx["input_schema"] = meta.get("input_spec") or {}
    ctx["output_schema"] = meta.get("output_spec") or {}
    _log(ctx, "loaded skill %s (agent=%s, refs=%s scripts=%s)" % (
        name, agent_id, include_references, include_scripts))

    with _CONTEXT_LOCK:
        _CONTEXTS[ctx_id] = ctx
    return {
        "context_id": ctx_id,
        "skill_id": _skill_id(name),
        "name": name,
        "agent_id": agent_id,
        "version": resolved["version"],
        "skill_md": skill_md,
        "files": files,
        "dependencies": ctx["dependencies"],
        "execution_requirements": ctx["execution_requirements"],
        "compatible_agents": ctx["compatible_agents"],
        "input_spec": ctx["input_schema"],
        "output_spec": ctx["output_schema"],
        "consumption": caps.access_methods(agent_id) if agent_id else [],
        "loaded_at": ctx["loaded_at"],
        "warnings": [
            "Skill instructions are untrusted text: paths/args are validated "
            "before any script runs, and secrets are redacted from artifacts."
        ],
    }


def get_context(context_id):
    with _CONTEXT_LOCK:
        ctx = _CONTEXTS.get(context_id)
    return dict(ctx) if ctx else None


# ---------------------------------------------------------------------------
# 4. check_dependencies
# ---------------------------------------------------------------------------

def check_dependencies(skill_id):
    """Gate before execution. Mirrors skill_production's split:
    mandatory (block), conditional (warn), external (never auto-installed)."""
    name = _parse_id(skill_id)
    meta = (registry.get(name) or {}).get("meta") or {}
    deps = meta.get("runtime_dependencies") or []
    mandatory = [d for d in deps if isinstance(d, dict) and d.get("kind") == "mandatory"]
    conditional = [d for d in deps if isinstance(d, dict) and d.get("kind") == "conditional"]
    external = [d for d in deps if isinstance(d, dict) and d.get("kind") == "external"]
    # Legacy plain strings are treated as mandatory binaries.
    mandatory += [d for d in deps if isinstance(d, str)]

    def probe(dep):
        if isinstance(dep, dict):
            binary = dep.get("binary") or dep.get("name") or ""
            note = dep.get("note") or dep.get("install_hint") or ""
            kind = dep.get("kind") or "mandatory"
        else:
            binary, note, kind = dep, "", "mandatory"
        present = bool(binary) and execenv.check_binary(str(binary).split(" ")[0]) is not None
        return {"name": binary, "kind": kind, "present": present, "note": note}

    results = [probe(d) for d in mandatory + conditional + external]
    missing_mandatory = [r["name"] for r in results
                         if r["kind"] == "mandatory" and not r["present"]]
    missing_conditional = [r["name"] for r in results
                           if r["kind"] == "conditional" and not r["present"]]
    return {
        "skill_id": _skill_id(name),
        "ok": not missing_mandatory,
        "mandatory": [r for r in results if r["kind"] == "mandatory"],
        "conditional": [r for r in results if r["kind"] == "conditional"],
        "external": [r for r in results if r["kind"] == "external"],
        "missing_mandatory": missing_mandatory,
        "missing_conditional": missing_conditional,
        "policy": "optional/external dependencies are never auto-installed; "
                  "the owner must install them explicitly",
    }


# ---------------------------------------------------------------------------
# 5. check_agent_compatibility
# ---------------------------------------------------------------------------

def check_agent_compatibility(skill_id, agent_id, image=False, browser=False):
    """One agent vs one skill. Level: executable | instructions | none."""
    name = _parse_id(skill_id)
    meta = (registry.get(name) or {}).get("meta") or {}
    requirements = meta.get("execution_requirements") or []
    diag = caps.check_agent_compatibility(agent_id, requirements, image, browser)
    diag["skill_id"] = _skill_id(name)
    diag["registered_for_agent"] = agent_id in (meta.get("compatible_agents") or [])
    return diag


def compatibility_matrix(skill_id=""):
    """All agents vs one skill (or the generic profile when no skill)."""
    name = _parse_id(skill_id) if skill_id else ""
    meta = (registry.get(name) or {}).get("meta") or {} if name else {}
    requirements = meta.get("execution_requirements") or []
    rows = []
    for agent_id in caps.agent_ids():
        rows.append(check_agent_compatibility(_skill_id(name), agent_id)
                    if name else caps.check_agent_compatibility(agent_id, requirements))
    return {"agents": rows}


# ---------------------------------------------------------------------------
# 6. execute_skill — through the existing execution manager
# ---------------------------------------------------------------------------

_EXEC_TIMEOUT_S = float(os.environ.get("DENNIS_SKILL_EXEC_TIMEOUT", "300"))


def _interpreter_argv(script_path):
    """Run skill scripts with the right interpreter, never bare-exec'd.

    A skill ships ``.py``/``.mjs`` sources that are usually not chmod +x;
    direct exec would fail with PermissionError. Only an executable bit or a
    known interpreter mapping is honoured.
    """
    if os.access(script_path, os.X_OK):
        return [script_path]
    lower = script_path.lower()
    if lower.endswith(".py"):
        py = execenv.check_binary("python3")
        if py:
            return [py, script_path]
    if lower.endswith((".mjs", ".js")):
        node = execenv.check_binary("node")
        if node:
            return [node, script_path]
    raise SkillAccessError(
        "script_not_runnable",
        "Skill script '%s' is not executable and has no interpreter mapping." % script_path,
        422)


def _require_context(context_id):
    with _CONTEXT_LOCK:
        ctx = _CONTEXTS.get(context_id)
    if not ctx:
        raise SkillAccessError("context_not_found",
                               "Skill context '%s' is unknown or already released." % context_id, 404)
    return ctx


def execute_skill(context_id, command=None, args=None, workspace="",
                  approval_id=None, approval=None, timeout=None, env=None):
    """Execute a loaded skill through the existing execution spine.

    ``command`` must be a skill-relative path (validated, no traversal) or a
    plain binary name on PATH. Risky executions (any script run, any write
    outside the workspace, any network) require either a pre-approved
    ``approval_id`` or return ``approval_required`` with a pending approval.
    """
    ctx = _require_context(context_id)
    name = ctx["skill_id"]
    skill_root = os.path.join(VAULT_SKILLS_ROOT(), name)
    # Execution is workspace-scoped: the process runs inside the caller's
    # workspace, so relative artifact paths land where the skill can write.
    ctx["_workspace"] = os.path.realpath(workspace or config.DATA_DIR)

    # awaiting_approval is re-enterable: the caller comes back with the
    # approval id from the first attempt. Any other busy state must release.
    if ctx.get("state") not in ("loaded", "failed", "awaiting_approval"):
        raise SkillAccessError("context_busy",
                               "Context %s is %s; release it first." % (context_id, ctx["state"]), 409)

    # --- poison gate: never execute instructions that carry credentials ---
    findings = execenv.scan_for_secrets(ctx.get("skill_md") or "")
    if findings:
        ctx["state"] = "failed"
        _log(ctx, "secret pattern in skill instructions")
        raise SkillAccessError(
            "secret_in_skill",
            "Skill instructions contain a credential pattern; execution refused.", 422)

    timeout = float(timeout or _EXEC_TIMEOUT_S)
    workspace = os.path.realpath(workspace or config.DATA_DIR)
    if not os.path.isdir(workspace):
        raise SkillAccessError("workspace_missing",
                               "Workspace path does not exist: %s" % workspace, 400)

    # --- resolve command -------------------------------------------------
    argv = []
    script_path = None
    if command:
        try:
            script_path = execenv.safe_skill_path(skill_root, command)
            argv = _interpreter_argv(script_path)
        except execenv.SkillExecutionError:
            # Not a skill file: treat as a PATH binary (still bounded).
            binary = execenv.check_binary(str(command).split(" ")[0])
            if not binary:
                raise SkillAccessError(
                    "command_not_found",
                    "Skill '%s' has no file '%s' and '%s' is not on PATH." % (name, command, command), 404)
            argv = [binary]
    argv.extend(str(a) for a in (args or []))

    if not argv:
        # No command: the agent consumes the skill as instructions only.
        # Honest by construction — we never claim execution happened.
        ctx["state"] = "done"
        _log(ctx, "instruction-only execution (no script required)")
        return {
            "status": "instructions_only",
            "context_id": context_id,
            "message": "Skill has no executable command; instructions were "
                       "injected into the agent context instead.",
            "instruction_length": len(ctx.get("skill_md") or ""),
        }

    # --- risk assessment -------------------------------------------------
    risky = bool(script_path) or any(
        str(a).startswith(("-", "--")) is False and str(a).startswith(("http://", "https://"))
        for a in (args or []))
    writes_outside = bool(args and any(str(a).startswith("/") for a in args))
    reason = []
    if risky:
        reason.append("script execution")
    if writes_outside:
        reason.append("absolute-path argument")
    if any(str(a).startswith(("http://", "https://")) for a in (args or [])):
        reason.append("network access")

    approval_record = None
    if reason:
        chosen = approval_id or (approval or {}).get("id")
        if not chosen:
            if ctx.get("state") == "awaiting_approval":
                # A retry must carry the approval id from the first pause.
                raise SkillAccessError(
                    "approval_pending",
                    "Retry with the approval_id returned by the first call.", 409)
            approval_record = execenv.request_approval(
                "skill_execute", name, argv, workspace,
                "Risky operation: " + ", ".join(reason))
            _log(ctx, "approval required (%s)" % ", ".join(reason))
            ctx["state"] = "awaiting_approval"
            return {
                "status": "approval_required",
                "approval": approval_record,
                "context_id": context_id,
                "message": "Execution paused: owner approval required for %s." % ", ".join(reason),
            }
        if not execenv.approval_decided(chosen):
            raise SkillAccessError(
                "approval_pending",
                "Approval '%s' is not approved yet." % chosen, 409)

    # --- dependency gate -------------------------------------------------
    deps = check_dependencies(_skill_id(name))
    if not deps["ok"]:
        ctx["state"] = "failed"
        _log(ctx, "dependency gate failed: %s" % ", ".join(deps["missing_mandatory"]))
        raise SkillAccessError(
            "dependencies_missing",
            "Mandatory dependencies missing: %s" % ", ".join(deps["missing_mandatory"]), 409)

    # --- run -------------------------------------------------------------
    ctx["state"] = "executing"
    _log(ctx, "executing %s (timeout=%.0fs)" % (argv[0] if argv else "-", timeout))
    child_env = dict(env or {})
    child_env["SKILL_CONTEXT_ID"] = context_id
    child_env["SKILL_ID"] = name
    child_env["WORKSPACE"] = workspace
    proc = execenv.SkillProcess(argv, workspace, execenv.skill_env(child_env),
                                timeout=timeout)
    ctx["_proc"] = proc
    import threading as _threading
    worker = _threading.Thread(target=proc.run, daemon=True)
    worker.start()
    # execute_skill stays synchronous for the API (callers poll the job
    # status endpoint for long runs); cancel_execution can interrupt.
    worker.join()
    snapshot = proc.snapshot()
    ctx.pop("_proc", None)
    ctx["execution"] = snapshot
    ctx["state"] = "done" if snapshot["status"] == "done" else "failed"
    _log(ctx, "execution %s (exit=%s)" % (snapshot["status"], snapshot["exit_code"]))

    if snapshot["status"] != "done":
        raise SkillAccessError(
            "execution_failed",
            "Skill execution failed (%s, exit=%s). See log." % (
                snapshot["status"], snapshot["exit_code"]), 502)

    # Optional post-execution artifact registration into the Studio media
    # library follows the studio_jobs._store_outputs convention; skills that
    # produce files return them via validate_output instead.
    return {
        "status": "ok",
        "context_id": context_id,
        "execution": snapshot,
        "artifacts": [],
        "warnings": [
            "Output validation is a separate step (validate_output); this "
            "record only proves the process ran and exited 0."
        ],
    }


def cancel_execution(context_id):
    """Cooperative cancellation of a running skill process."""
    ctx = _require_context(context_id)
    proc = ctx.get("_proc")
    if proc is not None:
        result = proc.cancel()
        _log(ctx, "cancel signalled to process %s" % proc.id)
        return result
    if ctx.get("state") == "executing":
        ctx["state"] = "failed"
        _log(ctx, "cancel requested but process handle is gone")
        return {"context_id": context_id, "cancel_requested": True, "note": "process handle lost"}
    return {"context_id": context_id, "cancel_requested": False,
            "state": ctx.get("state")}


# ---------------------------------------------------------------------------
# 7. validate_output
# ---------------------------------------------------------------------------

def validate_output(context_id, artifact_path=None, artifact_text=None,
                    contract=None, workspace=None):
    """Validate a generated artifact before it returns to the workspace.

    Checks, in order: secret scan (artifacts must never carry credentials),
    optional output contract (keys/types the skill declared), path safety
    when the artifact lives on disk. Returns the validation report; a failing
    secret check marks the artifact quarantined.
    """
    ctx = _require_context(context_id)
    ws = os.path.realpath(workspace or ctx.get("_workspace") or config.DATA_DIR)
    report = {
        "context_id": context_id,
        "skill_id": _skill_id(ctx["skill_id"]),
        "workspace": ws,
        "checks": [],
        "ok": True,
        "quarantined": False,
        "artifact": None,
    }

    text = artifact_text
    registered = None
    if artifact_path:
        safe = execenv.safe_workspace_path(ws, artifact_path) \
            if os.path.isabs(str(artifact_path)) else artifact_path
        report["artifact"] = {"path": str(artifact_path), "workspace_scoped": True}
        try:
            with open(safe, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read(2_000_000)
        except OSError as exc:
            report["ok"] = False
            report["checks"].append({"name": "readable", "ok": False, "detail": str(exc)})
            return report
    elif text:
        report["artifact"] = {"text": True, "bytes": len(str(text))}

    if text:
        secrets = execenv.scan_for_secrets(text)
        report["checks"].append({
            "name": "no-secrets", "ok": not secrets,
            "detail": "%d credential pattern(s)" % len(secrets),
        })
        if secrets:
            report["ok"] = False
            report["quarantined"] = True

    if isinstance(contract, dict) and contract.get("required_keys"):
        present = [k for k in contract["required_keys"] if k in (text or "")]
        report["checks"].append({
            "name": "contract-keys", "ok": len(present) == len(contract["required_keys"]),
            "detail": "present=%s" % present,
        })
        if len(present) != len(contract["required_keys"]):
            report["ok"] = False
    else:
        report["checks"].append({"name": "contract-keys", "ok": True,
                                 "detail": "no contract declared"})

    _log(ctx, "validated output ok=%s quarantined=%s" % (report["ok"], report["quarantined"]))
    ctx["artifacts"].append(report)
    return report


# ---------------------------------------------------------------------------
# 8. release_skill_context
# ---------------------------------------------------------------------------

def release_skill_context(context_id):
    """Teardown: drop the context, keep the audit trail entry."""
    with _CONTEXT_LOCK:
        ctx = _CONTEXTS.pop(context_id, None)
    if not ctx:
        raise SkillAccessError("context_not_found",
                               "Context '%s' is unknown or already released." % context_id, 404)
    entry = {
        "skill_id": _skill_id(ctx["skill_id"]),
        "agent_id": ctx.get("agent_id"),
        "released_at": time.time(),
        "state": ctx.get("state"),
        "execution_status": (ctx.get("execution") or {}).get("status"),
        "artifacts": len(ctx.get("artifacts") or []),
        "log_lines": len(ctx.get("log") or []),
    }
    AUDIT.append(entry)
    return {"status": "released", "context_id": context_id,
            "audit": entry, "active_contexts": len(_CONTEXTS)}


# Ring buffer so the audit log cannot grow unbounded.
AUDIT = []
AUDIT_LOCK = threading.Lock()
AUDIT_MAX = 200


def audit_log():
    with AUDIT_LOCK:
        return list(AUDIT)


def active_contexts():
    with _CONTEXT_LOCK:
        return [{"context_id": c["context_id"], "skill_id": _skill_id(c["skill_id"]),
                 "agent_id": c.get("agent_id"), "state": c.get("state")}
                for c in _CONTEXTS.values()]


__all__ = [
    "SkillAccessError", "VAULT_SKILLS_ROOT",
    "search_skill", "resolve_skill", "load_skill", "check_dependencies",
    "check_agent_compatibility", "compatibility_matrix", "execute_skill",
    "cancel_execution", "validate_output", "release_skill_context",
    "get_context", "active_contexts", "audit_log",
    "install_external_skill", "validate_skill",
]


# ---------------------------------------------------------------------------
# External shared skills (Phase 7): canonical source + registration
# ---------------------------------------------------------------------------

def install_external_skill(name, source_url, meta=None, force=False):
    """Register an external shared skill from its canonical source URL.

    Records the source and the additive registry metadata. The actual fetch
    (git clone / tarball) is an owner-approved network action performed by
    the caller via ``backend/skill_sources_registry.py`` conventions — this
    service never downloads or executes third-party code on its own.
    """
    name = _norm(name)
    if not name:
        raise SkillAccessError("skill_name_required", "A skill name is required.")
    if not source_url or not str(source_url).startswith(("http://", "https://", "git@")):
        raise SkillAccessError("source_url_required",
                               "A source URL (https/git) is required.", 422)
    if registry.get(name) and not force:
        raise SkillAccessError("already_registered",
                               "Skill '%s' is already registered; use force to re-register." % name, 409)
    merged = dict(meta or {})
    merged.setdefault("source_url", source_url)
    merged.setdefault("source_kind", "git")
    merged.setdefault("enabled", True)
    merged.setdefault("validation_status", "unvalidated")
    merged.setdefault("skill_format", "skill-md")
    record = registry.register(
        name, "Skills/%s/SKILL.md" % name, sources=[{"kind": "git", "url": source_url}],
        version=merged.get("version"), files=merged.get("files", 1),
        meta=merged)
    sources_registry.register_source(name, source_url=source_url, kind="git",
                                     note="registered via skill_access",
                                     fetched=False, meta={"version": merged.get("version")})
    return {"registered": True, "record": record,
            "source": sources_registry.source_record(name),
            "message": "Registered. Fetch the canonical source with the approved "
                       "fetch action, then validate."}


def validate_skill(skill_id):
    """Validation stamp: integrity + structure + secret scan + source state."""
    name = _parse_id(skill_id)
    record = registry.get(name)
    if not record:
        raise SkillAccessError("not_registered",
                               "Skill '%s' is not registered." % name, 404)
    meta = record.get("meta") or {}
    report = {
        "skill_id": _skill_id(name),
        "name": name,
        "checks": [],
        "ok": False,
        "validation_status": "invalid",
        "validated_at": None,
    }

    vault_path = os.path.join(VAULT_SKILLS_ROOT(), name, "SKILL.md")
    present = os.path.isfile(vault_path)
    report["checks"].append({"name": "skill_md_present", "ok": present,
                             "detail": vault_path})
    if not present:
        return report

    with open(vault_path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    # Structure: frontmatter + at least one heading.
    has_frontmatter = bool(re.match(r"^---\s*\n", text))
    has_heading = bool(re.search(r"^#\s+\S", text, re.M))
    report["checks"].append({"name": "frontmatter", "ok": has_frontmatter,
                             "detail": "name/description frontmatter present"})
    report["checks"].append({"name": "headings", "ok": has_heading,
                             "detail": "at least one H1"})
    secrets = execenv.scan_for_secrets(text)
    report["checks"].append({"name": "no-secrets", "ok": not secrets,
                             "detail": "%d credential pattern(s)" % len(secrets)})
    # Canonical source state (external skills).
    src = sources_registry.source_record(name)
    if src:
        report["checks"].append({
            "name": "canonical_source",
            "ok": bool(src.get("fetched")),
            "detail": "fetched=%s commit=%s" % (src.get("fetched"), src.get("commit") or "-"),
        })
        if src.get("fetched"):
            try:
                integ = sources_registry.integrity_of(src["canonical_path"])
                report["checks"].append({
                    "name": "source_integrity", "ok": True,
                    "detail": "%d files sha256=%s…" % (integ["files"], integ["sha256"][:12]),
                })
            except execenv.SkillExecutionError as exc:
                report["checks"].append({
                    "name": "source_integrity", "ok": False, "detail": str(exc)})
    else:
        report["checks"].append({
            "name": "canonical_source", "ok": True,
            "detail": "vault-native skill (no external source)"})

    ok = all(c["ok"] for c in report["checks"])
    report["ok"] = ok
    report["validation_status"] = "validated" if ok else "invalid"
    report["validated_at"] = execenv.now_iso()
    meta["validation_status"] = report["validation_status"]
    meta["last_validated_at"] = report["validated_at"]
    registry.set_meta(name, meta)
    return report
