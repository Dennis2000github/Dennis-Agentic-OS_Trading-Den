"""DENNIS centralized TTS Router.

One provider registry shared by every workspace (Hermes Apollo, Ads Studio,
YouTube Studio). The selected provider is configurable at three levels:

    1. global default provider
    2. per-studio preference (ads / youtube / apollo)
    3. voice-preset override (a preset pins its own provider, e.g. JARVIS)

The default provider is Kokoro — the shared local inference service on
``http://127.0.0.1:8880`` (see ``tts_service/server.py`` and
``scripts/kokoro_service.py``). ElevenLabs remains an optional cloud
provider; its API key is stored backend-side only (0600 file) and is never
sent to the frontend. Provider selection is independent per studio, so an
Ads Studio preference can never overwrite the Apollo voice configuration.

This module deliberately keeps no audio state: audio bytes flow straight
from the provider to the HTTP response (or into ``data/tts/audio`` when a
caller asks for a saved copy).
"""

import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import config as dennis_config

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

TTS_DIR = os.path.join(dennis_config.DATA_DIR, "tts")
CONFIG_FILE = os.path.join(TTS_DIR, "config.json")
AUDIO_DIR = os.path.join(TTS_DIR, "audio")
SECRETS_FILE = os.path.join(TTS_DIR, "secrets.json")  # 0600; provider API keys

KOKORO_BASE_URL = os.environ.get("KOKORO_TTS_URL", "http://127.0.0.1:8880")
MAX_TEXT_CHARS = 6000

# Backend-side concurrency cap: at most two generations in flight toward the
# inference service; additional callers wait (the service also queues).
_GENERATION_SEM = threading.BoundedSemaphore(2)

STUDIOS = ("apollo", "ads", "youtube")

# ---------------------------------------------------------------------------
# Provider registry (dynamic — new providers register themselves here)
# ---------------------------------------------------------------------------

PROVIDERS = {
    "kokoro": {
        "label": "Kokoro (local, no paid API)",
        "local": True,
        "requires_key": False,
        "default": True,
    },
    "elevenlabs": {
        "label": "ElevenLabs (cloud, optional)",
        "local": False,
        "requires_key": True,
    },
    "fish": {
        "label": "Fish Audio (cloud, optional)",
        "local": False,
        "requires_key": True,
        # Verified against official docs 2026-11-20:
        # POST https://api.fish.audio/v1/tts, Bearer key, `model` header,
        # JSON body {text, format, ...}. Configurable via FISH_AUDIO_TTS_URL.
        "endpoint_verified_on_this_machine": True,
    },
    "browser": {
        "label": "Browser voices (fallback only)",
        "local": True,
        "requires_key": False,
        "fallback_only": True,
    },
}

# ---------------------------------------------------------------------------
# Model catalog (curated, verified — never guessed)
#
# `cost` ordering is what the UI uses for "free-first": free -> free-tier ->
# paid -> deprecated. Kokoro voices are discovered live from the local service;
# cloud voice IDs are entered by the owner (no authenticated list calls).
# ---------------------------------------------------------------------------

MODEL_SOURCES = {
    "kokoro": "GET http://127.0.0.1:8880/v1/audio/voices (live local service)",
    "fish": "https://docs.fish.audio/developer-guide/models-pricing/models-overview.md",
    "elevenlabs": "https://elevenlabs.io/docs/overview/models.md",
}

MODEL_CATALOG = {
    "kokoro": [
        {"id": "kokoro-v1.0", "label": "kokoro-v1.0", "cost": "free",
         "note": "Local inference on this machine. 54 voices discovered live. No API key, no network cost."},
    ],
    "fish": [
        {"id": "s2.1-pro-free", "label": "S2.1 Pro Free", "cost": "free",
         "note": "$0 under fair-use limits. Same model quality as s2.1-pro; no TTFA/DPA guarantees."},
        {"id": "s2.1-pro", "label": "S2.1 Pro", "cost": "paid",
         "note": "Recommended production model with TTFA/DPA guarantees."},
        {"id": "s2-pro", "label": "S2 Pro", "cost": "paid",
         "note": "Previous generation, multi-speaker and expression control."},
        {"id": "s1", "label": "S1 (deprecated)", "cost": "deprecated",
         "note": "Deprecated; retired 2026-12-31, after which requests are served by s2.1-pro."},
    ],
    "elevenlabs": [
        {"id": "eleven_flash_v2", "label": "Flash v2", "cost": "free-tier",
         "note": "English only, ultra-low latency (~135ms). Available on the Free plan."},
        {"id": "eleven_flash_v2_5", "label": "Flash v2.5", "cost": "free-tier",
         "note": "32 languages, ~100ms latency, 50% lower price per character. Available on the Free plan."},
        {"id": "eleven_multilingual_v2", "label": "Multilingual v2", "cost": "free-tier",
         "note": "29 languages, most stable on long-form. Available on the Free plan."},
        {"id": "eleven_v3", "label": "Eleven v3", "cost": "paid",
         "note": "Expressive, 70+ languages, 5,000 character limit."},
        {"id": "eleven_v4", "label": "Eleven v4", "cost": "paid",
         "note": "Highest quality, 90+ languages, 10,000 character limit."},
        {"id": "eleven_v4_turbo", "label": "Eleven v4 Turbo", "cost": "paid",
         "note": "Real-time, ~100ms median latency, 90+ languages."},
    ],
}

