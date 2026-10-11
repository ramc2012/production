"""Background jobs: a small thread pool with progress and cooperative cancellation, tracked in
the ``job`` table. No Redis or Celery to install: one process, ``settings.workers`` threads.

A job function receives a ``JobContext``; it calls ``ctx.progress(stage, fraction)`` now and then,
which also raises ``JobCancelled`` once someone asked to cancel the job.
"""
from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Dict, Optional

from ..db import session_scope
from ..models import Job, now

log = logging.getLogger("flowtwin.jobs")


class JobCancelled(Exception):
    pass


class JobContext:
    def __init__(self, job_id: int):
        self.job_id = job_id
        self._last = -1.0

    def cancelled(self) -> bool:
        with session_scope() as db:
            j = db.get(Job, self.job_id)
            return bool(j and j.cancel_requested)

    def progress(self, stage: str, fraction: float) -> None:
        with session_scope() as db:
            j = db.get(Job, self.job_id)
            if j is None:
                return
            if j.cancel_requested:
                raise JobCancelled()
            j.stage = stage[:120]
            j.progress = max(0.0, min(1.0, float(fraction)))


class JobManager:
    def __init__(self, workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="flowtwin-job")
        self.futures: Dict[int, Future] = {}
        self.lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[JobContext], Optional[dict]], params: Optional[dict] = None,
               created_by: str = "system") -> int:
        with session_scope() as db:
            j = Job(kind=kind, params=params or {}, created_by=created_by)
            db.add(j)
            db.flush()
            job_id = j.id
        fut = self.pool.submit(self._run, job_id, fn)
        with self.lock:
            self.futures[job_id] = fut
        return job_id

    def _run(self, job_id: int, fn) -> None:
        with session_scope() as db:
            j = db.get(Job, job_id)
            if j.cancel_requested:
                j.status, j.finished_at = "cancelled", now()
                return
            j.status, j.started_at = "running", now()
        try:
            result = fn(JobContext(job_id)) or {}
            status, error = "done", ""
        except JobCancelled:
            result, status, error = {}, "cancelled", "Cancelled by user"
        except Exception as e:  # noqa: BLE001 - a job failure must never kill the worker
            log.exception("job %s failed", job_id)
            result, status, error = {}, "failed", f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=6)}"
        with session_scope() as db:
            j = db.get(Job, job_id)
            j.status, j.result, j.error, j.finished_at = status, result, error[:20000], now()
            if status == "done":
                j.progress, j.stage = 1.0, "done"

    def cancel(self, job_id: int) -> bool:
        with session_scope() as db:
            j = db.get(Job, job_id)
            if j is None or j.status in ("done", "failed", "cancelled"):
                return False
            j.cancel_requested = True
        return True

    def wait(self, job_id: int, timeout: Optional[float] = None) -> None:
        with self.lock:
            fut = self.futures.get(job_id)
        if fut is not None:
            fut.result(timeout=timeout)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)


def recover_interrupted() -> None:
    """At start-up: jobs and runs left 'running' by a stopped server are marked failed."""
    from ..models import Run
    with session_scope() as db:
        for j in db.query(Job).filter(Job.status.in_(["queued", "running"])):
            j.status, j.error, j.finished_at = "failed", "Server restarted while the job was running", now()
        for r in db.query(Run).filter(Run.status.in_(["queued", "running"])):
            r.status, r.log, r.finished_at = "failed", (r.log or "") + "\nServer restarted during the run", now()


_manager: Optional[JobManager] = None


def manager() -> JobManager:
    global _manager
    if _manager is None:
        from ..settings import get_settings
        _manager = JobManager(get_settings().workers)
    return _manager


def set_manager(m: Optional[JobManager]) -> None:
    global _manager
    _manager = m
