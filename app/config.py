"""Paths and persisted user settings."""
import json
import os
import threading
from pathlib import Path
from urllib.parse import urlparse

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


def _read_file() -> dict:
    try:
        d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(_read_file())
    if not s.get("api_key"):
        s["api_key"] = _read_apikey_file()
    return s


def check_base_url(url: str) -> str:
    """Only https endpoints (plain http is allowed for a local server)."""
    url = (url or "").strip().rstrip("/")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("API 位址格式不正確，例如 https://api.deepseek.com")
    if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("API 位址必須使用 https（避免金鑰以明文傳送）")
    return url


def _clean(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k not in DEFAULT_SETTINGS or v is None:
            continue
        if k == "base_url":
            v = check_base_url(v)
        elif k == "device":
            if v not in ("cuda", "cpu"):
                continue
        elif k == "max_chars":
            try:
                v = max(16, min(120, int(v)))
            except (TypeError, ValueError):
                continue
        elif not isinstance(v, str):
            continue
        out[k] = v
    return out


def save_settings(patch: dict) -> dict:
    """Merge `patch` into settings.json (atomically). The key from apikey.txt
    is never copied into settings.json."""
    with _lock:
        s = _read_file()
        s.update(_clean(patch))
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SETTINGS_FILE)
    return load_settings()


def public_settings() -> dict:
    s = load_settings()
    key = s.get("api_key", "")
    s["api_key_set"] = bool(key)
    s["api_key"] = (key[:5] + "…" + key[-4:]) if len(key) > 12 else ""
    return s
