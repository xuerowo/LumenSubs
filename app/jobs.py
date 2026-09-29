"""Tiny background-job manager (threads + progress polling)."""
import logging
import threading
import time
import traceback
import uuid
from typing import Callable, Dict, Optional

log = logging.getLogger("lumen.jobs")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str, project: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.project = project
        self.status = "running"      # running | done | error | cancelled
        self.progress = 0.0
        self.result = None
        self.error = ""
        self.note = ""               # what the job is waiting for, shown next to the progress
        self.created = time.time()
        self._cancel = threading.Event()

    def set(self, p: float):
        self.progress = max(self.progress, min(1.0, float(p)))
        self.note = ""
        if self._cancel.is_set():
            raise Cancelled()

    def wait_note(self, note: str):
        self.note = note
        if self._cancel.is_set():
            raise Cancelled()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self):
        self._cancel.set()

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 4),
                "result": self.result if self.status == "done" else None, "error": self.error, "note": self.note}


JOBS: Dict[str, Job] = {}
_lock = threading.Lock()


def get(jid: str) -> Optional[Job]:
    with _lock:
        return JOBS.get(jid)


def running(project: str = "", kind: str = ""):
    with _lock:
        return [j for j in JOBS.values() if j.status == "running" and (not project or j.project == project)
                and (not kind or j.kind == kind)]


def cancel_all(project: str = ""):
    for j in running(project):
        j.cancel()


def start(kind: str, fn: Callable[[Job], object], project: str = "") -> Job:
    return run(Job(kind, project), fn)


def run(job: Job, fn: Callable[[Job], object]) -> Job:
    """Register `job` and run fn(job) in a background thread."""
    kind = job.kind
    now = time.time()
    with _lock:
        # prune finished jobs older than an hour
        for k in [k for k, j in JOBS.items() if j.status != "running" and now - j.created > 3600]:
            del JOBS[k]
        JOBS[job.id] = job

    def runner():
        try:
            job.result = fn(job)
            job.progress = 1.0
            job.status = "done"
        except Cancelled:
            job.status = "cancelled"
        except Exception as e:
            log.error("job %s (%s) failed:\n%s", job.id, kind, traceback.format_exc())
            job.status, job.error = ("cancelled", "") if job.cancelled() else ("error", str(e) or e.__class__.__name__)
    threading.Thread(target=runner, daemon=True, name=f"job-{kind}").start()
    return job
