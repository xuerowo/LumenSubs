"""Child processes that must not outlive the app.

On Windows every FFmpeg we start is put into a Job Object created with
KILL_ON_JOB_CLOSE: when the LumenSubs process ends — normally, by closing the
console window, or by a crash — Windows closes the job handle and terminates
the children, so no orphaned encoder keeps the GPU busy or files locked.
"""
import logging
import os
import subprocess
import threading

log = logging.getLogger("lumen.procs")

NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_job = None
_job_lock = threading.Lock()


def _windows_job():
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                       "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    h = k32.CreateJobObjectW(None, None)
    if not h:
        raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000          # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):   # ExtendedLimitInformation
        raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
    return k32, h          # the handle is intentionally never closed: it lives as long as this process


def _bind(proc: subprocess.Popen):
    global _job
    if os.name != "nt":
        return
    with _job_lock:
        if _job is None:
            try:
                _job = _windows_job()
            except Exception as e:          # pragma: no cover - only without the Win32 API
                log.warning("cannot create a job object (%s); child processes may outlive the app", e)
                _job = False
        if not _job:
            return
        k32, h = _job
        if not k32.AssignProcessToJobObject(h, int(proc._handle)):
            log.warning("could not attach process %s to the job object", proc.pid)


def popen(args, **kw) -> subprocess.Popen:
    """subprocess.Popen for helper tools (FFmpeg): no console window, and the
    process dies with the app."""
    kw.setdefault("creationflags", NO_WINDOW)
    proc = subprocess.Popen(args, **kw)
    _bind(proc)
    return proc


def clean_env() -> dict:
    """Environment for child processes, without the per-launch access token."""
    env = dict(os.environ)
    env.pop("LUMEN_TOKEN", None)
    return env
