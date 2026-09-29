"""Paths, persisted user settings and crash-safe JSON files."""
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
# user data (projects, settings, logs); LUMEN_WORKSPACE moves it elsewhere
WORK_DIR = Path(os.environ["LUMEN_WORKSPACE"]).resolve() if os.environ.get("LUMEN_WORKSPACE") else ROOT / "workspace"
PROJECTS_DIR = WORK_DIR / "projects"
SETTINGS_FILE = WORK_DIR / "settings.json"
APIKEY_FILE = ROOT / "apikey.txt"
LOG_DIR = WORK_DIR / "logs"
TMP_DIR = WORK_DIR / "tmp"
TRASH_DIR = WORK_DIR / "trash"

ASR_MODEL = os.environ.get("LUMEN_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
ALIGNER_MODEL = os.environ.get("LUMEN_ALIGNER_MODEL", "Qwen/Qwen3-ForcedAligner-0.6B")
VAD_MODEL = "onnx-community/silero-vad"

DEFAULT_SETTINGS = {
    "api_key": "",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "glossary": "",
    "device": "cuda",
    "max_chars": 42,
    "export_dir": "",
    "consent_host": "",           # translation host the user agreed to send transcripts to
}

_lock = threading.Lock()


def ensure_dirs():
    for d in (PROJECTS_DIR, LOG_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- crash-safe files
def replace_retry(src: Path, dst: Path, tries: int = 12):
    """os.replace that survives Windows' transient sharing violations (another
    thread or a virus scanner reading the target at that very moment)."""
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.03 * (i + 1))


def _read_retry(path: Path, tries: int = 8) -> str:
    for i in range(tries):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.03 * (i + 1))
    return ""  # pragma: no cover


def bak_path(path: Path) -> Path:
    return path.with_name(path.name + ".bak")


def read_json(path: Path) -> Tuple[Optional[dict], bool]:
    """(data, recovered). Falls back to the .bak copy when the file is damaged;
    (None, False) when neither exists, raises ValueError when both are unreadable."""
    errors = []
    for i, p in enumerate((path, bak_path(path))):
        try:
            d = json.loads(_read_retry(p))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            errors.append(e)
            continue
        if isinstance(d, dict):
            return d, i == 1
        errors.append(ValueError("not a JSON object"))
    if errors:
        raise ValueError(f"{path.name} 已損毀：{errors[0]}")
    return None, False


def write_json(path: Path, data: dict, indent: Optional[int] = None, backup_every: float = 30.0):
    """Write atomically (unique temp file + fsync + replace) and keep a recent
    copy of the previous version in <name>.bak for crash recovery."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex[:6]}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=indent))
            f.flush()
            os.fsync(f.fileno())
        bak = bak_path(path)
        if path.exists():
            try:
                if not bak.exists() or time.time() - bak.stat().st_mtime > backup_every:
                    btmp = bak.with_name(bak.name + f".{uuid.uuid4().hex[:6]}.tmp")
                    shutil.copyfile(path, btmp)
                    replace_retry(btmp, bak)
            except OSError:
                pass            # the backup is a bonus; never fail the save for it
        replace_retry(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- settings
class SettingsError(ValueError):
    pass


def _read_apikey_file() -> str:
    try:
        return APIKEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _read_file(strict: bool = False) -> dict:
    try:
        d, _ = read_json(SETTINGS_FILE)
    except ValueError as e:
        if strict:
            raise SettingsError(f"無法讀取設定檔（{e}），為避免覆蓋你的設定，這次沒有儲存。請稍後再試。")
        return {}
    return d or {}


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


def api_host(settings: dict) -> str:
    return (urlparse(settings.get("base_url") or DEFAULT_SETTINGS["base_url"]).hostname or "").lower()


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
    """Merge `patch` into settings.json (atomically). A settings file that
    exists but cannot be read is never overwritten. The key from apikey.txt
    is never copied into settings.json."""
    with _lock:
        ensure_dirs()
        s = _read_file(strict=True)
        s.update(_clean(patch))
        write_json(SETTINGS_FILE, s, indent=2, backup_every=0)
    return load_settings()


def public_settings() -> dict:
    s = load_settings()
    key = s.get("api_key", "")
    s["api_key_set"] = bool(key)
    s["api_key"] = (key[:5] + "…" + key[-4:]) if len(key) > 12 else ""
    s["api_host"] = api_host(s)
    return s


# ---------------------------------------------------------------- translation usage
USAGE_FILE = WORK_DIR / "usage.json"
_ulock = threading.Lock()


def add_usage(u: dict):
    """Add one run's token counts to this month's total (workspace/usage.json)."""
    if not u or not u.get("requests"):
        return
    with _ulock:
        try:
            d, _ = read_json(USAGE_FILE)
        except ValueError:
            d = None
        d = d or {}
        m = d.setdefault(time.strftime("%Y-%m"), {})
        for k in ("requests", "prompt", "completion", "cache_hit"):
            m[k] = int(m.get(k, 0)) + int(u.get(k, 0) or 0)
        ensure_dirs()
        write_json(USAGE_FILE, d, indent=1, backup_every=0)


def usage_month() -> dict:
    try:
        d, _ = read_json(USAGE_FILE)
    except ValueError:
        d = None
    return dict((d or {}).get(time.strftime("%Y-%m"), {}))


# ---------------------------------------------------------------- models
def hf_hub_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"


def model_snapshot(repo: str) -> Optional[Path]:
    """Folder of the cached snapshot of `repo` if it is complete (every weight
    shard present and non-empty), else None. Downloads that were interrupted
    leave a snapshot folder behind, so its mere existence proves nothing."""
    base = hf_hub_dir() / ("models--" + repo.replace("/", "--"))
    try:
        rev = (base / "refs" / "main").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    snap = base / "snapshots" / rev
    if not (snap / "config.json").is_file():
        return None
    idx = snap / "model.safetensors.index.json"
    try:
        if idx.is_file():
            shards = set(json.loads(idx.read_text(encoding="utf-8")).get("weight_map", {}).values())
            if not shards or not all(_nonempty(snap / s) for s in shards):
                return None
        elif not any(_nonempty(p) for p in list(snap.glob("*.safetensors")) + list(snap.glob("*.bin"))):
            return None
    except (OSError, ValueError):
        return None
    return snap


def _nonempty(p: Path) -> bool:
    try:
        return p.stat().st_size > 0
    except OSError:
        return False


def vad_model_path() -> Optional[Path]:
    env = os.environ.get("LUMEN_VAD_ONNX")
    if env and os.path.exists(env):
        return Path(env)
    base = hf_hub_dir() / ("models--" + VAD_MODEL.replace("/", "--")) / "snapshots"
    for p in sorted(base.glob("*/onnx/model.onnx")):
        if _nonempty(p):
            return p
    return None


def models_ready() -> bool:
    return all(model_snapshot(m) for m in (ASR_MODEL, ALIGNER_MODEL))
