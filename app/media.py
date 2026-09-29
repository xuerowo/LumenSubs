"""FFmpeg helpers: probing, browser-friendly previews, 16 kHz audio, peaks."""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .jobs import Cancelled

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
NO_WINDOW = 0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0

BROWSER_VCODECS = {"h264", "vp8", "vp9", "av1"}
BROWSER_ACODECS = {"aac", "mp3", "opus", "vorbis", "flac", None}
BROWSER_VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov"}
BROWSER_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"}


def run(args, timeout: float = 60, **kw):
    """Short helper commands (probe, capability checks)."""
    return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, timeout=timeout, **kw)


def ffmpeg(args, duration: float = 0.0, progress: Optional[Callable[[float], None]] = None,
           cancelled: Optional[Callable[[], bool]] = None):
    """Run a (possibly long) FFmpeg command: reports progress 0‥1, stops as
    soon as `cancelled()` turns true and raises RuntimeError with the tail of
    stderr on failure. stderr goes to a temp file so the pipe never blocks."""
    with tempfile.TemporaryFile() as errf:
        proc = subprocess.Popen([FFMPEG, "-y", "-hide_banner", "-nostats", "-loglevel", "error",
                                 "-progress", "pipe:1", *args],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errf,
                                creationflags=NO_WINDOW)
        try:
            for raw in proc.stdout:
                line = raw.decode("ascii", "ignore").strip()
                if progress and duration > 0 and line.startswith(("out_time_us=", "out_time_ms=")):
                    try:
                        progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration))
                    except ValueError:
                        pass
                if cancelled and cancelled():
                    proc.kill()
                    break
        except BaseException:          # e.g. Cancelled raised by the progress callback
            proc.kill()
            proc.wait()
            raise
        proc.wait()
        if cancelled and cancelled():
            raise Cancelled()
        if proc.returncode != 0:
            errf.seek(0)
            raise RuntimeError(errf.read().decode("utf-8", "ignore")[-400:].strip() or f"exit {proc.returncode}")


def has_encoder(name: str) -> bool:
    if not hasattr(has_encoder, "_cache"):
        try:
            has_encoder._cache = run([FFMPEG, "-hide_banner", "-encoders"]).stdout.decode("utf-8", "ignore")
        except (OSError, subprocess.TimeoutExpired):
            has_encoder._cache = ""
    return f" {name} " in has_encoder._cache


def nvenc_ok() -> bool:
    if not hasattr(nvenc_ok, "_v"):
        ok = False
        if has_encoder("h264_nvenc"):
            try:
                r = run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.2",
                         "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=30)
                ok = r.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
        nvenc_ok._v = ok
    return nvenc_ok._v


def probe(path: Path) -> dict:
    try:
        r = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    except subprocess.TimeoutExpired:
        raise ValueError("讀取媒體資訊逾時，檔案可能已損毀")
    if r.returncode != 0:
        raise ValueError("無法讀取媒體檔：" + r.stderr.decode("utf-8", "ignore")[-300:])
    d = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
    fmt = d.get("format", {})
    info = {"duration": float(fmt.get("duration") or 0), "format": fmt.get("format_name", ""),
            "has_video": False, "has_audio": False, "vcodec": None, "acodec": None,
            "width": 0, "height": 0, "fps": 0.0, "rotation": 0}
    for s in d.get("streams", []):
        is_cover = (s.get("disposition") or {}).get("attached_pic", 0) == 1 or s.get("codec_name") in ("png", "bmp", "gif")
        if s.get("codec_type") == "video" and not info["has_video"] and not is_cover:
            info.update(has_video=True, vcodec=s.get("codec_name"), width=int(s.get("width") or 0),
                        height=int(s.get("height") or 0))
            try:
                n, dd = (s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1").split("/")
                info["fps"] = float(n) / float(dd) if float(dd) else 0.0
            except ValueError:
                pass
            rot = 0
            for sd in s.get("side_data_list", []) or []:
                if "rotation" in sd:
                    rot = int(sd["rotation"])
            rot = int((s.get("tags") or {}).get("rotate", rot))
            if abs(rot) in (90, 270):
                info["width"], info["height"] = info["height"], info["width"]
            info["rotation"] = rot
            if not info["duration"]:
                info["duration"] = float(s.get("duration") or 0)
        elif s.get("codec_type") == "audio" and not info["has_audio"]:
            info.update(has_audio=True, acodec=s.get("codec_name"), channels=s.get("channels"),
                        sample_rate=int(s.get("sample_rate") or 0))
            if not info["duration"]:
                info["duration"] = float(s.get("duration") or 0)
    return info


def make_preview(src: Path, info: dict, out_dir: Path, progress=None, cancelled=None) -> Path:
    """Return a file the browser can play smoothly (original when possible)."""
    ext = src.suffix.lower()
    dur = info.get("duration") or 0
    if info["has_video"]:
        if ext in BROWSER_VIDEO_EXT and info["vcodec"] in BROWSER_VCODECS and info["acodec"] in BROWSER_ACODECS:
            return src
        out = out_dir / "preview.mp4"
        venc = ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"] if nvenc_ok() else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"]
        try:
            ffmpeg(["-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", *venc, "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)], dur, progress, cancelled)
        except RuntimeError as e:
            raise RuntimeError(f"預覽轉檔失敗：{e}")
        return out
    # audio
    if ext in BROWSER_AUDIO_EXT and info["acodec"] in {"aac", "mp3", "opus", "vorbis", "flac"} and src.stat().st_size < 400e6:
        return src
    out = out_dir / "preview.m4a"
    try:
        ffmpeg(["-i", str(src), "-vn", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)],
               dur, progress, cancelled)
    except RuntimeError as e:
        raise RuntimeError(f"音訊轉檔失敗：{e}")
    return out


def load_audio16k(src: Path, cache: Optional[Path] = None, duration: float = 0.0, progress=None,
                  cancelled=None) -> np.ndarray:
    """Mono 16 kHz float32 PCM. FFmpeg writes straight to disk, so peak memory
    is one copy of the audio."""
    if cache and cache.exists():
        return np.fromfile(cache, dtype=np.float32)
    if cache:
        out = cache.with_name(cache.name + ".part")
    else:
        fd, name = tempfile.mkstemp(suffix=".f32")
        os.close(fd)
        out = Path(name)
    try:
        try:
            ffmpeg(["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", str(out)],
                   duration, progress, cancelled)
        except RuntimeError as e:
            raise RuntimeError(f"音訊解碼失敗：{e}")
        wav = np.fromfile(out, dtype=np.float32)
        peak = float(np.max(np.abs(wav))) if wav.size else 0.0
        if peak > 1.0:
            wav /= peak
        if cache:
            wav.tofile(out)
            os.replace(out, cache)
        return wav
    finally:
        if out.exists():
            try:
                out.unlink()
            except OSError:
                pass


def peaks(wav: np.ndarray, per_sec: int = 50) -> list:
    """Normalised peak envelope (0-255) for the timeline waveform."""
    hop = 16000 // per_sec
    n = int(np.ceil(len(wav) / hop))
    if n == 0:
        return []
    pad = np.zeros(n * hop, dtype=np.float32)
    pad[: len(wav)] = np.abs(wav)
    pk = pad.reshape(n, hop).max(axis=1)
    ref = float(np.percentile(pk, 99.5)) or 1.0
    pk = np.clip(pk / ref, 0, 1) ** 0.7
    return (pk * 255).astype(np.uint8).tolist()


def image_info(path: Path) -> dict:
    from PIL import Image
    with Image.open(path) as im:
        return {"width": im.width, "height": im.height}
