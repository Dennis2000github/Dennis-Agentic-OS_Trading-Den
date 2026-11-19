"""UPIF entitlement ledger: verified free quota, balances and funding source.

The ledger is the single place the generation router asks "may this model run
for free *right now*?". It never trusts a model name, a website claim or an
unverified balance:

* Every entitlement entry has a ``verified_at`` timestamp and a TTL.
* Pollinations distinguishes purchased Pollen from reward/quest Pollen; only
  reward-funded Pollen is FREE VERIFIED. If the API cannot distinguish the
  origin, the entry is UNKNOWN and FREE ONLY refuses it.
* Replicate free runs are counted per model and marked exhausted when the
  account's free allocation is spent.
* Hugging Face distinguishes HF-routed included credits from custom upstream
  provider keys.
* Puter is USER-FUNDED unless an explicit user authorization exists.
* PicassoIA is PAID SUBSCRIPTION unless an active Infinite plan is verified.

The ledger is persisted so exhaustion survives a restart.
"""

from __future__ import annotations

import json
import os
import threading
import time

from . import config
from .upif_contracts import (
    ENT_FREE_TRIAL, ENT_FREE_VERIFIED, ENT_PAID_API,
    ENT_PAID_SUBSCRIPTION, ENT_UNKNOWN, ENT_USER_FUNDED, FREE_ENTITLEMENTS,
    VERIFY_TTL_SHORT,
)

LEDGER_FILE = os.path.join(config.DATA_DIR, "upif_entitlements.json")
_STORE_VERSION = 1
_LOCK = threading.RLock()


def _ensure_dir():
    os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)


def _read():
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"version": _STORE_VERSION, "entries": {}, "spend": {}}


def _write(data):
    _ensure_dir()
    tmp = LEDGER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, LEDGER_FILE)
    os.chmod(LEDGER_FILE, 0o600)


def _entry(model_id):
    return {
        "model_id": model_id,
        "entitlement": ENT_UNKNOWN,
        "verified_at": 0.0,
        "ttl": VERIFY_TTL_SHORT,
        "remaining": None,
        "unit": "",
        "funding_source": "none",
        "origin": "",
        "note": "",
        "paid_required": False,
        "exhausted": False,
        "expires_at": 0.0,
    }


class EntitlementLedger:
    """Persistent per-model entitlement + spend tracking."""

    def __init__(self):
        self._data = {"version": _STORE_VERSION, "entries": {}, "spend": {}}
        self._loaded = False

    def _ensure(self):
        if self._loaded:
            return
        with _LOCK:
            if self._loaded:
                return
            self._data = _read()
            self._loaded = True

    # -- writes ------------------------------------------------------------
    def record(self, model_id, entitlement, remaining=None, unit="",
               funding_source="none", origin="", note="", paid_required=False,
               ttl=VERIFY_TTL_SHORT, expires_at=0.0):
        self._ensure()
        with _LOCK:
            entry = self._data["entries"].setdefault(model_id, _entry(model_id))
            entry.update({
                "entitlement": entitlement,
                "verified_at": time.time(),
                "ttl": ttl,
                "remaining": remaining,
                "unit": unit,
                "funding_source": funding_source,
                "origin": origin,
                "note": note,
                "paid_required": bool(paid_required),
                "expires_at": float(expires_at or 0),
            })
            if entitlement not in FREE_ENTITLEMENTS:
                entry["exhausted"] = True
            _write(self._data)
            return dict(entry)

    def spend(self, model_id, amount, funding_source):
        """Record actual usage of a free/paid entitlement."""
        self._ensure()
        with _LOCK:
            entry = self._data["entries"].setdefault(model_id, _entry(model_id))
            spend = self._data["spend"].setdefault(model_id, {"count": 0, "total": 0.0})
            spend["count"] += 1
            spend["total"] += float(amount or 0)
            spend["last_spend_at"] = time.time()
            spend["funding_source"] = funding_source
            if entry.get("remaining") is not None:
                entry["remaining"] = max(0.0, float(entry["remaining"]) - float(amount or 0))
                if entry["remaining"] <= 0:
                    entry["exhausted"] = True
            _write(self._data)
            return dict(entry)

    def exhaust(self, model_id, reason):
        self._ensure()
        with _LOCK:
            entry = self._data["entries"].setdefault(model_id, _entry(model_id))
            entry["exhausted"] = True
            entry["note"] = reason
            _write(self._data)

    # -- reads ---------------------------------------------------------------
    def entry(self, model_id):
        self._ensure()
        with _LOCK:
            entry = self._data["entries"].get(model_id)
        return dict(entry) if entry else None

    def current(self, model_id):
        """Entitlement now, downgraded to UNKNOWN when the probe expired."""
        entry = self.entry(model_id)
        if not entry:
            return ENT_UNKNOWN
        if entry.get("exhausted"):
            return ENT_UNKNOWN if entry["entitlement"] in FREE_ENTITLEMENTS else entry["entitlement"]
        if time.time() - entry.get("verified_at", 0) > entry.get("ttl", VERIFY_TTL_SHORT):
            return ENT_UNKNOWN
        if entry.get("expires_at") and time.time() > entry["expires_at"]:
            return ENT_UNKNOWN
        return entry["entitlement"]

    def may_run_free(self, model_id):
        """FREE ONLY gate: current entitlement must be in FREE_ENTITLEMENTS."""
        return self.current(model_id) in FREE_ENTITLEMENTS

    def is_exhausted(self, model_id):
        entry = self.entry(model_id)
        return bool(entry and entry.get("exhausted"))

    def summary(self):
        self._ensure()
        with _LOCK:
            return {
                "entries": {k: dict(v) for k, v in self._data["entries"].items()},
                "spend": {k: dict(v) for k, v in self._data["spend"].items()},
            }

    def reset(self):
        with _LOCK:
            self._data = {"version": _STORE_VERSION, "entries": {}, "spend": {}}
            _write(self._data)


LEDGER = EntitlementLedger()
