"""Native OS file dialogs for the local app (Save As / choose folder).

The dialog runs in a short-lived child process so Tk never touches the
server's threads; the window is forced to the foreground.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from .procs import NO_WINDOW, clean_env

_SCRIPT = r'''
import sys, json, tkinter as tk
from tkinter import filedialog
a = json.loads(sys.argv[1])
root = tk.Tk(); root.withdraw()
root.attributes("-topmost", True); root.update(); root.lift(); root.focus_force()
kw = dict(parent=root, title=a.get("title") or None, initialdir=a.get("dir") or None)
if a.get("mode") == "dir":
    p = filedialog.askdirectory(mustexist=True, **kw)
else:
    types = [tuple(t) for t in (a.get("types") or [])] + [("All files", "*.*")]
    p = filedialog.asksaveasfilename(initialfile=a.get("file") or "", defaultextension=a.get("ext") or "",
                                     filetypes=types, confirmoverwrite=True, **kw)
root.destroy()
sys.stdout.write(json.dumps({"path": p or ""}))
'''


class DialogUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def default_dir() -> str:
    for p in (Path.home() / "Videos", Path.home() / "Downloads", Path.home()):
        if p.is_dir():
            return str(p)
    return os.getcwd()


def ask(mode: str = "save", title: str = "", directory: str = "", file: str = "", ext: str = "", types=None) -> str:
    if not available():
        raise DialogUnavailable("無法開啟選擇資料夾視窗：這個 Python 沒有安裝 tcl/tk。請重新安裝 Python 並勾選"
                                "「tcl/tk and IDLE」，或直接在欄位中輸入完整路徑。")
    args = {"mode": mode, "title": title, "dir": directory if directory and os.path.isdir(directory) else default_dir(),
            "file": file, "ext": ext, "types": types or []}
    try:
        r = subprocess.run([sys.executable, "-c", _SCRIPT, json.dumps(args)], capture_output=True, timeout=600,
                           creationflags=NO_WINDOW, env=clean_env())
    except subprocess.TimeoutExpired:
        return ""
    try:
        p = json.loads(r.stdout.decode("utf-8", "ignore") or "{}").get("path", "")
    except ValueError:
        p = ""
    return os.path.normpath(p) if p else ""
