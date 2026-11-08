"""Cancellation tests for the minimal job manager.

These tests only use the standard library, so they run before FastAPI is
installed:

    python backend/test_cancel.py
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from job_manager import (  # noqa: E402
    ANALYSING,
    CANCELLED,
    COLLECTING,
    DONE,
    JobCancelled,
    JobManager,
)


def wait_for(manager: JobManager, job_id: str, predicate, timeout: float = 5.0) -> dict:
    """Poll until the job snapshot satisfies ``predicate``."""
    deadline = time.monotonic() + timeout
    last: dict | None = None
    while time.monotonic() < deadline:
        last = manager.get_job(job_id)
        if last is not None and predicate(last):
            return last
        time.sleep(0.005)
    raise AssertionError(f"timeout waiting for job {job_id}; last snapshot: {last}")


class QueuedCancelTests(unittest.TestCase):
    def test_cancel_before_worker_starts(self) -> None:
        manager = JobManager(step_delay=0)
        job = manager.create_job("Test Kitchen")
        self.assertEqual(job["state"], "queued")

        result = manager.request_cancel(job["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["job"]["state"], CANCELLED)
        self.assertEqual(result["job"]["progress"], "cancelled")
        self.assertIsNone(result["job"]["report"])
        self.assertIsNotNone(result["job"]["finished_at"])

        # Cancelling again is idempotent, not an error.
        again = manager.request_cancel(job["id"])
        self.assertTrue(again["ok"])
        self.assertEqual(again["job"]["state"], CANCELLED)

        # The public checkpoint used by a real collector raises immediately.
        with self.assertRaises(JobCancelled):
            manager.check_cancel(job["id"])

    def test_cancel_queued_job_behind_running_job(self) -> None:
        manager = JobManager(collect_steps=100, analyse_steps=20, step_delay=0.01)
        manager.start_worker()
        self.addCleanup(manager.shutdown)

        first = manager.create_job("Running job")
        wait_for(manager, first["id"], lambda j: j["state"] == COLLECTING)

        second = manager.create_job("Queued job")
        self.assertEqual(manager.get_job(second["id"])["state"], "queued")

        result = manager.request_cancel(second["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["job"]["state"], CANCELLED)

        # The running job is unaffected by cancelling another job.
        self.assertEqual(manager.get_job(first["id"])["state"], COLLECTING)

        manager.request_cancel(first["id"])
        wait_for(manager, first["id"], lambda j: j["state"] == CANCELLED)


class RunningCancelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = JobManager(
            collect_steps=100,
            analyse_steps=100,
            step_delay=0.01,
        )
        self.manager.start_worker()
        self.addCleanup(self.manager.shutdown)

    def test_cancel_while_collecting(self) -> None:
        job = self.manager.create_job("Collecting job")
        wait_for(self.manager, job["id"], lambda j: j["state"] == COLLECTING)

        result = self.manager.request_cancel(job["id"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["job"]["cancel_requested"])
        self.assertIn(result["job"]["state"], (COLLECTING, CANCELLED))

        final = wait_for(self.manager, job["id"], lambda j: j["state"] == CANCELLED)
        self.assertIsNone(final["report"])
        self.assertIsNotNone(final["finished_at"])

        # Give a late commit a chance to appear; it must not.
        time.sleep(0.05)
        self.assertEqual(self.manager.get_job(job["id"])["state"], CANCELLED)
        self.assertIsNone(self.manager.get_job(job["id"])["report"])

    def test_cancel_while_analysing(self) -> None:
        job = self.manager.create_job("Analysing job")
        wait_for(self.manager, job["id"], lambda j: j["state"] == ANALYSING)

        result = self.manager.request_cancel(job["id"])
        self.assertTrue(result["ok"])
        self.assertTrue(result["job"]["cancel_requested"])

        final = wait_for(self.manager, job["id"], lambda j: j["state"] == CANCELLED)
        self.assertIsNone(final["report"])

    def test_cancel_after_done_is_rejected(self) -> None:
        manager = JobManager(collect_steps=2, analyse_steps=2, step_delay=0.005)
        manager.start_worker()
        self.addCleanup(manager.shutdown)

        job = manager.create_job("Finished job")
        done = wait_for(manager, job["id"], lambda j: j["state"] == DONE)
        self.assertIsNotNone(done["report"])

        result = manager.request_cancel(job["id"])
        self.assertFalse(result["ok"])
        self.assertIn("already done", result["reason"])
        self.assertEqual(result["job"]["state"], DONE)
        self.assertIsNotNone(result["job"]["report"])

    def test_unknown_job(self) -> None:
        result = self.manager.request_cancel("does-not-exist")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "not_found")
        self.assertIsNone(result["job"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