COST_ORDER = {"free": 0, "free-tier": 1, "paid": 2, "deprecated": 3}

# Voice lists for cloud providers are fetched LIVE with the owner's stored
# key (read-only GETs, short timeout, small in-process cache). No voice IDs
# are ever invented: when a key is missing or the list call fails, the picker
# falls back to honest manual voice-ID entry.
VOICE_LIST_SOURCES = {
    "fish": "GET https://api.fish.audio/model?page_size=100 (Bearer key) — "
            "docs.fish.audio/api-reference/endpoint/model/list-models.md",
    "elevenlabs": "GET https://api.elevenlabs.io/v1/voices?page_size=100 "
                  "(xi-api-key header)",
}

_VOICE_LIST_TTL = 300.0
_VOICE_LIST_LOCK = threading.Lock()
_VOICE_LIST_CACHE = {"at": 0.0, "data": {}}


def _fetch_https_json(host, path, headers, timeout=8):
    """Small read-only HTTPS GET helper. Returns parsed JSON or None."""
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    try:
        conn.request("GET", path, headers=headers)
        res = conn.getresponse()
        raw = res.read()
        if res.status != 200:
            return None
        return json.loads(raw.decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _elevenlabs_voice_list():
    key = _elevenlabs_key()
    if not key:
        return []
    payload = _fetch_https_json(
        "api.elevenlabs.io", "/v1/voices?page_size=100",
        {"xi-api-key": key, "Accept": "application/json"})
    out = []
    for v in (payload or {}).get("voices") or []:
        vid = str(v.get("voice_id") or "")
        if not vid:
            continue
        labels = v.get("labels") or {}
        out.append({
            "id": vid,
            "label": str(v.get("name") or vid)[:80],
            "lang": str(labels.get("language") or labels.get("accent") or "")[:24],
        })
    return out


def _fish_voice_list():
    key = _fish_key()
    if not key:
        return []
    payload = _fetch_https_json(
        "api.fish.audio", "/model?page_size=100&sort_by=task_count",
        {"Authorization": "Bearer " + key, "Accept": "application/json"})
    out = []
    for m in (payload or {}).get("items") or []:
        mid = str(m.get("_id") or "")
        if not mid:
            continue
        langs = m.get("languages") or []
        out.append({
            "id": mid,
            "label": str(m.get("title") or mid)[:80],
            "lang": ", ".join(str(l) for l in langs[:2])[:24],
        })
    return out


def provider_voices(pid):
    """Live voice list for a cloud provider; [] when no key or fetch fails.

    Cached for _VOICE_LIST_TTL seconds so opening the picker never hammers
    the provider. Read-only GETs only — nothing here mutates provider state.
    """
    with _VOICE_LIST_LOCK:
        now = time.time()
        cached = _VOICE_LIST_CACHE["data"].get(pid)
        if cached is not None and now - _VOICE_LIST_CACHE["at"] <= _VOICE_LIST_TTL:
            return cached
    fetchers = {"elevenlabs": _elevenlabs_voice_list, "fish": _fish_voice_list}
    fetcher = fetchers.get(pid)
    if fetcher is None:
        return []
    result = fetcher() or []
    with _VOICE_LIST_LOCK:
        _VOICE_LIST_CACHE["at"] = time.time()
        _VOICE_LIST_CACHE["data"][pid] = result
    return result


def catalog():
    """Read-only free-first catalog.

    Kokoro voices come from the local service; cloud provider voices come
    live from the provider via the stored key (see ``provider_voices``).
    Every provider therefore renders as a dropdown; the honest manual-entry
    fallback only appears when a list is genuinely unavailable.
    """
    voices = []
    for entry in kokoro_voices():
        if isinstance(entry, dict):
            vid = str(entry.get("id") or entry.get("voice_id") or entry.get("name") or "")
            label = str(entry.get("name") or entry.get("label") or vid)
            lang = str(entry.get("language") or entry.get("lang") or "")
        else:
            vid = str(entry)
            label = vid
            lang = ""
        if vid:
            voices.append({"id": vid, "label": label, "lang": lang})
    if not voices:
        # Honest fallback: no invented voice list when the service is down.
        voices = [{"id": "", "label": "Kokoro offline — no voices available", "lang": ""}]

    providers = []
    for pid, slot in PROVIDERS.items():
        if slot.get("fallback_only"):
            continue
        models = sorted(MODEL_CATALOG.get(pid, []),
                        key=lambda m: (COST_ORDER.get(m.get("cost"), 9), m.get("id")))
        if pid == "kokoro":
            voices_for_pid = voices
        else:
            voices_for_pid = provider_voices(pid)
        # Dropdown whenever a real list exists; manual entry otherwise.
        voice_mode = "list" if voices_for_pid else "custom"
        providers.append({
            "id": pid,
            "label": slot.get("label", pid),
            "local": bool(slot.get("local")),
            "requires_key": bool(slot.get("requires_key")),
            "rank": min((COST_ORDER.get(m.get("cost"), 9) for m in models), default=9),
            "models": models,
            "voices": voices_for_pid,
            "voice_mode": voice_mode,
            "voice_source": VOICE_LIST_SOURCES.get(pid, ""),
            "source": MODEL_SOURCES.get(pid, ""),
        })
    providers.sort(key=lambda p: (p["rank"], 0 if p["local"] else 1, p["id"]))
    return {
        "available": True,
        "cost_order": ["free", "free-tier", "paid", "deprecated"],
        "providers": providers,
        "note": "Voice lists are fetched live: Kokoro from the local service, "
                "cloud providers via read-only GETs with the stored key. "
                "Without a key the picker falls back to manual voice-ID entry.",
    }


# ---------------------------------------------------------------------------
# Default configuration — JARVIS preset is persistent and provider-independent
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "version": 1,
    "global_default_provider": "kokoro",
    "global_default_model_id": None,
    "global_default_voice_id": None,
    "studios": {
        "apollo": {"provider": "kokoro", "voice_preset": "jarvis-british-male",
                   "model_id": None, "voice_id": None},
        "ads": {"provider": "kokoro", "voice_preset": "narrator-en-us",
                "model_id": None, "voice_id": None},
        "youtube": {"provider": "kokoro", "voice_preset": "narrator-en-us",
                    "model_id": None, "voice_id": None},
    },
    "presets": {
        "jarvis-british-male": {
            "name": "JARVIS — BRITISH MALE",
            "provider": "kokoro",
            "voice_id": "bm_george",
            "fallback_voice_id": "bm_lewis",
            "lang": "en-gb",
            "speed": 1.03,
            "output": "pcm",
            "sample_rate": 24000,
            "model_id": None,
        },
        "narrator-en-us": {
            "name": "Narrator — American Male",
            "provider": "kokoro",
            "voice_id": "am_michael",
            "fallback_voice_id": "am_adam",
            "lang": "en-us",
            "speed": 1.0,
            "output": "wav",
            "sample_rate": 24000,
            "model_id": None,
        },
    },
    "apollo": {
        # Browser speechSynthesis stays available ONLY as an explicit
        # fallback when the selected provider cannot serve audio.
        "browser_fallback": True,
        # Suspend mic recognition while Apollo is speaking so the
        # microphone never transcribes Jarvis's own voice.
        "duck_mic": True,
    },
}


