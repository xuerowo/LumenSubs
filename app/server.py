"""LumenSubs HTTP API + static front-end."""
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, jobs, media
from .asr import ENGINE
from .segmenter import segment
from .translator import TranslateError, Translator, parse_glossary

log = logging.getLogger("lumen")
app = FastAPI(title="LumenSubs")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aiff", ".aif", ".amr", ".webm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".avif"}


# ------------------------------------------------------------------ projects
def pdir(pid: str) -> Path:
    if not pid or not all(c.isalnum() or c in "-_" for c in pid):
        raise HTTPException(400, "bad project id")
    d = config.PROJECTS_DIR / pid
    if not d.exists():
        raise HTTPException(404, "專案不存在")
    return d


_plock = threading.Lock()


def load_project(pid: str) -> dict:
    return json.loads((pdir(pid) / "project.json").read_text(encoding="utf-8"))


def save_project(pid: str, patch: dict) -> dict:
    with _plock:
        p = load_project(pid)
        p.update(patch)
        p["updated"] = time.time()
        f = pdir(pid) / "project.json"
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, f)
        return p


def file_url(pid: str, name: str) -> str:
    """URL for a project file, versioned by mtime so replaced media reloads."""
    f = config.PROJECTS_DIR / pid / name
    v = int(f.stat().st_mtime) if f.exists() else 0
    return f"/api/projects/{pid}/files/{quote(name)}?v={v}"


@app.post("/api/projects")
def create_project(body: dict = Body(default={})):
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    d = config.PROJECTS_DIR / pid
    d.mkdir(parents=True)
    p = {"id": pid, "name": body.get("name") or "未命名專案", "created": time.time(), "updated": time.time(),
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
    patch = {}
    if "state" in body:
        patch["state"] = body["state"]
    if "name" in body:
        patch["name"] = str(body["name"])[:120]
    p = save_project(pid, patch)
    return {"ok": True, "updated": p["updated"]}


@app.post("/api/projects/{pid}/save")
async def beacon_save(pid: str, request: Request):
    """sendBeacon target used when the window closes."""
    try:
        body = json.loads((await request.body()).decode("utf-8"))
    except ValueError:
        raise HTTPException(400, "bad json")
    return put_project(pid, body)


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    d = pdir(pid)
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@app.get("/api/projects/{pid}/files/{name:path}")
def get_file(pid: str, name: str, request: Request):
    d = pdir(pid)
    f = (d / name).resolve()
    if d.resolve() not in f.parents or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{pid}/exports/{name}")
def get_export(pid: str, name: str, dl: int = 1):
    d = pdir(pid) / "exports"
    f = (d / name).resolve()
    if d.resolve() not in f.parents or not f.exists():
        raise HTTPException(404)
    return FileResponse(f, filename=name if dl else None)


@app.post("/api/reveal")
def reveal(body: dict = Body(default={})):
    """Show a file in Explorer (select it) or open it with the default app."""
    target = Path(body.get("path") or "")
    if not target.exists():
        raise HTTPException(404, "找不到檔案")
    try:
        if os.name == "nt":
            import subprocess
            if body.get("open"):
                os.startfile(str(target))
            elif target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(target))
    except Exception as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


# ------------------------------------------------------------------ file dialogs / local paths
@app.post("/api/dialog")
def dialog(body: dict = Body(default={})):
    from . import dialogs
    start = body.get("dir") or config.load_settings().get("export_dir") or ""
    path = dialogs.ask(body.get("mode", "save"), body.get("title", ""), start, body.get("file", ""),
                       body.get("ext", ""), body.get("types") or [])
    return {"path": path}


@app.get("/api/default-dir")
def default_export_dir():
    from . import dialogs
    d = config.load_settings().get("export_dir") or ""
    return {"dir": d if d and os.path.isdir(d) else dialogs.default_dir()}


def _check_target(path: str) -> Path:
    if not path or not os.path.isabs(path):
        raise HTTPException(400, "請指定完整的輸出路徑")
    t = Path(path)
    if not t.parent.is_dir():
        raise HTTPException(400, f"資料夾不存在：{t.parent}")
    if any(c in t.name for c in '<>:"/|?*'):
        raise HTTPException(400, "檔名含有不允許的字元")
    return t


@app.post("/api/path/check")
def path_check(body: dict = Body(...)):
    t = _check_target(body.get("path", ""))
    return {"exists": t.exists(), "path": str(t)}


@app.post("/api/export/subtitle")
def save_subtitle(body: dict = Body(...)):
    t = _check_target(body.get("path", ""))
    try:
        t.write_text(body.get("content", ""), encoding="utf-8-sig", newline="")
    except OSError as e:
        raise HTTPException(500, f"無法寫入檔案：{e}")
    config.save_settings({"export_dir": str(t.parent)})
    return {"path": str(t), "size": t.stat().st_size}


