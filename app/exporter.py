"""Video export with FFmpeg.

Hard subtitles: the browser renders every cue with the exact same canvas
renderer used by the live preview (full-frame transparent PNGs). They are
sequenced with the concat demuxer and overlaid — so the output is pixel-
identical to the preview (fonts, strokes, shadows, rounded boxes, RTL…).
Soft subtitles: an ASS track muxed into MKV.
Audio mode: background still (rendered by the browser, incl. crop + veil)
+ optional audio-reactive bars streamed from numpy + subtitles.
"""
import logging
import os
import re
import subprocess
from pathlib import Path

import numpy as np

from .config import replace_retry
from .media import FFMPEG, SAFE_INPUT, SDR_TAGS, _tail, run_process, tonemap_filter, use_nvenc
from .viz import CENTER_Y, VIZ_FPS, MaskRenderer

log = logging.getLogger("lumen.export")

CQ = {"h": 19, "m": 23, "l": 29}
CRF = {"h": 18, "m": 21, "l": 26}
ABR = {"h": "256k", "m": "192k", "l": "128k"}
# subtitle codecs that can be copied from the source into an MKV unchanged
MKV_SUB_COPY = {"ass", "ssa", "subrip", "srt", "hdmv_pgs_subtitle", "dvd_subtitle", "webvtt"}


def _venc(q: str, still: bool = False, nvenc: bool = True):
    if nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(CQ.get(q, 23)),
                "-b:v", "0", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "medium", "-crf", str(CRF.get(q, 21)), "-pix_fmt", "yuv420p"] + \
        (["-tune", "stillimage"] if still else [])


def _encode(job, build, W: int, H: int, dur: float, feeder_factory=None, log_path: Path = None):
    """Encode with NVENC when it can handle the frame size; if the hardware
    encoder fails (too large, driver trouble, another app holding it), fall
    back to libx264 once instead of failing the whole export."""
    tries = [True, False] if use_nvenc(W, H) else [False]
    for k, nv in enumerate(tries):
        try:
            run_ffmpeg(job, build(nv), dur, feeder=feeder_factory() if feeder_factory else None, log_path=log_path)
            return
        except RuntimeError as e:
            if k + 1 == len(tries) or job.cancelled():
                raise
            log.warning("NVENC export failed, retrying with libx264: %s", str(e)[:300])
            job.progress = 0.05


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|#%&\r\n]+', "_", s or "").strip(" .") or "LumenSubs"
    return s[:80]


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 999):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    return path


FRAME_NAME = re.compile(r"^c\d{5}\.png$")


def write_concat(frames_dir: Path, cues: list, duration: float) -> Path:
    """Concat list of subtitle frames. Frame names are validated so the list
    can only reference PNGs inside frames_dir (read with the demuxer's safe mode)."""
    lines = ["ffconcat version 1.0"]
    t = 0.0
    blank = "blank.png"
    for c in sorted(cues, key=lambda c: float(c["start"])):
        if not FRAME_NAME.match(str(c.get("frame", ""))):
            raise ValueError(f"invalid frame name: {c.get('frame')!r}")
        s, e = max(t, float(c["start"])), min(duration, float(c["end"]))
        if e - s < 0.01:
            continue
        if s - t > 0.0005:
            lines += [f"file '{blank}'", f"duration {s - t:.4f}"]
        lines += [f"file '{c['frame']}'", f"duration {e - s:.4f}"]
        t = e
    lines += [f"file '{blank}'", f"duration {max(0.05, duration - t + 1):.4f}", f"file '{blank}'"]
    p = frames_dir / "subs.ffconcat"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _grad_png(path: Path, w: int, h: int):
    from PIL import Image
    y = np.linspace(0, 1, h)[:, None]
    top, bot = np.array([255, 255, 255]), np.array([170, 230, 255])
    # symmetric: bright in the middle row band, cooler at the tips
    k = np.abs(y - 0.5) * 2
    rgb = (top * (1 - k) + bot * k)[:, None, :].repeat(w, axis=1).reshape(h, w, 3)
    Image.fromarray(rgb.astype(np.uint8), "RGB").save(path)


def run_ffmpeg(job, args, duration: float, feeder=None, log_path: Path = None):
    cmd = [FFMPEG, "-y", "-hide_banner", "-nostats", "-progress", "pipe:1", *args]
    errf = open(log_path, "wb") if log_path else subprocess.DEVNULL

    def on_line(line: str):
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=")[1])
                job.progress = max(job.progress, min(0.99, 0.05 + 0.94 * us / 1e6 / max(0.1, duration)))
            except ValueError:
                pass
    rc = None
    try:
        rc = run_process(cmd, on_line, job.cancelled, stderr=errf, feeder=feeder)
    finally:
        if log_path:
            errf.close()
        if rc != 0:
            try:
                Path(args[-1]).unlink(missing_ok=True)
            except OSError:
                pass
    if rc != 0:
        tail = log_path.read_text("utf-8", "ignore") if log_path else ""
        raise RuntimeError("FFmpeg 輸出失敗：" + _tail(tail, 400, 600))