def _now():
    return dennis_config.now_iso()


# ---------------------------------------------------------------------------
# Config persistence (atomic JSON, git-ignored data dir)
# ---------------------------------------------------------------------------

def _validate_model_id(provider, model_id):
    """Return model_id when it is in the catalog for provider, else None."""
    if model_id is None:
        return None
    allowed = {m["id"] for m in MODEL_CATALOG.get(provider, [])}
    return model_id if model_id in allowed else None


def load_config():
    stored = dennis_config.read_json(CONFIG_FILE)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy of defaults
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key == "studios" and isinstance(value, dict):
                for studio, pref in value.items():
                    if studio in cfg["studios"] and isinstance(pref, dict):
                        cfg["studios"][studio].update(pref)
            elif key == "presets" and isinstance(value, dict):
                cfg["presets"].update(value)
            elif key == "apollo" and isinstance(value, dict):
                cfg["apollo"].update(value)
            else:
                cfg[key] = value
    # Compatibility aliases for the frontend client (js/tts.js): studio prefs
    # use `preset`, presets carry their own `id`, providers carry `enabled`.
    for studio, pref in cfg["studios"].items():
        pref.setdefault("preset", pref.get("voice_preset"))
        pref.setdefault("fallback_browser", cfg["apollo"]["browser_fallback"])
    for slug, preset in cfg["presets"].items():
        preset.setdefault("id", slug)
    # Model ids are only ever returned when they exist in the verified catalog.
    cfg["global_default_model_id"] = _validate_model_id(
        cfg.get("global_default_provider"), cfg.get("global_default_model_id"))
    for studio, pref in cfg["studios"].items():
        pref["model_id"] = _validate_model_id(
            pref.get("provider"), pref.get("model_id"))
    # The provider registry is exposed on the config object too: the Apollo
    # voice panel and the Settings card both render selects from it.
    cfg.setdefault("providers", {})
    for pid, slot in PROVIDERS.items():
        view = dict(slot)
        view["default"] = (pid == cfg["global_default_provider"])
        if pid == "kokoro":
            view["enabled"] = True
        elif pid == "elevenlabs":
            view["enabled"] = bool(_elevenlabs_key())
        else:
            view["enabled"] = True
        cfg["providers"][pid] = view
    return cfg