# ------------------------------------------------------------------ media
@app.post("/api/projects/{pid}/media")
async def upload_media(pid: str, kind: str = Form(...), file: UploadFile = File(...)):
    d = pdir(pid)
    if kind not in ("video", "audio", "image"):
        raise HTTPException(400, "kind")
    ext = Path(file.filename or "").suffix.lower()
    allowed = {"video": VIDEO_EXT, "audio": AUDIO_EXT | VIDEO_EXT, "image": IMAGE_EXT}[kind]
    if ext not in allowed:
        raise HTTPException(400, f"不支援的檔案格式：{ext or '未知'}")
    # stream to a temporary name; the current media stays intact until the
    # new file has been validated
    tmp = d / f"_{kind}_upload{ext}"
    with open(tmp, "wb") as out:
        while True:
            chunk = await file.read(8 << 20)
            if not chunk:
                break
            out.write(chunk)
    name = file.filename or tmp.name
    dst = d / f"{kind}_src{ext}"

    def swap_in():
        for f in d.glob(f"{kind}_src*"):
            try:
                f.unlink()
            except OSError:
                pass
        shutil.rmtree(d / f"{kind}_work", ignore_errors=True)
        os.replace(tmp, dst)

    if kind == "image":
        try:
            info = media.image_info(tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise HTTPException(400, "無法讀取圖片")
        swap_in()
        m = {"name": name, "file": dst.name, "url": file_url(pid, dst.name), **info, "ready": True}
        p = load_project(pid)
        p["media"]["image"] = m
        save_project(pid, {"media": p["media"]})
        return {"media": m}

    def work(job: jobs.Job):
        try:
            info = media.probe(tmp)
            if kind == "video" and not info["has_video"]:
                raise ValueError("檔案中沒有影像軌，請改用「音訊製片」模式")
            if not info["has_audio"]:
                raise ValueError("檔案中沒有音軌，無法轉錄")
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        swap_in()
        try:
            return process(job, info)
        except Exception:
            p = load_project(pid)
            p["media"].pop(kind, None)
            save_project(pid, {"media": p["media"]})
            raise

    def process(job: jobs.Job, info: dict):
        job.set(0.1)
        slot_dir = d / f"{kind}_work"
        slot_dir.mkdir(exist_ok=True)
        prev = media.make_preview(dst, info, slot_dir)
        job.set(0.55)
        wav = media.load_audio16k(dst, slot_dir / "audio16k.f32")
        job.set(0.8)
        pk = media.peaks(wav)
        (slot_dir / "peaks.json").write_text(json.dumps(pk), encoding="utf-8")
        vizp = None
        if kind == "audio":
            from .viz import compute_bands
            compute_bands(wav).tofile(slot_dir / "viz.bin")
            vizp = file_url(pid, f"{kind}_work/viz.bin")
        rel = prev.relative_to(d).as_posix()
        m = {"name": name, "file": dst.name, "preview": file_url(pid, rel), "info": info,
             "peaks": file_url(pid, f"{kind}_work/peaks.json"), "viz": vizp,
             "size": dst.stat().st_size, "ready": True}
        p = load_project(pid)
        p["media"][kind] = m
        save_project(pid, {"media": p["media"]})
        return {"media": m}

    job = jobs.start("media", work, pid)
    return {"job": job.id}


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    j = jobs.JOBS.get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    return j.to_dict()


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    j = jobs.JOBS.get(jid)
    if j:
        j.cancel()
    return {"ok": True}


# ------------------------------------------------------------------ transcription / translation
def _audio_for(pid: str, mode: str):
    d = pdir(pid)
    p = load_project(pid)
    m = (p.get("media") or {}).get(mode)
    if not m:
        raise HTTPException(400, "請先上傳" + ("影片" if mode == "video" else "音訊"))
    return d / m["file"], d / f"{mode}_work" / "audio16k.f32", m


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
        wav = media.load_audio16k(src_file, cache)
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
        tr_error = ""
        if want_tr and cues:
            try:
                tr = Translator(settings)
                out = tr.translate_all(cues, detected, body.get("tgt_lang", "zh-TW"), body.get("tone", "natural"),
                                       body.get("opts") or {}, progress=lambda p: job.set(0.62 + 0.37 * p),
                                       cancelled=job.cancelled)
                for c, t in zip(cues, out):
                    c["tgt"] = t
            except jobs.Cancelled:
                raise
            except Exception as e:
                if job.cancelled():
                    raise jobs.Cancelled()
                log.exception("translation failed")
                tr_error = str(e)
        for c in cues:
            c.setdefault("tgt", "")
        return {"segs": cues, "detected": detected, "duration": res["duration"], "translate_error": tr_error}

    job = jobs.start("generate", work, pid)
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
        cues = [{"src": s.get("src", "")} for s in segs]
        opts = dict(body.get("opts") or {})
        opts["proofread"] = False          # never touch text the user may have edited
        out = tr.translate_all(cues, body.get("src_lang", ""), body.get("tgt_lang", "zh-TW"), body.get("tone", "natural"),
                               opts, progress=job.set, cancelled=job.cancelled)
        return {"tgt": out}

    job = jobs.start("translate", work, pid)
    return {"job": job.id}


@app.post("/api/projects/{pid}/retranslate")
def retranslate(pid: str, body: dict = Body(...)):
    pdir(pid)
    try:
        tr = Translator()
        t = tr.retranslate(body.get("segs") or [], int(body.get("index", 0)), body.get("src_lang", ""),
                           body.get("tgt_lang", "zh-TW"), body.get("tone", "natural"), body.get("opts") or {},
                           body.get("current", ""))
    except TranslateError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"翻譯服務錯誤：{e}")
    return {"tgt": t}


