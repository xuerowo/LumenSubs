"""LumenSubs launcher — starts the local server and opens the app window."""
import json
import logging
import logging.handlers
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402
from app.version import VERSION  # noqa: E402

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

# stay offline only when every model file is really there; an interrupted
# download must still be able to finish (see bootstrap.ensure_models)
if config.models_ready():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

LOCK_FILE = config.WORK_DIR / "lumen.lock"
INSTANCE_FILE = config.WORK_DIR / "instance.json"


def free_port(start=8765):
    for p in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def single_instance():
    """Hold an OS lock on workspace/lumen.lock for the life of the process
    (released automatically, even after a crash). Returns the open file, or
    None when another LumenSubs already runs on this workspace."""
    config.ensure_dirs()
    f = open(LOCK_FILE, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def open_app_window(url: str):
    if "--no-browser" in sys.argv:
        return
    candidates = [
        shutil.which("msedge"), shutil.which("chrome"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    env = dict(os.environ)
    env.pop("LUMEN_TOKEN", None)
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1600,960"], env=env)
            return
    webbrowser.open(url)


def reuse_running_instance() -> bool:
    """Another LumenSubs is running: bring up a window of it instead of
    starting a second server on the same data."""
    try:
        inst = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
        port, token = int(inst["port"]), str(inst["token"])
    except (OSError, ValueError, KeyError):
        port, token = 0, ""
    print("\n  LumenSubs 已經在執行中（同時開兩個會互相覆蓋資料）。")
    if port and _port_open(port):
        print("  正在開啟它的視窗…\n")
        if "--no-browser" in sys.argv:
            print(f"  網址：http://127.0.0.1:{port}/#k={token}\n")
        else:
            open_app_window(f"http://127.0.0.1:{port}/#k={token}")
        return True
    print("  請使用已開啟的 LumenSubs 視窗；若找不到，請關閉所有 LumenSubs 主控台視窗後再執行 start.bat。\n")
    return False


def setup_logging():
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(config.LOG_DIR / "lumen.log", maxBytes=2_000_000, backupCount=3,
                                              encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[sh, fh])


def main():
    import uvicorn
    lock = single_instance()
    if lock is None:
        sys.exit(0 if reuse_running_instance() else 1)
    setup_logging()
    port = int(os.environ.get("LUMEN_PORT") or free_port())
    # per-launch secret: every /api call must carry it, so other web pages or
    # local programs cannot drive the app (see app/server.py)
    token = os.environ.get("LUMEN_TOKEN") or secrets.token_urlsafe(24)
    os.environ["LUMEN_PORT"], os.environ["LUMEN_TOKEN"] = str(port), token
    # lets a second start.bat open a window of this instance (same user, same workspace)
    config.write_json(INSTANCE_FILE, {"port": port, "token": token, "pid": os.getpid()}, backup_every=1e9)
    url = f"http://127.0.0.1:{port}/#k={token}"
    print(f"\n  LumenSubs {VERSION} 已啟動 → http://127.0.0.1:{port}")
    if "--no-browser" in sys.argv:
        print(f"  在瀏覽器開啟：{url}\n  （網址含本次啟動的存取金鑰，請勿分享）")
    print(f"  關閉這個視窗即結束 LumenSubs。紀錄檔：{config.LOG_DIR / 'lumen.log'}\n")
    try:
        from app import sysinfo
        for w in sysinfo.warnings(config.load_settings().get("device", "cuda")):
            print(f"  ⚠ {w}")
    except Exception:
        pass
    threading.Thread(target=lambda: (time.sleep(1.2), open_app_window(url)), daemon=True).start()
    try:
        uvicorn.run("app.server:app", host="127.0.0.1", port=port, log_level="warning")
    finally:
        try:
            INSTANCE_FILE.unlink()
        except OSError:
            pass
        lock.close()


if __name__ == "__main__":
    main()
