"""Static-path guard for the local DENNIS OS HTTP server.

The server serves the app directory as its web root. API routes are owner-gated,
but the ``SimpleHTTPRequestHandler`` fallback must never expose runtime state,
credentials, source code or dot-directories to a browser. This module keeps the
deny-list small, explicit and testable.
"""

from __future__ import annotations

from urllib.parse import unquote

# Prefixes are checked against the decoded, slash-normalized request path.
PRIVATE_PREFIXES = (
    "/data/",
    "/backend/",
    "/tests/",
    "/.venv/",
    "/node_modules/",
    "/public-plugins/repo/",
)

PRIVATE_EXACT = (
    "/data",
    "/backend",
    "/tests",
    "/.git",
    "/.env",
    "/.venv",
    "/node_modules",
)


def is_private(path):
    """True when a static GET must be refused.

    Dotfiles and dot-directories are denied anywhere in the path so a nested
    ``.env`` or ``.claude`` file cannot leak through the static fallback.
    """
    raw = str(path or "").strip()
    if not raw:
        return False
    decoded = unquote(raw)
    normalized = "/" + decoded.lstrip("/")
    if any(part.startswith(".") for part in normalized.split("/") if part):
        return True
    if normalized.rstrip("/") in PRIVATE_EXACT:
        return True
    return any(normalized.startswith(prefix) for prefix in PRIVATE_PREFIXES)