# ------------------------------------------------------------------ export
@app.post("/api/projects/{pid}/export/frames")
async def upload_frames(pid: str, session: str = Form(...), files: List[UploadFile] = File(...)):
    d = pdir(pid)
    if not session.isalnum():
        raise HTTPException(400, "session")
    fd = d / "exports" / f"_frames_{session}"
    fd.mkdir(parents=True, exist_ok=True)
    for f in files:
        nm = Path(f.filename or "").name
        if not nm.endswith(".png"):
            continue
        (fd / nm).write_bytes(await f.read())
    return {"ok": True}


@app.post("/api/projects/{pid}/export/video")
def export_video(pid: str, body: dict = Body(...)):
    from .exporter import export_video as do_export
    d = pdir(pid)
    session = body.get("session", "")
    if not session.isalnum():
        raise HTTPException(400, "session")
    fd = d / "exports" / f"_frames_{session}"
    meta = body.get("meta") or {}
    p = load_project(pid)
    mode = meta.get("mode", "video")
    mm = p["media"].get(mode)
    if not mm:
        raise HTTPException(400, "找不到媒體檔")
    src = d / mm["file"]
    medias = {"video": src} if mode == "video" else {"audio": src, "viz": d / "audio_work" / "viz.bin"}

    if meta.get("out_path"):
        _check_target(meta["out_path"])

    def work(job: jobs.Job):
        try:
            res = do_export(job, meta, fd, d / "exports", medias)
            if meta.get("out_path"):
                config.save_settings({"export_dir": str(Path(res["path"]).parent)})
            return res
        finally:
            shutil.rmtree(fd, ignore_errors=True) if not os.environ.get("LUMEN_KEEP_FRAMES") else None

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


@app.post("/api/settings")
def set_settings(body: dict = Body(...)):
    patch = dict(body)
    if not patch.get("api_key") or "…" in str(patch.get("api_key")):
        patch.pop("api_key", None)
    old_dev = config.load_settings().get("device")
    s = config.save_settings(patch)
    if patch.get("device") and patch["device"] != old_dev and ENGINE.model is not None:
        threading.Thread(target=ENGINE.load, args=(patch["device"],), daemon=True).start()
    return config.public_settings()


@app.post("/api/settings/test")
def test_settings(body: dict = Body(default={})):
    s = config.load_settings()
    for k in ("base_url", "model"):
        if body.get(k):
            s[k] = body[k]
    if body.get("api_key") and "…" not in body["api_key"]:
        s["api_key"] = body["api_key"]
    try:
        t0 = time.time()
        Translator(s).test()
        return {"ok": True, "ms": int((time.time() - t0) * 1000)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:300]}, status_code=200)


@app.post("/api/models/load")
def load_models():
    if ENGINE.status not in ("loading",):
        threading.Thread(target=ENGINE.load, daemon=True).start()
    return {"ok": True}


# ------------------------------------------------------------------ static
@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/web", StaticFiles(directory=str(config.WEB_DIR)), name="web")


@app.on_event("startup")
def _startup():
    if not os.environ.get("LUMEN_NO_PRELOAD"):
        threading.Thread(target=lambda: _safe_load(), daemon=True).start()
    threading.Thread(target=media.nvenc_ok, daemon=True).start()


def _safe_load():
    try:
        ENGINE.load()
    except Exception:
        pass
