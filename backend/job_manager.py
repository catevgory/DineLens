"""Minimal single-worker job manager with cooperative cancellation.

This is a teaching/demo skeleton, not the final analyzer. It shows where
cancellation checks belong so the real collector and analysis pipeline can be
plugged in without changing the API contract.

State machine:
    queued -> collecting -> analysing -> done
       |          |             |
       +----------+-------------+--> cancelled
                  |
                  +--> needs_operator_attention (reserved)
                  +--> failed
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


QUEUED = "queued"
COLLECTING = "collecting"
ANALYSING = "analysing"
DONE = "done"
FAILED = "failed"
NEEDS_OPERATOR = "needs_operator"
CANCELLED = "cancelled"

ACTIVE_STATES = frozenset({QUEUED, COLLECTING, ANALYSING})
TERMINAL_STATES = frozenset({DONE, FAILED, CANCELLED})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobCancelled(Exception):
    """Raised inside the worker when a cancellation request is observed."""


@dataclass
class Job:
    id: str
    store: str
    state: str = QUEUED
    cancel_requested: bool = False
    progress: str = "queued"
    error: str | None = None
    report: dict | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    finished_at: str | None = None

    def snapshot(self) -> dict:
        """Return a JSON-safe copy for the API/frontend."""
        return asdict(self)


class JobManager:
    """One worker thread, one job at a time.

    Cancellation is cooperative:
      - queued jobs become cancelled immediately;
      - collecting/analysing jobs get a flag and the worker stops at the next
        checkpoint;
      - a cancelled job never produces a report or history entry.
    """

    def __init__(
        self,
        *,
        collect_steps: int = 5,
        analyse_steps: int = 5,
        step_delay: float = 0.4,
    ) -> None:
        if collect_steps < 1 or analyse_steps < 1:
            raise ValueError("collect_steps and analyse_steps must be >= 1")
        if step_delay < 0:
            raise ValueError("step_delay must be >= 0")

        self._collect_steps = collect_steps
        self._analyse_steps = analyse_steps
        self._step_delay = step_delay

        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    # ---------------------------------------------------------------- lifecycle
    def start_worker(self) -> None:
        """Start the single background worker. Safe to call twice."""
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="dinelens-worker",
                daemon=True,
            )
            self._worker.start()

    def shutdown(self, timeout: float = 2.0) -> None:
        """Ask the worker to stop and wait briefly for it."""
        self._stop.set()
        self._queue.put(None)
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=timeout)

    # ------------------------------------------------------------------- public
    def create_job(self, store: str) -> dict:
        store = (store or "").strip()
        if not store:
            raise ValueError("store is required")

        job = Job(id=uuid.uuid4().hex[:12], store=store)
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return job.snapshot()

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at)
            return [job.snapshot() for job in jobs]

    def check_cancel(self, job_id: str) -> None:
        """Public checkpoint for the real collector and analyzer.

        Call this between pages, scrolls, analysis batches or model calls.
        It raises ``JobCancelled`` once a cancellation has been requested.
        Never call it in the middle of a blocking request you cannot abort;
        finish that request first, then check.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"unknown job: {job_id}")
            if job.cancel_requested or job.state == CANCELLED:
                raise JobCancelled()

    def request_cancel(self, job_id: str) -> dict:
        """Request cancellation.

        Returns {"ok": True, "job": ...} when accepted (or already cancelled),
        and {"ok": False, "reason": ..., "job": ...} when the job is finished
        or unknown.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return {"ok": False, "reason": "not_found", "job": None}

            if job.state == CANCELLED:
                return {"ok": True, "job": job.snapshot()}

            if job.state == QUEUED:
                job.cancel_requested = True
                job.state = CANCELLED
                job.progress = "cancelled"
                job.finished_at = _now()
                return {"ok": True, "job": job.snapshot()}

            if job.state in (COLLECTING, ANALYSING):
                job.cancel_requested = True
                job.progress = "cancelling..."
                return {"ok": True, "job": job.snapshot()}

            return {
                "ok": False,
                "reason": f"job is already {job.state}",
                "job": job.snapshot(),
            }

    # ------------------------------------------------------------------ worker
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if job_id is None:
                break
            self._run_job(job_id)

    def _check_cancel(self, job: Job) -> None:
        self.check_cancel(job.id)

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.state != QUEUED or job.cancel_requested:
                return
            job.state = COLLECTING
            job.started_at = _now()
            job.progress = f"collecting 0/{self._collect_steps}"

        try:
            # Replace this block with the real collector. Check cancellation
            # between pages / scrolls, never inside a single blocking call.
            for step in range(1, self._collect_steps + 1):
                self._check_cancel(job)
                time.sleep(self._step_delay)
                self._check_cancel(job)
                with self._lock:
                    job.progress = f"collecting {step}/{self._collect_steps}"

            with self._lock:
                job.state = ANALYSING
                job.progress = f"analysing 0/{self._analyse_steps}"

            # Replace this block with the real cleaning/screening/scoring
            # pipeline. Check cancellation between batches or stages.
            for step in range(1, self._analyse_steps + 1):
                self._check_cancel(job)
                time.sleep(self._step_delay)
                self._check_cancel(job)
                with self._lock:
                    job.progress = f"analysing {step}/{self._analyse_steps}"

            # Commit point: no report/history if a cancel arrived before this.
            with self._lock:
                if job.cancel_requested:
                    raise JobCancelled()
                job.state = DONE
                job.progress = "done"
                job.report = {
                    "store": job.store,
                    "summary": "minimal demo report; replace with real analysis",
                }
                job.finished_at = _now()

        except JobCancelled:
            with self._lock:
                job.state = CANCELLED
                job.cancel_requested = True
                job.progress = "cancelled"
                job.report = None
                job.finished_at = _now()

        except Exception as exc:  # keep the worker alive for the next job
            with self._lock:
                job.state = FAILED
                job.error = str(exc)
                job.progress = "failed"
                job.finished_at = _now()
