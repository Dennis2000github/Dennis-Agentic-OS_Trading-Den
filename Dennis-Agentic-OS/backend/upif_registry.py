"""UPIF provider registry: providers -> services -> integration channels.

A *provider* is the company; a *service* is the particular API; a *model* is
a generation model; a *channel* is the execution mechanism (REST, SDK, MCP,
gateway, local, website). One provider can expose several channels and one
service can host many models — models are never registered as providers.

The registry is dynamic: each adapter reports its discovered models and the
registry merges them over any locally stored overrides. Website-only
services are registered (so the UI can show them) but flagged as not
programmatically executable.
"""

from __future__ import annotations

import json
import os
import threading
import time

from . import config
from .upif_contracts import (
    AVAIL_BOTH, AVAIL_FREE_VERIFIED, AVAIL_FREE_UNKNOWN, AVAIL_PAID_ONLY,
    AVAIL_PROGRAMMATIC, AVAIL_UNAVAILABLE, AVAIL_WEBSITE, CH_MCP,
    CH_NATIVE_REST, CH_SDK, CH_WEBSITE, ENT_FREE_VERIFIED,
    ENTITLEMENTS, UPIFError, effective_entitlement, is_free, new_model_record,
)

REGISTRY_FILE = os.path.join(config.DATA_DIR, "upif_registry.json")
_STORE_VERSION = 1
_LOCK = threading.RLock()

#: Channels an adapter can declare per service.
SERVICE_CHANNELS = (CH_NATIVE_REST, CH_SDK, CH_MCP, CH_WEBSITE)


def _ensure_dir():
    os.makedirs(os.path.dirname(REGISTRY_FILE), exist_ok=True)


def _read_store():
    try:
        with open(REGISTRY_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"version": _STORE_VERSION, "providers": {}, "policies": {}}


def _write_store(data):
    _ensure_dir()
    tmp = REGISTRY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, REGISTRY_FILE)
    os.chmod(REGISTRY_FILE, 0o600)


# ---------------------------------------------------------------------------
# Static provider/service skeleton. Models come from live discovery, so this
# table only records what the spec asked for: provider, service, channels.
# ---------------------------------------------------------------------------
PROVIDERS = {
    "cloudflare": {
        "label": "Cloudflare",
        "services": {
            "workers_ai": {
                "label": "Workers AI",
                "channels": [CH_NATIVE_REST, CH_SDK],
                "website": False,
                "note": "10,000-Neuron daily free allocation; model-specific limits.",
            },
        },
    },
    "pollinations": {
        "label": "Pollinations AI",
        "services": {
            "generation_api": {
                "label": "Generation API",
                "channels": [CH_NATIVE_REST, CH_MCP],
                "website": False,
                "note": "Keyless image generation; Pollen balance must be reward-funded.",
            },
        },
    },
    "replicate": {
        "label": "Replicate",
        "services": {
            "predictions_api": {
                "label": "Predictions API",
                "channels": [CH_NATIVE_REST, CH_SDK],
                "website": False,
                "note": "Try-for-Free collection is limited; per-model eligibility must be verified.",
            },
        },
    },
    "huggingface": {
        "label": "Hugging Face",
        "services": {
            "inference_providers": {
                "label": "Inference Providers",
                "channels": [CH_NATIVE_REST, CH_SDK],
                "website": False,
                "note": "Monthly included credits apply only to HF-routed requests.",
            },
        },
    },
    "stability": {
        "label": "Stability AI",
        "services": {
            "developer_api": {
                "label": "Developer API",
                "channels": [CH_NATIVE_REST],
                "website": False,
                "note": "Initial free credits are a limited trial entitlement.",
            },
        },
    },
    "puter": {
        "label": "Puter",
        "services": {
            "puter_js_ai": {
                "label": "Puter.js AI",
                "channels": [CH_SDK, CH_WEBSITE],
                "website": True,
                "note": "User-Pays execution; the authorized user's entitlement must be checked.",
            },
        },
    },
    "picassoia": {
        "label": "PicassoIA",
        "services": {
            "picassoia_api": {
                "label": "PicassoIA API",
                "channels": [CH_NATIVE_REST, CH_MCP],
                "website": False,
                "note": "Zero-per-prediction pricing needs the Infinite subscription.",
            },
        },
    },
}


