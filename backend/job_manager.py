"""Single-worker job manager with pipeline injection and persistent results.

The application injects collection, analysis and storage services. The original
timed constructor remains available for existing cancellation tests and callers.

State machine:
    queued -> collecting -> analysing -> done
       |          |             |
       +----------+-------------+--> cancelled
                  |
                  +--> needs_operator
                  +--> failed
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
import logging
from dataclasses import asdict, dataclass, field, replace
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


class NeedsOperator(Exception):
    """The configured source needs human intervention before processing."""


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
    owner_id: str = ""
    branch: dict | None = None
    report_id: str | None = None
    operator_note: str | None = None

    def snapshot(self) -> dict:
        """Return a JSON-safe copy for the API/frontend."""
        result = asdict(self)
        result.pop("owner_id")
        result.pop("operator_note")
        return result


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
        collector=None,
        analyser=None,
        storage=None,
    ) -> None:
        if collect_steps < 1 or analyse_steps < 1:
            raise ValueError("collect_steps and analyse_steps must be >= 1")
        if step_delay < 0:
            raise ValueError("step_delay must be >= 0")

        self._collect_steps = collect_steps
        self._analyse_steps = analyse_steps
        self._step_delay = step_delay
        self._collector, self._analyser, self.storage = collector, analyser, storage
        self._process_lock = None

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
            if self.storage:
                from .storage import WorkerLock
                self._process_lock = WorkerLock(self.storage.path)
                self._process_lock.acquire()
                try:
                    self.storage.cleanup()
                    self._jobs = {data["id"]: Job(**data) for data in self.storage.load_jobs()}
                    self._queue = queue.Queue()
                    for job in self._jobs.values():
                        if job.state in ACTIVE_STATES:
                            if job.cancel_requested:
                                job.state, job.progress, job.finished_at = CANCELLED, "cancelled", _now()
                            else:
                                job.state, job.progress = QUEUED, "recovered after restart"
                                self._queue.put(job.id)
                            self._save(job)
                except BaseException:
                    self._process_lock.release()
                    raise
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._worker_loop,
                name="dinelens-worker",
                daemon=True,
            )
            self._worker.start()

    def status(self) -> dict:
        """Small read-only snapshot for the local control panel."""
        with self._lock:
            counts: dict[str, int] = {}
            for job in self._jobs.values():
                counts[job.state] = counts.get(job.state, 0) + 1
            return {
                "worker_alive": bool(self._worker and self._worker.is_alive()),
                "queue_size": self._queue.qsize(),
                "jobs": counts,
            }

    def shutdown(self, timeout: float = 2.0) -> None:
        """Ask the worker to stop and wait briefly for it."""
        self._stop.set()
        self._queue.put(None)
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=timeout)
        if self._process_lock and not (worker and worker.is_alive()):
            self._process_lock.release()

    # ------------------------------------------------------------------- public
    def _save(self, job):
        if self.storage:
            self.storage.save_job(asdict(job))

    def _snapshot(self, job):
        result = job.snapshot()
        if self.storage and job.report_id:
            result["report"] = self.storage.report(job.report_id, job.owner_id)
        return result

    def create_job(self, store: str, *, owner_id="", branch=None, idempotency_key=None) -> dict:
        store = (store or "").strip()
        if not store:
            raise ValueError("store is required")

        with self._lock:
            request_hash = None
            if self.storage:
                from .storage import token_hash, encode
                request_hash = token_hash(encode({"store": store, "branch": branch}))
                existing = self.storage.existing_job(owner_id, idempotency_key, request_hash)
                if existing:
                    return self._snapshot(self._jobs[existing])
            job = Job(id=uuid.uuid4().hex, store=store, owner_id=owner_id, branch=branch)
            if self.storage:
                self.storage.save_job(asdict(job), key=idempotency_key, request_hash=request_hash)
            self._jobs[job.id] = job
        self._queue.put(job.id)
        return self._snapshot(job)

    def get_job(self, job_id: str, owner_id=None) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot(job) if job and (owner_id is None or job.owner_id == owner_id) else None

    def list_jobs(self, owner_id=None) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at)
            return [self._snapshot(job) for job in jobs if owner_id is None or job.owner_id == owner_id]

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
            if self._stop.is_set():
                raise InterruptedError("Worker shutting down")

    def request_cancel(self, job_id: str, owner_id=None) -> dict:
        """Request cancellation.

        Returns {"ok": True, "job": ...} when accepted (or already cancelled),
        and {"ok": False, "reason": ..., "job": ...} when the job is finished
        or unknown.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or (owner_id is not None and job.owner_id != owner_id):
                return {"ok": False, "reason": "not_found", "job": None}

            if job.state == CANCELLED:
                return {"ok": True, "job": self._snapshot(job)}

            if job.state == QUEUED:
                job.cancel_requested = True
                job.state = CANCELLED
                job.progress = "cancelled"
                job.finished_at = _now()
                self._save(job)
                return {"ok": True, "job": self._snapshot(job)}

            if job.state in (COLLECTING, ANALYSING):
                job.cancel_requested = True
                job.progress = "cancelling..."
                self._save(job)
                return {"ok": True, "job": self._snapshot(job)}

            return {
                "ok": False,
                "reason": f"job is already {job.state}",
                "job": self._snapshot(job),
            }

    # ------------------------------------------------------------------ worker
    def _worker_loop(self) -> None:
        last_cleanup = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    job_id = self._queue.get(timeout=0.2)
                except queue.Empty:
                    if self.storage and time.monotonic() - last_cleanup >= 60:
                        try:
                            self.storage.cleanup()
                        except Exception:
                            logging.exception("DineLens retention cleanup failed")
                        last_cleanup = time.monotonic()
                    continue
                if job_id is None:
                    break
                self._run_job(job_id)
                if self.storage:
                    try:
                        self.storage.cleanup()
                    except Exception:
                        logging.exception("DineLens retention cleanup failed")
        finally:
            if self._process_lock:
                self._process_lock.release()

    def _progress(self, job, stage):
        self.check_cancel(job.id)
        with self._lock:
            job.progress = stage
            self._save(job)

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
            self._save(job)

        try:
            # Reuse collected input when recovering an unfinished analysis.
            rows = self.storage.checkpoint(job.id) if self.storage else None
            if self._collector:
                if rows is None:
                    rows = self._collector(job, lambda: self.check_cancel(job.id))
                    self._check_cancel(job)
                    if self.storage:
                        self.storage.checkpoint(job.id, rows)
            else:
                for step in range(1, self._collect_steps + 1):
                    self._check_cancel(job)
                    time.sleep(self._step_delay)
                    self._check_cancel(job)
                    with self._lock:
                        job.progress = f"collecting {step}/{self._collect_steps}"

            with self._lock:
                job.state = ANALYSING
                job.progress = f"analysing 0/{self._analyse_steps}"
                self._save(job)

            # The application injects the configured analysis engine here.
            if self._analyser:
                report = self._analyser(rows, job.branch, cancel_check=lambda: self.check_cancel(job.id),
                                        progress=lambda stage: self._progress(job, stage))
                report["source_mode"] = getattr(self._collector, "source_mode", "live")
                report["sources"] = [job.branch["source"]]
            else:
                for step in range(1, self._analyse_steps + 1):
                    self._check_cancel(job)
                    time.sleep(self._step_delay)
                    self._check_cancel(job)
                    with self._lock:
                        job.progress = f"analysing {step}/{self._analyse_steps}"
                report = {"store": job.store, "summary": "minimal demo report; replace with real analysis"}

            # Commit point: no report/history if a cancel arrived before this.
            with self._lock:
                if job.cancel_requested:
                    raise JobCancelled()
                completed = replace(job, state=DONE, progress="done", finished_at=_now(), report=report)
                if self.storage:
                    completed.report_id = self.storage.complete(asdict(completed), report)
                    completed.report = None
                self._jobs[job.id] = completed

        except JobCancelled:
            with self._lock:
                job.state = CANCELLED
                job.cancel_requested = True
                job.progress = "cancelled"
                job.report = None
                job.finished_at = _now()
                self._save(job)

        except InterruptedError:
            # A shutdown leaves durable work queued for the next process.
            with self._lock:
                job.state, job.progress = QUEUED, "waiting for restart"
                self._save(job)

        except NeedsOperator as exc:
            self._finish_error(job, NEEDS_OPERATOR, str(exc))

        except Exception as exc:  # keep the worker alive for the next job
            self._finish_error(job, FAILED, str(exc))

    def _finish_error(self, job, state, message):
        with self._lock:
            job.state = CANCELLED if job.cancel_requested else state
            job.progress = job.state
            job.error = None if job.cancel_requested else message
            job.report = None
            job.finished_at = _now()
            self._save(job)
