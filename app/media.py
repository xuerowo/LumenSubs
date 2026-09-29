"""FFmpeg helpers: probing, browser-friendly previews, 16 kHz audio, peaks."""
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from . import config
from .jobs import Cancelled
from .procs import NO_WINDOW, popen


def _find_tool(name: str) -> str:
    """Absolute path of an FFmpeg tool: the project's ffmpeg/bin first, then
    PATH. Never a bare name, so Windows cannot pick up a same-named program
    from the current directory."""
    exe = name + (".exe" if os.name == "nt" else "")
    local = config.ROOT / "ffmpeg" / "bin" / exe
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    return os.path.abspath(found) if found else name


FFMPEG = _find_tool("ffmpeg")
FFPROBE = _find_tool("ffprobe")

BROWSER_VCODECS = {"h264", "vp8", "vp9", "av1"}
BROWSER_ACODECS = {"aac", "mp3", "opus", "vorbis", "flac", None}
BROWSER_VIDEO_EXT = {".mp4", ".m4v", ".webm", ".mov"}
BROWSER_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wav"}
# video codecs that can be copied into an MP4 preview as they are
MP4_COPY_VCODECS = {"h264", "av1", "vp9"}
# container formats that are playlists / indirections rather than media:
# FFmpeg would follow the references inside (possibly to other local files)
UNSAFE_FORMATS = {"hls", "concat", "ffconcat", "image2", "image2pipe", "tty", "lavfi", "sdp", "rtp", "rtsp",
                  "data", "mpjpeg", "dash", "webm_dash_manifest"}
# NVENC H.264 cannot encode frames larger than this in either dimension
NVENC_MAX = 4096
# FFmpeg that prints nothing for this long is considered stuck
STALL_SEC = 600
# input options for untrusted media files: only local files may be opened
SAFE_INPUT = ["-protocol_whitelist", "file,pipe"]
HDR_TRC = {"smpte2084", "arib-std-b67"}


def run(args, timeout: float = 60, **kw):
    """Short helper commands (probe, capability checks)."""
    return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, timeout=timeout, **kw)


def _tail(text: str, head: int = 600, tail: int = 900) -> str:
    """Keep the start (the actual cause) and the end of an FFmpeg error log."""
    text = text.strip()
    if len(text) <= head + tail + 20:
        return text
    return text[:head].rstrip() + "\n…\n" + text[-tail:].lstrip()


def run_process(cmd: List[str], on_line: Callable[[str], None], cancelled: Callable[[], bool],
                stderr=None, feeder: Optional[Callable] = None, stall: float = STALL_SEC) -> int:
    """Run FFmpeg with `-progress pipe:1`: stdout lines go to on_line (which
    may raise to abort); cancellation is checked twice a second even while
    FFmpeg prints nothing, and a process that stays silent for `stall` seconds
    is killed. Returns the exit code; raises Cancelled / RuntimeError."""
    proc = popen(cmd, stdin=subprocess.PIPE if feeder else subprocess.DEVNULL, stdout=subprocess.PIPE,
                 stderr=stderr if stderr is not None else subprocess.DEVNULL)
    lines: "queue.Queue[Optional[bytes]]" = queue.Queue()

    def reader():
        try:
            for raw in proc.stdout:
                lines.put(raw)
        finally:
            lines.put(None)
    threading.Thread(target=reader, daemon=True).start()
    if feeder:
        threading.Thread(target=feeder, args=(proc,), daemon=True).start()
    last = time.time()
    try:
        while True:
            try:
                raw = lines.get(timeout=0.5)
            except queue.Empty:
                raw = b""
            if raw is None:
                break
            if raw:
                last = time.time()
                on_line(raw.decode("ascii", "ignore").strip())
            if cancelled():
                proc.kill()
                proc.wait()
                raise Cancelled()
            if time.time() - last > stall:
                proc.kill()
                proc.wait()
                raise RuntimeError(f"FFmpeg 超過 {int(stall // 60)} 分鐘沒有任何進度，已中止（檔案可能損毀）")
        proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        raise
    return proc.returncode


