"""UPIF shared asset library integration.

Generated assets are stored in the *existing* Studio media library
(``backend/studio_media.py``) so agents, Studios and the UI share one asset
store. No second library is created.
"""

from __future__ import annotations

import hashlib
import time

from . import studio_media as media


class AssetLibrary:
    """Thin wrapper over the shared Studio media library."""

    def store_bytes(self, data, content_type, kind="image", filename="upif-asset",
                    meta=None):
        digest = hashlib.sha256(data).hexdigest()
        record = media.add_bytes(
            data, content_type, kind=kind, filename=filename,
            meta={**(meta or {}), "provider": "upif", "sha256": digest,
                  "stored_at": time.time()})
        return {
            "media_id": record.get("id"),
            "url": "/api/studio/media/" + str(record.get("id")),
            "kind": kind,
            "sha256": digest,
        }

    def store_url(self, url, kind=None, meta=None):
        record = media.add_from_url(url, kind=kind,
                                    meta={**(meta or {}), "provider": "upif"})
        return {
            "media_id": record.get("id"),
            "url": "/api/studio/media/" + str(record.get("id")),
            "kind": record.get("kind"),
        }

    def get(self, media_id):
        return media.get(media_id)

    def open_path(self, media_id):
        return media.open_path(media_id)

    def list(self, kind=None, limit=60):
        return media.list_items(kind=kind, limit=limit)

    def delete(self, media_id):
        return media.delete(media_id)


ASSETS = AssetLibrary()
