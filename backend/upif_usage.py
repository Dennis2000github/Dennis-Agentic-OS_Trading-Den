"""UPIF usage ledger (Phase 11): funding source per operation.

Every generation records *what paid for it* so FREE ONLY enforcement and
audit agree. Estimated or unknown pricing is never marked zero.

Funding sources:
  included_daily_allowance, included_monthly_allowance, promotional_credit,
  earned_reward_credit, user_funded_balance, purchased_prepaid_credit,
  subscription_entitlement, paid_api_billing, none
"""

from __future__ import annotations

import json
import os
import threading
import time

from . import config

LEDGER_FILE = os.path.join(config.DATA_DIR, "upif_usage.json")
_STORE_VERSION = 1
_LOCK = threading.RLock()

FUNDING_SOURCES = (
    "included_daily_allowance", "included_monthly_allowance",
    "promotional_credit", "earned_reward_credit", "user_funded_balance",
    "purchased_prepaid_credit", "subscription_entitlement", "paid_api_billing",
    "none",
)


def _ensure_dir():
    os.makedirs(os.path.dirname(LEDGER_FILE), exist_ok=True)


def _read():
    try:
        with open(LEDGER_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"version": _STORE_VERSION, "records": []}


def _write(data):
    _ensure_dir()
    tmp = LEDGER_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, LEDGER_FILE)
    os.chmod(LEDGER_FILE, 0o600)


class UsageLedger:
    """Append-only usage record with FREE ONLY audit support."""

    def __init__(self):
        self._records = []
        self._loaded = False

    def _ensure(self):
        if self._loaded:
            return
        with _LOCK:
            if self._loaded:
                return
            self._records = _read().get("records", [])
            self._loaded = True

    def record(self, job_id, provider_id, model_id, operation, entitlement,
               funding_source, estimated_cost=None, actual_cost=None,
               cost_unit="", note=""):
        if funding_source not in FUNDING_SOURCES:
            funding_source = "none"
        entry = {
            "job_id": job_id,
            "provider_id": provider_id,
            "model_id": model_id,
            "operation": operation,
            "entitlement": entitlement,
            "funding_source": funding_source,
            "estimated_cost": estimated_cost,
            "actual_cost": actual_cost,
            "cost_unit": cost_unit,
            "note": note,
            "at": time.time(),
        }
        with _LOCK:
            self._ensure()
            self._records.append(entry)
            if len(self._records) > 10000:
                self._records = self._records[-5000:]
            _write({"version": _STORE_VERSION, "records": self._records})
        return entry

    def records(self, provider_id=None, model_id=None, funding_source=None,
                limit=100):
        self._ensure()
        with _LOCK:
            rows = list(self._records)
        if provider_id:
            rows = [r for r in rows if r["provider_id"] == provider_id]
        if model_id:
            rows = [r for r in rows if r["model_id"] == model_id]
        if funding_source:
            rows = [r for r in rows if r["funding_source"] == funding_source]
        rows.reverse()
        return rows[:limit]

    def total_cost(self, provider_id=None, model_id=None):
        rows = self.records(provider_id=provider_id, model_id=model_id,
                            limit=100000)
        total = 0.0
        for row in rows:
            try:
                total += float(row.get("actual_cost") or 0)
            except (TypeError, ValueError):
                pass
        return total

    def reset(self):
        with _LOCK:
            self._records = []
            _write({"version": _STORE_VERSION, "records": []})


USAGE = UsageLedger()
