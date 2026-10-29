#!/usr/bin/env python3
"""Build the pinned Higgsfield model catalog used by Studio.

The runtime never fetches documentation. This maintenance script crawls the
official Higgsfield model pages, extracts each workflow's endpoint ID and
complete input JSON schema, and writes a deterministic JSON snapshot.

Usage (from the repo root):

    python3 scripts/build_higgsfield_catalog.py
    python3 scripts/build_higgsfield_catalog.py --out backend/catalog/higgsfield-0.1.0.json

Only the official API catalog is captured. The MCP project's experimental web
backend (cloud.higgsfield.ai) is intentionally excluded.

Source: https://docs.higgsfield.ai/docs/models.md
License note: the schemas are factual API documentation. The wrapper design is
informed by Hikhakk/higgsfield-mcp-unified (MIT); see backend/catalog/README.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

BASE = "https://docs.higgsfield.ai"
API_BASE = "https://api.higgsfield.ai"
USER_AGENT = "DENNIS-Agentic-OS-Studio-Catalog/1.0"
CATEGORIES = (("video-generation", "video"), ("image-generation", "image"))

CATALOG_VERSION = "higgsfield-0.1.0"


def fetch(path, retries=3):
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(
            BASE + path, headers={"User-Agent": USER_AGENT, "Accept": "text/markdown"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError("Could not fetch %s: %s" % (path, last))


def links(text, prefix):
    return sorted(set(re.findall(r"\]\((%s/[a-z0-9-]+)\)" % re.escape(prefix), text)))


def schema_from_page(text):
    """Return the largest object JSON schema embedded in the page."""
    best = None
    for block in re.findall(r"```json[^\n]*\n(.*?)```", text, re.S):
        cleaned = "\n".join(re.sub(r"^>\s?", "", line) for line in block.splitlines())
        try:
            data = json.loads(cleaned)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "object" and data.get("properties"):
            if best is None or len(json.dumps(data)) > len(json.dumps(best)):
                best = data
    return best


def endpoint_from_page(text):
    match = re.search(
        r"\*\*Endpoint:\*\*\s*`POST\s+(https://api\.higgsfield\.ai/[^`]+)`", text
    )
    if not match:
        return None
    url = match.group(1).strip()
    if not url.startswith(API_BASE + "/"):
        return None
    return url[len(API_BASE) + 1:]


def title_from_page(text):
    match = re.search(r"^#\s+(.+)$", text, re.M)
    title = match.group(1).strip() if match else ""
    title = re.sub(r"\s+API$", "", title).strip()
    return title


def mode_for(path, kind, schema):
    """Map the documented workflow to a normalized Studio mode."""
    slug = path.rsplit("/", 1)[-1]
    props = (schema or {}).get("properties", {})
    required = set((schema or {}).get("required", []))

    if slug == "create-character":
        return "character_training"
    if kind == "image":
        if slug in ("text-to-image", "generate") and "image_url" not in props and "image_urls" not in props:
            return "text_to_image"
        if "image_url" in props or "image_urls" in props or "input_image_urls" in props:
            return "image_edit"
        return "text_to_image"

    if "text-to-video" in slug:
        return "text_to_video"
    if "image-to-video" in slug:
        return "image_to_video"
    if "reference-to-video" in slug:
        return "reference_to_video"
    if "first-last-frame" in slug:
        return "first_last_frame"
    if "video-edit" in slug:
        return "video_edit"
    if "video-extend" in slug:
        return "video_extend"
    if "video-reference" in slug:
        return "video_reference"
    if "motion-control" in path or "motion-transfer" in path:
        return "motion_transfer"
    if "object-swap" in path:
        return "object_swap"
    if "restyle" in path:
        return "restyle"
    if "image_url" in required or "image_urls" in required:
        return "image_to_video"
    return "text_to_video"


def crawl():
    families = {}
    for category, kind in CATEGORIES:
        text = fetch("/docs/models/%s.md" % category)
        for link in links(text, "/docs/models"):
            # /docs/models/<family> only; skip nested workflow links.
            if link.count("/") != 3:
                continue
            families[link] = kind

    models = []
    seen_pages = set()
    for family, kind in sorted(families.items()):
        text = fetch(family + ".md")
        for page in links(text, family):
            if page in seen_pages:
                continue
            seen_pages.add(page)
            page_text = fetch(page + ".md")
            endpoint = endpoint_from_page(page_text)
            schema = schema_from_page(page_text)
            if not endpoint or not schema:
                print("  skip (no endpoint/schema): %s" % page, file=sys.stderr)
                continue
            mode = mode_for(page, kind, schema)
            models.append(
                {
                    "id": endpoint,
                    "label": title_from_page(page_text) or endpoint,
                    "kind": kind,
                    "mode": mode,
                    "endpoint": endpoint,
                    "doc_path": page,
                    "schema": schema,
                    "generation": mode != "character_training",
                    "capabilities": {
                        "estimate": False,
                        "cancel": "queued_only",
                        "progress": False,
                        "upload": True,
                        "webhook": True,
                        "polling": True,
                    },
                }
            )
            time.sleep(0.04)

    models.sort(key=lambda m: (m["kind"], m["mode"], m["id"]))
    return models


def build(out_path):
    models = crawl()
    if not models:
        raise SystemExit("No models were discovered; refusing to write an empty catalog.")
    payload = {
        "schema_version": 1,
        "catalog_version": CATALOG_VERSION,
        "provider": "higgsfield",
        "api_base": API_BASE,
        "source": {
            "docs": BASE + "/docs/models.md",
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "builder": "scripts/build_higgsfield_catalog.py",
            "experimental_web_backend": False,
        },
        "models": models,
    }
    raw = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    payload["source"]["sha256"] = digest
    raw = json.dumps(payload, indent=2, sort_keys=False) + "\n"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(raw)
    print("Wrote %d models to %s (%d bytes, sha256 %s)"
          % (len(models), out_path, len(raw), digest[:16]))
    print("Kinds:", {k: sum(1 for m in models if m["kind"] == k) for k in ("image", "video")})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "backend", "catalog", "%s.json" % CATALOG_VERSION,
        ),
    )
    args = parser.parse_args()
    build(args.out)


if __name__ == "__main__":
    main()
