#!/usr/bin/env python3
"""Author a complete img2threejs ObjectSculptSpec for a cosmic avatar emblem.

Shared skeleton for the six COSMOS emblems; per-emblem identity data lives in
`<emblem>/emblem-def.json` (written per emblem next to the workspace).
Usage: author_spec.py <workspace-dir>
"""
import copy
import json
import os
import sys

BUILD_PASSES = [
    {"id": "blockout", "name": "Blockout", "goal": "Frame the emblem's bounding volumes and layer order."},
    {"id": "structural-pass", "name": "Structure", "goal": "Place every particle system and sprite layer in the scene graph."},
    {"id": "form-refinement", "name": "Form", "goal": "Tune spiral winding, silhouette envelope and layer density against the reference."},
    {"id": "material-pass", "name": "Material", "goal": "Wire emissive gradients, additive blending and hue ramps."},
    {"id": "surface-pass", "name": "Surface", "goal": "Micro features: hotspots, diffraction crosses, dust filaments, twinkle."},
    {"id": "lighting-pass", "name": "Lighting", "goal": "Self-emissive scene: verify bloom balance without lights."},
    {"id": "interaction-pass", "name": "Interaction", "goal": "Idle animation: rigid rotation speed, pulse phase, reduced-motion fallback."},
    {"id": "optimization-pass", "name": "Optimization", "goal": "Triangle/draw-call budget and offscreen-render path for avatar slots."},
]

VIEWPOINTS = [
    {"id": "front", "label": "Front — the reference framing", "camera": "azimuth 0°, elevation 0°, distance 3.2"},
    {"id": "tilted", "label": "Tilted 30° — reveals disc thickness and layer depth", "camera": "azimuth 30°, elevation 28°, distance 3.2"},
    {"id": "edge", "label": "Near-edge — disc plane thickness and dust layering", "camera": "azimuth 90°, elevation 62°, distance 3.2"},
]


def rgba(hex_color, alpha):
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return "rgba(%d, %d, %d, %s)" % (r, g, b, alpha)


def bands(macro_freq, meso_freq, micro_freq):
    return [
        {"id": "macro", "frequency": macro_freq, "amplitude": 1.0,
         "intent": "whole-emblem luminous envelope"},
        {"id": "meso", "frequency": meso_freq, "amplitude": 0.6,
         "intent": "per-layer structure (arms, knots, ring segments)"},
        {"id": "micro", "frequency": micro_freq, "amplitude": 0.3,
         "intent": "per-sprite twinkle and edge softness"},
    ]


def component(cid, name, level, role, primitive, topology, rationale, parent,
              dims, importance, confidence, recipe, local_features=None,
              geometry=None, attachment=None, material_refs=None):
    comp = {
        "id": cid, "name": name, "level": level, "role": role,
        "importance": importance, "confidence": confidence,
        "primitive": primitive,
        "topologyClass": topology,
        "topologyRationale": rationale,
        "geometryDescriptor": geometry or {
            "topologyIntent": "additive particle/sprite volume; no solid surface",
            "edgeTreatment": {"type": "none", "bevelRadius": 0.0, "segments": 1},
            "deformationStack": [],
            "uvStrategy": "generated procedural coordinates",
            "normalStrategy": "unlit additive sprites (normals unused)",
        },
        "parent": parent,
        "dimensions": dict(dims, units="relative"),
        "colorMaterialRecipe": recipe,
    }
    if local_features:
        comp["localFeatures"] = local_features
    if material_refs:
        comp["materialRefs"] = material_refs
    if attachment:
        comp["attachment"] = attachment
    return comp


def material(id, name, base, gradient_stops, note, extra=None):
    mat = {
        "id": id,
        "name": name,
        "type": "standard",
        "shaderModel": "AdditiveBlendedPoints/Sprites (emissive; MeshBasicMaterial-class, unlit)",
        "baseColor": base,
        "color": base,
        "albedo": {
            "dominant": base,
            "secondary": [stop[1] for stop in gradient_stops],
            "samplingNotes": note,
        },
        "colorVariation": {
            "palette": [base] + [stop[1] for stop in gradient_stops],
            "pattern": "radial-gradient",
            "amplitude": 0.35,
            "heightCorrelation": 0.0,
        },
        "textureResolution": 1024,
        "textureProjection": {
            "mode": "generated canvas sprite texture (radial falloff), screen-facing",
            "repeat": [1.0, 1.0],
        },
        # Honest PBR framing for an unlit scene: maximum roughness (no specular
        # response at all); the "surface response" role is played by per-sprite
        # alpha falloff, documented in emission.
        "roughness": {"base": 1.0,
                      "map": "none — unlit emissive scene; no specular response by design"},
        "metalness": {"base": 0.0,
                      "map": "none — no dielectric/metal response in an emission-only scene"},
        "ambientOcclusion": {
            "response": "n/a — additive blending; occlusion is expressed by layer order and dark dust sprites (dust-filament material), not AO maps",
        },
        "surfaceFrequencyBands": bands(1.0, 6.0, 24.0),
        "emission": {
            "mode": "additive",
            "gradientStops": [
                {"color": rgba(c, a), "position": p} for (p, c, a) in gradient_stops
            ],
        },
        "localOverrides": [],
    }
    if extra:
        mat.update(extra)
    return mat


