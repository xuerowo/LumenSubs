"""Paths and persisted user settings."""
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
WORK_DIR = ROOT / "workspace"
PROJECTS_DIR = WORK_DIR / "projects"
SETTINGS_FILE = WORK_DIR / "settings.json"
APIKEY_FILE = ROOT / "apikey.txt"

ASR_MODEL = os.environ.get("LUMEN_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
ALIGNER_MODEL = os.environ.get("LUMEN_ALIGNER_MODEL", "Qwen/Qwen3-ForcedAligner-0.6B")

PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "glossary": "",
    "device": "cuda",
    "max_chars": 42,
    "export_dir": "",
}

_lock = threading.Lock()


def _read_apikey_file() -> str:
    try:
        return APIKEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if not s.get("api_key"):
        s["api_key"] = _read_apikey_file()
    return s


def save_settings(patch: dict) -> dict:
    with _lock:
        s = load_settings()
        for k in DEFAULT_SETTINGS:
            if k in patch and patch[k] is not None:
                s[k] = patch[k]
        SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        return s


def public_settings() -> dict:
    s = load_settings()
    key = s.get("api_key", "")
    s["api_key_set"] = bool(key)
    s["api_key"] = (key[:5] + "…" + key[-4:]) if len(key) > 12 else ""
    return s
