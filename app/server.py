"""LumenSubs HTTP API + static front-end.

Access control: the server only listens on 127.0.0.1, but a web page in any
browser (DNS rebinding, cross-site requests) or another local program could
still reach it. So every request must name this machine in its Host header,
cross-origin requests are refused, and every /api call must carry the
per-launch token that run.py hands to the app window (X-Lumen-Token header,
or ?t= for sendBeacon). Only media files, which <video>/<img> load without
headers, skip the token.
"""
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, List
from urllib.parse import quote, urlparse

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, fonts, jobs, media
from .asr import ENGINE
from .segmenter import segment
from .translator import TranslateError, Translator, parse_glossary

log = logging.getLogger("lumen")

TOKEN = os.environ.get("LUMEN_TOKEN") or secrets.token_urlsafe(24)
if not os.environ.get("LUMEN_TOKEN"):
    os.environ["LUMEN_TOKEN"] = TOKEN
    log.warning("LUMEN_TOKEN not set — open http://127.0.0.1:<port>/#k=%s", TOKEN)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".amr", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".avif"}
SUB_EXT = {".srt", ".vtt", ".ass", ".ssa", ".txt"}
OUT_VIDEO_EXT = {".mp4", ".mkv"}
UPLOAD_FRAME = re.compile(r"^(c\d{5}|blank|bg)\.png$")


@asynccontextmanager
async def lifespan(_app):
    _startup()
    yield


app = FastAPI(title="LumenSubs", lifespan=lifespan)


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


def _token_exempt(request: Request) -> bool:
    path = request.url.path
    if not path.startswith("/api/"):
        return True
    # media elements (<video>, <img>, fetch of peaks) cannot send headers
    return request.method in ("GET", "HEAD") and re.match(r"^/api/projects/[\w-]+/(files|exports)/", path) is not None


@app.middleware("http")
async def access_guard(request: Request, call_next):
    if not _local(*_split_host(request.headers.get("host", ""))):
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    origin = request.headers.get("origin")
    if origin is not None and not _origin_ok(origin):
        return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
    if not _token_exempt(request):
        tok = request.headers.get("x-lumen-token") or request.query_params.get("t") or ""
        if not secrets.compare_digest(tok.encode(), TOKEN.encode()):
            return JSONResponse({"detail": "存取金鑰無效，請關閉此視窗並重新執行 start.bat", "code": "token"},
                                status_code=401)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


# ------------------------------------------------------------------ projects
def pdir(pid: str) -> Path:
    if not pid or not all(c.isalnum() or c in "-_" for c in pid):
        raise HTTPException(400, "bad project id")
    d = config.PROJECTS_DIR / pid
    if not d.exists():
        raise HTTPException(404, "專案不存在")
    return d


_plock = threading.RLock()


def load_project(pid: str) -> dict:
    return json.loads((pdir(pid) / "project.json").read_text(encoding="utf-8"))


def update_project(pid: str, fn: Callable[[dict], object]) -> dict:
    """Atomic read-modify-write of project.json (fn mutates the dict)."""
    with _plock:
        p = load_project(pid)
        fn(p)
        p["updated"] = time.time()
        f = pdir(pid) / "project.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
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


@app.post("/api/projects")
def create_project(body: dict = Body(default={})):
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    d = config.PROJECTS_DIR / pid
    d.mkdir(parents=True)
    p = {"id": pid, "name": str(body.get("name") or "未命名專案")[:120], "created": time.time(), "updated": time.time(),
         "media": {}, "state": {}}
    (d / "project.json").write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    return p


@app.get("/api/projects")
def list_projects():
    out = []
    for d in config.PROJECTS_DIR.iterdir():
        f = d / "project.json"
        if f.exists():
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                st = p.get("state") or {}
                mode = st.get("mode", "video")
                med = p.get("media") or {}
                out.append({"id": p["id"], "name": p.get("name"), "updated": p.get("updated", 0),
                            "mode": mode, "cues": len(st.get("segs") or []),
                            "primary": (med.get(mode) or {}).get("name", "")})
            except Exception:
                pass
    out.sort(key=lambda x: -x["updated"])
    return out[:50]


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    return load_project(pid)


@app.put("/api/projects/{pid}")
def put_project(pid: str, body: dict = Body(...)):
    def upd(p):
        if isinstance(body.get("state"), dict):
            p["state"] = body["state"]
        if "name" in body:
            p["name"] = str(body["name"])[:120]
        # the app has applied a finished job's result → forget it
        if body.get("ack_job") and (p.get("job") or {}).get("id") == body["ack_job"]:
            p["job"] = None
    p = update_project(pid, upd)
    return {"ok": True, "updated": p["updated"]}