def save_config(patch):
    """Validate + merge a partial config patch; returns the saved config."""
    cfg = load_config()
    if not isinstance(patch, dict):
        raise TTSError("config patch must be an object")

    if "global_default_provider" in patch:
        provider = str(patch["global_default_provider"])
        if provider not in PROVIDERS:
            raise TTSError(f"unknown provider: {provider}")
        cfg["global_default_provider"] = provider
    if "global_default_model_id" in patch:
        cfg["global_default_model_id"] = _validate_model_id(
            cfg["global_default_provider"], patch["global_default_model_id"])
    if "global_default_voice_id" in patch:
        voice = patch["global_default_voice_id"]
        cfg["global_default_voice_id"] = (str(voice)[:64] or None) if voice else None

    if isinstance(patch.get("studios"), dict):
        for studio, pref in patch["studios"].items():
            if studio not in STUDIOS or not isinstance(pref, dict):
                continue
            target = cfg["studios"][studio]
            provider = pref.get("provider")
            if provider is not None:
                if provider == "":
                    pass  # "Global default" option in the UI: keep current
                elif provider not in PROVIDERS:
                    raise TTSError(f"unknown provider: {provider}")
                else:
                    target["provider"] = provider
            # `preset` (frontend) and `voice_preset` are aliases of each other.
            preset = pref.get("preset", pref.get("voice_preset"))
            if preset is not None:
                preset = str(preset)
                if preset and preset not in cfg["presets"]:
                    raise TTSError(f"unknown preset: {preset}")
                target["voice_preset"] = preset
                target["preset"] = preset
            if pref.get("speed") is not None:
                target["speed"] = max(0.5, min(2.0, float(pref["speed"])))
            if "fallback_browser" in pref:
                target["fallback_browser"] = bool(pref["fallback_browser"])
            if "model_id" in pref:
                target["model_id"] = _validate_model_id(
                    target.get("provider", "kokoro"), pref["model_id"])
            if "voice_id" in pref:
                voice = pref["voice_id"]
                target["voice_id"] = (str(voice)[:64] or None) if voice else None

    if isinstance(patch.get("presets"), dict):
        for slug, preset in patch["presets"].items():
            if not isinstance(preset, dict):
                continue
            clean = _clean_preset(slug, preset)
            if clean is not None:
                cfg["presets"][slug] = clean

    if isinstance(patch.get("apollo"), dict):
        for key in ("browser_fallback", "duck_mic"):
            if key in patch["apollo"]:
                cfg["apollo"][key] = bool(patch["apollo"][key])

    if isinstance(patch.get("provider_secrets"), dict):
        for provider, secret in patch["provider_secrets"].items():
            if isinstance(secret, dict) and secret.get("api_key"):
                set_provider_secret(provider, secret["api_key"])

    cfg.pop("providers", None)  # derived view — never persisted
    dennis_config.write_json(CONFIG_FILE, cfg)
    return cfg


def _clean_preset(slug, preset):
    slug = re.sub(r"[^a-z0-9-]+", "-", str(slug).lower()).strip("-")
    if not slug:
        return None
    # Patches MERGE with the stored preset: a UI that only changes speed must
    # not silently reset the voice, language or fallback of an existing preset.
    existing = load_config()["presets"].get(slug) or {}

    def pick(key, default):
        value = preset.get(key, existing.get(key))
        return default if value is None else value

    provider = str(pick("provider", "kokoro"))
    if provider not in PROVIDERS:
        raise TTSError(f"unknown provider in preset: {provider}")
    return {
        "name": str(pick("name", slug))[:80],
        "provider": provider,
        "voice_id": str(pick("voice_id", "bm_george"))[:64],
        "fallback_voice_id": str(pick("fallback_voice_id", ""))[:64],
        "lang": str(pick("lang", "en-us"))[:12],
        "speed": max(0.5, min(2.0, float(pick("speed", 1.0)))),
        "output": "pcm" if pick("output", "wav") == "pcm" else "wav",
        "sample_rate": int(pick("sample_rate", 24000)),
        "model_id": _validate_model_id(provider, pick("model_id", None)),
    }


# ---------------------------------------------------------------------------
# Provider / preset resolution (3-level selection)
# ---------------------------------------------------------------------------

def effective_provider(studio, preset_slug=None):
    """Resolve the effective provider for a studio request.

    Order: explicit preset (which pins its own provider) -> studio preference
    -> global default -> kokoro. A studio can therefore never change the
    provider another studio uses. An explicitly requested but unknown preset
    is an error (never a silent fallback to some other voice).
    """
    cfg = load_config()
    preset = None
    if preset_slug:
        preset = cfg["presets"].get(preset_slug)
        if preset is None:
            raise TTSError(f"unknown preset: {preset_slug}")
    if preset:
        return str(preset.get("provider") or cfg["global_default_provider"]), preset, cfg
    studio_pref = cfg["studios"].get(studio or "", {})
    provider = studio_pref.get("provider") or cfg["global_default_provider"] or "kokoro"
    preset_slug = studio_pref.get("voice_preset")
    preset = cfg["presets"].get(preset_slug) if preset_slug else None
    return provider, preset, cfg


def _voice_is_valid_for(provider, voice):
    """True when ``voice`` is usable on ``provider``.

    Validated against the live list when one is available (Kokoro's local
    list, cloud lists via the stored key). When no list can be fetched the
    voice is accepted as-is — that is the documented manual-entry path.
    This guard exists so a voice saved under one provider (e.g. Kokoro's
    ``af_bella``) can never be sent to another provider (ElevenLabs 404s).
    """
    if not voice:
        return False
    if provider == "kokoro":
        known = set()
        for entry in kokoro_voices():
            if isinstance(entry, dict):
                known.add(str(entry.get("id") or entry.get("voice_id") or ""))
            else:
                known.add(str(entry))
        known.discard("")
        return (not known) or voice in known
    listing = provider_voices(provider)
    if not listing:
        return True  # manual-entry fallback (no key configured / fetch failed)
    return any(v.get("id") == voice for v in listing)


