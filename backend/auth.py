"""Local owner identity and session token.

DENNIS Agentic OS currently runs as a single local user. Rather than bolt on a
user database, every request is attributed to the local owner and every
WebSocket connection must present the process session token. This is the
smallest change that satisfies "authenticate every WebSocket and check the user
owns the session" without inventing a fake multi-tenant system.

The token is generated once and stored in ``data/auth.json`` (git-ignored
directory, never sent to logs). It is *not* an upstream provider credential --
those never leave the backend process.
"""

import hmac
import os
import secrets

from . import config


def _load_or_create_token():
    data = config.read_json(config.AUTH_FILE)
    if isinstance(data, dict) and data.get("token"):
        return data["token"]
    token = secrets.token_urlsafe(32)
    config.write_json(config.AUTH_FILE, {"owner_id": config.DEFAULT_OWNER, "token": token})
    return token


class Auth:
    def __init__(self):
        self.owner_id = config.DEFAULT_OWNER
        self.token = _load_or_create_token()

    def issue(self):
        """Return the session descriptor handed to the same-origin browser."""
        return {"owner_id": self.owner_id, "token": self.token}

    def check(self, candidate):
        if not candidate:
            return False
        return hmac.compare_digest(str(candidate), self.token)

    def owner_for(self, candidate):
        """Return the owner id when the token is valid, else ``None``."""
        return self.owner_id if self.check(candidate) else None


AUTH = Auth()
