"""Skill package compiler.

A compiled skill is a directory, not a single Markdown file.  Files are created
conditionally from the evidence actually collected, and every file is listed in
``manifests/package.json`` with its SHA-256 so publication can verify the exact
artifact the user reviewed.
"""

import hashlib
import json
import os
import re
import time

from . import skill_classify, skill_production

PACKAGE_SCHEMA = 1
PACKAGE_VERSION = "1.0.0"
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_BYTES = 16 * 1024 * 1024

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_REF_RE = re.compile(r"(?:^|[\s(])['\"]?((?:assets|scripts|tests|examples|manifests)/[A-Za-z0-9._/-]+)")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|access[_-]?token|client[_-]?secret)"
    r"\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}")


def _clean(value, limit=2000):
    return " ".join(str(value or "").split())[:limit]


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _yaml_scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    text = str(value if value is not None else "")
    if text == "":
        return '""'
    if re.search(r"[:#\[\]{},&*!|>'\"%@`\n]", text) or text.strip() != text or text[0] in "-? ":
        return json.dumps(text, ensure_ascii=False)
    return text


def _yaml(value, indent=0):
    pad = "  " * indent
    lines = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append("%s%s:" % (pad, key))
                rendered = _yaml(item, indent + 1)
                if not rendered:
                    lines[-1] += " {}" if isinstance(item, dict) else " []"
                else:
                    lines.extend(rendered)
            else:
                lines.append("%s%s: %s" % (pad, key, _yaml_scalar(item)))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                rendered = _yaml(item, indent + 1)
                prefix = "  " * (indent + 1)
                if rendered and rendered[0].startswith(prefix):
                    lines.append("%s- %s" % (pad, rendered[0][len(prefix):]))
                    lines.extend(rendered[1:])
                else:
                    lines.append("%s-" % pad)
                    lines.extend(rendered)
            elif isinstance(item, list):
                lines.append("%s-" % pad)
                lines.extend(_yaml(item, indent + 1))
            else:
                lines.append("%s- %s" % (pad, _yaml_scalar(item)))
    else:
        lines.append("%s%s" % (pad, _yaml_scalar(value)))
    return lines


def _frontmatter(text):
    match = _FRONTMATTER_RE.match(text or "")
    if not match:
        return {}, ""
    fields = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip().strip('"\'')
    return fields, (text or "")[match.end():]


def _has_heading(content, name):
    """Match a Markdown heading that contains ``name`` (e.g. 'Execution workflow')."""
    pattern = r"(?im)^#{1,4}\s+.*" + re.escape(name).replace(r"\ ", r"[\s-]+")
    return re.search(pattern, content or "") is not None


