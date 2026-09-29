"""LumenSubs HTTP API + static front-end.

Access control: the server only listens on 127.0.0.1, but a web page in any
browser (DNS rebinding, cross-site requests) or another local program could
still reach it. So every request must name this machine in its Host header,
cross-origin requests are refused, and every /api call must carry the
per-launch token that run.py hands to the app window (X-Lumen-Token header,
or ?t= for media elements and sendBeacon, which cannot send headers).
"""
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, List
from urllib.parse import quote, urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config, fonts, jobs, media, sysinfo
from .asr import ENGINE
from .segmenter import segment
from .translator import TranslateError, Translator, parse_glossary
from .version import VERSION

log = logging.getLogger("lumen")

# the token is read once and removed from the environment, so helper programs
# (FFmpeg, dialogs, players opened from the app) never inherit it
TOKEN = os.environ.pop("LUMEN_TOKEN", "") or ""
if not TOKEN:
    TOKEN = secrets.token_urlsafe(24)
    log.warning("LUMEN_TOKEN not set — open http://127.0.0.1:<port>/#k=%s", TOKEN)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".amr", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".avif"}
SUB_EXT = {".srt", ".vtt", ".ass", ".ssa", ".txt"}
OUT_VIDEO_EXT = {".mp4", ".mkv"}
UPLOAD_FRAME = re.compile(r"^(c\d{5}|blank|bg)\.png$")
# fixed content types for files served to the page (never taken from the Windows registry)
MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
        ".mkv": "video/x-matroska", ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".aac": "audio/aac", ".ogg": "audio/ogg",
        ".opus": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif", ".avif": "image/avif",
        ".json": "application/json", ".bin": "application/octet-stream"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; "
       "frame-ancestors 'none'")
# progress split of /generate: transcription, then translation
TR_SHARE = 0.6
# an upload needs room for the browser's temp copy, the project copy and a preview
UPLOAD_SPACE_FACTOR = 2.2


@asynccontextmanager
async def lifespan(_app):
    _startup()
    yield
    jobs.cancel_all()          # FFmpeg children die with the process anyway (see procs.py)


app = FastAPI(title="LumenSubs", lifespan=lifespan)


# ------------------------------------------------------------------ errors
_EN = {"Not Found": "找不到", "Method Not Allowed": "不支援的操作", "Internal Server Error": "伺服器內部錯誤"}


