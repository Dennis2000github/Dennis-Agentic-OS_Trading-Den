"""Async Studio job store and poller.

Provider generation is asynchronous. Submitting returns a provider request id;
a background poller advances the job with real provider states. There is no
synthetic percentage: the UI shows the provider state plus elapsed time, and an
indeterminate indicator only while the request is genuinely queued/running.

Jobs are persisted to ``data/studio/jobs.json`` so the library and history
survive a restart. A job interrupted before the provider accepted it is marked
failed rather than silently resubmitted.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import config
from . import studio_contracts as contracts
from . import studio_media

STUDIO_DIR = os.path.join(config.DATA_DIR, "studio")
JOBS_FILE = os.path.join(STUDIO_DIR, "jobs.json")

MAX_JOBS = 200
MAX_FINISHED = 150
POLL_MIN = 2.0
POLL_MAX = 15.0
POLL_WORKERS = 4
MAX_POLL_FAILURES = 8


class JobError(Exception):
    def __init__(self, code, message, status=400, retryable=False):
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable
        super().__init__(message)

    def as_error(self):
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


def _now():
    return time.time()


class JobStore:
    def __init__(self, path=JOBS_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._jobs = {}
        self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print("[studio] could not read jobs: %s" % exc)
            return
        items = data.get("jobs") if isinstance(data, dict) else None
        if isinstance(items, list):
            for job in items:
                if isinstance(job, dict) and job.get("id"):
                    self._jobs[job["id"]] = job

    def _persist_locked(self):
        directory = os.path.dirname(self.path) or STUDIO_DIR
        os.makedirs(directory, mode=0o700, exist_ok=True)
        payload = {"version": 1, "jobs": list(self._jobs.values())}
        tmp = self.path + ".tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            print("[studio] could not persist jobs: %s" % exc)

    def _prune_locked(self):
        if len(self._jobs) <= MAX_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.get("state") in contracts.TERMINAL_STATES),
            key=lambda j: j.get("finished_at") or j.get("created_at") or 0,
        )
        for job in finished[: max(0, len(self._jobs) - MAX_FINISHED)]:
            self._jobs.pop(job["id"], None)

    def create(self, **fields):
        now = _now()
        job = {
            "id": "stj_" + uuid.uuid4().hex[:16],
            "state": "queued",
            "raw_state": None,
            "error": None,
            "outputs": [],
            "cancel_requested": False,
            "submitting": True,
            "poll_failures": 0,
            "poll_interval": POLL_MIN,
            "next_poll_at": now + POLL_MIN,
            "created_at": now,
            "created_iso": config.now_iso(),
            "updated_at": now,
            "submitted_at": None,
            "finished_at": None,
            "provider_request_id": None,
            "status_url": None,
            "cancel_url": None,
        }
        job.update(fields)
        with self._lock:
            self._prune_locked()
            self._jobs[job["id"]] = job
            self._persist_locked()
            return dict(job)

    def update(self, job_id, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.update(fields)
            job["updated_at"] = _now()
            self._persist_locked()
            return dict(job)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def delete(self, job_id):
        with self._lock:
            if job_id not in self._jobs:
                return False
            self._jobs.pop(job_id, None)
            self._persist_locked()
            return True

    def list(self, limit=50, state=None):
        with self._lock:
            jobs = list(self._jobs.values())
        if state:
            allowed = set(state.split(","))
            jobs = [j for j in jobs if j.get("state") in allowed]
        jobs.sort(key=lambda j: j.get("created_at") or 0, reverse=True)
        return [dict(j) for j in jobs[: max(1, min(int(limit or 50), MAX_JOBS))]]

    def active(self):
        with self._lock:
            return [
                dict(j) for j in self._jobs.values()
                if j.get("state") not in contracts.TERMINAL_STATES
            ]

    def find_active(self, request_hash):
        with self._lock:
            for job in self._jobs.values():
                if job.get("request_hash") == request_hash and job.get("state") not in contracts.TERMINAL_STATES:
                    return dict(job)
        return None


def public_job(job, now=None):
    """Add computed fields; never mutate the stored record."""
    if not job:
        return None
    now = now or _now()
    started = job.get("submitted_at") or job.get("created_at") or now
    finished = job.get("finished_at")
    data = dict(job)
    data["elapsed_seconds"] = round(max(0.0, (finished or now) - started), 1)
    data["age_seconds"] = round(max(0.0, now - (job.get("created_at") or now)), 1)
    data["terminal"] = job.get("state") in contracts.TERMINAL_STATES
    data["can_cancel"] = job.get("state") == "queued"
    return data


class StudioRunner:
    def __init__(self, registry, store=None, poll_min=POLL_MIN, poll_max=POLL_MAX):
        self.registry = registry
        self.store = store or JobStore()
        self.poll_min = poll_min
        self.poll_max = poll_max
        self._stop = threading.Event()
        self._monitor = None
        self._monitor_lock = threading.Lock()
        self._polling = set()
        self._polling_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=POLL_WORKERS, thread_name_prefix="studio-poll")

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        with self._monitor_lock:
            if self._monitor and self._monitor.is_alive():
                return self
            self._stop.clear()
            self._monitor = threading.Thread(target=self._loop, daemon=True, name="studio-monitor")
            self._monitor.start()
        return self

    def shutdown(self):
        self._stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def recover(self):
        """Resume polling after a restart; never auto-resubmit a lost submit."""
        for job in self.store.active():
            if job.get("submitting") and not job.get("provider_request_id"):
                self.store.update(
                    job["id"],
                    state="failed",
                    submitting=False,
                    finished_at=_now(),
                    error={
                        "code": "interrupted",
                        "message": "The server restarted before the provider accepted this request. Submit it again.",
                        "retryable": True,
                    },
                )
            else:
                self.store.update(job["id"], next_poll_at=_now())
        self.start()

    # -- submission --------------------------------------------------------
    def submit(self, provider_id, model_id, params, auto_download=True):
        provider = self.registry.get(provider_id)
        model = provider.model(model_id)
        summary = contracts.derive_summary(model)
        if not summary.get("parameterized"):
            raise JobError("invalid_model", "This model is not a generation workflow.")
        clean = contracts.validate_params(model, params)
        digest = contracts.request_hash(provider_id, model_id, clean)
        existing = self.store.find_active(digest)
        if existing:
            return public_job(existing), True

        job = self.store.create(
            provider=provider_id,
            model_id=model_id,
            model_label=model.get("label") or model_id,
            kind=model.get("kind") or "video",
            mode=model.get("mode"),
            params=contracts.redact(clean),
            prompt=str(clean.get("prompt") or "")[:500],
            request_hash=digest,
            idempotency_key="dennis-" + uuid.uuid4().hex,
            auto_download=bool(auto_download),
        )
        self.start()
        threading.Thread(
            target=self._submit, args=(job["id"], clean), daemon=True, name="studio-submit"
        ).start()
        return public_job(job), False

    def _submit(self, job_id, params):
        job = self.store.get(job_id)
        if not job:
            return
        provider = self.registry.get(job["provider"])
        model = provider.model(job["model_id"])
        if job.get("cancel_requested"):
            self.store.update(
                job_id, state="cancelled", submitting=False, finished_at=_now(),
                raw_state="cancelled",
            )
            return
        try:
            result = provider.submit(model, params, job["idempotency_key"])
        except Exception as exc:
            error = self._error(exc)
            self.store.update(
                job_id,
                state="failed",
                submitting=False,
                finished_at=_now(),
                error=error,
                raw_state=None,
            )
            return
        state, error = contracts.map_state(job["provider"], result.get("raw_state"))
        self.store.update(
            job_id,
            state=state,
            raw_state=result.get("raw_state"),
            error=error,
            submitting=False,
            submitted_at=_now(),
            provider_request_id=result.get("provider_request_id"),
            status_url=result.get("status_url"),
            cancel_url=result.get("cancel_url"),
            next_poll_at=_now() + self.poll_min,
            poll_interval=self.poll_min,
        )
        if state in contracts.TERMINAL_STATES:
            self.store.update(job_id, finished_at=_now())
            return
        # A cancel may have arrived while the submission was in flight.
        latest = self.store.get(job_id)
        if latest and latest.get("cancel_requested") and state == "queued":
            try:
                self.cancel(job_id)
            except Exception:
                pass

    # -- polling -----------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            now = _now()
            for job in self.store.active():
                if job.get("submitting") or job.get("state") in contracts.TERMINAL_STATES:
                    continue
                if (job.get("next_poll_at") or 0) > now:
                    continue
                with self._polling_lock:
                    if job["id"] in self._polling:
                        continue
                    self._polling.add(job["id"])
                self._executor.submit(self._poll, job["id"])
            self._stop.wait(0.05 if self.poll_min < 1.0 else 1.0)

    def _poll(self, job_id):
        try:
            job = self.store.get(job_id)
            if not job or job.get("state") in contracts.TERMINAL_STATES or job.get("submitting"):
                return
            provider = self.registry.get(job["provider"])
            try:
                result = provider.status(job)
            except Exception as exc:
                error = self._error(exc)
                failures = int(job.get("poll_failures") or 0) + 1
                interval = min(self.poll_max, max(self.poll_min, float(job.get("poll_interval") or self.poll_min) * 2))
                if error.get("retryable") and failures < MAX_POLL_FAILURES:
                    self.store.update(
                        job_id,
                        poll_failures=failures,
                        poll_interval=interval,
                        next_poll_at=_now() + interval,
                        error=error,
                    )
                    return
                self.store.update(
                    job_id,
                    state="failed",
                    finished_at=_now(),
                    poll_failures=failures,
                    error=error,
                )
                return

            state = result.get("state")
            if state not in contracts.STATES:
                state = "failed"
            error = result.get("error") if state in ("failed", "cancelled") else None
            outputs = result.get("outputs") or []
            fields = {
                "state": state,
                "raw_state": result.get("raw_state"),
                "error": error,
                "poll_failures": 0,
                "poll_interval": self.poll_min,
                "next_poll_at": _now() + self.poll_min,
            }
            if state == "succeeded":
                fields["outputs"] = self._store_outputs(job, outputs)
                fields["finished_at"] = _now()
            elif state in ("failed", "cancelled"):
                fields["finished_at"] = _now()
            self.store.update(job_id, **fields)
        finally:
            with self._polling_lock:
                self._polling.discard(job_id)

    def _store_outputs(self, job, outputs):
        records = []
        for output in outputs[:8]:
            item = dict(output)
            item["media_id"] = None
            item["download_error"] = None
            if job.get("auto_download", True) and item.get("url"):
                try:
                    record = studio_media.add_from_url(
                        item["url"],
                        kind=item.get("kind") or job.get("kind"),
                        meta={
                            "provider": job.get("provider"),
                            "model_id": job.get("model_id"),
                            "job_id": job.get("id"),
                            "title": (job.get("model_label") or "Generation")
                            + (" — " + job["prompt"][:60] if job.get("prompt") else ""),
                        },
                    )
                    item["media_id"] = record["id"]
                except studio_media.MediaError as exc:
                    item["download_error"] = str(exc)
            records.append(item)
        return records

    # -- control -----------------------------------------------------------
    def cancel(self, job_id):
        job = self.store.get(job_id)
        if not job:
            raise JobError("job_not_found", "Job not found.", 404)
        if job.get("state") in contracts.TERMINAL_STATES:
            raise JobError("job_finished", "This job has already finished.", 409)
        if job.get("state") == "running":
            raise JobError(
                "cancel_not_allowed",
                "Generation has started and can no longer be cancelled.",
                409,
            )
        if job.get("submitting"):
            self.store.update(job_id, cancel_requested=True)
            return public_job(self.store.get(job_id))
        provider = self.registry.get(job["provider"])
        try:
            provider.cancel(job)
        except Exception as exc:
            raise self._error(exc)
        updated = self.store.update(
            job_id,
            state="cancelled",
            raw_state="cancelled",
            finished_at=_now(),
        )
        return public_job(updated)

    def delete(self, job_id):
        job = self.store.get(job_id)
        if not job:
            raise JobError("job_not_found", "Job not found.", 404)
        if job.get("state") not in contracts.TERMINAL_STATES:
            raise JobError("job_active", "Cancel the job before deleting it.", 409)
        self.store.delete(job_id)
        return {"deleted": job_id}

    @staticmethod
    def _error(exc):
        if hasattr(exc, "as_error"):
            return exc.as_error()
        if isinstance(exc, JobError):
            return exc.as_error()
        return {
            "code": "unexpected_error",
            "message": contracts.redact_text(str(exc) or exc.__class__.__name__),
            "retryable": False,
        }
