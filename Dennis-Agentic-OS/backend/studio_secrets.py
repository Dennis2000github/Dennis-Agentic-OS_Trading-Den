"""Private credential storage for Studio providers.

Provider secrets never reach the browser: the API returns only a boolean, the
source (environment or stored file) and a short key hint. The stored file lives
under the git-ignored ``data/`` directory and is written atomically with mode
0600; its directory is mode 0700.

Environment variables override the stored file, which keeps CI and one-off
smoke tests from writing secrets to disk.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import threading

from . import config

STUDIO_DIR = os.path.join(config.DATA_DIR, "studio")
CREDENTIALS_FILE = os.path.join(STUDIO_DIR, "credentials.json")
FILE_VERSION = 1

# Field ids are internal; env maps each field to one or more variable names
# (first match wins). Labels are for the Studio setup form only.
PROVIDERS = {
    "higgsfield": {
        "label": "Higgsfield",
        "fields": ("api_key", "secret"),
        "field_labels": {"api_key": "API key", "secret": "API secret"},
        "env": {
            "api_key": ("HIGGSFIELD_API_KEY",),
            "secret": ("HIGGSFIELD_SECRET",),
        },
    },
    "fal": {
        "label": "fal.ai",
        "fields": ("api_key",),
        "field_labels": {"api_key": "API key"},
        "env": {"api_key": ("FAL_KEY",)},
    },
    "runway": {
        "label": "Runway",
        "fields": ("api_key",),
        "field_labels": {"api_key": "API secret"},
        "env": {"api_key": ("RUNWAYML_API_SECRET",)},
    },
    "luma": {
        "label": "Luma AI",
        "fields": ("api_key",),
        "field_labels": {"api_key": "API key"},
        "env": {"api_key": ("LUMA_AGENTS_API_KEY", "LUMA_API_KEY")},
    },
    "google_veo": {
        "label": "Google Veo",
        "fields": ("api_key",),
        "field_labels": {"api_key": "Gemini API key"},
        "env": {"api_key": ("GEMINI_API_KEY", "GOOGLE_API_KEY")},
    },
    "minimax": {
        "label": "MiniMax / Hailuo",
        "fields": ("api_key",),
        "field_labels": {"api_key": "API key"},
        "env": {"api_key": ("MINIMAX_API_KEY",)},
    },
    "replicate": {
        "label": "Replicate",
        "fields": ("api_key",),
        "field_labels": {"api_key": "API token"},
        "env": {"api_key": ("REPLICATE_API_TOKEN",)},
    },
}

_lock = threading.RLock()


class SecretError(Exception):
    pass


def _ensure_dir():
    os.makedirs(STUDIO_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(STUDIO_DIR, 0o700)
    except OSError:
        pass


def _read_all():
    if not os.path.isfile(CREDENTIALS_FILE):
        return {"version": FILE_VERSION, "providers": {}}
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SecretError("Stored credentials cannot be read: %s" % exc)
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        raise SecretError("Stored credentials have an unsupported format.")
    return data


def _write_all(data):
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=STUDIO_DIR, prefix=".cred-", suffix=".json")
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, CREDENTIALS_FILE)
        os.chmod(CREDENTIALS_FILE, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _spec(provider):
    spec = PROVIDERS.get(provider)
    if not spec:
        raise SecretError("Unknown credential provider: %s" % provider)
    return spec


def known(provider):
    return provider in PROVIDERS


def credential_schema(provider):
    """Public, secret-free field description for the Setup UI."""
    spec = _spec(provider)
    env_names = []
    for names in spec["env"].values():
        if isinstance(names, (list, tuple)):
            env_names.extend(names)
        else:
            env_names.append(names)
    labels = spec.get("field_labels") or {}
    return {
        "provider": provider,
        "label": spec["label"],
        "fields": [
            {
                "id": field,
                "label": labels.get(field) or field.replace("_", " ").title(),
                "secret": True,
            }
            for field in spec["fields"]
        ],
        "env": env_names,
    }


def _env_names(spec, field):
    raw = spec["env"].get(field)
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(raw)
    return (raw,)


def _env_values(provider):
    spec = _spec(provider)
    values = {}
    for field in spec["fields"]:
        for env_name in _env_names(spec, field):
            value = os.environ.get(env_name)
            if value:
                values[field] = value
                break
    return values


def _any_env_set(provider):
    spec = _spec(provider)
    for field in spec["fields"]:
        for env_name in _env_names(spec, field):
            if os.environ.get(env_name):
                return True
    return False


def get(provider):
    """Return ``(values, source)`` or ``(None, 'none')``.

    Environment values win as a whole: a half-configured environment entry is
    ignored in favour of a complete stored pair.
    """
    spec = _spec(provider)
    env_values = _env_values(provider)
    if all(env_values.get(field) for field in spec["fields"]):
        return env_values, "env"
    with _lock:
        data = _read_all()
    stored = data.get("providers", {}).get(provider)
    if isinstance(stored, dict) and all(stored.get(field) for field in spec["fields"]):
        return {field: stored[field] for field in spec["fields"]}, "stored"
    return None, "none"


def status(provider):
    spec = _spec(provider)
    values, source = get(provider)
    schema = credential_schema(provider)
    if not values:
        return {
            "configured": False,
            "source": "none",
            "hint": None,
            "updated_at": None,
            "missing": list(spec["fields"]),
            "env_override": _any_env_set(provider),
            "fields": schema["fields"],
            "env": schema["env"],
        }
    with _lock:
        data = _read_all()
    stored = data.get("providers", {}).get(provider) or {}
    key = values.get("api_key") or next(iter(values.values()), "") or ""
    return {
        "configured": True,
        "source": source,
        "hint": ("…" + key[-4:]) if len(key) >= 4 else None,
        "updated_at": stored.get("updated_at") if source == "stored" else None,
        "missing": [],
        "env_override": source == "env",
        "fields": schema["fields"],
        "env": schema["env"],
    }


def set_values(provider, values):
    """Store a complete credential set. Raises ``SecretError`` on bad input."""
    spec = _spec(provider)
    if not isinstance(values, dict):
        raise SecretError("Credentials must be an object.")
    unknown = set(values) - set(spec["fields"])
    if unknown:
        raise SecretError("Unsupported credential field: %s" % sorted(unknown)[0])
    clean = {}
    for field in spec["fields"]:
        value = values.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SecretError("%s is required." % field)
        if len(value) > 4096:
            raise SecretError("%s is too long." % field)
        if any(ch in value for ch in ("\n", "\r", "\x00")):
            raise SecretError("%s contains an invalid character." % field)
        clean[field] = value.strip()
    with _lock:
        data = _read_all()
        data.setdefault("providers", {})[provider] = dict(
            clean, updated_at=config.now_iso()
        )
        _write_all(data)
    return status(provider)


def delete(provider):
    _spec(provider)
    with _lock:
        data = _read_all()
        providers = data.get("providers") or {}
        if provider not in providers:
            return status(provider)
        providers.pop(provider, None)
        data["providers"] = providers
        _write_all(data)
    return status(provider)
