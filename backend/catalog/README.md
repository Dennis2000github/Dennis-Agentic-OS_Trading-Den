# Studio model catalog

`higgsfield-0.1.0.json` is a pinned snapshot of the **official** Higgsfield API
model documentation. The running server never fetches documentation; it only
reads this file.

## What is captured

For every documented image/video workflow:

- the endpoint ID (used verbatim as the Studio model ID),
- the workflow title, kind (`image` / `video`) and normalized mode,
- the complete input JSON schema published on the model page,
- provider capabilities that are actually documented.

The catalog is intentionally limited to the public API
(`https://api.higgsfield.ai`). The community MCP project's experimental web
backend (`cloud.higgsfield.ai`, browser cookies/Cloudflare) is **not** used and
must not be added here.

## Regenerating

```bash
python3 scripts/build_higgsfield_catalog.py
```

The builder prints the model count and writes a deterministic JSON file. Review
the diff before committing: a changed `source.fetched_at` is expected, schema
changes are not.

## Provenance and attribution

- Source: <https://docs.higgsfield.ai/docs/models.md> and its 84 workflow pages.
- The adapter/MCP design is informed by
  [Hikhakk/higgsfield-mcp-unified](https://github.com/Hikhakk/higgsfield-mcp-unified)
  (MIT). No code from that project is vendored.
- JSON schemas are factual API documentation. Model names and endpoints belong
  to Higgsfield.

## Notes

- `source.sha256` is the digest of the payload **before** the digest field is
  added. `backend/studio_catalog.py` verifies structure, not the digest.
- A model absent from this snapshot is not exposed in Studio. Adding a provider
  means adding a catalog file with the same `schema_version`; the loader rejects
  unknown versions.
