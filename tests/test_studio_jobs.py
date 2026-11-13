"""Tests for the Studio job store and poller.

A fake provider drives deterministic state sequences, so no network or
credentials are involved.

Run:  python3 -m unittest tests.test_studio_jobs
"""

import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import studio_jobs as sj
from backend import studio_providers as providers

SCHEMA = {
    "type": "object",
    "required": ["prompt"],
    "additionalProperties": False,
    "properties": {"prompt": {"type": "string", "minLength": 1}},
}


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached in %.1fs" % timeout)


class FakeProvider:
    def __init__(self, statuses=None):
        self.statuses = list(statuses or [{
            "state": "queued", "raw_state": "queued", "error": None, "outputs": []
        }])
        self.submitted = []
        self.cancels = 0
        self.submit_error = None

    def model(self, model_id):
        return {
            "id": model_id,
            "label": "Fake model",
            "kind": "video",
            "mode": "text_to_video",
            "generation": True,
            "schema": SCHEMA,
            "endpoint": model_id,
        }

    def submit(self, model, params, idempotency_key):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append({"params": params, "key": idempotency_key})
        return {
            "provider_request_id": "req-1",
            "status_url": None,
            "cancel_url": None,
            "raw_state": "queued",
        }

    def status(self, job):
        item = self.statuses[0] if len(self.statuses) == 1 else self.statuses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def cancel(self, job):
        self.cancels += 1
        return {"state": "cancelled"}


class FakeRegistry:
    def __init__(self, provider):
        self.provider = provider

    def get(self, provider_id):
        if provider_id != "fake":
            raise providers.ProviderError("unknown_provider", "unknown", 404)
        return self.provider


class JobRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = sj.JobStore(os.path.join(self.tmp.name, "jobs.json"))
        self.provider = FakeProvider()
        self.registry = FakeRegistry(self.provider)
        self.runner = sj.StudioRunner(
            self.registry, store=self.store, poll_min=0.01, poll_max=0.02
        )
        self.media_patch = mock.patch.object(
            sj.studio_media,
            "add_from_url",
            side_effect=lambda url, kind=None, meta=None: {
                "id": "med_1", "url": url, "kind": kind or "video"
            },
        )
        self.media_patch.start()

    def tearDown(self):
        self.runner.shutdown()
        self.media_patch.stop()
        self.tmp.cleanup()

    def test_submit_polls_to_success_and_downloads_outputs(self):
        self.provider.statuses = [
            {"state": "queued", "raw_state": "queued", "error": None, "outputs": []},
            {"state": "running", "raw_state": "in_progress", "error": None, "outputs": []},
            {
                "state": "succeeded",
                "raw_state": "completed",
                "error": None,
                "outputs": [{"url": "https://cdn.example.com/v.mp4", "kind": "video"}],
            },
        ]
        job, duplicate = self.runner.submit("fake", "model", {"prompt": "a cat"})
        self.assertFalse(duplicate)
        done = wait_for(lambda: self.store.get(job["id"])["state"] == "succeeded")
        self.assertIsNotNone(done)
        final = self.store.get(job["id"])
        self.assertEqual(final["raw_state"], "completed")
        self.assertEqual(final["outputs"][0]["url"], "https://cdn.example.com/v.mp4")
        self.assertEqual(final["outputs"][0]["media_id"], "med_1")
        self.assertIsNotNone(final["finished_at"])

    def test_duplicate_active_request_is_reused(self):
        job1, dup1 = self.runner.submit("fake", "model", {"prompt": "same"})
        job2, dup2 = self.runner.submit("fake", "model", {"prompt": "same"})
        self.assertFalse(dup1)
        self.assertTrue(dup2)
        self.assertEqual(job1["id"], job2["id"])
        self.assertEqual(len(self.provider.submitted), 1)

    def test_different_params_create_a_new_job(self):
        job1, _ = self.runner.submit("fake", "model", {"prompt": "one"})
        job2, dup = self.runner.submit("fake", "model", {"prompt": "two"})
        self.assertFalse(dup)
        self.assertNotEqual(job1["id"], job2["id"])

    def test_invalid_params_fail_before_submission(self):
        with self.assertRaises(Exception):
            self.runner.submit("fake", "model", {"prompt": "x", "unknown": 1})
        self.assertEqual(self.provider.submitted, [])

    def test_cancel_queued_job(self):
        self.provider.statuses = [{
            "state": "queued", "raw_state": "queued", "error": None, "outputs": []
        }]
        job, _ = self.runner.submit("fake", "model", {"prompt": "x"})
        wait_for(lambda: self.store.get(job["id"])["submitting"] is False)
        cancelled = self.runner.cancel(job["id"])
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(self.provider.cancels, 1)

    def test_cancel_running_job_is_rejected(self):
        self.provider.statuses = [{
            "state": "running", "raw_state": "in_progress", "error": None, "outputs": []
        }]
        job, _ = self.runner.submit("fake", "model", {"prompt": "x"})
        wait_for(lambda: self.store.get(job["id"])["state"] == "running")
        with self.assertRaises(sj.JobError) as ctx:
            self.runner.cancel(job["id"])
        self.assertEqual(ctx.exception.status, 409)

    def test_retryable_status_failures_are_retried_then_fail(self):
        self.provider.statuses = [providers.ProviderError("network_error", "offline", 502, True)]
        job, _ = self.runner.submit("fake", "model", {"prompt": "x"})
        wait_for(lambda: self.store.get(job["id"])["state"] == "failed", timeout=8)
        final = self.store.get(job["id"])
        self.assertEqual(final["poll_failures"], sj.MAX_POLL_FAILURES)
        self.assertTrue(final["error"]["retryable"])

    def test_non_retryable_status_failure_fails_immediately(self):
        self.provider.statuses = [providers.ProviderError("not_found", "gone", 404, False)]
        job, _ = self.runner.submit("fake", "model", {"prompt": "x"})
        wait_for(lambda: self.store.get(job["id"])["state"] == "failed")
        self.assertEqual(self.store.get(job["id"])["poll_failures"], 1)

    def test_submit_error_is_reported_without_leaking_secrets(self):
        self.provider.submit_error = providers.ProviderError(
            "authentication_failed", "bad Key abc:secret", 401
        )
        job, _ = self.runner.submit("fake", "model", {"prompt": "x"})
        wait_for(lambda: self.store.get(job["id"])["state"] == "failed")
        final = self.store.get(job["id"])
        self.assertEqual(final["error"]["code"], "authentication_failed")
        self.assertNotIn("abc:secret", final["error"]["message"])

    def test_delete_requires_terminal_state(self):
        self.provider.statuses = [{
            "state": "running", "raw_state": "in_progress", "error": None, "outputs": []
        }]
        job, _ = self.runner.submit("fake", "model", {"prompt": "x"})
        wait_for(lambda: self.store.get(job["id"])["state"] == "running")
        with self.assertRaises(sj.JobError):
            self.runner.delete(job["id"])

    def test_recover_marks_interrupted_submissions_failed(self):
        interrupted = self.store.create(
            provider="fake", model_id="model", submitting=True, state="queued"
        )
        resumed = self.store.create(
            provider="fake", model_id="model", submitting=False, state="queued",
            provider_request_id="req-9",
        )
        self.runner.recover()
        self.assertEqual(self.store.get(interrupted["id"])["state"], "failed")
        self.assertEqual(
            self.store.get(interrupted["id"])["error"]["code"], "interrupted"
        )
        self.assertNotEqual(self.store.get(resumed["id"])["state"], "failed")

    def test_public_job_exposes_elapsed_and_cancelability(self):
        job = self.store.create(
            provider="fake", model_id="model", created_at=time.time() - 10,
            submitted_at=time.time() - 5,
        )
        public = sj.public_job(job)
        self.assertGreaterEqual(public["elapsed_seconds"], 4)
        self.assertTrue(public["can_cancel"])
        self.assertFalse(public["terminal"])


if __name__ == "__main__":
    unittest.main()