def render_strategy(analysis):
    analysis = analysis or {}
    strategy = analysis.get("strategy") or {}
    hook = analysis.get("hook") or {}
    retention = analysis.get("retention") or {}
    lines = ["# Reverse-Engineering Report", "",
             "Observed facts, transcript facts, metrics and hypotheses are "
             "labelled separately. Hypotheses are not presented as causes.", ""]
    lines += ["## Creative strategy", ""]
    for key, label in (("audience", "Audience"), ("objective", "Objective"),
                       ("problem_or_desire", "Problem or desire"),
                       ("positioning", "Positioning"),
                       ("value_proposition", "Value proposition"),
                       ("differentiation", "Differentiation"),
                       ("emotional_appeal", "Emotional appeal"),
                       ("intended_outcome", "Intended viewer outcome")):
        if strategy.get(key):
            lines.append("- **%s:** %s" % (label, strategy[key]))
    lines += ["", "## Hook engineering", ""]
    for key, label in (("opening_visual", "Opening visual"),
                       ("opening_narration", "Opening narration"),
                       ("first_three_seconds", "First three seconds"),
                       ("curiosity_gap", "Curiosity gap"),
                       ("pattern_interruption", "Pattern interruption"),
                       ("emotional_trigger", "Emotional trigger"),
                       ("formula", "Hook formula"),
                       ("continuation_mechanism", "Continuation mechanism")):
        if hook.get(key):
            lines.append("- **%s:** %s" % (label, hook[key]))
    beats = (analysis.get("story") or {}).get("structure") or []
    if beats:
        lines += ["", "## Storytelling structure", "",
                  "| Beat | Start | End | What happens | Source | Confidence |",
                  "|---|---|---|---|---|---|"]
        for beat in beats:
            lines.append("| %s | %ss | %ss | %s | %s | %s |" % (
                beat.get("beat"), beat.get("start"), beat.get("end"),
                (beat.get("what") or "").replace("|", "/"),
                beat.get("source"), beat.get("confidence")))
    if retention:
        lines += ["", "## Retention engineering", ""]
        for key, label in (("pacing", "Scene pacing"),
                           ("information_density", "Information density"),
                           ("visual_changes", "Visual changes"),
                           ("narration_pacing", "Narration pacing"),
                           ("music_changes", "Music changes"),
                           ("payoff_timing", "Payoff timing")):
            if retention.get(key):
                lines.append("- **%s:** %s" % (label, retention[key]))
        for key, label in (("curiosity_loops", "Curiosity loops"),
                           ("delayed_reveals", "Delayed reveals"),
                           ("pattern_interruptions", "Pattern interruptions")):
            if retention.get(key):
                lines.append("- **%s:** %s" % (label, "; ".join(retention[key])))
    if analysis.get("psychology"):
        lines += ["", "## Audience psychology (hypotheses)"]
        for item in analysis["psychology"]:
            lines.append("- **%s:** %s _(%s, %s)_" % (
                item.get("trigger"), item.get("how"),
                item.get("source"), item.get("confidence")))
    performance = analysis.get("performance") or {}
    lines += ["", "## Performance evidence", "",
              "Only supplied metrics are recorded. No causal claim is made.", ""]
    observed = performance.get("observed") or {}
    lines += ["- **Observed:** views=%s likes=%s comments=%s upload=%s" % (
        observed.get("views"), observed.get("likes"), observed.get("comments"),
        observed.get("upload_date") or "unknown")]
    if performance.get("hypotheses"):
        lines += ["", "### Hypotheses (not verified)"]
        for item in performance["hypotheses"]:
            lines.append("- %s _(basis: %s; %s)_" % (
                item.get("claim"), item.get("basis") or "n/a", item.get("confidence")))
    if performance.get("limitations"):
        lines += ["", "### Limitations"]
        lines += ["- " + item for item in performance["limitations"]]
    if analysis.get("limitations"):
        lines += ["", "## Limitations"]
        lines += ["- " + item for item in analysis["limitations"]]
    return "\n".join(lines).strip() + "\n"