def ffmpeg(args, duration: float = 0.0, progress: Optional[Callable[[float], None]] = None,
           cancelled: Optional[Callable[[], bool]] = None):
    """Run a (possibly long) FFmpeg command: reports progress 0‥1, stops as
    soon as `cancelled()` turns true and raises RuntimeError with the start
    and end of stderr on failure. stderr goes to a temp file so the pipe never blocks."""
    cancelled = cancelled or (lambda: False)

    def on_line(line: str):
        if progress and duration > 0 and line.startswith(("out_time_us=", "out_time_ms=")):
            try:
                progress(min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration))
            except ValueError:
                pass
    with tempfile.TemporaryFile() as errf:
        rc = run_process([FFMPEG, "-y", "-hide_banner", "-nostats", "-loglevel", "error", "-progress", "pipe:1", *args],
                         on_line, cancelled, stderr=errf)
        if rc != 0:
            errf.seek(0)
            raise RuntimeError(_tail(errf.read().decode("utf-8", "ignore")) or f"exit {rc}")


def has_encoder(name: str) -> bool:
    if not hasattr(has_encoder, "_cache"):
        try:
            has_encoder._cache = run([FFMPEG, "-hide_banner", "-encoders"]).stdout.decode("utf-8", "ignore")
        except (OSError, subprocess.TimeoutExpired):
            has_encoder._cache = ""
    return f" {name} " in has_encoder._cache


def has_filter(name: str) -> bool:
    if not hasattr(has_filter, "_cache"):
        try:
            has_filter._cache = run([FFMPEG, "-hide_banner", "-filters"]).stdout.decode("utf-8", "ignore")
        except (OSError, subprocess.TimeoutExpired):
            has_filter._cache = ""
    return f" {name} " in has_filter._cache


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


def use_nvenc(width: int, height: int) -> bool:
    return nvenc_ok() and 0 < width <= NVENC_MAX and 0 < height <= NVENC_MAX


def x264_args(crf: int = 22, preset: str = "veryfast") -> list:
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def is_hdr(info: dict) -> bool:
    return (info.get("color_transfer") or "") in HDR_TRC


def tonemap_filter() -> Optional[str]:
    """HDR (PQ/HLG) → SDR BT.709, or None when this FFmpeg lacks zscale."""
    if not (has_filter("zscale") and has_filter("tonemap")):
        return None
    return ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv")


SDR_TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]


def probe(path: Path) -> dict:
    try:
        r = run([FFPROBE, "-v", "error", *SAFE_INPUT, "-print_format", "json", "-show_format", "-show_streams", str(path)])
    except subprocess.TimeoutExpired:
        raise ValueError("讀取媒體資訊逾時，檔案可能已損毀")
    except OSError:
        raise ValueError("找不到 ffprobe，請重新執行 start.bat 安裝 FFmpeg")
    if r.returncode != 0:
        raise ValueError("無法讀取媒體檔：" + r.stderr.decode("utf-8", "ignore")[-300:])
    d = json.loads(r.stdout.decode("utf-8", "ignore") or "{}")
    fmt = d.get("format", {})
    info = {"duration": float(fmt.get("duration") or 0), "format": fmt.get("format_name", ""),
            "has_video": False, "has_audio": False, "vcodec": None, "acodec": None,
            "width": 0, "height": 0, "fps": 0.0, "rotation": 0, "audio_tracks": 0, "subs": []}
    for s in d.get("streams", []):
        is_cover = (s.get("disposition") or {}).get("attached_pic", 0) == 1 or s.get("codec_name") in ("png", "bmp", "gif")
        if s.get("codec_type") == "video" and not info["has_video"] and not is_cover:
            info.update(has_video=True, vcodec=s.get("codec_name"), width=int(s.get("width") or 0),
                        height=int(s.get("height") or 0), color_transfer=s.get("color_transfer") or "",
                        color_primaries=s.get("color_primaries") or "")
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
        elif s.get("codec_type") == "audio":
            info["audio_tracks"] += 1
            if not info["has_audio"]:
                info.update(has_audio=True, acodec=s.get("codec_name"), channels=s.get("channels"),
                            sample_rate=int(s.get("sample_rate") or 0))
                if not info["duration"]:
                    info["duration"] = float(s.get("duration") or 0)
        elif s.get("codec_type") == "subtitle":
            info["subs"].append(s.get("codec_name") or "")
    info["hdr"] = is_hdr(info)
    return info


