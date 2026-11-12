"""Small in-process job runner for the Agent Skill Builder.

The OS uses a single local HTTP server, so a bounded thread pool plus a
dictionary of job snapshots is enough.  It gives the UI real progress stages,
prevents duplicate submissions for the same source, and supports cooperative
cancellation between pipeline stages.

Snapshots are persisted to a small JSON file when a store path is supplied.
After a restart, unfinished jobs are marked ``interrupted`` and keep their
original request so the UI can resubmit the exact same work.
"""

import json
import os
import tempfile
import threading
import time
import uuid

MAX_JOBS = 40
MAX_PARALLEL = 2

WORKSPACE = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "agent-skill-builder")
)
DEFAULT_STORE = os.environ.get(
    "DENNIS_SB_JOBS_FILE", os.path.join(WORKSPACE, ".cache", "jobs.json"))


class JobError(Exception):
    pass


class JobCancelled(Exception):
    pass


class Job:
    def __init__(self, kind, key, stages, request=None):
        self.id = "sbj_" + uuid.uuid4().hex[:16]
        self.kind = kind
        self.key = key
        self.stages = list(stages or [])
        self.status = "queued"
        self.stage = self.stages[0] if self.stages else ""
        self.stage_index = 0
        self.detail = "Queued"
        self.percent = 0
        self.started_at = time.time()
        self.finished_at = None
        self.result = None
        self.error = None
        self.cancelled = False
        self.request = request or {}
        self._lock = threading.RLock()
        self._on_change = None

    def set_stage(self, name, detail="", percent=None):
        with self._lock:
            if self.cancelled:
                raise JobCancelled()
            if name not in self.stages:
                self.stages.append(name)
            self.stage = name
            self.stage_index = self.stages.index(name)
            if detail:
                self.detail = detail
            if percent is not None:
                self.percent = max(0, min(100, int(percent)))
            elif self.stages:
                self.percent = int(round(100.0 * self.stage_index / max(1, len(self.stages) - 1)))
            if self._on_change:
                self._on_change()

    def detail_update(self, detail):
        with self._lock:
            self.detail = detail or ""

    def cancel(self):
        with self._lock:
            if self.status in ("done", "error", "cancelled"):
                return False
            self.cancelled = True
            self.status = "cancelled"
            self.detail = "Cancelled"
            self.finished_at = time.time()
            return True

    def snapshot(self):
        with self._lock:
            elapsed = (self.finished_at or time.time()) - self.started_at
            return {
                "id": self.id,
                "kind": self.kind,
                "key": self.key,
                "status": self.status,
                "stage": self.stage,
                "stage_index": self.stage_index,
                "stages": list(self.stages),
                "detail": self.detail,
                "percent": self.percent,
                "elapsed_seconds": round(elapsed, 2),
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "result": self.result,
                "error": self.error,
                "cancelled": self.cancelled,
                "request": self.request or {},
            }

    def to_dict(self):
        return self.snapshot()

    @classmethod
    def from_dict(cls, data):
        data = data if isinstance(data, dict) else {}
        job = cls(data.get("kind") or "generate", data.get("key") or "",
                  data.get("stages") or [], request=data.get("request") or {})
        job.id = data.get("id") or job.id
        job.status = data.get("status") or "queued"
        job.stage = data.get("stage") or ""
        job.stage_index = int(data.get("stage_index") or 0)
        job.detail = data.get("detail") or ""
        job.percent = int(data.get("percent") or 0)
        job.started_at = float(data.get("started_at") or time.time())
        job.finished_at = data.get("finished_at")
        job.result = data.get("result")
        job.error = data.get("error")
        job.cancelled = bool(data.get("cancelled"))
        return job


class JobRunner:
    def __init__(self, stages=None, max_parallel=MAX_PARALLEL, store_path=None):
        self._jobs = {}
        self._lock = threading.RLock()
        self._stages = list(stages or [])
        self._slots = threading.Semaphore(max_parallel)
        self._store = store_path
        self._load()

    def _load(self):
        if not self._store or not os.path.isfile(self._store):
            return
        try:
            with open(self._store, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return
        for item in data.get("jobs") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            job = Job.from_dict(item)
            if job.status in ("queued", "running"):
                job.status = "interrupted"
                job.detail = "Interrupted by restart — resubmit to continue"
                job.finished_at = job.finished_at or time.time()
            self._jobs[job.id] = job

    def _persist_locked(self):
        if not self._store:
            return
        directory = os.path.dirname(self._store)
        os.makedirs(directory, exist_ok=True)
        payload = {"version": 1,
                   "jobs": [job.to_dict() for job in self._jobs.values()]}
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".jobs-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self._store)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def _touch(self):
        with self._lock:
            self._persist_locked()

    def submit(self, kind, key, fn, stages=None, request=None):
        """Start ``fn(job)`` or return an existing running job for ``key``."""
        with self._lock:
            self._prune_locked()
            for job in self._jobs.values():
                if job.key == key and job.status in ("queued", "running"):
                    return job.snapshot(), True
            job = Job(kind, key, stages or self._stages, request=request)
            job._on_change = self._touch
            self._jobs[job.id] = job
            self._persist_locked()

        def work():
            acquired = self._slots.acquire(timeout=120)
            if not acquired:
                with job._lock:
                    job.status = "error"
                    job.error = "The job queue is busy. Try again in a moment."
                    job.finished_at = time.time()
                self._touch()
                return
            try:
                with job._lock:
                    if job.cancelled:
                        raise JobCancelled()
                    job.status = "running"
                    job.detail = "Starting"
                self._touch()
                result = fn(job)
                with job._lock:
                    if job.cancelled:
                        raise JobCancelled()
                    job.status = "done"
                    job.result = result
                    job.percent = 100
                    job.detail = "Completed"
                    job.finished_at = time.time()
            except JobCancelled:
                with job._lock:
                    job.status = "cancelled"
                    job.detail = "Cancelled"
                    job.finished_at = job.finished_at or time.time()
            except Exception as exc:  # user-facing errors become job.error
                with job._lock:
                    job.status = "error"
                    job.error = str(exc) or exc.__class__.__name__
                    job.detail = "Failed"
                    job.finished_at = time.time()
            finally:
                self._touch()
                self._slots.release()

        threading.Thread(target=work, daemon=True, name="skill-job").start()
        return job.snapshot(), False

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise JobError("Job not found")
            result = job.cancel()
            self._persist_locked()
            return result

    def list(self, limit=20):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
            return [j.snapshot() for j in jobs[:limit]]

    def resume_request(self, job_id):
        """Return the stored request for a job so the UI can resubmit it."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise JobError("Job not found")
            return job.snapshot()

    def _prune_locked(self):
        if len(self._jobs) <= MAX_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values()
             if j.status in ("done", "error", "cancelled", "interrupted")),
            key=lambda j: j.finished_at or j.started_at,
        )
        for job in finished[:max(0, len(self._jobs) - MAX_JOBS)]:
            self._jobs.pop(job.id, None)


RUNNER = JobRunner(store_path=DEFAULT_STORE)