def _attach(files) -> list:
    """Embed font files (MKV attachments) so players render the ASS styles as designed."""
    args = []
    for i, f in enumerate(files or []):
        # set the file name explicitly — FFmpeg would otherwise store the full local path
        args += ["-attach", str(f), f"-metadata:s:t:{i}", "mimetype=application/x-truetype-font",
                 f"-metadata:s:t:{i}", f"filename={Path(f).name}"]
    return args


def _finish(tmp: Path, final: Path) -> dict:
    """Give the finished file its real name. If the target is locked (e.g. a
    player has the previous version open), keep the result under a new name
    instead of throwing away a long encode."""
    renamed = False
    try:
        replace_retry(tmp, final, tries=15)
    except OSError:
        alt = unique(final.with_name(f"{final.stem} (新){final.suffix}"))
        os.replace(tmp, alt)
        final, renamed = alt, True
    return {"file": final.name, "path": str(final), "size": final.stat().st_size, "renamed": renamed}


def check_writable(final: Path):
    """Fail before encoding (not after) when the target cannot be written."""
    if final.exists():
        try:
            with open(final, "r+b"):
                pass
        except PermissionError:
            raise PermissionError(f"「{final.name}」正被其他程式使用（例如播放器），請先關閉它，或改用其他檔名")
    probe = final.with_name(f".{final.stem}.writetest")
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        raise PermissionError(f"無法寫入資料夾「{final.parent}」：{e.strerror or e}")


