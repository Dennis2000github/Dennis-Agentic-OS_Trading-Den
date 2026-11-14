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
    "browser": {
        "label": "Browser voices (fallback only)",
        "local": True,
        "requires_key": False,
        "fallback_only": True,
    },
}

# ---------------------------------------------------------------------------
# Default configuration — JARVIS preset is persistent and provider-independent
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "version": 1,
    "global_default_provider": "kokoro",
    "studios": {
        "apollo": {"provider": "kokoro", "voice_preset": "jarvis-british-male"},
        "ads": {"provider": "kokoro", "voice_preset": "narrator-en-us"},
        "youtube": {"provider": "kokoro", "voice_preset": "narrator-en-us"},
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
    # The provider registry is exposed on the config object too: the Apollo
    # voice panel and the Settings card both render selects from it.
    cfg.setdefault("providers", {})
    for pid, slot in PROVIDERS.items():
        view = dict(slot)
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
    if provider != "elevenlabs":
        raise TTSError(f"no secret storage defined for provider: {provider}")
    _save_secret("elevenlabs_api_key", str(api_key))


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

def elevenlabs_status():
    key = _elevenlabs_key()
    out = {"configured": bool(key), "quota": None}
    if not key:
        return out
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
             speed=None, lang=None, response_format=None):
    """Generate speech for a studio through the resolved provider.

    Returns ``(audio_bytes, content_type, sample_rate, meta)``.
    """
    provider, preset, _cfg = effective_provider(studio, preset_slug)
    if provider == "elevenlabs":
        audio, ctype, rate = elevenlabs_speech(
            text, voice_id or (preset or {}).get("voice_id") or "pNInz6obpgDQGcFmaJgB")
        return audio, ctype, rate, {"provider": "elevenlabs",
                                    "preset": preset_slug,
                                    "voice_id": voice_id or (preset or {}).get("voice_id")}
    # kokoro (default)
    voice = voice_id or (preset or {}).get("voice_id") or "bm_george"
    cfg = load_config()
    studio_speed = (cfg["studios"].get(studio or "") or {}).get("speed")
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
    # Provider usability: kokoro = local service (always offerable), elevenlabs
    # = a key is configured, browser = frontend fallback engine.
    providers_view = {}
    for pid, slot in PROVIDERS.items():
        entry = dict(slot)
        if pid == "kokoro":
            entry["enabled"] = True
        elif pid == "elevenlabs":
            entry["enabled"] = bool(_elevenlabs_key())
        else:
            entry["enabled"] = True
        providers_view[pid] = entry
    return {
        "providers": providers_view,
        "global_default_provider": cfg["global_default_provider"],
        "studios": cfg["studios"],
        "presets": sorted(cfg["presets"].keys()),
        "apollo": cfg["apollo"],
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
    }


class TTSError(Exception):
    """Raised for invalid TTS requests or provider failures."""