@app.exception_handler(StarletteHTTPException)
async def http_error(_request: Request, exc: StarletteHTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse({"detail": _EN.get(detail, detail)}, status_code=exc.status_code, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, exc: RequestValidationError):
    return JSONResponse({"detail": "請求內容不完整或格式不正確"}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(_request: Request, exc: Exception):
    log.exception("unhandled error")
    msg = str(exc) or exc.__class__.__name__
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        msg = "磁碟空間不足"
    return JSONResponse({"detail": f"發生未預期的錯誤：{msg[:300]}（詳細記錄在 workspace/logs/lumen.log）"},
                        status_code=500)


# ------------------------------------------------------------------ access control
def _split_host(host: str):
    host = (host or "").strip().lower()
    if host.startswith("["):                       # [::1]:8765
        name, _, rest = host[1:].partition("]")
        return name, rest.lstrip(":")
    name, _, port = host.partition(":")
    return name, port


def _local(name: str, port: str) -> bool:
    want = os.environ.get("LUMEN_PORT", "")
    return name in LOCAL_HOSTS and (not want or port == want)


def _origin_ok(origin: str) -> bool:
    p = urlparse(origin)
    return p.scheme == "http" and _local((p.hostname or "").lower(), str(p.port or ""))


@app.middleware("http")
async def access_guard(request: Request, call_next):
    if not _local(*_split_host(request.headers.get("host", ""))):
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    origin = request.headers.get("origin")
    if origin is not None and not _origin_ok(origin):
        return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    if request.url.path.startswith("/api/"):
        tok = request.headers.get("x-lumen-token") or request.query_params.get("t") or ""
        if not secrets.compare_digest(tok.encode(), TOKEN.encode()):
            return JSONResponse({"detail": "存取金鑰無效，請關閉此視窗並重新執行 start.bat", "code": "token"},
                                status_code=401)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


# ------------------------------------------------------------------ projects
def pdir(pid: str) -> Path:
    if not pid or not all(c.isalnum() or c in "-_" for c in pid):
        raise HTTPException(400, "專案編號不正確")
    d = config.PROJECTS_DIR / pid
    if not d.exists():
        raise HTTPException(404, "專案不存在")
    return d


_plock = threading.RLock()


def _read_project(d: Path):
    """(project, recovered_from_backup); raises HTTPException when unreadable."""
    try:
        p, rec = config.read_json(d / "project.json")
    except ValueError:
        raise HTTPException(500, "專案檔已損毀，也沒有可用的備份")
    if p is None:
        raise HTTPException(404, "專案不存在")
    return p, rec


def load_project(pid: str) -> dict:
    with _plock:
        return _read_project(pdir(pid))[0]


def update_project(pid: str, fn: Callable[[dict], object]) -> dict:
    """Atomic read-modify-write of project.json (fn mutates the dict)."""
    with _plock:
        d = pdir(pid)
        p = _read_project(d)[0]
        fn(p)
        p["updated"] = time.time()
        config.write_json(d / "project.json", p)
        return p


def save_project(pid: str, patch: dict) -> dict:
    return update_project(pid, lambda p: p.update(patch))


def file_url(pid: str, name: str) -> str:
    """URL for a project file, versioned by mtime so replaced media reloads."""
    f = config.PROJECTS_DIR / pid / name
    v = int(f.stat().st_mtime) if f.exists() else 0
    return f"/api/projects/{pid}/files/{quote(name)}?v={v}"


def work_dir(d: Path, kind: str, m: dict) -> Path:
    return d / (m.get("work") or f"{kind}_work")


def _dir_size(d: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


@app.post("/api/projects")
def create_project(body: dict = Body(default={})):
    config.ensure_dirs()
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = config.PROJECTS_DIR / pid
    d.mkdir(parents=True)
    p = {"id": pid, "name": str(body.get("name") or "未命名專案")[:120], "created": time.time(), "updated": time.time(),
         "rev": 0, "media": {}, "state": {}}
    config.write_json(d / "project.json", p)
    return p


@app.get("/api/projects")
def list_projects():
    out = []
    if not config.PROJECTS_DIR.exists():
        return out
    for d in config.PROJECTS_DIR.iterdir():
        f = d / "project.json"
        if not d.is_dir() or not (f.exists() or config.bak_path(f).exists()):
            continue
        try:
            with _plock:
                p, rec = config.read_json(f)
            st = p.get("state") or {}
            mode = st.get("mode", "video")
            med = p.get("media") or {}
            out.append({"id": p.get("id", d.name), "name": p.get("name"), "updated": p.get("updated", 0),
                        "mode": mode, "cues": len(st.get("segs") or []), "recovered": rec,
                        "primary": (med.get(mode) or {}).get("name", ""), "size": _dir_size(d)})
        except Exception as e:
            log.warning("project %s is unreadable: %s", d.name, e)
            out.append({"id": d.name, "name": "（專案檔已損毀，無法開啟）", "updated": d.stat().st_mtime, "mode": "video",
                        "cues": 0, "broken": True, "primary": "", "size": _dir_size(d)})
    out.sort(key=lambda x: -x["updated"])
    return out


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    with _plock:
        p, rec = _read_project(pdir(pid))
    if rec:
        p["recovered"] = True
    return p


@app.put("/api/projects/{pid}")
def put_project(pid: str, body: dict = Body(...)):
    """Save the editor state. `base_rev` is the revision the window started
    from: if another window saved in between, the save is refused (409)
    unless `force` is set, instead of silently overwriting its work."""
    def upd(p):
        rev = int(p.get("rev") or 0)
        if isinstance(body.get("state"), dict):
            base = body.get("base_rev")
            if base is not None and int(base) != rev and not body.get("force"):
                raise HTTPException(409, "此專案已在其他視窗中被修改")
            p["state"] = body["state"]
            p["rev"] = rev + 1
        if "name" in body:
            p["name"] = str(body["name"])[:120]
        # the app has applied a finished job's result → forget it
        if body.get("ack_job") and (p.get("job") or {}).get("id") == body["ack_job"]:
            p["job"] = None
    try:
        p = update_project(pid, upd)
    except HTTPException as e:
        if e.status_code == 409:
            return JSONResponse({"detail": e.detail, "code": "conflict", "rev": load_project(pid).get("rev", 0)},
                                status_code=409)
        raise
    return {"ok": True, "updated": p["updated"], "rev": p.get("rev", 0)}


@app.post("/api/projects/{pid}/save")
async def beacon_save(pid: str, request: Request):
    """sendBeacon target used when the window closes."""
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except ValueError:
        raise HTTPException(400, "資料格式不正確")
    if not isinstance(body, dict):
        raise HTTPException(400, "資料格式不正確")
    return await run_in_threadpool(put_project, pid, body)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    d = pdir(pid)
    for j in jobs.running(pid):
        j.cancel()
    # move it out of the project list first, so a partly failed delete never
    # leaves a half project behind; what cannot be removed now (a file still
    # open in a player) is cleared at the next start
    config.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    trash = config.TRASH_DIR / f"{pid}-{uuid.uuid4().hex[:6]}"
    moved = False
    for _ in range(20):
        try:
            os.replace(d, trash)
            moved = True
            break
        except OSError:
            time.sleep(0.1)          # a cancelled job may still be closing its files
    target = trash if moved else d
    shutil.rmtree(target, ignore_errors=True)
    if not moved and (d / "project.json").exists():
        try:
            (d / "project.json").unlink()
        except OSError:
            raise HTTPException(409, "專案中的檔案正被其他程式使用，請關閉播放器等程式後再刪除")
    return {"ok": True, "leftover": target.exists()}


@app.get("/api/projects/{pid}/files/{name:path}")
def get_file(pid: str, name: str):
    d = pdir(pid)
    f = (d / name).resolve()
    if d.resolve() not in f.parents or not f.is_file() or f.name.startswith("project.json"):
        raise HTTPException(404)
    return FileResponse(f, media_type=MIME.get(f.suffix.lower(), "application/octet-stream"),
                        headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{pid}/exports/{name}")
def get_export(pid: str, name: str, dl: int = 1):
    d = pdir(pid) / "exports"
    f = (d / name).resolve()
    if d.resolve() not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, filename=name if dl else None, media_type=MIME.get(f.suffix.lower(), "application/octet-stream"))


# ------------------------------------------------------------------ local paths
_written = set()          # files this server wrote during this session


def _pkey(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _remember(p):
    _written.add(_pkey(p))


def _remember_dir(path: str):
    """Remember the export folder for next time; never let this fail an export."""
    try:
        config.save_settings({"export_dir": str(Path(path).parent)})
    except Exception as e:
        log.warning("could not remember the export folder: %s", e)


def _is_unc(path: str) -> bool:
    return path.startswith(("\\\\", "//"))


@app.post("/api/reveal")
def reveal(body: dict = Body(default={})):
    """Show an exported file in Explorer, or open it with the default app.
    Only files this app has written in this session can be opened."""
    path = str(body.get("path") or "")
    if not path or _is_unc(path) or _pkey(path) not in _written:
        raise HTTPException(403, "只能開啟本程式輸出的檔案")
    target = Path(path)
    if not target.is_file():
        raise HTTPException(404, "找不到檔案")
    try:
        if os.name == "nt":
            if body.get("open"):
                os.startfile(str(target))
            else:
                subprocess.Popen(["explorer", "/select,", str(target)])
        elif shutil.which("open"):                 # macOS
            subprocess.Popen(["open", str(target)] if body.get("open") else ["open", "-R", str(target)])
        elif shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(target if body.get("open") else target.parent)])
        else:
            raise HTTPException(501, "此系統不支援自動開啟檔案，請手動開啟：" + str(target))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"無法開啟：{e}")
    return {"ok": True}


@app.post("/api/dialog")
def dialog(body: dict = Body(default={})):
    from . import dialogs
    start = str(body.get("dir") or config.load_settings().get("export_dir") or "")
    if _is_unc(start):
        start = ""
    try:
        path = dialogs.ask("dir" if body.get("mode") == "dir" else "save", str(body.get("title", ""))[:100], start,
                           str(body.get("file", ""))[:200], str(body.get("ext", ""))[:10], [])
    except dialogs.DialogUnavailable as e:
        raise HTTPException(501, str(e))
    return {"path": path}


@app.get("/api/default-dir")
def default_export_dir():
    from . import dialogs
    d = config.load_settings().get("export_dir") or ""
    return {"dir": d if d and not _is_unc(d) and os.path.isdir(d) else dialogs.default_dir()}


def _check_target(path: str, exts: set) -> Path:
    if _is_unc(path):
        raise HTTPException(400, "不支援網路路徑，請選擇本機資料夾")
    if not path or not os.path.isabs(path):
        raise HTTPException(400, "請指定完整的輸出路徑")
    t = Path(path)
    if any(c in t.name for c in '<>:"/|?*') or not t.stem.strip():
        raise HTTPException(400, "檔名含有不允許的字元")
    if t.suffix.lower() not in exts:
        raise HTTPException(400, f"副檔名必須是 {'、'.join(sorted(exts))}")
    if not t.parent.is_dir():
        raise HTTPException(400, f"資料夾不存在：{t.parent}")
    return t


@app.post("/api/path/check")
def path_check(body: dict = Body(...)):
    t = _check_target(str(body.get("path", "")), SUB_EXT | OUT_VIDEO_EXT)
    return {"exists": t.exists(), "path": str(t)}


@app.post("/api/export/subtitle")
def save_subtitle(body: dict = Body(...)):
    t = _check_target(str(body.get("path", "")), SUB_EXT)
    if t.exists() and not body.get("overwrite"):
        raise HTTPException(409, "檔案已存在")
    try:
        tmp = t.with_name(f".{t.name}.{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_text(str(body.get("content", "")), encoding="utf-8-sig", newline="")
        config.replace_retry(tmp, t)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise HTTPException(409, f"「{t.name}」正被其他程式使用，請關閉後再試，或改用其他檔名")
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(500, f"無法寫入檔案：{e}")
    _remember(t)
    _remember_dir(str(t))
    return {"path": str(t), "size": t.stat().st_size}


# ------------------------------------------------------------------ media
_inflight = set()          # file names of uploads still being processed (never cleaned up)
_inflight_lock = threading.Lock()


def _cleanup_media(d: Path, kind: str, keep: set):
    """Best effort: files still open elsewhere are removed at next start."""
    with _inflight_lock:
        keep = set(keep) | _inflight
    for f in d.glob(f"{kind}_src*"):
        if f.name not in keep:
            try:
                f.unlink()
            except OSError:
                pass
    for w in d.glob(f"{kind}_work*"):
        if w.name not in keep:
            shutil.rmtree(w, ignore_errors=True)


def _need_space(d: Path, size: int):
    free = shutil.disk_usage(str(d)).free
    need = int(size * UPLOAD_SPACE_FACTOR) + (512 << 20)
    if size and free < need:
        raise HTTPException(507, f"磁碟空間不足：這個檔案需要約 {need / 2**30:.1f} GB 可用空間"
                                 f"（{d.anchor or d} 目前剩 {free / 2**30:.1f} GB），請先清出空間")


@app.post("/api/projects/{pid}/media/check")
def media_check(pid: str, body: dict = Body(...)):
    """Called before an upload starts, so a full disk is reported before the
    user waits for gigabytes to transfer."""
    _need_space(pdir(pid), int(body.get("size") or 0))
    return {"ok": True}


def _set_bg(pid: str, key: str, value):
    def upd(p):
        bg = dict(p.get("bg") or {})
        if value is None:
            bg.pop(key, None)
        else:
            bg[key] = value
        p["bg"] = bg
    try:
        update_project(pid, upd)
    except Exception as e:
        log.warning("could not record background job: %s", e)


@app.post("/api/projects/{pid}/media")
async def upload_media(pid: str, kind: str = Form(...), file: UploadFile = File(...)):
    d = pdir(pid)
    if kind not in ("video", "audio", "image"):
        raise HTTPException(400, "媒體類型不正確")
    ext = Path(file.filename or "").suffix.lower()
    allowed = {"video": VIDEO_EXT, "audio": AUDIO_EXT | VIDEO_EXT, "image": IMAGE_EXT}[kind]
    if ext not in allowed:
        raise HTTPException(400, f"不支援的檔案格式：{ext or '未知'}")
    size = getattr(file, "size", None) or 0
    _need_space(d, size)
    # every upload gets fresh names; the current media is only replaced once
    # the new file has been fully processed
    tag = uuid.uuid4().hex[:8]
    dst = d / f"{kind}_src_{tag}{ext}"
    wd = d / f"{kind}_work_{tag}"
    with _inflight_lock:
        _inflight.update({dst.name, wd.name})

    def release():
        with _inflight_lock:
            _inflight.difference_update({dst.name, wd.name})
    try:
        with open(dst, "wb") as out:
            while True:
                chunk = await file.read(8 << 20)
                if not chunk:
                    break
                await run_in_threadpool(out.write, chunk)
    except OSError as e:
        dst.unlink(missing_ok=True)
        release()
        raise HTTPException(507 if getattr(e, "errno", None) == 28 else 500,
                            "磁碟空間不足，無法儲存檔案" if getattr(e, "errno", None) == 28 else f"無法儲存檔案：{e}")
    except BaseException:
        dst.unlink(missing_ok=True)
        release()
        raise
    name = Path(file.filename or dst.name).name

    def commit(m: dict):
        update_project(pid, lambda p: p.setdefault("media", {}).__setitem__(kind, m))
        release()
        _cleanup_media(d, kind, {m["file"], m.get("work", "")})

    if kind == "image":
        try:
            info = media.image_info(dst)
        except Exception:
            dst.unlink(missing_ok=True)
            release()
            raise HTTPException(400, "無法讀取圖片")
        m = {"name": name, "file": dst.name, "url": file_url(pid, dst.name), **info, "ready": True}
        commit(m)
        return {"media": m}

    # a newer upload of the same kind replaces one that is still being processed
    for j in jobs.running(pid, "media"):
        if getattr(j, "media_kind", "") == kind:
            j.cancel()

    def work(job: jobs.Job):
        try:
            try:
                info = media.probe(dst)
                media.check_safe(info)
                if kind == "video" and not info["has_video"]:
                    raise ValueError("檔案中沒有影像軌，請改用「音訊製片」模式")
                if not info["has_audio"]:
                    raise ValueError("檔案中沒有音軌，無法轉錄")
                job.set(0.02)
                wd.mkdir()
                dur = info.get("duration") or 0
                prev = media.make_preview(dst, info, wd, progress=lambda p: job.set(0.02 + 0.53 * p),
                                          cancelled=job.cancelled)
                job.set(0.55)
                wav = media.load_audio16k(dst, wd / "audio16k.f32", dur, progress=lambda p: job.set(0.55 + 0.25 * p),
                                          cancelled=job.cancelled)
                job.set(0.8)
                (wd / "peaks.json").write_text(json.dumps(media.peaks(wav)), encoding="utf-8")
                vizp = None
                if kind == "audio":
                    from .viz import compute_bands
                    compute_bands(wav).tofile(wd / "viz.bin")
                    vizp = file_url(pid, f"{wd.name}/viz.bin")
                job.set(0.97)
                m = {"name": name, "file": dst.name, "work": wd.name,
                     "preview": file_url(pid, prev.relative_to(d).as_posix()),
                     "info": info, "peaks": file_url(pid, f"{wd.name}/peaks.json"), "viz": vizp,
                     "size": dst.stat().st_size, "ready": True}
                commit(m)
            except BaseException:
                release()
                dst.unlink(missing_ok=True)
                shutil.rmtree(wd, ignore_errors=True)
                raise
            return {"media": m}
        finally:
            _set_bg(pid, "media", None)

    job = jobs.Job("media", pid)
    job.media_kind = kind
    _set_bg(pid, "media", {"id": job.id, "kind": kind, "name": name})
    jobs.run(job, work)
    return {"job": job.id}


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "找不到這個背景工作（程式可能已重新啟動）")
    return j.to_dict()


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    j = jobs.get(jid)
    if j:
        j.cancel()
    return {"ok": True}


def _tracked(pid: str, kind: str, fn: Callable[[jobs.Job], dict]) -> Callable[[jobs.Job], dict]:
    """Record the running job in project.json and keep its result there until
    the app acknowledges it, so a paid transcription/translation survives a
    closed or reloaded window."""
    def work(job: jobs.Job):
        save_project(pid, {"job": {"id": job.id, "kind": kind}})
        try:
            res = fn(job)
        except BaseException:
            try:
                save_project(pid, {"job": None})
            except Exception as e:
                log.warning("could not clear the job record of %s: %s", pid, e)
            raise
        try:
            save_project(pid, {"job": {"id": job.id, "kind": kind, "result": res, "time": time.time()}})
        except Exception:
            log.exception("could not store job result")
        return res
    return work


# ------------------------------------------------------------------ transcription / translation
def _audio_for(pid: str, mode: str):
    d = pdir(pid)
    p = load_project(pid)
    m = (p.get("media") or {}).get(mode)
    if not m or not m.get("ready"):
        raise HTTPException(400, "請先上傳" + ("影片" if mode == "video" else "音訊"))
    return d / m["file"], work_dir(d, mode, m) / "audio16k.f32", m


def _chinese_script(texts: List[str]) -> str:
    """zh-TW if the transcript is written in Traditional characters, else zh-CN."""
    try:
        import opencc
        t2s = opencc.OpenCC("t2s")
    except Exception:
        return "zh-CN"
    sample = "".join(texts)[:4000]
    changed = sum(1 for a, b in zip(sample, t2s.convert(sample)) if a != b)
    return "zh-TW" if changed >= max(3, len(sample) * 0.01) else "zh-CN"


def _record_usage(tr: Translator):
    try:
        config.add_usage(tr.usage)
    except Exception as e:
        log.warning("could not record usage: %s", e)


@app.post("/api/projects/{pid}/generate")
def generate(pid: str, body: dict = Body(...)):
    """body: mode, src_lang ('auto'|code), tgt_lang, tone, opts{keep_names,context}, max_chars, translate"""
    mode = body.get("mode", "video")
    src_file, cache, m = _audio_for(pid, mode)
    settings = config.load_settings()
    want_tr = bool(body.get("translate", True))
    if want_tr and not settings.get("api_key"):
        raise HTTPException(400, "尚未設定翻譯服務的 API Key（設定 → 翻譯），或關閉「自動翻譯」只做轉錄")

    def work(job: jobs.Job):
        share = TR_SHARE if want_tr else 0.97
        wav = media.load_audio16k(src_file, cache, (m.get("info") or {}).get("duration") or 0,
                                  cancelled=job.cancelled)
        job.set(0.01)
        src_lang = body.get("src_lang") or "auto"
        glossary = [a for a, _ in parse_glossary(settings.get("glossary", ""))]
        ctx = ("Keywords: " + ", ".join(glossary)) if glossary else ""
        res = ENGINE.transcribe(wav, None if src_lang == "auto" else src_lang, ctx,
                                progress=lambda p: job.set(0.02 + share * p),
                                cancelled=job.cancelled, note=job.wait_note)
        cues = segment(res["words"], int(body.get("max_chars") or settings.get("max_chars", 42)), res["duration"])
        detected = res["language"] or (src_lang if src_lang != "auto" else "")
        if src_lang in ("zh-TW", "zh-CN") and detected in ("zh-CN", "zh-TW"):
            detected = src_lang
        elif src_lang == "auto" and detected == "zh-CN":
            detected = _chinese_script([c["src"] for c in cues])
        tr_error, failed, usage = "", 0, None
        if want_tr and cues:
            tr = None
            try:
                tr = Translator(settings)
                out = tr.translate_all(cues, detected, body.get("tgt_lang", "zh-TW"), body.get("tone", "natural"),
                                       body.get("opts") or {}, progress=lambda p: job.set(0.02 + share + (0.97 - share) * p),
                                       cancelled=job.cancelled)
                for c, t in zip(cues, out):
                    c["tgt"] = t
                for i, old in tr.fixes.items():
                    cues[i]["src_orig"] = old      # the app marks proofread lines and can undo them
                failed = len(tr.failed)
            except jobs.Cancelled:
                raise
            except Exception as e:
                if job.cancelled():
                    raise jobs.Cancelled()
                log.exception("translation failed")
                tr_error = str(e)
            finally:
                if tr:
                    _record_usage(tr)
                    usage = tr.usage
        for c in cues:
            c.setdefault("tgt", "")
        return {"segs": cues, "detected": detected, "duration": res["duration"], "translate_error": tr_error,
                "translate_failed": failed, "aligned": res.get("aligned", 1.0), "usage": usage}

    job = jobs.start("generate", _tracked(pid, "generate", work), pid)
    return {"job": job.id}


@app.post("/api/projects/{pid}/translate")
def translate(pid: str, body: dict = Body(...)):
    """Re-translate all given cues (keeps the user's edited source text)."""
    pdir(pid)
    segs = body.get("segs") or []
    settings = config.load_settings()
    if not settings.get("api_key"):
        raise HTTPException(400, "尚未設定翻譯服務的 API Key（設定 → 翻譯）")

    def work(job: jobs.Job):
        tr = Translator(settings)
        cues = [{"src": str(s.get("src", ""))} for s in segs]
        src = [c["src"] for c in cues]
        opts = dict(body.get("opts") or {})
        opts["proofread"] = False          # never touch text the user may have edited
        try:
            out = tr.translate_all(cues, body.get("src_lang", ""), body.get("tgt_lang", "zh-TW"),
                                   body.get("tone", "natural"), opts, progress=job.set, cancelled=job.cancelled)
        except TranslateError:
            if job.cancelled():
                raise jobs.Cancelled()
            raise
        finally:
            _record_usage(tr)
        # the source each translation belongs to: lines edited meanwhile are not overwritten
        return {"tgt": out, "src": src, "failed": len(tr.failed), "count": len(cues), "usage": tr.usage}

    job = jobs.start("translate", _tracked(pid, "translate", work), pid)
    return {"job": job.id}


@app.post("/api/projects/{pid}/retranslate")
def retranslate(pid: str, body: dict = Body(...)):
    pdir(pid)
    segs = body.get("segs") or []
    try:
        index = int(body.get("index", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "字幕索引不正確")
    if not 0 <= index < len(segs):
        raise HTTPException(400, "字幕索引超出範圍")
    tr = None
    try:
        tr = Translator(timeout=90, max_retries=0)
        t = tr.retranslate(segs, index, body.get("src_lang", ""), body.get("tgt_lang", "zh-TW"),
                           body.get("tone", "natural"), body.get("opts") or {}, body.get("current", ""))
    except TranslateError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"翻譯服務錯誤：{e}")
    finally:
        if tr:
            _record_usage(tr)
    return {"tgt": t}


# ------------------------------------------------------------------ export
@app.post("/api/projects/{pid}/export/frames")
async def upload_frames(pid: str, session: str = Form(...), files: List[UploadFile] = File(...)):
    d = pdir(pid)
    if not session.isalnum() or len(session) > 32:
        raise HTTPException(400, "輸出工作代碼不正確")
    fd = d / "exports" / f"_frames_{session}"
    fd.mkdir(parents=True, exist_ok=True)
    for f in files:
        nm = Path(f.filename or "").name
        if not UPLOAD_FRAME.match(nm):
            continue
        data = await f.read()
        await run_in_threadpool((fd / nm).write_bytes, data)
    return {"ok": True}


@app.post("/api/projects/{pid}/export/frames/discard")
def discard_frames(pid: str, body: dict = Body(default={})):
    """The app gave up on an export before encoding started."""
    session = str(body.get("session", ""))
    if session.isalnum():
        shutil.rmtree(pdir(pid) / "exports" / f"_frames_{session}", ignore_errors=True)
    return {"ok": True}


def _keep_log(fd: Path) -> str:
    """Copy a failed export's FFmpeg log to workspace/logs (the frame folder is deleted)."""
    src = fd / "ffmpeg.log"
    if not src.exists():
        return ""
    try:
        config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        dst = config.LOG_DIR / time.strftime("ffmpeg-%Y%m%d-%H%M%S.log")
        shutil.copyfile(src, dst)
        return str(dst)
    except OSError:
        return ""


@app.post("/api/projects/{pid}/export/video")
def export_video(pid: str, body: dict = Body(...)):
    from .exporter import FRAME_NAME, check_writable, export_video as do_export
    d = pdir(pid)
    session = str(body.get("session", ""))
    if not session.isalnum() or len(session) > 32:
        raise HTTPException(400, "輸出工作代碼不正確")
    if jobs.running(pid, "export"):
        raise HTTPException(409, "這個專案已有影片正在輸出，請等它完成或取消後再試")
    fd = d / "exports" / f"_frames_{session}"
    meta = body.get("meta") or {}
    p = load_project(pid)
    mode = meta.get("mode", "video")
    if mode not in ("video", "audio"):
        raise HTTPException(400, "輸出模式不正確")
    mm = p["media"].get(mode)
    if not mm or not mm.get("ready"):
        raise HTTPException(400, "找不到媒體檔")
    try:
        meta["width"], meta["height"] = int(meta["width"]), int(meta["height"])
        meta["duration"] = float(meta["duration"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "輸出尺寸或長度不正確")
    if not (16 <= meta["width"] <= 8192 and 16 <= meta["height"] <= 8192 and meta["duration"] > 0):
        raise HTTPException(400, "輸出尺寸或長度不正確")
    if any(not FRAME_NAME.match(str(c.get("frame", ""))) for c in meta.get("cues") or []):
        raise HTTPException(400, "字幕畫格名稱不正確")
    src = d / mm["file"]
    medias = ({"video": src, "info": mm.get("info") or {}} if mode == "video"
              else {"audio": src, "viz": work_dir(d, "audio", mm) / "viz.bin"})
    embed = [f for f in (meta.get("fonts") or []) if f in fonts.FAMILIES][:4] if meta.get("burn") == "soft" else []

    if meta.get("out_path"):
        t = _check_target(str(meta["out_path"]), OUT_VIDEO_EXT)
        if t.exists() and not meta.get("overwrite"):
            raise HTTPException(409, "檔案已存在")
        try:
            check_writable(t)
        except PermissionError as e:
            raise HTTPException(409, str(e))

    def work(job: jobs.Job):
        try:
            if embed:                  # MKV: carry the subtitle fonts along
                medias["fonts"] = fonts.for_embedding(embed)
            try:
                res = do_export(job, meta, fd, d / "exports", medias)
            except RuntimeError as e:
                kept = _keep_log(fd)
                raise RuntimeError(str(e) + (f"\n（完整記錄：{kept}）" if kept else ""))
            _remember(res["path"])
            if meta.get("out_path"):
                _remember_dir(res["path"])
            return res
        finally:
            _set_bg(pid, "export", None)
            if not os.environ.get("LUMEN_KEEP_FRAMES"):
                shutil.rmtree(fd, ignore_errors=True)

    job = jobs.Job("export", pid)
    _set_bg(pid, "export", {"id": job.id, "path": str(meta.get("out_path") or "")})
    jobs.run(job, work)
    return {"job": job.id}


# ------------------------------------------------------------------ settings / status
@app.get("/api/status")
def status():
    g = sysinfo.gpu()
    s = config.public_settings()
    return {"asr": ENGINE.status, "asr_error": ENGINE.error, "device": ENGINE.device, "gpu": g.get("name", ""),
            "vram_gb": g.get("vram_gb"), "ram_gb": sysinfo.ram_gb(), "nvenc": media.nvenc_ok(), "version": VERSION,
            "warnings": sysinfo.warnings(s.get("device", "cuda")), "settings": s, "usage": config.usage_month()}


@app.get("/api/diag")
def diag():
    """Plain-text facts for bug reports (no key, no file contents)."""
    try:
        ff = media.run([media.FFMPEG, "-hide_banner", "-version"], timeout=10).stdout.decode("utf-8", "ignore").splitlines()[0]
    except Exception as e:
        ff = f"unavailable ({e})"
    info = sysinfo.summary()
    s = config.load_settings()
    lines = [f"LumenSubs {VERSION}", f"OS: {info['os']}", f"Python: {info['python']}", f"RAM: {info['ram_gb']} GB",
             f"GPU: {info['gpu'].get('name', '-')} ({info['gpu'].get('vram_gb', '-')} GB)",
             f"ASR: {ENGINE.status} on {ENGINE.device} {ENGINE.error or ''}".rstrip(),
             f"NVENC: {media.nvenc_ok()}", f"FFmpeg: {ff}", f"Translation host: {config.api_host(s)} · model {s.get('model')}",
             f"Workspace free: {sysinfo.free_gb(config.WORK_DIR)} GB"]
    try:
        import torch
        lines.append(f"torch: {torch.__version__} (CUDA {torch.version.cuda})")
    except Exception:
        lines.append("torch: not importable")
    return {"text": "\n".join(lines)}


@app.get("/api/settings")
def get_settings():
    return config.public_settings()


def _new_key(body: dict) -> str:
    k = str(body.get("api_key") or "").strip()
    return "" if "…" in k else k


def _endpoint_change_guard(body: dict):
    """Never send the saved key to a different endpoint: a new API address
    must come with its key."""
    if not body.get("base_url"):
        return
    try:
        new = config.check_base_url(body["base_url"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    old = (config.load_settings().get("base_url") or "").rstrip("/")
    if new != old and not _new_key(body):
        raise HTTPException(400, "更換 API 位址時，請一併輸入 API Key（避免把已儲存的金鑰送到新的位址）")


@app.post("/api/settings")
def set_settings(body: dict = Body(...)):
    _endpoint_change_guard(body)
    patch = dict(body)
    patch.pop("export_dir", None)
    if _new_key(body):
        patch["api_key"] = _new_key(body)
    else:
        patch.pop("api_key", None)
    old_dev = config.load_settings().get("device")
    try:
        config.save_settings(patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if patch.get("device") and patch["device"] != old_dev and ENGINE.model is not None:
        threading.Thread(target=_safe_load, args=(patch["device"],), daemon=True).start()
    return config.public_settings()


@app.post("/api/settings/test")
def test_settings(body: dict = Body(default={})):
    _endpoint_change_guard(body)
    s = config.load_settings()
    if body.get("base_url"):
        s["base_url"] = config.check_base_url(body["base_url"])
    if body.get("model"):
        s["model"] = str(body["model"])
    if _new_key(body):
        s["api_key"] = _new_key(body)
    try:
        t0 = time.time()
        Translator(s, timeout=30, max_retries=0).test()
        return {"ok": True, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=200)


@app.post("/api/models/load")
def load_models():
    if ENGINE.status not in ("loading",):
        threading.Thread(target=_safe_load, daemon=True).start()
    return {"ok": True}


@app.post("/api/models/unload")
def unload_models():
    if jobs.running(kind="generate"):
        raise HTTPException(409, "正在生成字幕，請等它完成後再釋放模型")
    ENGINE.unload()
    return {"ok": True}


# ------------------------------------------------------------------ fonts
@app.get("/fonts/fonts.css")
def fonts_css():
    return Response(fonts.css(), media_type="text/css", headers={"Cache-Control": "no-cache"})


@app.get("/fonts/file/{name}")
def font_file(name: str):
    try:
        p = fonts.ensure_file(name)
    except Exception as e:
        log.warning("font %s unavailable: %s", name, e)
        raise HTTPException(502, "字型暫時無法下載")
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="font/ttf", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ------------------------------------------------------------------ static
@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


app.mount("/web", StaticFiles(directory=str(config.WEB_DIR)), name="web")


def _startup():
    config.ensure_dirs()
    # uploads are spooled to disk by the web framework: keep that copy on the
    # workspace drive instead of the (often small) system drive
    tempfile.tempdir = str(config.TMP_DIR)
    threading.Thread(target=_sweep_workspace, daemon=True).start()
    # the models take ~6.5 GB of GPU memory, ~13 GB of RAM on the CPU: preload
    # only on a GPU; on the CPU they load when a transcription starts
    if not os.environ.get("LUMEN_NO_PRELOAD") and _gpu_preferred():
        threading.Thread(target=_safe_load, daemon=True).start()
    threading.Thread(target=media.nvenc_ok, daemon=True).start()


def _gpu_preferred() -> bool:
    if config.load_settings().get("device") == "cpu":
        return False
    return bool(sysinfo.gpu())


STALE_CACHE_DAYS = 14


def _sweep_workspace():
    """Remove leftovers of interrupted uploads/exports/deletes and media files
    that no project references any more (e.g. files that were locked when
    replaced). Decoded-audio caches of projects untouched for two weeks are
    dropped too — they are re-created when needed."""
    for d in (config.TRASH_DIR, config.TMP_DIR):
        if d.exists():
            for x in d.iterdir():
                shutil.rmtree(x, ignore_errors=True) if x.is_dir() else x.unlink(missing_ok=True)
    if not config.PROJECTS_DIR.exists():
        return
    for d in config.PROJECTS_DIR.iterdir():
        try:
            p, _ = config.read_json(d / "project.json")
        except (OSError, ValueError):
            continue
        if not p:
            continue
        for f in d.glob("project.json.*.tmp"):
            f.unlink(missing_ok=True)
        for fd in (d / "exports").glob("_frames_*"):
            shutil.rmtree(fd, ignore_errors=True)
        for f in d.glob("_*_upload*"):          # temp names used by older versions
            f.unlink(missing_ok=True)
        stale = time.time() - float(p.get("updated") or 0) > STALE_CACHE_DAYS * 86400
        for kind in ("video", "audio", "image"):
            m = (p.get("media") or {}).get(kind) or {}
            keep = {m.get("file", ""), m.get("work") or (f"{kind}_work" if m else "")}
            _cleanup_media(d, kind, keep)
            if stale and m:
                (work_dir(d, kind, m) / "audio16k.f32").unlink(missing_ok=True)
        # jobs that were running when the server stopped will never finish
        patch = {}
        if (p.get("job") or {}) and "result" not in p["job"]:
            patch["job"] = None
        if p.get("bg"):
            patch["bg"] = {}
        if patch:
            try:
                save_project(d.name, patch)
            except Exception as e:
                log.warning("could not reset jobs of %s: %s", d.name, e)


def _safe_load(device=None):
    try:
        ENGINE.load(device)
    except Exception as e:
        log.warning("model load failed: %s", e)