@app.post("/api/projects/{pid}/save")
async def beacon_save(pid: str, request: Request):
    """sendBeacon target used when the window closes."""
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except ValueError:
        raise HTTPException(400, "bad json")
    if not isinstance(body, dict):
        raise HTTPException(400, "bad json")
    return await run_in_threadpool(put_project, pid, body)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    d = pdir(pid)
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@app.get("/api/projects/{pid}/files/{name:path}")
def get_file(pid: str, name: str):
    d = pdir(pid)
    f = (d / name).resolve()
    if d.resolve() not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{pid}/exports/{name}")
def get_export(pid: str, name: str, dl: int = 1):
    d = pdir(pid) / "exports"
    f = (d / name).resolve()
    if d.resolve() not in f.parents or not f.is_file():
        raise HTTPException(404)
    return FileResponse(f, filename=name if dl else None)


# ------------------------------------------------------------------ local paths
_written = set()          # files this server wrote during this session


def _pkey(p) -> str:
    return os.path.normcase(os.path.abspath(str(p)))


def _remember(p):
    _written.add(_pkey(p))


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
            import subprocess
            if body.get("open"):
                os.startfile(str(target))
            else:
                subprocess.Popen(["explorer", "/select,", str(target)])
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


@app.post("/api/dialog")
def dialog(body: dict = Body(default={})):
    from . import dialogs
    start = str(body.get("dir") or config.load_settings().get("export_dir") or "")
    if _is_unc(start):
        start = ""
    path = dialogs.ask("dir" if body.get("mode") == "dir" else "save", str(body.get("title", ""))[:100], start,
                       str(body.get("file", ""))[:200], str(body.get("ext", ""))[:10], [])
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
        t.write_text(str(body.get("content", "")), encoding="utf-8-sig", newline="")
    except OSError as e:
        raise HTTPException(500, f"無法寫入檔案：{e}")
    _remember(t)
    config.save_settings({"export_dir": str(t.parent)})
    return {"path": str(t), "size": t.stat().st_size}


# ------------------------------------------------------------------ media
def _cleanup_media(d: Path, kind: str, keep: set):
    """Best effort: files still open elsewhere are removed at next start."""
    for f in d.glob(f"{kind}_src*"):
        if f.name not in keep:
            try:
                f.unlink()
            except OSError:
                pass
    for w in d.glob(f"{kind}_work*"):
        if w.name not in keep:
            shutil.rmtree(w, ignore_errors=True)


@app.post("/api/projects/{pid}/media")
async def upload_media(pid: str, kind: str = Form(...), file: UploadFile = File(...)):
    d = pdir(pid)
    if kind not in ("video", "audio", "image"):
        raise HTTPException(400, "kind")
    ext = Path(file.filename or "").suffix.lower()
    allowed = {"video": VIDEO_EXT, "audio": AUDIO_EXT | VIDEO_EXT, "image": IMAGE_EXT}[kind]
    if ext not in allowed:
        raise HTTPException(400, f"不支援的檔案格式：{ext or '未知'}")
    # every upload gets fresh names; the current media is only replaced once
    # the new file has been fully processed
    tag = uuid.uuid4().hex[:8]
    dst = d / f"{kind}_src_{tag}{ext}"
    try:
        with open(dst, "wb") as out:
            while True:
                chunk = await file.read(8 << 20)
                if not chunk:
                    break
                await run_in_threadpool(out.write, chunk)
    except Exception:
        dst.unlink(missing_ok=True)
        raise
    name = Path(file.filename or dst.name).name

    def commit(m: dict):
        update_project(pid, lambda p: p.setdefault("media", {}).__setitem__(kind, m))
        _cleanup_media(d, kind, {m["file"], m.get("work", "")})

    if kind == "image":
        try:
            info = media.image_info(dst)
        except Exception:
            dst.unlink(missing_ok=True)
            raise HTTPException(400, "無法讀取圖片")
        m = {"name": name, "file": dst.name, "url": file_url(pid, dst.name), **info, "ready": True}
        commit(m)
        return {"media": m}

    wd = d / f"{kind}_work_{tag}"

    def work(job: jobs.Job):
        try:
            info = media.probe(dst)
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
        except BaseException:
            dst.unlink(missing_ok=True)
            shutil.rmtree(wd, ignore_errors=True)
            raise
        m = {"name": name, "file": dst.name, "work": wd.name, "preview": file_url(pid, prev.relative_to(d).as_posix()),
             "info": info, "peaks": file_url(pid, f"{wd.name}/peaks.json"), "viz": vizp,
             "size": dst.stat().st_size, "ready": True}
        commit(m)
        return {"media": m}

    job = jobs.start("media", work, pid)
    return {"job": job.id}


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
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
            except Exception:
                pass
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