def resolve_voice(provider, studio=None, preset_slug=None, preset=None,
                  explicit_voice_id=None, cfg=None):
    """Resolve the voice for a request — shared by buffered and streaming paths.

    Precedence: explicit request argument -> the picker's saved per-studio
    voice -> the picker's saved global default voice -> the legacy preset's
    voice (only when that preset targets this provider) -> provider default.

    Every candidate is checked against the live provider voice list
    (``_voice_is_valid_for``) so stale cross-provider selections are skipped
    instead of erroring. The picker's saved selections always beat the
    preset; the preset only remains a styling fallback (speed/lang/output).
    Returns ``(voice, preset)`` where ``preset`` is the caller's preset when
    one was supplied.
    """
    if cfg is None:
        cfg = load_config()
    if preset is None and preset_slug:
        preset = (cfg.get("presets") or {}).get(preset_slug) or None
    studio_pref = (cfg.get("studios") or {}).get(studio or "") or {}
    preset_voice = ((preset or {}).get("voice_id")
                    if (preset or {}).get("provider") == provider else None)
    candidates = [
        explicit_voice_id,
        studio_pref.get("voice_id"),
        cfg.get("global_default_voice_id"),
        preset_voice,
    ]
    for candidate in candidates:
        if candidate and _voice_is_valid_for(provider, str(candidate)[:64]):
            return str(candidate)[:64], preset
    # Provider defaults (documented fallbacks, never a silent Jarvis).
    if provider == "elevenlabs":
        return "pNInz6obpgDQGcFmaJgB", preset  # ElevenLabs docs default ("Adam")
    if provider == "kokoro":
        return "bm_george", preset
    return None, preset  # fish: server-side default voice when none selected


# ---------------------------------------------------------------------------
# Secrets (ElevenLabs) — backend-side only, never returned to the frontend
# ---------------------------------------------------------------------------

def _load_secrets():
    data = dennis_config.read_json(SECRETS_FILE)
    return data if isinstance(data, dict) else {}


def _save_secret(name, value):
    os.makedirs(TTS_DIR, exist_ok=True)
    secrets = _load_secrets()
    secrets[name] = value
    dennis_config.write_json(SECRETS_FILE, secrets)
    try:
        os.chmod(SECRETS_FILE, 0o600)
    except OSError:
        pass


def _elevenlabs_key():
    return (
        os.environ.get("ELEVENLABS_API_KEY")
        or _load_secrets().get("elevenlabs_api_key")
        or ""
    )


def set_provider_secret(provider, api_key):
    """Store a provider API key backend-side (0600). Never echoes it back."""
    if provider == "elevenlabs":
        _save_secret("elevenlabs_api_key", str(api_key))
        return
    if provider == "fish":
        # Fish Audio keys live in ~/.jarvis (0700 dir / 0600 file), never in
        # the repository. No browser UI writes this value.
        from . import jarvis_os
        jarvis_os.ensure_private_home()
        secrets = {}
        try:
            with open(jarvis_os.SECRETS_FILE, "r", encoding="utf-8") as handle:
                secrets = json.load(handle)
        except (OSError, ValueError):
            secrets = {}
        if not isinstance(secrets, dict):
            secrets = {}
        secrets["fish_audio_api_key"] = str(api_key)
        with open(jarvis_os.SECRETS_FILE, "w", encoding="utf-8") as handle:
            json.dump(secrets, handle)
        os.chmod(jarvis_os.SECRETS_FILE, 0o600)
        return
    raise TTSError(f"no secret storage defined for provider: {provider}")


def _fish_key():
    """Fish Audio key from env or ~/.jarvis/secrets.json (0600). Never returned."""
    env_key = os.environ.get("FISH_API_KEY") or os.environ.get("FISH_AUDIO_API_KEY")
    try:
        from . import jarvis_os
        return env_key or jarvis_os.read_secret("fish_audio_api_key") or ""
    except Exception:
        return env_key or ""


FISH_TTS_URL = "https://api.fish.audio/v1/tts"


def fish_audio_status():
    key = _fish_key()
    return {
        "configured": bool(key),
        "endpoint": os.environ.get("FISH_AUDIO_TTS_URL", FISH_TTS_URL),
        "endpoint_verified_on_this_machine": True,
        "model_header": os.environ.get("FISH_AUDIO_MODEL", "s2.1-pro"),
        "note": "Verified against official docs (docs.fish.audio, 2026-11-20): "
                "POST /v1/tts with token auth, `model` header, JSON {text, format}. "
                "Configure via environment or the private credential store. "
                "This status endpoint never places a live call.",
    }


