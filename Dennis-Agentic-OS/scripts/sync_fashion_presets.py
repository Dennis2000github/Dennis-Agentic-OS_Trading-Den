"""Pin the Collart-fashion preset catalog into the repo.

Fetches the public material API once (the same endpoints the Collart Fashion
dashboard calls at runtime) and writes a compact projection to
``backend/catalog/fashion-presets-<version>.json``. Runtime only ever reads the
pinned file; it never contacts Collart.

Usage:  python3 scripts/sync_fashion_presets.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

BASE = "https://collart.ai/api/proxy/api/v2/app"
HEADERS = {
    "X-Material-Platform": "collart-fashion",
    "User-Agent": "Mozilla/5.0 (compatible; dennis-studio-sync)",
}
CATALOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "catalog")
CATALOG_FILE = os.path.join(CATALOG_DIR, "fashion-presets-0.1.0.json")

VIDEO_MODULES = ["fashion_templates"]
MODEL_MODULES = ["fashion_models", "Model_Western_New", "Model_Western"]
OUTFIT_MODULES = ["fashion_outfits"]


def _get(path, params):
    query = "&".join("%s=%s" % (key, value) for key, value in params.items())
    request = urllib.request.Request(
        "%s/%s?%s" % (BASE, path, query), headers=HEADERS, method="GET"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _module_records(module_id, page_size=250):
    records, page = [], 1
    while True:
        payload = _get("module/template", {
            "moduleUniqueId": module_id,
            "countryCode": "US",
            "pageIndex": page,
            "pageSize": page_size,
            "templateVersion": "1.0",
        })
        batch = payload.get("records") or []
        records.extend(batch)
        total = payload.get("total") or 0
        if not batch or len(records) >= total or page > 20:
            break
        page += 1
    return records


def _category_map(record):
    """uniqueId prefix -> list of display names, deduplicated and ordered."""
    families = {}
    for category in record.get("displayCategories") or []:
        unique = str(category.get("uniqueId") or "").strip()
        name = str(category.get("displayName") or "").strip()
        if not unique or not name:
            continue
        family = unique.rsplit("_", 1)[0]
        bucket = families.setdefault(family, [])
        if name not in bucket:
            bucket.append(name)
    return families


def _family_values(families, *bases):
    """Union of families whose key equals a base or starts with ``base_``."""
    found = []
    for base in bases:
        for key, names in families.items():
            if key == base or key.startswith(base + "_"):
                for name in names:
                    if name not in found:
                        found.append(name)
    return found


def project_video(record):
    families = _category_map(record)
    metrics = record.get("metrics") or {}
    video = record.get("previewVideoUrl") or record.get("detailVideoUrl") or ""
    if not video:
        return None
    outfits = metrics.get("outfitCount")
    return {
        "id": record.get("uniqueId"),
        "title": record.get("title") or record.get("uniqueId"),
        "cover": record.get("coverUrl") or "",
        "video": video,
        "duration": record.get("duration"),
        "outfits": outfits if isinstance(outfits, int) and 1 <= outfits <= 5 else 1,
        "engines": list(metrics.get("supportedVideoModels") or []),
        "type": _family_values(families, "template_type"),
        "style": _family_values(families, "template_race"),
        "model": _family_values(families, "identity"),
        "vip": bool(record.get("isVip")),
    }


def project_model(record):
    cover = record.get("coverUrl") or ""
    images = record.get("imageUrls") or []
    if not cover and not images:
        return None
    families = _category_map(record)
    return {
        "id": record.get("uniqueId"),
        "title": record.get("title") or record.get("uniqueId"),
        "cover": cover or images[0],
        "identity": _family_values(families, "identity"),
        "modeltype": _family_values(families, "model_modeltype"),
        "country": _family_values(families, "model_country"),
    }


def project_outfit(record):
    cover = record.get("coverUrl") or ""
    images = record.get("imageUrls") or []
    if not cover and not images:
        return None
    families = _category_map(record)
    return {
        "id": record.get("uniqueId"),
        "title": record.get("title") or record.get("uniqueId"),
        "cover": cover or images[0],
        "clothing": _family_values(families, "outfit_clothingtype"),
        "style": _family_values(families, "outfit_styletype"),
        "identity": _family_values(families, "identity"),
    }


def _dedupe(items):
    seen, out = set(), []
    for item in items:
        if not item or not item.get("id") or item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
    return out


def main():
    videos, models, outfits = [], [], []
    for module in VIDEO_MODULES:
        videos.extend(project_video(record) for record in _module_records(module))
    for module in MODEL_MODULES:
        models.extend(project_model(record) for record in _module_records(module))
    for module in OUTFIT_MODULES:
        outfits.extend(project_outfit(record) for record in _module_records(module))

    catalog = {
        "schema_version": 1,
        "catalog_version": "fashion-presets-0.1.0",
        "source": "collart.ai public material API (module/template, X-Material-Platform: collart-fashion)",
        "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "videos": _dedupe(videos),
        "models": _dedupe(models),
        "outfits": _dedupe(outfits),
    }

    os.makedirs(CATALOG_DIR, exist_ok=True)
    with open(CATALOG_FILE, "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print("videos=%d models=%d outfits=%d -> %s" % (
        len(catalog["videos"]), len(catalog["models"]), len(catalog["outfits"]), CATALOG_FILE))


if __name__ == "__main__":
    sys.exit(main())