class Registry:
    """In-memory view of providers, services, channels and discovered models."""

    def __init__(self):
        self._models = {}          # model_id -> record
        self._adapter_models = {}  # provider_id -> {model_id: record}
        self._lock = threading.RLock()
        self._loaded = False

    # -- loading -----------------------------------------------------------
    def _ensure_loaded(self):
        if self._loaded:
            return
        with _LOCK:
            if self._loaded:
                return
            store = _read_store()
            for provider in store.get("providers", {}).values():
                for model in provider.get("models", []):
                    self._models[model["model_id"]] = model
            self._loaded = True

    # -- registration ------------------------------------------------------
    def register_adapter(self, provider_id, models):
        """Merge an adapter's discovered models into the registry."""
        self._ensure_loaded()
        with self._lock:
            self._adapter_models[provider_id] = {}
            for entry in models or []:
                record = dict(entry)
                model_id = record.get("model_id")
                if not model_id:
                    continue
                record["provider_id"] = provider_id
                self._adapter_models[provider_id][model_id] = record
                self._models[model_id] = record

    def register_local(self, provider_id, service_id, model_id, **overrides):
        """Register a locally known model (tests, offline entries)."""
        record = new_model_record(provider_id, service_id, model_id, **overrides)
        with self._lock:
            self._models[model_id] = record
            self._adapter_models.setdefault(provider_id, {})[model_id] = record
        return record

    # -- queries -----------------------------------------------------------
    def providers(self):
        self._ensure_loaded()
        out = []
        for pid, spec in PROVIDERS.items():
            models = self.models(provider_id=pid)
            channels = set()
            for service in spec["services"].values():
                channels.update(service["channels"])
            out.append({
                "id": pid,
                "label": spec["label"],
                "services": self._services_view(pid, spec["services"]),
                "channels": sorted(channels),
                "model_count": len(models),
                "capabilities": sorted({c for m in models for c in m.get("capabilities", [])}),
                "programmatic": any(m.get("programmatic") for m in models),
                "website": any(s.get("website") for s in spec["services"].values()),
                "connected": self._connected(pid),
                "free_verified": any(is_free(m) for m in models),
                "availability": self._availability(pid, models),
                "last_verified_at": max((m.get("last_verified_at") or 0) for m in models) if models else 0,
            })
        return out

    def _services_view(self, provider_id, services):
        view = []
        for sid, spec in services.items():
            models = [m for m in self.models(provider_id=provider_id)
                      if m.get("service_id") == sid]
            view.append({
                "id": sid,
                "label": spec["label"],
                "channels": list(spec["channels"]),
                "website": spec["website"],
                "note": spec["note"],
                "programmatic": spec["website"] is False,
                "model_count": len(models),
                "capabilities": sorted({c for m in models for c in m.get("capabilities", [])}),
            })
        return view

    def _connected(self, provider_id):
        return any(m.get("auth_status") in ("authenticated", "anonymous", "user_authorized")
                   for m in self.models(provider_id=provider_id))

    def _availability(self, provider_id, models):
        if not models:
            return AVAIL_UNAVAILABLE
        if all(m.get("website_only") for m in models):
            return AVAIL_WEBSITE
        if any(is_free(m) for m in models):
            return AVAIL_FREE_VERIFIED
        if any(m.get("programmatic") for m in models):
            return AVAIL_PROGRAMMATIC
        return AVAIL_FREE_UNKNOWN

    def models(self, provider_id=None, service_id=None, capability=None,
               free_only=False, ttl=0):
        self._ensure_loaded()
        with self._lock:
            entries = list(self._models.values())
        out = []
        for m in entries:
            if provider_id and m.get("provider_id") != provider_id:
                continue
            if service_id and m.get("service_id") != service_id:
                continue
            if capability and capability not in (m.get("capabilities") or []):
                continue
            if free_only and not is_free(m, ttl):
                continue
            out.append(dict(m))
        return out

    def get_model(self, model_id):
        self._ensure_loaded()
        with self._lock:
            record = self._models.get(model_id)
        return dict(record) if record else None

    def find_model(self, provider_id, model_id):
        for m in self.models(provider_id=provider_id):
            if m["model_id"] == model_id:
                return m
        return None

    # -- entitlement verification -----------------------------------------
    def verify_entitlement(self, model_id, entitlement, detail=None,
                           remaining=None, unit="", rate_limit=None,
                           ttl_note=""):
        """Record a live entitlement probe; returns the updated record."""
        with self._lock:
            record = self._models.get(model_id)
            if not record:
                raise UPIFError("model_not_found", "Unknown model: %s" % model_id, 404)
            record["entitlement"] = entitlement
            record["entitlement_verified_at"] = time.time()
            record["last_verified_at"] = time.time()
            record["free_eligible"] = entitlement in (ENT_FREE_VERIFIED,)
            record["available"] = True
            if remaining is not None:
                record["remaining_quota"] = remaining
                record["quota_unit"] = unit
            if rate_limit:
                record["rate_limit"] = rate_limit
            if detail:
                record["entitlement_detail"] = detail
            if ttl_note:
                record["entitlement_ttl_note"] = ttl_note
            return dict(record)

    def mark_unavailable(self, model_id, reason):
        with self._lock:
            record = self._models.get(model_id)
            if record:
                record["available"] = False
                record["exclusion_reason"] = reason

    # -- persistence --------------------------------------------------------
    def save(self):
        self._ensure_loaded()
        with _LOCK:
            data = {"version": _STORE_VERSION, "providers": {}, "policies": {}}
            for pid in PROVIDERS:
                data["providers"][pid] = {
                    "models": self.models(provider_id=pid),
                }
            _write_store(data)

    def reset(self):
        with self._lock:
            self._models.clear()
            self._adapter_models.clear()
            self._loaded = False


REGISTRY = Registry()