@app.post("/api/projects/{pid}/generate")
def generate(pid: str, body: dict = Body(...)):
    """body: mode, src_lang ('auto'|code), tgt_lang, tone, opts{keep_names,context}, max_chars, translate"""
    mode = body.get("mode", "video")
    src_file, cache, m = _audio_for(pid, mode)
    settings = config.load_settings()
    want_tr = body.get("translate", True)
    if want_tr and not settings.get("api_key"):
        raise HTTPException(400, "尚未設定 DeepSeek API Key（設定 → 翻譯）")

    def work(job: jobs.Job):
        wav = media.load_audio16k(src_file, cache, (m.get("info") or {}).get("duration") or 0,
                                  cancelled=job.cancelled)
        job.set(0.01)
        if ENGINE.model is None:
            ENGINE.load()
        src_lang = body.get("src_lang") or "auto"
        glossary = [a for a, _ in parse_glossary(settings.get("glossary", ""))]
        ctx = ("Keywords: " + ", ".join(glossary)) if glossary else ""
        res = ENGINE.transcribe(wav, None if src_lang == "auto" else src_lang, ctx,
                                progress=lambda p: job.set(0.02 + 0.6 * p if want_tr else 0.02 + 0.97 * p))
        cues = segment(res["words"], int(body.get("max_chars") or settings.get("max_chars", 42)), res["duration"])
        detected = res["language"] or (src_lang if src_lang != "auto" else "")
        if src_lang in ("zh-TW", "zh-CN") and detected in ("zh-CN", "zh-TW"):
            detected = src_lang
        elif src_lang == "auto" and detected == "zh-CN":
            detected = _chinese_script([c["src"] for c in cues])
        tr_error, failed = "", 0
        if want_tr and cues:
            try:
                tr = Translator(settings)
                out = tr.translate_all(cues, detected, body.get("tgt_lang", "zh-TW"), body.get("tone", "natural"),
                                       body.get("opts") or {}, progress=lambda p: job.set(0.62 + 0.37 * p),
                                       cancelled=job.cancelled)
                for c, t in zip(cues, out):
                    c["tgt"] = t
                failed = len(tr.failed)
            except jobs.Cancelled:
                raise
            except Exception as e:
                if job.cancelled():
                    raise jobs.Cancelled()
                log.exception("translation failed")
                tr_error = str(e)
        for c in cues:
            c.setdefault("tgt", "")
        return {"segs": cues, "detected": detected, "duration": res["duration"], "translate_error": tr_error,
                "translate_failed": failed}

    job = jobs.start("generate", _tracked(pid, "generate", work), pid)
    return {"job": job.id}


@app.post("/api/projects/{pid}/translate")
def translate(pid: str, body: dict = Body(...)):
    """Re-translate all given cues (keeps the user's edited source text)."""
    pdir(pid)
    segs = body.get("segs") or []
    settings = config.load_settings()
    if not settings.get("api_key"):
        raise HTTPException(400, "尚未設定 DeepSeek API Key")

    def work(job: jobs.Job):
        tr = Translator(settings)
        cues = [{"src": str(s.get("src", ""))} for s in segs]
        opts = dict(body.get("opts") or {})
        opts["proofread"] = False          # never touch text the user may have edited
        try:
            out = tr.translate_all(cues, body.get("src_lang", ""), body.get("tgt_lang", "zh-TW"),
                                   body.get("tone", "natural"), opts, progress=job.set, cancelled=job.cancelled)
        except TranslateError:
            if job.cancelled():
                raise jobs.Cancelled()
            raise
        return {"tgt": out, "failed": len(tr.failed), "count": len(cues)}

    job = jobs.start("translate", _tracked(pid, "translate", work), pid)
    return {"job": job.id}