def fish_audio_speech(text, voice_id=None, model_id=None):
    """POST /v1/tts per verified Fish Audio docs. Returns (bytes, content_type, None)."""
    import urllib.parse
    key = _fish_key()
    if not key:
        raise TTSError("Fish Audio is not configured (set FISH_API_KEY or store a key in ~/.jarvis/secrets.json)")
    url = os.environ.get("FISH_AUDIO_TTS_URL", FISH_TTS_URL)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise TTSError("Fish Audio endpoint must be https")
    host = parsed.hostname or "api.fish.audio"
    port = parsed.port or 443
    path = (parsed.path or "/v1/tts") + (("?" + parsed.query) if parsed.query else "")
    model = model_id or os.environ.get("FISH_AUDIO_MODEL", "s2.1-pro")
    body = {"text": text, "format": "mp3"}
    reference_id = voice_id or os.environ.get("FISH_AUDIO_REFERENCE_ID")
    if reference_id:
        body["reference_id"] = reference_id
    payload = json.dumps(body).encode("utf-8")
    try:
        conn = http.client.HTTPSConnection(host, port, timeout=30)
        conn.request("POST", path, body=payload, headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "model": model,
            "Accept": "audio/mpeg",
        })
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
        ctype = resp.getheader("Content-Type") or "audio/mpeg"
        conn.close()
    except OSError as exc:
        raise TTSError("Fish Audio request failed: " + type(exc).__name__)
    if status != 200 or not raw:
        snippet = raw[:200].decode("utf-8", "replace") if raw else "empty response"
        raise TTSError(f"Fish Audio failed (HTTP {status}): {snippet}")
    return raw, ctype if "audio" in ctype else "audio/mpeg", None


# ---------------------------------------------------------------------------
# Kokoro adapter (shared local inference service)
# ---------------------------------------------------------------------------

def _kokoro_endpoint():
    """Parse KOKORO_BASE_URL into (host, port)."""
    from urllib.parse import urlparse
    parsed = urlparse(KOKORO_BASE_URL)
    return parsed.hostname or "127.0.0.1", parsed.port or 80


