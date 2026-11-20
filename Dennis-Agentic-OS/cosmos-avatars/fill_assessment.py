#!/usr/bin/env python3
"""Fill the agent-owned fields of an img2threejs pre-spec assessment.

The assessment script seeds the structure; the observation-backed values come
from the per-emblem image analysis (image-analysis.md, 8-layer protocol).
Usage: fill_assessment.py <workspace-dir> <assessment-fills.json>
"""
import json
import os
import sys


def fill(ws, fills_path):
    path = os.path.join(ws, "assessment.json")
    with open(path) as fh:
        a = json.load(fh)
    fills = json.load(open(fills_path))
    pa = a["preSpecAssessment"]
    pa["objectClass"] = fills["objectClass"]
    pa["complexity"]["scores"] = fills["scores"]
    pa["complexity"]["estimatedCounts"] = fills["estimatedCounts"]
    pa["complexity"]["reasoning"] = fills["reasoning"]
    pa["specDepthDecision"] = fills["specDepthDecision"]
    pa["unknownsToResolveBeforeImplementation"] = fills["unknowns"]
    pa["detailInventory"]["scanMethod"] = "agent-vision grid 3x3 (identity features from Layer 7)"
    pa["detailInventory"]["details"] = fills["detailInventory"]
    # Galaxy-specific quality contract replaces the seeded generic wording.
    qc = a["qualityContract"]
    qc.update(fills["qualityContract"])
    with open(path, "w") as fh:
        json.dump(a, fh, indent=2)
    print("filled", path)


if __name__ == "__main__":
    fill(sys.argv[1], sys.argv[2])