@app.post("/api/projects/{pid}/retranslate")
def retranslate(pid: str, body: dict = Body(...)):
    pdir(pid)
    segs = body.get("segs") or []
    try:
        index = int(body.get("index", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "index")
    if not 0 <= index < len(segs):
        raise HTTPException(400, "字幕索引超出範圍")
    try:
        tr = Translator(timeout=90, max_retries=0)
        t = tr.retranslate(segs, index, body.get("src_lang", ""), body.get("tgt_lang", "zh-TW"),
                           body.get("tone", "natural"), body.get("opts") or {}, body.get("current", ""))
    except TranslateError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"翻譯服務錯誤：{e}")
    return {"tgt": t}


# ------------------------------------------------------------------ export
@app.post("/api/projects/{pid}/export/frames")
async def upload_frames(pid: str, session: str = Form(...), files: List[UploadFile] = File(...)):
    d = pdir(pid)
    if not session.isalnum() or len(session) > 32:
        raise HTTPException(400, "session")
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


@app.post("/api/projects/{pid}/export/video")
def export_video(pid: str, body: dict = Body(...)):
    from .exporter import FRAME_NAME, export_video as do_export
    d = pdir(pid)
    session = str(body.get("session", ""))
    if not session.isalnum() or len(session) > 32:
        raise HTTPException(400, "session")
    fd = d / "exports" / f"_frames_{session}"
    meta = body.get("meta") or {}
    p = load_project(pid)
    mode = meta.get("mode", "video")
    if mode not in ("video", "audio"):
        raise HTTPException(400, "mode")
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
    medias = {"video": src} if mode == "video" else {"audio": src, "viz": work_dir(d, "audio", mm) / "viz.bin"}
    embed = [f for f in (meta.get("fonts") or []) if f in fonts.FAMILIES][:4] if meta.get("burn") == "soft" else []

    if meta.get("out_path"):
        t = _check_target(str(meta["out_path"]), OUT_VIDEO_EXT)
        if t.exists() and not meta.get("overwrite"):
            raise HTTPException(409, "檔案已存在")

    def work(job: jobs.Job):
        try:
            if embed:                  # MKV: carry the subtitle fonts along
                medias["fonts"] = fonts.for_embedding(embed)
            res = do_export(job, meta, fd, d / "exports", medias)
            _remember(res["path"])
            if meta.get("out_path"):
                config.save_settings({"export_dir": str(Path(res["path"]).parent)})
            return res
        finally:
            if not os.environ.get("LUMEN_KEEP_FRAMES"):
                shutil.rmtree(fd, ignore_errors=True)

    job = jobs.start("export", work, pid)
    return {"job": job.id}


# ------------------------------------------------------------------ settings / status
@app.get("/api/status")
def status():
    gpu = ""
    try:
        import torch
        if torch.cuda.is_available():
            gpu = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return {"asr": ENGINE.status, "asr_error": ENGINE.error, "device": ENGINE.device, "gpu": gpu,
            "nvenc": media.nvenc_ok(), "settings": config.public_settings()}


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
        threading.Thread(target=ENGINE.load, args=(patch["device"],), daemon=True).start()
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
        raise HTTPException(502, "font unavailable")
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="font/ttf", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ------------------------------------------------------------------ static
@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/web", StaticFiles(directory=str(config.WEB_DIR)), name="web")


def _startup():
    threading.Thread(target=_sweep_workspace, daemon=True).start()
    if not os.environ.get("LUMEN_NO_PRELOAD"):
        threading.Thread(target=_safe_load, daemon=True).start()
    threading.Thread(target=media.nvenc_ok, daemon=True).start()


def _sweep_workspace():
    """Remove leftovers of interrupted uploads/exports and media files that no
    project references any more (e.g. files that were locked when replaced)."""
    for d in config.PROJECTS_DIR.iterdir():
        try:
            p = json.loads((d / "project.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for fd in (d / "exports").glob("_frames_*"):
            shutil.rmtree(fd, ignore_errors=True)
        for f in d.glob("_*_upload*"):          # temp names used by older versions
            f.unlink(missing_ok=True)
        for kind in ("video", "audio", "image"):
            m = (p.get("media") or {}).get(kind) or {}
            keep = {m.get("file", ""), m.get("work") or (f"{kind}_work" if m else "")}
            _cleanup_media(d, kind, keep)
        # a job that was running when the server stopped will never finish
        if (p.get("job") or {}) and "result" not in p["job"]:
            try:
                save_project(d.name, {"job": None})
            except Exception:
                pass


def _safe_load():
    try:
        ENGINE.load()
    except Exception:
        pass
