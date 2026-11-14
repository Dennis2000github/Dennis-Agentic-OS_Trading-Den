"""Reverse-engineering intelligence: prompts, parsing and normalisation.

This module does not call any provider.  It builds evidence-grounded prompts,
parses strict JSON answers, and normalises them so every claim carries a
source label (``observed`` / ``transcript`` / ``metric`` / ``hypothesis``) and
a confidence value.

The creative strategy, hook, story, retention, psychology, production method
and performance layers live here.  Style and format classification are kept
in :mod:`backend.skill_classify` on purpose: they must never be merged.
"""

import json
import re

SOURCES = ("observed", "transcript", "metric", "hypothesis")
CONFIDENCE = ("low", "medium", "high")

_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n```\s*$", re.S)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


def _clean(value, limit=600):
    text = " ".join(str(value or "").split())
    return text[:limit]


def _list(value, limit=12):
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [_clean(item, 300) for item in value if _clean(item)][:limit]


def _source(value, default="hypothesis"):
    text = str(value or "").strip().lower()
    return text if text in SOURCES else default


def _confidence(value, default="low"):
    text = str(value or "").strip().lower()
    if text in CONFIDENCE:
        return text
    try:
        number = float(text)
        return "high" if number >= 0.75 else "medium" if number >= 0.45 else "low"
    except (TypeError, ValueError):
        return default


def _claims(value, limit=10):
    """Normalise ``[{claim, basis, source, confidence}]`` lists."""
    if not isinstance(value, list):
        return []
    claims = []
    for item in value:
        if isinstance(item, str):
            item = {"claim": item}
        if not isinstance(item, dict):
            continue
        claim = _clean(item.get("claim") or item.get("text"), 400)
        if not claim:
            continue
        claims.append({
            "claim": claim,
            "basis": _clean(item.get("basis") or item.get("evidence"), 400),
            "source": _source(item.get("source")),
            "confidence": _confidence(item.get("confidence")),
        })
        if len(claims) >= limit:
            break
    return claims


def parse_json(text):
    """Parse a model answer that should contain one JSON object."""
    raw = (text or "").strip()
    if not raw:
        return None
    fence = _FENCE_RE.match(raw)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    match = _JSON_OBJECT_RE.search(raw)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------
REVERSE_RULES = (
    "You are a video reverse-engineering analyst. Work only from the supplied "
    "EVIDENCE. Do not invent shots, dialogue, metrics, tools or timestamps. "
    "Label every claim with source: observed (seen in frames/scene data), "
    "transcript (said or captioned), metric (a real number supplied in the "
    "evidence/metadata), or hypothesis (an interpretation). Confidence is "
    "low | medium | high. If something is unknown, say so in limitations; do "
    "not fill gaps. Never claim a video is viral or that one technique caused "
    "its performance. Return ONLY valid JSON matching the requested shape.\n"
)


def reverse_prompt(evidence_text, title="", platform="", objective="reference"):
    return (
        REVERSE_RULES +
        "\nOBJECTIVE: %s\nTITLE: %s\nPLATFORM: %s\n\nEVIDENCE:\n%s\n\n"
        "Return one JSON object with these keys:\n"
        "{\n"
        '  "strategy": {"audience": "...", "objective": "...", '
        '"problem_or_desire": "...", "positioning": "...", '
        '"value_proposition": "...", "differentiation": "...", '
        '"emotional_appeal": "...", "intended_outcome": "...", '
        '"claims": [{"claim","basis","source","confidence"}]},\n'
        '  "hook": {"opening_visual": "...", "opening_narration": "...", '
        '"first_three_seconds": "...", "curiosity_gap": "...", '
        '"pattern_interruption": "...", "emotional_trigger": "...", '
        '"formula": "...", "continuation_mechanism": "...", '
        '"claims": [...]},\n'
        '  "story": {"structure": [{"beat":"introduction|setup|conflict|'
        'escalation|reveal|turning_point|climax|resolution|payoff|cta", '
        '"start": 0.0, "end": 0.0, "what": "...", "source": "...", '
        '"confidence": "..."}]},\n'
        '  "retention": {"pacing": "...", "information_density": "...", '
        '"curiosity_loops": ["..."], "delayed_reveals": ["..."], '
        '"pattern_interruptions": ["..."], "visual_changes": "...", '
        '"narration_pacing": "...", "music_changes": "...", '
        '"payoff_timing": "...", "claims": [...]},\n'
        '  "psychology": [{"trigger":"curiosity|surprise|fear|aspiration|'
        'social_proof|relatability|urgency|anticipation|emotional_contrast|'
        'transformation", "how": "...", "source": "...", "confidence": "..."}],\n'
        '  "production_method": {"shot_selection": "...", "camera_movement": "...", '
        '"scene_construction": "...", "asset_generation": "...", '
        '"editing": "...", "animation": "...", "sound_design": "...", '
        '"music_sync": "...", "caption_timing": "...", "color_grading": "...", '
        '"rendering": "...", "post": "..."},\n'
        '  "performance": {"observed": {"views": null, "likes": null, '
        '"comments": null, "upload_date": null, "channel": ""}, '
        '"verified": {}, "comparables": [], "hypotheses": [...], '
        '"limitations": ["..."]},\n'
        '  "limitations": ["..."]\n'
        "}\n"
        "Use timestamps in seconds from the evidence. Use the story beats only "
        "when the evidence supports them; otherwise return fewer beats. Keep "
        "strings short and concrete.\n" % (
            objective, title or "unknown", platform or "unknown", evidence_text)
    )


def repair_prompt(raw):
    return (
        "The following answer was meant to be one JSON object but failed to "
        "parse. Return the same content as valid JSON only, with no prose and "
        "no code fences. Preserve every claim and limitation.\n\n" + (raw or "")[:12000]
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def normalize_reverse(data, evidence=None):
    """Force a stable, evidence-labelled reverse-engineering object."""
    data = data if isinstance(data, dict) else {}
    strategy = data.get("strategy") if isinstance(data.get("strategy"), dict) else {}
    hook = data.get("hook") if isinstance(data.get("hook"), dict) else {}
    story = data.get("story") if isinstance(data.get("story"), dict) else {}
    retention = data.get("retention") if isinstance(data.get("retention"), dict) else {}
    production = (data.get("production_method")
                  if isinstance(data.get("production_method"), dict) else {})
    performance = (data.get("performance")
                   if isinstance(data.get("performance"), dict) else {})

    beats = []
    for item in story.get("structure") or []:
        if not isinstance(item, dict):
            continue
        beats.append({
            "beat": _clean(item.get("beat"), 40),
            "start": _number(item.get("start")),
            "end": _number(item.get("end")),
            "what": _clean(item.get("what"), 400),
            "source": _source(item.get("source")),
            "confidence": _confidence(item.get("confidence")),
        })

    psychology = []
    for item in data.get("psychology") or []:
        if isinstance(item, str):
            item = {"trigger": item}
        if not isinstance(item, dict):
            continue
        psychology.append({
            "trigger": _clean(item.get("trigger"), 40),
            "how": _clean(item.get("how"), 400),
            "source": _source(item.get("source")),
            "confidence": _confidence(item.get("confidence")),
        })

    observed = performance.get("observed") if isinstance(performance.get("observed"), dict) else {}
    observed = {
        "views": _int_or_none(observed.get("views")),
        "likes": _int_or_none(observed.get("likes")),
        "comments": _int_or_none(observed.get("comments")),
        "upload_date": _clean(observed.get("upload_date"), 40) or None,
        "channel": _clean(observed.get("channel"), 120),
    }
    # Only the actual source metadata may populate "observed" metrics.
    meta = (evidence or {}).get("source") or {}
    if observed["views"] is None and isinstance(meta.get("view_count"), int):
        observed["views"] = meta["view_count"]

    normalized = {
        "strategy": {key: _clean(strategy.get(key), 500) for key in (
            "audience", "objective", "problem_or_desire", "positioning",
            "value_proposition", "differentiation", "emotional_appeal",
            "intended_outcome")},
        "hook": {key: _clean(hook.get(key), 500) for key in (
            "opening_visual", "opening_narration", "first_three_seconds",
            "curiosity_gap", "pattern_interruption", "emotional_trigger",
            "formula", "continuation_mechanism")},
        "story": {"structure": beats},
        "retention": {
            "pacing": _clean(retention.get("pacing"), 500),
            "information_density": _clean(retention.get("information_density"), 500),
            "curiosity_loops": _list(retention.get("curiosity_loops")),
            "delayed_reveals": _list(retention.get("delayed_reveals")),
            "pattern_interruptions": _list(retention.get("pattern_interruptions")),
            "visual_changes": _clean(retention.get("visual_changes"), 500),
            "narration_pacing": _clean(retention.get("narration_pacing"), 500),
            "music_changes": _clean(retention.get("music_changes"), 500),
            "payoff_timing": _clean(retention.get("payoff_timing"), 500),
        },
        "psychology": psychology[:10],
        "production_method": {key: _clean(production.get(key), 400) for key in (
            "shot_selection", "camera_movement", "scene_construction",
            "asset_generation", "editing", "animation", "sound_design",
            "music_sync", "caption_timing", "color_grading", "rendering", "post")},
        "performance": {
            "observed": observed,
            "verified": performance.get("verified") if isinstance(
                performance.get("verified"), dict) else {},
            "comparables": _list(performance.get("comparables")),
            "hypotheses": _claims(performance.get("hypotheses")),
            "limitations": _list(performance.get("limitations")),
        },
        "limitations": _list(data.get("limitations")),
    }
    normalized["strategy"]["claims"] = _claims(strategy.get("claims"))
    normalized["hook"]["claims"] = _claims(hook.get("claims"))
    normalized["retention"]["claims"] = _claims(retention.get("claims"))
    return normalized


def _number(value):
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _int_or_none(value):
    try:
        number = int(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def timeline_consistency(reverse, evidence):
    """Deterministic checks that help the UI show what is grounded.

    Returns a list of human-readable notes; it never mutates analysis.
    """
    notes = []
    scenes = (evidence or {}).get("scenes") or []
    duration = float((evidence or {}).get("source", {}).get("duration_seconds") or 0)
    beats = ((reverse or {}).get("story") or {}).get("structure") or []
    for beat in beats:
        if beat.get("end", 0) > duration + 1.0:
            notes.append("story beat '%s' ends after the video (%.1fs > %.1fs)" % (
                beat.get("beat"), beat.get("end", 0), duration))
    if beats and scenes:
        covered = sum(max(0.0, b.get("end", 0) - b.get("start", 0)) for b in beats)
        if duration and covered > duration * 1.25:
            notes.append("story beats overlap more than the video duration")
    return notes
