"""Tiny background-job manager (threads + progress polling)."""
import logging
import threading
import time
import traceback
import uuid
from typing import Callable, Dict

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
        self.created = time.time()
        self._cancel = threading.Event()

    def set(self, p: float):
        self.progress = max(self.progress, min(1.0, float(p)))
        if self._cancel.is_set():
            raise Cancelled()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self):
        self._cancel.set()

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "progress": round(self.progress, 4),
                "result": self.result if self.status == "done" else None, "error": self.error}


JOBS: Dict[str, Job] = {}


def start(kind: str, fn: Callable[[Job], object], project: str = "") -> Job:
    job = Job(kind, project)
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
    # prune old jobs
    now = time.time()
    for k in [k for k, j in JOBS.items() if j.status != "running" and now - j.created > 3600]:
        JOBS.pop(k, None)
    return job