def render_analysis(draft):
    evidence = draft.get("evidence") or []
    lines = ["# Evidence", "",
             "Timestamp-aligned observations used to build this skill.", ""]
    if not evidence:
        lines.append("No structured media evidence was collected (text/repository source).")
    for index, item in enumerate(evidence, 1):
        source = item.get("source") or {}
        technical = item.get("technical") or {}
        lines += ["## %d. %s" % (index, source.get("title") or source.get("id")),
                  "",
                  "- Source: %s (`%s`)" % (source.get("url"), source.get("kind")),
                  "- Duration: %ss · %sx%s · %s fps" % (
                      source.get("duration_seconds"), source.get("width"),
                      source.get("height"), source.get("fps")),
                  "- Technical: %s / %s / %s" % (
                      technical.get("container"), technical.get("video_codec"),
                      technical.get("audio_codec"))]
        timeline = item.get("timeline") or {}
        lines.append("- Scenes: %s · cuts: %s · cuts/min: %s" % (
            timeline.get("scenes"), timeline.get("cuts"),
            timeline.get("cuts_per_minute")))
        scenes = item.get("scenes") or []
        if scenes:
            lines += ["", "| # | Start | End | Duration | Frame |",
                      "|---|---|---|---|---|"]
            for scene in scenes[:60]:
                lines.append("| %s | %ss | %ss | %ss | %s |" % (
                    scene.get("index"), scene.get("start"), scene.get("end"),
                    scene.get("duration"), scene.get("frame_ref") or "-"))
        if item.get("limitations"):
            lines += ["", "Limitations: " + "; ".join(item["limitations"])]
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_config(draft):
    dependencies = draft.get("dependencies") or {}
    selected = [item.get("id") for item in
                ((draft.get("production") or {}).get("stack") or [])
                if item.get("selected")]
    config_data = {
        "schema": PACKAGE_SCHEMA,
        "version": PACKAGE_VERSION,
        "skill": {
            "name": draft.get("name"),
            "description": draft.get("description"),
            "objective": draft.get("objective") or "reference",
            "mode": draft.get("mode") or "video",
        },
        "profiles": {
            "style": (draft.get("style_profile") or {}).get("style_id") or "",
            "format": (draft.get("format_profile") or {}).get("format_id") or "",
            "strategy": "STRATEGY.md" if draft.get("analysis") else "",
        },
        "output": {
            "configurations": (draft.get("format_profile") or {}).get(
                "output_configurations") or [],
        },
        "production": {
            "stack": selected,
            "entrypoint": "scripts/render.md",
        },
        "dependencies": {
            "manifest": "manifests/dependencies.json" if dependencies else "",
            "auto_install": False,
        },
        "validation": {
            "tests": "tests/validation.md",
            "replication": "tests/replication.md",
        },
        "provenance": {
            "sources": draft.get("sources") or [],
            "generated_at": draft.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    }
    return "\n".join(_yaml(config_data)).strip() + "\n"


def render_examples(draft):
    analysis = draft.get("analysis") or {}
    strategy = analysis.get("strategy") or {}
    hook = analysis.get("hook") or {}
    lines = ["# Example Brief", "",
             "Use this as the input shape for a new, unrelated topic. It must "
             "not reuse the reference video's content.", "",
             "- Topic: <new topic>",
             "- Audience: %s" % (strategy.get("audience") or "<audience>"),
             "- Objective: %s" % (strategy.get("objective") or "<objective>"),
             "- Emotional appeal: %s" % (strategy.get("emotional_appeal") or "<appeal>"),
             "- Hook pattern: %s" % (hook.get("formula") or "<hook formula>"),
             "", "## Required output brief", "",
             "1. Restate the audience problem in one sentence.",
             "2. Apply the hook formula to the new topic.",
             "3. Map the narrative beats to new events.",
             "4. Keep the style consistency rules from STYLE.md.",
             "5. Produce the primary output configuration from FORMAT.md.",
             "6. List dependencies and any external provider cost."]
    return "\n".join(lines).strip() + "\n"


def render_validation(draft):
    checks = (draft.get("validation") or {}).get("checks") or []
    lines = ["# Validation", "",
             "Static checks run at compile time. Replication requires a new topic "
             "and is scored separately.", ""]
    if checks:
        lines += ["| Check | Passed | Detail |", "|---|---|---|"]
        for check in checks:
            lines.append("| %s | %s | %s |" % (
                check.get("name"), "yes" if check.get("passed") else "no",
                (check.get("detail") or "").replace("|", "/")))
    else:
        lines.append("No validation results were recorded.")
    return "\n".join(lines).strip() + "\n"


def render_replication(draft):
    result = (draft.get("validation") or {}).get("replication") or {}
    lines = ["# Replication Test", "",
             "The replication test uses a different topic and measures "
             "methodology transfer, not content copying.", ""]
    if result:
        lines += ["- Status: %s" % ("passed" if result.get("passed") else "not passed"),
                  "- Topic used: %s" % (result.get("topic") or "not run")]
        for item in result.get("criteria") or []:
            lines.append("- **%s:** %s — %s" % (
                item.get("name"), "pass" if item.get("passed") else "fail",
                item.get("detail") or ""))
    else:
        lines.append("Not run. Enable 'Run replication test' before publication "
                     "if methodology transfer must be proven.")
    return "\n".join(lines).strip() + "\n"


def render_assets_readme():
    return (
        "# Assets\n\n"
        "Binary assets are not embedded in generated packages. Reference frames "
        "stay in `agent-skill-builder/.cache/media/` and are cited by timestamp. "
        "If a new asset is required, generate or supply it explicitly and record "
        "its provenance here before rendering.\n")


def package_files(draft):
    """Return ``{relative_path: text}`` for the package."""
    files = {
        "SKILL.md": draft.get("content") or "",
        "CONFIG.yaml": render_config(draft),
        "ANALYSIS.md": render_analysis(draft),
        "examples/example-brief.md": render_examples(draft),
        "tests/validation.md": render_validation(draft),
        "tests/replication.md": render_replication(draft),
        "assets/README.md": render_assets_readme(),
    }
    if draft.get("analysis"):
        files["STRATEGY.md"] = render_strategy(draft["analysis"])
    if draft.get("style_profile"):
        files["STYLE.md"] = skill_classify.style_text(draft["style_profile"])
    if draft.get("format_profile"):
        files["FORMAT.md"] = skill_classify.format_text(draft["format_profile"])
    if draft.get("production"):
        files["PRODUCTION.md"] = skill_production.production_text(draft["production"])
    if draft.get("dependencies"):
        files["DEPENDENCIES.md"] = skill_production.dependencies_text(draft["dependencies"])
        files["manifests/dependencies.json"] = json.dumps(
            draft["dependencies"], indent=2, ensure_ascii=False) + "\n"
    files["scripts/render.md"] = (
        "# Render entrypoint\n\n"
        "This file describes the execution order. It is not a shell script and "
        "must not be executed blindly. Review `DEPENDENCIES.md` and "
        "`PRODUCTION.md`, confirm approvals, then run the chosen renderer.\n\n"
        "1. Read `CONFIG.yaml` and `FORMAT.md`.\n"
        "2. Prepare assets and audio outside the package.\n"
        "3. Render the primary configuration.\n"
        "4. Run the quality-control checks in `PRODUCTION.md`.\n"
        "5. Record the output path and version in `manifests/package.json`.\n")
    return files


def compile_package(draft, package_dir, write=True):
    """Write the package and return ``(manifest, errors)``."""
    files = package_files(draft)
    errors = []
    total = 0
    written = []
    if write:
        os.makedirs(package_dir, exist_ok=True)
    for rel, text in files.items():
        if not isinstance(text, str):
            errors.append("%s is not text" % rel)
            continue
        payload = text.encode("utf-8")
        total += len(payload)
        if len(payload) > MAX_FILE_BYTES:
            errors.append("%s exceeds the per-file size limit" % rel)
            continue
        if write:
            path = os.path.join(package_dir, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        written.append({
            "path": rel, "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    if total > MAX_PACKAGE_BYTES:
        errors.append("package exceeds the total size limit")
    manifest = {
        "schema": PACKAGE_SCHEMA,
        "version": PACKAGE_VERSION,
        "name": draft.get("name") or "",
        "description": draft.get("description") or "",
        "entrypoint": "SKILL.md",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": written,
        "profiles": {
            "style": (draft.get("style_profile") or {}).get("style_id") or "",
            "format": (draft.get("format_profile") or {}).get("format_id") or "",
            "strategy": bool(draft.get("analysis")),
            "production": bool(draft.get("production")),
            "dependencies": bool(draft.get("dependencies")),
        },
        "provenance": draft.get("sources") or [],
    }
    if write:
        path = os.path.join(package_dir, "manifests", "package.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    # The manifest cannot contain its own hash, so it is listed by name only.
    manifest["manifest_file"] = "manifests/package.json"
    manifest["manifest_bytes"] = os.path.getsize(path) if write else 0
    return manifest, errors


def iter_package_files(root):
    """Yield ``(relative_path, absolute_path)`` for regular files, confined."""
    root = os.path.abspath(root)
    for current, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in sorted(names):
            if name.startswith("."):
                continue
            path = os.path.join(current, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel.startswith(".."):
                continue
            yield rel, path


def validate_package(root, manifest=None):
    """Validate structure, references, secrets and the recorded file hashes."""
    errors = []
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return ["package directory is missing"]
    skill_path = os.path.join(root, "SKILL.md")
    if not os.path.isfile(skill_path):
        errors.append("SKILL.md is missing")
        content = ""
    else:
        with open(skill_path, encoding="utf-8") as handle:
            content = handle.read()
        fields, body = _frontmatter(content)
        if not fields.get("name"):
            errors.append("SKILL.md frontmatter has no name")
        if not fields.get("description"):
            errors.append("SKILL.md frontmatter has no description")
        if len(body.strip()) < 200:
            errors.append("SKILL.md body is too short to be executable")
        for required in ("Objective", "When to use", "Inputs",
                         "Outputs", "Workflow", "Validation",
                         "Failure handling"):
            if not _has_heading(content, required):
                errors.append("SKILL.md is missing section: %s" % required)
    config_path = os.path.join(root, "CONFIG.yaml")
    if not os.path.isfile(config_path):
        errors.append("CONFIG.yaml is missing")
    manifest_path = os.path.join(root, "manifests", "package.json")
    recorded = manifest
    if recorded is None and os.path.isfile(manifest_path):
        try:
            with open(manifest_path, encoding="utf-8") as handle:
                recorded = json.load(handle)
        except (OSError, ValueError):
            errors.append("manifests/package.json is not valid JSON")
    if isinstance(recorded, dict):
        for item in recorded.get("files") or []:
            rel = item.get("path") or ""
            path = os.path.join(root, rel)
            if rel.startswith("..") or os.path.isabs(rel):
                errors.append("package manifest contains an unsafe path: %s" % rel)
                continue
            if not os.path.isfile(path):
                errors.append("manifest references a missing file: %s" % rel)
                continue
            if item.get("sha256") and _sha256(path) != item["sha256"]:
                errors.append("hash mismatch: %s" % rel)
    # Every relative reference in Markdown must exist; no file may leak a
    # credential (SKILL.md included).
    for rel, path in iter_package_files(root):
        if not rel.endswith(".md"):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        if rel != "SKILL.md":
            for ref in _REF_RE.findall(text):
                ref = ref.rstrip(").,;'\"")
                if ref in ("scripts/render.md",):
                    continue
                if not os.path.exists(os.path.join(root, ref)):
                    errors.append("%s references a missing file: %s" % (rel, ref))
        if _SECRET_RE.search(text):
            errors.append("%s appears to contain a credential" % rel)
    # Style and format must stay separate profiles.
    if content:
        has_style = "## Style" in content or "STYLE.md" in content
        has_format = "## Format" in content or "FORMAT.md" in content
        if not (has_style and has_format):
            errors.append("SKILL.md must reference both STYLE.md and FORMAT.md")
    return errors


def preview(root, limit=3000):
    """Text preview for the review UI; never returns binary data."""
    files = []
    for rel, path in iter_package_files(root):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        item = {"path": rel, "bytes": size}
        if rel.endswith((".md", ".json", ".yaml", ".yml", ".txt")):
            try:
                with open(path, encoding="utf-8") as handle:
                    item["preview"] = handle.read(limit)
            except OSError:
                item["preview"] = ""
        files.append(item)
    return {"root": root, "files": files,
            "total_bytes": sum(item["bytes"] for item in files)}
