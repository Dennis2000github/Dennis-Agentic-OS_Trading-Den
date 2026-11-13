"""Small in-process job runner for the Agent Skill Builder.

The OS uses a single local HTTP server, so a bounded thread pool plus a
dictionary of job snapshots is enough.  It gives the UI real progress stages,
prevents duplicate submissions for the same source, and supports cooperative
cancellation between pipeline stages.
"""

import threading
import time
import uuid

MAX_JOBS = 40
MAX_PARALLEL = 2


class JobError(Exception):
    pass


class Job:
    def __init__(self, kind, key, stages):
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
        self._lock = threading.Lock()

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
            }


class JobCancelled(Exception):
    pass


class JobRunner:
    def __init__(self, stages=None, max_parallel=MAX_PARALLEL):
        self._jobs = {}
        self._lock = threading.Lock()
        self._stages = list(stages or [])
        self._slots = threading.Semaphore(max_parallel)

    def submit(self, kind, key, fn, stages=None):
        """Start ``fn(job)`` or return an existing running job for ``key``."""
        with self._lock:
            self._prune_locked()
            for job in self._jobs.values():
                if job.key == key and job.status in ("queued", "running"):
                    return job.snapshot(), True
            job = Job(kind, key, stages or self._stages)
            self._jobs[job.id] = job

        def work():
            acquired = self._slots.acquire(timeout=120)
            if not acquired:
                with job._lock:
                    job.status = "error"
                    job.error = "The job queue is busy. Try again in a moment."
                    job.finished_at = time.time()
                return
            try:
                with job._lock:
                    if job.cancelled:
                        raise JobCancelled()
                    job.status = "running"
                    job.detail = "Starting"
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
            return job.cancel()

    def list(self, limit=20):
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.started_at, reverse=True)
            return [j.snapshot() for j in jobs[:limit]]

    def _prune_locked(self):
        if len(self._jobs) <= MAX_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values()
             if j.status in ("done", "error", "cancelled")),
            key=lambda j: j.finished_at or j.started_at,
        )
        for job in finished[:max(0, len(self._jobs) - MAX_JOBS)]:
            self._jobs.pop(job.id, None)


RUNNER = JobRunner()
