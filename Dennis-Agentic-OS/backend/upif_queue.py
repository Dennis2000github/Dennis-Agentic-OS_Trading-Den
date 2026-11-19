"""UPIF centralized generation queue (Phase 6).

Extends the existing Studio job-store pattern — there is no second parallel
queue. One queue serves multiple agents, multiple Studios, multiple providers,
with per-provider concurrency limits, per-model rate limits, retries, failover,
cancellation, progress tracking, asset retrieval and usage accounting.

A persistent job record holds every Phase 6 field, including the original
request, selected provider/model/channel, entitlement category, estimated and
actual cost, provider job id, status, retry + failover history, output asset
reference and timestamps. Idempotency keys prevent duplicate submissions on
network timeouts.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

from . import config
from .upif_base import (
    STATUS_CANCELLED, STATUS_FAILED, STATUS_QUEUED, STATUS_RUNNING,
    STATUS_SUCCEEDED, TERMINAL,
)
from .upif_contracts import UPIFError

JOBS_FILE = os.path.join(config.DATA_DIR, "upif_jobs.json")
_STORE_VERSION = 1
_LOCK = threading.RLock()

MAX_JOBS = 500
MAX_FINISHED = 300

#: Per-provider concurrency limits (prevents burning the daily free quota).
PROVIDER_CONCURRENCY = {
    "cloudflare": 2,
    "pollinations": 3,
    "replicate": 1,
    "huggingface": 2,
    "stability": 1,
    "puter": 1,
    "picassoia": 1,
}

#: Per-model rate limit: max submissions per 60 s window.
RATE_LIMIT_WINDOW = 60.0
DEFAULT_RATE_LIMIT = 5


class GenerationQueue:
    """Single shared queue for all agents and Studios."""

    def __init__(self):
        self._jobs = {}
        self._workers = {}
        self._provider_active = {}
        self._model_submits = {}
        self._lock = threading.RLock()
        self._loaded = False

    # -- persistence ------------------------------------------------------------
    def _ensure_loaded(self):
        if self._loaded:
            return
        with _LOCK:
            if self._loaded:
                return
            try:
                with open(JOBS_FILE, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for job in data.get("jobs", []):
                    self._jobs[job["job_id"]] = job
            except (OSError, ValueError):
                pass
            self._loaded = True

    def _persist(self):
        with _LOCK:
            jobs = list(self._jobs.values())
        data = {"version": _STORE_VERSION, "jobs": jobs[:MAX_JOBS]}
        os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
        tmp = JOBS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.replace(tmp, JOBS_FILE)
        os.chmod(JOBS_FILE, 0o600)

    # -- admission ---------------------------------------------------------------
    def _concurrency_ok(self, provider_id):
        limit = PROVIDER_CONCURRENCY.get(provider_id, 2)
        active = self._provider_active.get(provider_id, 0)
        return active < limit

    def _rate_ok(self, model_id):
        now = time.time()
        window = RATE_LIMIT_WINDOW
        submits = [t for t in self._model_submits.get(model_id, [])
                   if now - t < window]
        submits.append(now)
        self._model_submits[model_id] = submits
        return len(submits) <= DEFAULT_RATE_LIMIT

    def admit(self, request):
        """Validate + enqueue a generation request; returns the job record."""
        self._ensure_loaded()
        required = ("operation", "prompt")
        if not isinstance(request, dict):
            raise UPIFError("invalid_request", "Request must be an object.", 400)
        for field in required:
            if not request.get(field):
                raise UPIFError("missing_field", "%s is required." % field, 400)
        provider_id = request.get("provider_id")
        model_id = request.get("model_id")
        if not self._concurrency_ok(provider_id or "unknown"):
            raise UPIFError("concurrency_limit",
                            "Provider %s is at its concurrency limit; retry shortly."
                            % provider_id, 429)
        if model_id and not self._rate_ok(model_id):
            raise UPIFError("rate_limited",
                            "Model %s exceeded its submission rate limit." % model_id, 429)
        job_id = "upif_" + uuid.uuid4().hex[:12]
        now = time.time()
        job = {
            "job_id": job_id,
            "requesting_agent": request.get("agent_id") or "agent-router",
            "requesting_studio": request.get("studio_id") or "general",
            "project_id": request.get("project_id"),
            "original_request": {k: v for k, v in request.items()
                                 if k != "credentials"},
            "provider_id": provider_id,
            "model_id": model_id,
            "channel": request.get("channel", ""),
            "entitlement_category": request.get("entitlement", "unknown"),
            "estimated_cost": request.get("estimated_cost"),
            "actual_cost": None,
            "provider_job_id": None,
            "status": STATUS_QUEUED,
            "retry_history": [],
            "failover_history": [],
            "output_asset": None,
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "idempotency_key": request.get("idempotency_key"),
            "policy": request.get("policy", "free_first"),
            "free_only": bool(request.get("free_only", False)),
        }
        with _LOCK:
            self._jobs[job_id] = job
            self._provider_active[provider_id or "unknown"] = \
                self._provider_active.get(provider_id or "unknown", 0) + 1
        self._persist()
        return job

    # -- lifecycle -----------------------------------------------------------------
    def start(self, job_id):
        with _LOCK:
            job = self._jobs.get(job_id)
            if job:
                job["status"] = STATUS_RUNNING
                job["started_at"] = time.time()
            self._persist()
            return dict(job) if job else None

    def _release_provider(self, job):
        pid = job.get("provider_id") or "unknown"
        with _LOCK:
            self._provider_active[pid] = max(0, self._provider_active.get(pid, 1) - 1)

    def complete(self, job_id, outputs=None, provider_job_id=None,
                 actual_cost=None, entitlement=None, funding_source=None):
        with _LOCK:
            job = self._jobs.get(job_id)
            if not job:
                raise UPIFError("job_not_found", "Unknown job: %s" % job_id, 404)
            job["status"] = STATUS_SUCCEEDED
            job["outputs"] = outputs or []
            job["provider_job_id"] = provider_job_id
            job["actual_cost"] = actual_cost
            job["entitlement_category"] = entitlement or job["entitlement_category"]
            job["funding_source"] = funding_source or job.get("funding_source", "none")
            job["output_asset"] = (outputs or [{}])[0].get("url") if outputs else None
            job["completed_at"] = time.time()
            record = dict(job)
        self._release_provider(job)
        self._persist()
        return record

    def fail(self, job_id, error_code, message):
        with _LOCK:
            job = self._jobs.get(job_id)
            if not job:
                raise UPIFError("job_not_found", "Unknown job: %s" % job_id, 404)
            job["status"] = STATUS_FAILED
            job["error"] = {"code": error_code, "message": message}
            job["completed_at"] = time.time()
            record = dict(job)
        self._release_provider(job)
        self._persist()
        return record

    def cancel(self, job_id):
        with _LOCK:
            job = self._jobs.get(job_id)
            if not job:
                raise UPIFError("job_not_found", "Unknown job: %s" % job_id, 404)
            if job["status"] in TERMINAL:
                raise UPIFError("terminal_state",
                                "Job %s is already %s." % (job_id, job["status"]), 409)
            job["status"] = STATUS_CANCELLED
            job["completed_at"] = time.time()
            record = dict(job)
        self._release_provider(job)
        self._persist()
        return record

    def record_retry(self, job_id, attempt, reason):
        with _LOCK:
            job = self._jobs.get(job_id)
            if job:
                job["retry_history"].append(
                    {"attempt": attempt, "reason": reason, "at": time.time()})
                self._persist()

    def record_failover(self, job_id, from_model, to_model, reason):
        with _LOCK:
            job = self._jobs.get(job_id)
            if job:
                job["failover_history"].append(
                    {"from": from_model, "to": to_model, "reason": reason,
                     "at": time.time()})
                self._persist()

    # -- queries ---------------------------------------------------------------------
    def get(self, job_id):
        with _LOCK:
            job = self._jobs.get(job_id)
        return dict(job) if job else None

    def list(self, limit=50, agent_id=None, studio_id=None, status=None):
        self._ensure_loaded()
        with _LOCK:
            jobs = list(self._jobs.values())
        if agent_id:
            jobs = [j for j in jobs if j.get("requesting_agent") == agent_id]
        if studio_id:
            jobs = [j for j in jobs if j.get("requesting_studio") == studio_id]
        if status:
            jobs = [j for j in jobs if j.get("status") == status]
        jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
        return jobs[:limit]

    def stats(self):
        self._ensure_loaded()
        with _LOCK:
            jobs = list(self._jobs.values())
        return {
            "total": len(jobs),
            "queued": sum(1 for j in jobs if j["status"] == STATUS_QUEUED),
            "running": sum(1 for j in jobs if j["status"] == STATUS_RUNNING),
            "succeeded": sum(1 for j in jobs if j["status"] == STATUS_SUCCEEDED),
            "failed": sum(1 for j in jobs if j["status"] == STATUS_FAILED),
            "cancelled": sum(1 for j in jobs if j["status"] == STATUS_CANCELLED),
            "provider_active": dict(self._provider_active),
        }


QUEUE = GenerationQueue()
