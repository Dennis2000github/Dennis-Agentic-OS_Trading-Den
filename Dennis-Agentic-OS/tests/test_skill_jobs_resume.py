"""Tests for persisted, resumable Skill Builder jobs.

Run:  ../.venv/bin/python -m unittest tests.test_skill_jobs_resume -v
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import skill_jobs


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def stored_status(path):
    try:
        with open(path, encoding="utf-8") as handle:
            jobs = json.load(handle).get("jobs") or []
        return jobs[0].get("status") if jobs else None
    except (OSError, ValueError, IndexError):
        return None


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sb-jobs-")
        self.store = os.path.join(self.tmp, "jobs.json")
        self.gates = []

    def tearDown(self):
        for gate in self.gates:
            gate.set()
        time.sleep(0.05)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gate(self):
        gate = threading.Event()
        self.gates.append(gate)
        return gate

    def test_unfinished_job_becomes_interrupted_after_restart(self):
        runner = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        gate = self._gate()

        def work(job):
            job.set_stage("Video Reverse Engineering", "Analyzing", 30)
            gate.wait(5)
            return {"draft": {"name": "x"}}

        snapshot, reused = runner.submit(
            "generate", "key-1", work, stages=["Validating source",
                                              "Video Reverse Engineering"],
            request={"action": "generate", "urls": ["https://youtu.be/x"],
                     "mode": "video", "objective": "reference"})
        self.assertFalse(reused)
        self.assertTrue(wait_for(lambda: os.path.isfile(self.store)))
        self.assertTrue(wait_for(
            lambda: stored_status(self.store) == "running"))

        # A new process reads the same store: unfinished work is interrupted,
        # never silently resurrected, and its request is kept for resubmission.
        restarted = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        loaded = restarted.get(snapshot["id"])
        self.assertEqual(loaded["status"], "interrupted")
        self.assertIn("Interrupted", loaded["detail"])
        self.assertEqual(loaded["request"]["mode"], "video")
        self.assertEqual(loaded["request"]["urls"], ["https://youtu.be/x"])
        self.assertEqual(restarted.resume_request(snapshot["id"])["request"],
                         loaded["request"])

    def test_finished_job_survives_restart(self):
        runner = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        snapshot, _reused = runner.submit(
            "analyze", "key-2", lambda job: {"ok": True},
            stages=["Validating source", "Completed"],
            request={"action": "analyze", "urls": ["https://example.com"]})
        self.assertTrue(wait_for(
            lambda: runner.get(snapshot["id"])["status"] == "done"))
        self.assertTrue(wait_for(lambda: stored_status(self.store) == "done"))
        restarted = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        loaded = restarted.get(snapshot["id"])
        self.assertEqual(loaded["status"], "done")
        self.assertEqual(loaded["result"], {"ok": True})

    def test_cancel_is_persisted(self):
        runner = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        gate = self._gate()
        snapshot, _ = runner.submit(
            "generate", "key-3", lambda job: gate.wait(5),
            stages=["Validating source", "Completed"])
        self.assertTrue(runner.cancel(snapshot["id"]))
        self.assertTrue(wait_for(
            lambda: runner.get(snapshot["id"])["status"] == "cancelled"))
        restarted = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        self.assertEqual(restarted.get(snapshot["id"])["status"], "cancelled")

    def test_running_jobs_are_deduplicated(self):
        runner = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        gate = self._gate()
        first, reused = runner.submit("generate", "same", lambda job: gate.wait(5),
                                      stages=["Validating source"])
        second, reused2 = runner.submit("generate", "same", lambda job: None,
                                        stages=["Validating source"])
        self.assertFalse(reused)
        self.assertTrue(reused2)
        self.assertEqual(first["id"], second["id"])

    def test_pruning_bounds_the_store(self):
        runner = skill_jobs.JobRunner(max_parallel=2, store_path=self.store)
        ids = []
        for index in range(6):
            snapshot, _ = runner.submit(
                "analyze", "key-%d" % index, lambda job: {"i": 1},
                stages=["Validating source"])
            ids.append(snapshot["id"])
        self.assertTrue(wait_for(
            lambda: all(runner.get(i)["status"] == "done" for i in ids)))
        for index in range(skill_jobs.MAX_JOBS + 5):
            runner.submit("analyze", "extra-%d" % index, lambda job: None,
                          stages=["Validating source"])
        self.assertLessEqual(len(runner.list(limit=1000)), skill_jobs.MAX_JOBS + 1)

    def test_snapshot_contract_is_serialisable(self):
        job = skill_jobs.Job("generate", "k", ["Validating source", "Completed"],
                             request={"urls": ["u"]})
        job.set_stage("Completed", "done", 100)
        snapshot = job.snapshot()
        json.dumps(snapshot)
        for key in ("id", "kind", "key", "status", "stage", "stage_index",
                    "stages", "detail", "percent", "elapsed_seconds",
                    "started_at", "finished_at", "result", "error",
                    "cancelled", "request"):
            self.assertIn(key, snapshot)
        self.assertEqual(snapshot["percent"], 100)

    def test_unknown_job_raises(self):
        runner = skill_jobs.JobRunner(max_parallel=1, store_path=self.store)
        with self.assertRaises(skill_jobs.JobError):
            runner.cancel("sbj_missing")
        with self.assertRaises(skill_jobs.JobError):
            runner.resume_request("sbj_missing")


if __name__ == "__main__":
    unittest.main()