def export_video(job, meta: dict, frames_dir: Path, out_dir: Path, media: dict) -> dict:
    """meta: mode, burn, width, height, quality, duration, cues[{start,end,frame}],
    ass, viz, name.  media: {'video': Path, 'info': probe dict} or
    {'audio': Path, 'viz': Path|None}, plus optional 'fonts': [Path] to embed
    in soft-subtitle MKVs."""
    W, H = int(meta["width"]) // 2 * 2, int(meta["height"]) // 2 * 2
    q = meta.get("quality", "m")
    dur = float(meta["duration"])
    soft = meta.get("burn") == "soft"
    ext = ".mkv" if soft else ".mp4"
    if meta.get("out_path"):                       # user-chosen destination
        final = Path(meta["out_path"])
        if final.suffix.lower() != ext:
            final = final.with_name(final.name + ext) if final.suffix.lower() not in (".mp4", ".mkv") else final.with_suffix(ext)
        final.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_dir.mkdir(parents=True, exist_ok=True)
        final = unique(out_dir / (safe_name(meta.get("name")) + ext))
    check_writable(final)
    # encode into a temp file next to the target; only a finished file gets the real name
    out = final.with_name(f".{final.stem}.part{ext}")
    log_path = frames_dir / "ffmpeg.log"
    ass_path = None
    if soft:
        ass_path = frames_dir / "subs.ass"
        ass_path.write_text(meta.get("ass", ""), encoding="utf-8-sig")
    job.set(0.03)
    lang_tag = f"language={meta.get('sub_lang', 'und')}"

    if meta["mode"] == "video":
        src = media["video"]
        info = media.get("info") or {}
        if soft:
            # our track first (s:0, default); copyable subtitle tracks of the source follow
            keep_subs = [i for i, c in enumerate(info.get("subs") or []) if c in MKV_SUB_COPY]
            args = [*SAFE_INPUT, "-i", str(src), "-i", str(ass_path), "-map", "0:v:0", "-map", "0:a?", "-map", "1:0"]
            for i in keep_subs:
                args += ["-map", f"0:s:{i}"]
            args += ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy", "-c:s:0", "ass",
                     "-metadata:s:s:0", lang_tag, "-disposition:s:0", "default"]
            for k in range(1, len(keep_subs) + 1):
                args += [f"-disposition:s:{k}", "0"]
            args += [*_attach(media.get("fonts")), str(out)]
            run_ffmpeg(job, args, dur, log_path=log_path)
            return _finish(out, final)
        lst = write_concat(frames_dir, meta["cues"], dur)
        # HDR sources are tone-mapped to SDR first: the subtitle frames are SDR
        # colours, and the result then matches the (SDR) preview
        tm = tonemap_filter() if info.get("hdr") else None
        pre = f"{tm}," if tm else ""
        fc = (f"[0:v]{pre}scale={W}:{H}:flags=lanczos,setsar=1[b];[1:v]format=rgba[s];"
              f"[b][s]overlay=0:0:format=auto:eof_action=repeat,format=yuv420p[v]")

        def build(nv):
            return [*SAFE_INPUT, "-i", str(src), "-f", "concat", "-i", str(lst), "-filter_complex", fc,
                    "-map", "[v]", "-map", "0:a?", *_venc(q, nvenc=nv), *(SDR_TAGS if tm else []),
                    "-c:a", "aac", "-b:a", ABR.get(q, "192k"), "-movflags", "+faststart", "-t", f"{dur:.3f}", str(out)]
        _encode(job, build, W, H, dur, log_path=log_path)
        return _finish(out, final)

    # ---------------- audio mode
    audio = media["audio"]
    fps = VIZ_FPS
    # decode the still once and loop it in memory (-loop 1 would re-decode every frame)
    inputs = ["-i", str(frames_dir / "bg.png"), *SAFE_INPUT, "-i", str(audio)]
    fc = [f"[0:v]scale={W}:{H},setsar=1,format=rgba,loop=loop=-1:size=1:start=0,setpts=N/{fps}/TB[bg0]"]
    last = "bg0"
    n_in = 2
    feeder = None
    bands = None
    if meta.get("viz") and media.get("viz") and Path(media["viz"]).exists():
        bands = np.fromfile(media["viz"], dtype=np.uint8).reshape(-1, 28)
        mr = MaskRenderer(W // 2, H // 2)          # half-res mask, upscaled smoothly
        bw_full, bh_full = mr.box_w * 2, mr.box_h * 2
        _grad_png(frames_dir / "vizgrad.png", bw_full, bh_full)
        inputs += ["-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{mr.box_w}x{mr.box_h}", "-framerate", str(fps), "-i", "pipe:0",
                   "-i", str(frames_dir / "vizgrad.png")]
        vi, gi = n_in, n_in + 1
        n_in += 2
        x = (W - bw_full) // 2
        y = int(round(H * CENTER_Y - bh_full / 2))
        glow = max(2, W / 160)
        fc += [f"[{vi}:v]scale={bw_full}:{bh_full}:flags=bicubic,format=gray[m]",
               f"[{gi}:v]format=rgba,loop=loop=-1:size=1:start=0,setpts=N/{fps}/TB[col]", "[col][m]alphamerge,colorchannelmixer=aa=0.95[bars]",
               f"[bars]split[b1][b2]", f"[b2]gblur=sigma={glow:.1f},colorchannelmixer=rr=0.63:gg=0.86:bb=1:aa=0.7[gl]",
               f"[{last}][gl]overlay={x}:{y}:shortest=1[bgg]", f"[bgg][b1]overlay={x}:{y}:shortest=1[bgv]"]
        last = "bgv"
        total_frames = int(np.ceil(dur * fps)) + 2

        def feeder(proc, mr=mr, bands=bands, total=total_frames):
            try:
                cache = {}
                for f in range(total):
                    row = bands[min(f, len(bands) - 1)]
                    key = row.tobytes()
                    m = cache.get(key)
                    if m is None:
                        m = mr.render(row).tobytes()
                        if len(cache) < 4096:
                            cache[key] = m
                    proc.stdin.write(m)
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
    sub_in = None
    if not soft:
        lst = write_concat(frames_dir, meta["cues"], dur)
        inputs += ["-f", "concat", "-i", str(lst)]
        fc += [f"[{n_in}:v]format=rgba[s]", f"[{last}][s]overlay=0:0:format=auto:eof_action=repeat[vs]"]
        last = "vs"
        n_in += 1
    else:
        inputs += ["-i", str(ass_path)]
        sub_in = n_in
        n_in += 1
    fc.append(f"[{last}]format=yuv420p[v]")

    def build(nv):
        args = [*inputs, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "1:a:0"]
        if soft:
            args += ["-map", f"{sub_in}:0", "-c:s", "ass", "-metadata:s:s:0", lang_tag,
                     "-disposition:s:0", "default", *_attach(media.get("fonts"))]
        args += [*_venc(q, still=bands is None, nvenc=nv), "-r", str(fps), "-c:a", "aac", "-b:a", ABR.get(q, "192k"),
                 "-t", f"{dur:.3f}"]
        if not soft:
            args += ["-movflags", "+faststart"]
        args.append(str(out))
        return args
    _encode(job, build, W, H, dur, feeder_factory=(lambda: feeder) if feeder else None, log_path=log_path)
    return _finish(out, final)
