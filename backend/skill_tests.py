"""Validation and replication testing for generated skills.

Static checks are deterministic and always run.  Replication asks a fresh
model call to apply the *methodology* to a different topic and scores the
answer on named criteria.  The anti-copy guard compares the answer with the
source material so a skill cannot pass by repeating the reference video.
"""

import re

from . import skill_compiler

REQUIRED_SECTIONS = (
    "Objective", "When to use", "Inputs", "Outputs", "Workflow",
    "Validation", "Failure handling",
)

RECOMMENDED_SECTIONS = (
    "Required capabilities", "Production methodology", "Adaptation rules",
    "Dependencies",
)

REPLICATION_CRITERIA = (
    ("hook_formula", "Applies the extracted hook formula"),
    ("narrative_structure", "Reproduces the narrative structure"),
    ("visual_identity", "Maintains the visual identity"),
    ("editing_methodology", "Applies the editing methodology"),
    ("dependency_selection", "Selects appropriate dependencies"),
    ("production_plan", "Generates a production plan"),
    ("output_format", "Produces the expected output format"),
    ("quality_requirements", "Maintains quality requirements"),
    ("content_independence", "Does not reuse the reference content"),
)

_VIRAL_RE = re.compile(
    r"(?i)\b(went viral|guaranteed viral|this technique caused|"
    r"the reason it went viral|caused (?:its|the) success)\b")
_NGRAM = 8


def _section_present(content, name):
    pattern = r"(?im)^#{2,4}\s+.*%s" % re.escape(name)
    return re.search(pattern, content or "") is not None


def shingles(text, size=_NGRAM):
    words = re.findall(r"[a-z0-9']+", (text or "").lower())
    if len(words) < size:
        return set()
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def content_overlap(answer, source_text, size=_NGRAM):
    """Fraction of answer n-grams that also occur in the source text."""
    answer_grams = shingles(answer, size)
    if not answer_grams:
        return 0.0
    source_grams = shingles(source_text, size)
    if not source_grams:
        return 0.0
    return round(len(answer_grams & source_grams) / float(len(answer_grams)), 4)


def static_checks(draft, package_root=None):
    """Return ``{"checks": [...], "passed": bool, "errors": [...]}``."""
    content = (draft or {}).get("content") or ""
    checks = []

    def add(name, passed, detail="", required=True):
        checks.append({"name": name, "passed": bool(passed),
                       "detail": detail, "required": bool(required)})

    for section in REQUIRED_SECTIONS:
        add("Section: %s" % section, _section_present(content, section),
            "Required executable section.")
    for section in RECOMMENDED_SECTIONS:
        add("Section: %s" % section, _section_present(content, section),
            "Recommended executable section.", required=False)
    add("Frontmatter name/description",
        "name:" in content[:400] and "description:" in content[:900],
        "Skill must be discoverable by an agent.")
    add("No unverified viral/causal claim", not _VIRAL_RE.search(content),
        "Causal performance claims require metrics and evidence.")
    add("References STYLE.md", "STYLE.md" in content,
        "Style profile must stay independent.")
    add("References FORMAT.md", "FORMAT.md" in content,
        "Format profile must stay independent.")
    add("Has strategy evidence", bool((draft or {}).get("analysis")),
        "Reverse-engineering report present.")
    add("Has dependencies plan", bool((draft or {}).get("dependencies")),
        "Dependency manifest present.")
    add("Has production plan", bool((draft or {}).get("production")),
        "Production technology plan present.")

    errors = []
    if package_root:
        errors = skill_compiler.validate_package(package_root)
        add("Package validates", not errors, "; ".join(errors[:4]))
    errors = [check["detail"] for check in checks
              if check.get("required", True) and not check["passed"] and check["detail"]]
    required_passed = all(check["passed"] for check in checks
                          if check.get("required", True))
    return {"checks": checks,
            "passed": bool(required_passed),
            "errors": errors}


def replication_prompt(draft, new_topic):
    analysis = (draft or {}).get("analysis") or {}
    strategy = analysis.get("strategy") or {}
    hook = analysis.get("hook") or {}
    style = (draft or {}).get("style_profile") or {}
    fmt = (draft or {}).get("format_profile") or {}
    production = (draft or {}).get("production") or {}
    beats = (analysis.get("story") or {}).get("structure") or []
    stack = [item.get("name") for item in production.get("stack") or []
             if item.get("selected")]
    return (
        "You are testing whether a generated skill transfers METHODOLOGY. "
        "Use a completely different topic: %s. Do not reuse any scene, line, "
        "character or asset from the reference video. Return ONLY JSON.\n\n"
        "METHODOLOGY TO APPLY:\n"
        "Hook formula: %s\nNarrative beats: %s\nStyle: %s / %s\n"
        "Editing: %s\nProduction stack: %s\nOutput: %s\n\n"
        "Return:\n"
        '{"topic":"%s",\n'
        ' "hook":"...", "structure":["..."], "visual_identity":["..."],\n'
        ' "editing":["..."], "dependencies":["..."], "production_plan":["..."],\n'
        ' "output_spec":"...", "quality_checks":["..."],\n'
        ' "criteria":{"hook_formula":true,"narrative_structure":true,'
        '"visual_identity":true,"editing_methodology":true,'
        '"dependency_selection":true,"production_plan":true,'
        '"output_format":true,"quality_requirements":true},\n'
        ' "evidence":["..."]}\n' % (
            new_topic,
            hook.get("formula") or "not extracted",
            "; ".join("%s %s-%ss" % (b.get("beat"), b.get("start"), b.get("end"))
                      for b in beats) or "not extracted",
            style.get("style_name") or "unknown",
            style.get("visual_language") or "",
            "; ".join(analysis.get("production_method", {}).values())[:600] or "unknown",
            ", ".join(stack) or "ffmpeg, python",
            (fmt.get("format_name") or "unknown") + " " +
            "; ".join(config.get("name", "") for config in
                      fmt.get("output_configurations") or []),
            new_topic,
        )
    )


def evaluate_replication(data, source_text="", topic=""):
    """Score a replication answer. Missing criteria always fail."""
    data = data if isinstance(data, dict) else {}
    criteria = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}
    results = []
    answer_text = "\n".join(
        str(data.get(key) or "") for key in
        ("hook", "structure", "visual_identity", "editing", "dependencies",
         "production_plan", "output_spec", "quality_checks", "evidence")
    )
    overlap = content_overlap(answer_text, source_text) if source_text else 0.0
    for key, label in REPLICATION_CRITERIA:
        if key == "content_independence":
            passed = overlap <= 0.15 and bool(criteria.get(key, True))
            detail = "overlap with reference material: %.1f%%" % (overlap * 100)
        else:
            passed = bool(criteria.get(key))
            detail = "declared by the replication run" if passed else "criterion missing or false"
        results.append({"id": key, "name": label, "passed": passed, "detail": detail})
    if not topic:
        topic = str(data.get("topic") or "")
    return {
        "passed": all(item["passed"] for item in results),
        "topic": topic,
        "criteria": results,
        "content_overlap": overlap,
        "source_chars": len(source_text or ""),
    }
