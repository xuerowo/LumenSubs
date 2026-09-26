"""LumenSubs launcher — starts the local server and opens the app window."""
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def _models_cached() -> bool:
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return all((hub / f"models--Qwen--{m}").exists() for m in ("Qwen3-ASR-1.7B", "Qwen3-ForcedAligner-0.6B"))


if _models_cached():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")


def free_port(start=8765):
    for p in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


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
    for exe in candidates:
        if exe and os.path.exists(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1600,960"])
            return
    webbrowser.open(url)


def main():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    port = int(os.environ.get("LUMEN_PORT") or free_port())
    url = f"http://127.0.0.1:{port}/"
    print(f"\n  LumenSubs 已啟動 → {url}\n")
    threading.Thread(target=lambda: (time.sleep(1.2), open_app_window(url)), daemon=True).start()
    uvicorn.run("app.server:app", host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