def kokoro_health(timeout=3.0):
    """Health of the shared Kokoro service, or None when unreachable."""
    host, port = _kokoro_endpoint()
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        if resp.status != 200:
            return None
        data = json.loads(body.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def kokoro_voices(timeout=5.0):
    host, port = _kokoro_endpoint()
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/v1/audio/voices")
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        if resp.status != 200:
            return []
        data = json.loads(body.decode("utf-8"))
        if isinstance(data, dict):
            if isinstance(data.get("voices"), list):
                return data["voices"]
            if isinstance(data.get("voice_ids"), list):
                return data["voice_ids"]
        return []
    except (OSError, ValueError):
        return []


def kokoro_speech(text, voice_id="bm_george", speed=1.0, lang=None,
                  response_format="wav", timeout=600.0):
    """Generate audio through the shared Kokoro service.

    Returns ``(audio_bytes, content_type, sample_rate)``.
    Raises :class:`TTSError` with an actionable message on failure.
    """
    if not text or not text.strip():
        raise TTSError("text is required")
    if len(text) > MAX_TEXT_CHARS:
        raise TTSError(f"text exceeds {MAX_TEXT_CHARS} characters")
    health = kokoro_health(timeout=2.0)
    if health is None:
        raise TTSError(
            "Kokoro service is not reachable on 127.0.0.1:8880 — start it with "
            "`python3 scripts/kokoro_service.py start` or use POST /api/tts/service "
            "{\"action\":\"start\"}.")
    if health.get("load_error"):
        raise TTSError(f"Kokoro model error: {health['load_error']}")
    if lang is None:
        lang = "en-gb" if str(voice_id).startswith("b") else "en-us"
    payload = json.dumps({
        "model": "kokoro",
        "input": text,
        "voice": voice_id,
        "response_format": response_format,
        "speed": float(speed),
        "lang": lang,
    }).encode("utf-8")
    with _GENERATION_SEM:
        host, port = _kokoro_endpoint()
        try:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
            conn.request(
                "POST", "/v1/audio/speech", body=payload,
                headers={"Content-Type": "application/json",
                         "Content-Length": str(len(payload))})
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
        except OSError as exc:
            raise TTSError(f"Kokoro service request failed: {exc}")
        if resp.status != 200:
            try:
                err = json.loads(body.decode("utf-8"))
                message = err.get("error", {}).get("message", "") if isinstance(
                    err.get("error"), dict) else str(err.get("error") or err)
            except (ValueError, AttributeError):
                message = f"HTTP {resp.status}"
            raise TTSError(f"Kokoro synthesis failed: {message}")
    ctype = resp.getheader("Content-Type") or (
        "audio/wav" if response_format == "wav" else "audio/pcm")
    sample_rate = 24000
    header_rate = resp.getheader("X-Audio-Sample-Rate")
    if header_rate and header_rate.strip().isdigit():
        sample_rate = int(header_rate.strip())
    else:
        match = re.search(r"rate\s*=\s*(\d+)", ctype)
        if match:
            sample_rate = int(match.group(1))
    return body, ctype, sample_rate


def ensure_service(wait_s=90):
    """Start the shared service if (and only if) nothing healthy is listening."""
    if kokoro_health(timeout=2.0) is not None:
        return {"action": "noop", "reason": "already healthy"}
    return service_control("start", wait_s=wait_s)


# ---------------------------------------------------------------------------
# ElevenLabs adapter (optional cloud provider)
# ---------------------------------------------------------------------------

_EL_STATUS_CACHE = {"ts": 0.0, "data": None}
_EL_STATUS_TTL = 60.0  # seconds — the subscription call is a slow outbound API hit


def elevenlabs_status():
    key = _elevenlabs_key()
    out = {"configured": bool(key), "quota": None}
    if not key:
        return out
    # Cache: /api/tts/status polls this on every dashboard refresh, and a live
    # subscription check adds ~2s of outbound latency to every poll.
    now = time.time()
    cached = _EL_STATUS_CACHE["data"]
    if cached is not None and now - _EL_STATUS_CACHE["ts"] < _EL_STATUS_TTL:
        return dict(cached)
    try:
        conn = http.client.HTTPSConnection("api.elevenlabs.io", timeout=10)
        conn.request("GET", "/v1/user/subscription", headers={"xi-api-key": key})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        if resp.status == 200:
            data = json.loads(body.decode("utf-8"))
            out["quota"] = {
                "character_count": data.get("character_count"),
                "character_limit": data.get("character_limit"),
                "tier": data.get("tier"),
            }
    except (OSError, ValueError):
        pass
    _EL_STATUS_CACHE["ts"] = now
    _EL_STATUS_CACHE["data"] = dict(out)
    return out


def elevenlabs_speech(text, voice_id, model_id="eleven_multilingual_v2"):
    key = _elevenlabs_key()
    if not key:
        raise TTSError(
            "ElevenLabs is not configured. Set the API key via POST /api/tts/config "
            "{\"provider_secrets\": {\"elevenlabs\": {\"api_key\": \"...\"}}} or the "
            "ELEVENLABS_API_KEY environment variable.")
    if len(text) > MAX_TEXT_CHARS:
        raise TTSError(f"text exceeds {MAX_TEXT_CHARS} characters")
    path = f"/v1/text-to-speech/{voice_id}"
    payload = json.dumps({"text": text, "model_id": model_id}).encode("utf-8")
    with _GENERATION_SEM:
        try:
            conn = http.client.HTTPSConnection("api.elevenlabs.io", timeout=120)
            conn.request("POST", path, body=payload, headers={
                "xi-api-key": key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
                "Content-Length": str(len(payload)),
            })
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
        except OSError as exc:
            raise TTSError(f"ElevenLabs request failed: {exc}")
        if resp.status != 200:
            raise TTSError(f"ElevenLabs synthesis failed: HTTP {resp.status}")
    return body, "audio/mpeg", None


# ---------------------------------------------------------------------------
# Unified generation entry point used by the HTTP routes
# ---------------------------------------------------------------------------

def generate(text, studio="apollo", preset_slug=None, voice_id=None,
             speed=None, lang=None, response_format=None, model_id=None):
    """Generate speech for a studio through the resolved provider.

    Returns ``(audio_bytes, content_type, sample_rate, meta)``.
    ``model_id`` resolution order: explicit argument -> per-studio config ->
    preset -> global default. Values are validated against the verified
    catalog before being sent to a provider.
    """
    provider, preset, cfg = effective_provider(studio, preset_slug)
    studio_pref = cfg["studios"].get(studio or "") or {}
    resolved_model = _validate_model_id(
        provider,
        model_id or studio_pref.get("model_id") or (preset or {}).get("model_id")
        or cfg.get("global_default_model_id"))
    # Voice precedence lives in resolve_voice(): the picker's saved studio /
    # global selection always beats the legacy preset's voice, a preset voice
    # never leaks across providers, and stale cross-provider selections are
    # skipped against the live voice list. The preset itself is kept for
    # styling (speed/lang/output) — never as a voice override.
    resolved_voice, _ = resolve_voice(
        provider, studio=studio, preset_slug=preset_slug, preset=preset,
        explicit_voice_id=voice_id, cfg=cfg)
    if provider == "elevenlabs":
        voice = resolved_voice or "pNInz6obpgDQGcFmaJgB"
        chosen_model = resolved_model or "eleven_multilingual_v2"
        audio, ctype, rate = elevenlabs_speech(text, voice, model_id=chosen_model)
        return audio, ctype, rate, {"provider": "elevenlabs",
                                    "preset": preset_slug,
                                    "voice_id": voice,
                                    "model_id": chosen_model}
    if provider == "fish":
        voice = resolved_voice
        chosen_model = resolved_model or os.environ.get("FISH_AUDIO_MODEL", "s2.1-pro")
        audio, ctype, rate = fish_audio_speech(
            text, voice, model_id=chosen_model)
        return audio, ctype, rate, {"provider": "fish",
                                    "preset": preset_slug,
                                    "voice_id": voice,
                                    "model_id": chosen_model,
                                    "endpoint_verified_on_this_machine": True}
    # kokoro (default)
    voice = resolved_voice or "bm_george"
    studio_speed = studio_pref.get("speed")
    spd = float(speed if speed is not None
                else (preset or {}).get("speed", studio_speed or 1.0))
    language = lang or (preset or {}).get("lang")
    fmt = response_format or (preset or {}).get("output") or "wav"
    audio, ctype, rate = kokoro_speech(text, voice_id=voice, speed=spd,
                                       lang=language, response_format=fmt)
    return audio, ctype, rate, {
        "provider": "kokoro",
        "preset": preset_slug,
        "voice_id": voice,
        "model_id": resolved_model or "kokoro-v1.0",
        "speed": spd,
        "lang": language,
        "sample_rate": rate,
    }


# ---------------------------------------------------------------------------
# Saved voiceovers (studios keep generated audio under data/tts/audio)
# ---------------------------------------------------------------------------

_AUDIO_MAX_BYTES = 200 * 1024 * 1024  # prune the audio dir beyond 200 MB


def save_audio(audio_bytes, name=None, meta=None, content_type=None):
    """Persist generated audio and return a UI-facing record."""
    if not audio_bytes:
        raise TTSError("no audio to save")
    ctype = content_type or (meta or {}).get("content_type") or ""
    ext = ".mp3" if "mpeg" in ctype or "mp3" in ctype else ".wav"
    slug = re.sub(r"[^a-z0-9-_]+", "-", str(name or "voiceover").lower()).strip("-") or "voiceover"
    filename = f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}{ext}"
    os.makedirs(AUDIO_DIR, exist_ok=True)
    path = os.path.join(AUDIO_DIR, filename)
    with open(path, "wb") as f:
        f.write(audio_bytes)
    _prune_audio_dir()
    record = {
        "file": filename,
        "url": f"/api/tts/audio/{filename}",
        "bytes": len(audio_bytes),
        "content_type": ctype or ("audio/wav" if ext == ".wav" else "audio/mpeg"),
        "sample_rate": (meta or {}).get("sample_rate"),
        "provider": (meta or {}).get("provider"),
        "voice_id": (meta or {}).get("voice_id"),
        "saved_at": _now(),
    }
    return record