def check_safe(info: dict):
    """Refuse playlists and other indirections dressed up as a media file."""
    fmts = set((info.get("format") or "").split(","))
    if fmts & UNSAFE_FORMATS:
        raise ValueError("這個檔案不是一般的影音檔（是播放清單或參照其他檔案的格式），為了安全不予讀取")


def make_preview(src: Path, info: dict, out_dir: Path, progress=None, cancelled=None) -> Path:
    """Return a file the browser can play smoothly (original when possible)."""
    ext = src.suffix.lower()
    dur = info.get("duration") or 0
    if info["has_video"]:
        vc, ac = info["vcodec"], info["acodec"]
        hdr = info.get("hdr")
        if ext in BROWSER_VIDEO_EXT and vc in BROWSER_VCODECS and ac in BROWSER_ACODECS and not hdr:
            return src
        out = out_dir / "preview.mp4"
        base = [*SAFE_INPUT, "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?"]
        aud = ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)]
        if vc in MP4_COPY_VCODECS and not hdr:
            # only the audio (or the container) is the problem: keep the picture as it is
            try:
                ffmpeg([*base, "-c:v", "copy", *aud], dur, progress, cancelled)
                return out
            except RuntimeError:
                out.unlink(missing_ok=True)
        vf = ["-vf", tonemap_filter() + ",format=yuv420p"] if hdr and tonemap_filter() else ["-pix_fmt", "yuv420p"]
        tags = SDR_TAGS if hdr and tonemap_filter() else []
        encoders = []
        if use_nvenc(info.get("width") or 0, info.get("height") or 0):
            encoders.append(["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"])
        encoders.append(x264_args(22))
        err = None
        for venc in encoders:
            try:
                ffmpeg([*base, *venc, *vf, *tags, *aud], dur, progress, cancelled)
                return out
            except RuntimeError as e:
                err = e
                out.unlink(missing_ok=True)
        raise RuntimeError(f"預覽轉檔失敗：{err}")
    # audio
    if ext in BROWSER_AUDIO_EXT and info["acodec"] in {"aac", "mp3", "opus", "vorbis", "flac"} and src.stat().st_size < 400e6:
        return src
    out = out_dir / "preview.m4a"
    try:
        ffmpeg([*SAFE_INPUT, "-i", str(src), "-vn", "-map", "0:a:0", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(out)], dur, progress, cancelled)
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
            ffmpeg([*SAFE_INPUT, "-i", str(src), "-vn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "f32le", str(out)],
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
    """Normalised peak envelope (0-255) for the timeline waveform. Works on
    views of the audio, so long recordings need no extra copies of it."""
    hop = 16000 // per_sec
    n_full = len(wav) // hop
    pk = np.empty(n_full + (1 if len(wav) % hop else 0), dtype=np.float32)
    if pk.size == 0:
        return []
    step = hop * 20000                       # ~6.7 min of audio per slice
    for a in range(0, n_full * hop, step):
        b = min(n_full * hop, a + step)
        pk[a // hop: b // hop] = np.abs(wav[a:b].reshape(-1, hop)).max(axis=1)
    if len(wav) % hop:
        pk[-1] = float(np.abs(wav[n_full * hop:]).max())
    ref = float(np.percentile(pk, 99.5)) or 1.0
    pk = np.clip(pk / ref, 0, 1) ** 0.7
    return (pk * 255).astype(np.uint8).tolist()


def image_info(path: Path) -> dict:
    from PIL import Image
    with Image.open(path) as im:
        return {"width": im.width, "height": im.height}