def action_profile(role, inherit=False):
    return {
        "animationRole": role,
        "pivot": ({"mode": "inherit-parent", "localPosition": [0, 0, 0], "axis": [0, 1, 0],
                   "confidence": 0.9} if inherit
                  else {"mode": "root-origin", "localPosition": [0, 0, 0], "axis": [0, 1, 0],
                        "confidence": 0.9}),
        "transformChannels": {"rotationY": True},
        "sockets": [],
    }


def build(defn):
    assessment = json.load(open(os.path.join(defn["workspace"], "assessment.json")))
    mats = [material(**m) for m in defn["materials"]]
    for extra in defn.get("materialExtras", []):
        for m in mats:
            if m["id"] == extra["id"]:
                m.update(extra["patch"])
    comps = []
    for c in defn["components"]:
        recipe = c["recipe"]
        comps.append(component(
            c["id"], c["name"], c["level"], c["role"], c["primitive"], c["topology"],
            c["rationale"], c.get("parent"), c["dims"], c["importance"], c["confidence"],
            {
                "dominantAlbedo": rgba(recipe["dominant"], recipe.get("alpha", 1.0)),
                "secondaryAlbedo": rgba(recipe["secondary"], recipe.get("alpha", 1.0)),
                "materialClass": recipe.get("materialClass", "glass"),
                "materialClassConfidence": recipe.get("materialClassConfidence", 0.7),
                "colorGradient": {
                    "type": recipe.get("gradientType", "radial"),
                    "stops": [
                        {"color": rgba(stop[1], stop[2]), "position": stop[0]}
                        for stop in recipe["stops"]
                    ],
                },
            },
            local_features=c.get("localFeatures"),
            geometry=c.get("geometry"),
            attachment=c.get("attachment"),
            material_refs=c.get("materialRefs"),
        ))
        comps[-1]["actionProfile"] = action_profile(
            "pivot-holder" if c["level"] == "macro" and not c.get("parent") else "rigid-follower",
            inherit=bool(c.get("parent")),
        )

    targets = []
    for t in defn["featureReviewTargets"]:
        targets.append({
            "id": t["id"],
            "name": t["name"],
            "tier": t.get("tier", "critical"),
            "passIds": t["passIds"],
            "minimumScore": t.get("minimumScore", 0.8),
            "mustPass": t.get("mustPass", True),
            "componentRefs": t["componentRefs"],
            "evidenceRefs": t.get("evidenceRefs", ["full-object"]),
        })

    return {
        "targetName": defn["targetName"],
        "targetId": defn["targetId"],
        "schemaVersion": "1.5",
        "terminologyProfile": {
            "geometryTerms": ["point sprite volume", "additive blended plane-card", "instanced cluster", "logarithmic spiral placement", "radial gradient sprite"],
            "materialTerms": ["emissive", "additive blending", "radial falloff sprite", "hue ramp stops", "absorptive dark filament"],
            "lightingTerms": ["self-emissive scene", "bloom balance", "no scene lights", "core pulse"],
        },
        "sourceImage": "../references/%s-admission.png" % defn["targetId"],
        "referenceCamera": {
            "projection": "perspective",
            "fovDegrees": 40,
            "azimuthDegrees": 0,
            "elevationDegrees": 22,
            "distance": 3.2,
            "framing": "emblem fills ~0.85 of frame width, centered",
            "confidence": 0.9,
            "basis": "single stylized space-art view; framing chosen to match the emblem crop",
        },
        "suitability": "pass",
        "scores": defn["scores"],
        "preSpecAssessment": assessment["preSpecAssessment"],
        "qualityContract": assessment["qualityContract"],
        "qualityTargets": {
            "reviewViewpoints": [v["id"] + " — " + v["label"] for v in VIEWPOINTS],
            "reviewSizePx": 512,
            "avatarSizePx": 96,
        },
        "selfCorrectLoop": {
            "maxPerPass": 3,
            "maxTotal": 6,
            "visualAcceptance": {
                "reviewer": "agent vision (structured comparison sheet + per-feature scoring)",
                "threshold": 0.8,
                "featureThreshold": 0.8,
                "featureReviewPolicy": {
                    "enabled": True,
                    "adaptiveEscalation": True,
                    "rule": "every critical featureReviewTarget must reach its minimumScore; a failed critical feature blocks continue even when the global score passes",
                },
                "layers": ["silhouette", "structure", "materials", "micro-features"],
            },
            "screenshotPolicy": {
                "perPass": "capture at review viewpoint 512 px before every review decision",
                "turntable": "4 azimuths (0/90/180/270) at final pass",
                "storage": "cosmos-avatars/<emblem>/renders/",
            },
        },
        "featureReviewTargets": targets,
        "sculptPipeline": {
            "passOrder": [p["id"] for p in BUILD_PASSES],
            "passGateMode": "locked-sequential",
            "note": "Particle-first: spiral structure and silhouette land in structural/form passes; materials are emission gradients, not PBR surfaces.",
        },
        "lookDevTargets": {
            "materialPass": {
                "minimumTextureResolution": 256,
                "note": "Sprite textures are generated canvas gradients; 256 px is sufficient at avatar scale.",
            },
            "targets": [
                {"id": "emission-balance", "goal": "Core bloom and arm glow read as one scene at 96 px without clipping to white."},
                {"id": "hue-ramp", "goal": defn["lookDevHueRamp"]},
                {"id": "depth-order", "goal": "Backdrop stars behind disc glow; foreground diffraction stars in front."},
            ],
        },
        "actionReadiness": {
            "required": False,
            "rationale": "Avatar: static camera, one rigid rotation pivot on the root group; no sockets, articulation, destruction or colliders.",
            "anchors": [
                {"id": "idle-spin", "kind": "rotation", "axis": "y", "rateRadPerSec": 0.15,
                 "note": "slow rigid disc rotation preserving spiral chirality"},
            ],
        },
        "assumptions": defn["assumptions"],
        "coordinateFrame": {
            "up": "+Y",
            "forward": "+Z",
            "units": "relative (disc diameter = 2.0)",
            "origin": "emblem center (galactic core)",
        },
        "silhouette": defn["silhouette"],
        "viewEvidence": [
            {"id": "front-reference", "view": "front", "file": "../references/%s-admission.png" % defn["targetId"],
             "note": "single stylized view; far side documented in assumptions; isolation border excluded from judgment"},
        ],
        "componentTree": comps,
        "materials": mats,
        "repetitionSystems": defn["repetitionSystems"],
        "buildPasses": BUILD_PASSES,
        "visualEvidence": [],
        "reviewHistory": [],
        "lodPlan": [
            {"tier": "avatar", "maxTriangles": 3000, "note": "rail tabs 30 px"},
            {"tier": "header", "maxTriangles": 6000, "note": "agent headers / detail views"},
        ],
        "performanceBudget": {
            "qualityPriority": "visual-identity-at-small-size",
            "targetTriangles": 6000,
            "maxDrawCalls": 12,
            "textureSize": 256,
            "fpsTarget": 30,
            "optimizationPolicy": "particles over meshes; shared runtime renders each emblem once per frame and blits to all slots",
        },
        "lightingFromPhoto": [
            "Key: none — every layer is self-emissive (additive blending); the emission gradients ARE the lighting",
            "Tone: renderer.toneMapping = ACESFilmicToneMapping, exposure 1.0 — keeps the core bloom from clipping to flat white",
            "Depth cue: the dark dust sprites act as the scene's contact shadow layer between arm glow and core (the only occlusion behavior in an additive scene); no ground plane exists",
        ],
        "proceduralStrategy": defn["proceduralStrategy"],
        "animationAnchors": [
            {"id": "idle-spin", "target": "root", "kind": "rotation-y", "rate": 0.15},
            {"id": "core-pulse", "target": defn["pulseTarget"], "kind": "scale-pulse", "amplitude": 0.06, "periodSeconds": 4.0},
        ],
        "destructionAnchors": [],
        "risks": defn["risks"],
        "localSpecSearch": assessment.get("localSpecSearch"),
        "mustNotDo": [
            "Do not use a single textured plane as the 'galaxy' — the spiral is placed particles.",
            "Do not use unseeded randomness — every stochastic placement records its seed.",
            "Do not add scene lights or PBR shading — the scene is self-emissive.",
            "Do not exceed the 6k triangle budget — identity lives in sprites and gradients.",
        ],
    }


def main():
    ws = sys.argv[1]
    defn = json.load(open(os.path.join(ws, "emblem-def.json")))
    defn["workspace"] = ws
    spec = build(defn)
    out = os.path.join(ws, "object-sculpt-spec.json")
    with open(out, "w") as fh:
        json.dump(spec, fh, indent=2)
    print("wrote", out)


if __name__ == "__main__":
    main()