def _prune_audio_dir():
    try:
        entries = []
        total = 0
        for name in os.listdir(AUDIO_DIR):
            p = os.path.join(AUDIO_DIR, name)
            if os.path.isfile(p):
                st = os.stat(p)
                entries.append((st.st_mtime, st.st_size, p))
                total += st.st_size
        if total <= _AUDIO_MAX_BYTES:
            return
        entries.sort()
        for _, size, p in entries:
            if total <= _AUDIO_MAX_BYTES * 0.8:
                break
            os.unlink(p)
            total -= size
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Service lifecycle (delegates to scripts/kokoro_service.py — single process)
# ---------------------------------------------------------------------------

def service_control(action, wait_s=90):
    if action not in ("start", "stop", "restart", "status", "health"):
        raise TTSError(f"unknown service action: {action}")
    script = os.path.join(dennis_config.DATA_DIR, "..", "scripts", "kokoro_service.py")
    script = os.path.normpath(script)
    if not os.path.isfile(script):
        raise TTSError("service manager script not found")
    cmd = [sys.executable, script, action]
    env = dict(os.environ)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=max(120, wait_s + 30), env=env)
    except subprocess.TimeoutExpired:
        raise TTSError("service control timed out")
    out = proc.stdout.strip() or proc.stderr.strip() or "{}"
    try:
        parsed = json.loads(out)
    except ValueError:
        parsed = {"raw": out}
    parsed["action_requested"] = action
    parsed["ok"] = proc.returncode == 0
    return parsed


# ---------------------------------------------------------------------------
# Status summary for the UI / tests
# ---------------------------------------------------------------------------

def status():
    cfg = load_config()
    health = kokoro_health()
    reachable = health is not None
    if health is not None:
        # Normalize schema variants across local Kokoro service builds so the
        # frontend can always read health.model_loaded / voice_count.
        health = dict(health)
        health.setdefault(
            "model_loaded",
            bool(health.get("loaded") or health.get("model_loaded")
                 or health.get("status") == "ready"))
        health.setdefault("voice_count",
                          health.get("voice_count") or health.get("voices") or 0)
    # Provider usability must reflect live state, never a hardcoded True:
    # kokoro = local service reachable; cloud = key configured.
    providers_view = {}
    for pid, slot in PROVIDERS.items():
        entry = dict(slot)
        entry["default"] = (pid == cfg["global_default_provider"])
        if pid == "kokoro":
            entry["enabled"] = reachable
            entry["available"] = reachable
        elif pid == "elevenlabs":
            entry["enabled"] = bool(_elevenlabs_key())
            entry["available"] = entry["enabled"]
        elif pid == "fish":
            entry["enabled"] = bool(_fish_key())
            entry["available"] = entry["enabled"]
        else:
            entry["enabled"] = True
            entry["available"] = True
        providers_view[pid] = entry
    return {
        "providers": providers_view,
        "global_default_provider": cfg["global_default_provider"],
        "global_default_model_id": cfg.get("global_default_model_id"),
        "global_default_voice_id": cfg.get("global_default_voice_id"),
        "studios": cfg["studios"],
        "presets": sorted(cfg["presets"].keys()),
        "apollo": cfg["apollo"],
        "models": MODEL_CATALOG,
        "kokoro": {
            "url": KOKORO_BASE_URL,
            "reachable": reachable,
            "healthy": reachable,
            "loaded": bool((health or {}).get("loaded", (health or {}).get("model_loaded"))),
            "status": (health or {}).get("status"),
            "model": (health or {}).get("model_version") or (health or {}).get("model"),
            "voice_count": (health or {}).get("voice_count") or (health or {}).get("voices") or 0,
            "error": (health or {}).get("error") or (health or {}).get("load_error"),
            "health": health or {},
        },
        "elevenlabs": elevenlabs_status(),
        "fish": fish_audio_status(),
    }


class TTSError(Exception):
    """Raised for invalid TTS requests or provider failures."""
