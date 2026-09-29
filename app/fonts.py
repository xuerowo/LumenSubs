"""Local font cache.

The page and the subtitle renderer use Google Fonts families. Instead of
letting the browser fetch them from Google on every start, the server
downloads each font file (full static TTF) the first time it is needed and
serves it from workspace/fonts afterwards, so

  * the app works offline once a font has been used (or prefetched at setup),
  * the browser never contacts a third party,
  * the same TTF files can be embedded in MKV exports (soft subtitles).

Each face gets the unicode-range of its family (taken from Google's sliced
web CSS), so the browser only downloads fallback fonts for scripts that
actually appear in the text.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from . import config

log = logging.getLogger("lumen.fonts")

FAMILIES: Dict[str, List[int]] = {
    "Manrope": [400, 500, 600, 700, 800],
    "JetBrains Mono": [400, 500, 600],
    "Noto Sans TC": [400, 500, 700, 900],
    "Noto Serif TC": [500, 700, 900],
    "LXGW WenKai TC": [400, 700],
    "Noto Sans SC": [400, 500, 700, 900],
    "Noto Sans JP": [400, 500, 700, 900],
    "Noto Sans KR": [400, 500, 700, 900],
    "Noto Sans Thai": [400, 500, 700, 900],
    "Noto Sans Arabic": [400, 500, 700, 900],
    "Noto Sans Hebrew": [400, 500, 700, 900],
    "Noto Sans Devanagari": [400, 500, 700, 900],
    "Inter": [400, 500, 700, 900],
}
# what a fresh install needs for the UI and the default subtitle style
DEFAULTS = [("Manrope", w) for w in FAMILIES["Manrope"]] + [("JetBrains Mono", w) for w in FAMILIES["JetBrains Mono"]] + \
           [("Noto Sans TC", w) for w in (400, 500, 700)]

API = os.environ.get("LUMEN_FONTS_API", "https://fonts.googleapis.com").rstrip("/")
FONT_DIR = config.WORK_DIR / "fonts"
INDEX = FONT_DIR / "index.json"
UA_TTF = "Mozilla/4.0 (compatible)"                 # legacy UA → full static TTF files
UA_WEB = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/126.0 Safari/537.36")              # modern UA → sliced woff2 with unicode-range

_lock = threading.Lock()
_file_locks: Dict[str, threading.Lock] = {}
_index: Optional[dict] = None
_failed_at = 0.0


def file_name(family: str, weight: int) -> str:
    return f"{family.replace(' ', '')}-{int(weight)}.ttf"


def _get(url: str, ua: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _css_url() -> str:
    fam = "&".join("family=" + urllib.parse.quote_plus(f) + ":wght@" + ";".join(map(str, ws))
                   for f, ws in FAMILIES.items())
    return f"{API}/css2?{fam}&display=swap"


_FACE = re.compile(r"@font-face\s*{([^}]*)}", re.S)


def _faces(css: str):
    for body in _FACE.findall(css):
        fam = re.search(r"font-family:\s*'([^']+)'", body)
        wt = re.search(r"font-weight:\s*(\d+)", body)
        url = re.search(r"url\(([^)]+)\)", body)
        rng = re.search(r"unicode-range:\s*([^;]+);", body)
        if fam and wt and url:
            yield fam.group(1), int(wt.group(1)), url.group(1).strip("'\""), rng.group(1).strip() if rng else ""


def _merge_ranges(parts: List[str], slack: int = 0) -> str:
    """Union of unicode-range lists. Gaps up to `slack` code points are
    bridged: coarse ranges still keep scripts apart, but CSS stays small."""
    iv = []
    for p in parts:
        for tok in p.split(","):
            tok = tok.strip().upper().replace("U+", "")
            if not tok:
                continue
            if "?" in tok:
                a, b = int(tok.replace("?", "0"), 16), int(tok.replace("?", "F"), 16)
            elif "-" in tok:
                a, b = (int(x, 16) for x in tok.split("-", 1))
            else:
                a = b = int(tok, 16)
            iv.append((a, b))
    iv.sort()
    out = []
    for a, b in iv:
        if out and a <= out[-1][1] + 1 + slack:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return ", ".join(f"U+{a:X}" if a == b else f"U+{a:X}-{b:X}" for a, b in out)


def build_index() -> dict:
    """Ask the fonts API where every face lives (needs network, ~2 requests)."""
    ttf = {(f, w): u for f, w, u, _ in _faces(_get(_css_url(), UA_TTF).decode("utf-8"))}
    ranges: Dict[str, List[str]] = {}
    try:
        for f, _w, _u, r in _faces(_get(_css_url(), UA_WEB).decode("utf-8")):
            if r:
                ranges.setdefault(f, []).append(r)
    except Exception as e:                    # ranges only make loading smarter
        log.warning("could not read unicode ranges: %s", e)
    faces = []
    for (f, w), u in sorted(ttf.items()):
        if not u.startswith("https://"):
            continue
        faces.append({"family": f, "weight": w, "url": u, "file": file_name(f, w),
                      "range": _merge_ranges(ranges.get(f, []), slack=128)})
    if not faces:
        raise RuntimeError("fonts API returned no faces")
    d = {"version": 1, "faces": faces}
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, INDEX)
    return d


def index(build: bool = True) -> Optional[dict]:
    global _index, _failed_at
    with _lock:
        if _index is None:
            try:
                _index = json.loads(INDEX.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _index = None
        if _index is None and build and time.time() - _failed_at > 60:
            try:
                _index = build_index()
            except Exception as e:
                _failed_at = time.time()
                log.warning("font index unavailable (offline?): %s", e)
        return _index


def css() -> str:
    """@font-face rules pointing at /fonts/file/…; empty when offline and never built."""
    d = index()
    if not d:
        return "/* fonts unavailable (offline) — system fonts are used */\n"
    out = []
    for f in d["faces"]:
        rng = f";unicode-range:{f['range']}" if f.get("range") else ""
        out.append(f"@font-face{{font-family:'{f['family']}';font-style:normal;font-weight:{f['weight']};"
                   f"font-display:swap;src:url(/fonts/file/{f['file']}) format('truetype'){rng}}}")
    return "\n".join(out) + "\n"


def face(name: str) -> Optional[dict]:
    d = index(build=False) or {}
    return next((f for f in d.get("faces", []) if f["file"] == name), None)


def ensure_file(name: str) -> Optional[Path]:
    """Path of a cached font file, downloading it on first use."""
    f = face(name)
    if not f:
        return None
    path = FONT_DIR / f["file"]
    if path.exists():
        return path
    with _lock:
        lk = _file_locks.setdefault(name, threading.Lock())
    with lk:
        if path.exists():
            return path
        data = _get(f["url"], UA_TTF, timeout=120)
        if data[:4] not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
            raise RuntimeError(f"unexpected font data for {name}")
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
        log.info("font cached: %s (%.1f MB)", name, len(data) / 1e6)
    return path


def pick(family: str, want: int) -> Optional[str]:
    """File name of the available weight of `family` closest to `want`."""
    ws = FAMILIES.get(family)
    if not ws:
        return None
    return file_name(family, min(ws, key=lambda w: (abs(w - want), w)))


def for_embedding(families: List[str]) -> List[Path]:
    """Regular + bold files of each family (ASS styles only know bold on/off)."""
    index()
    names = []
    for fam in dict.fromkeys(families):
        for w in (400, 700):
            n = pick(fam, w)
            if n and n not in names:
                names.append(n)
    out = []
    for n in names:
        try:
            p = ensure_file(n)
            if p:
                out.append(p)
        except Exception as e:
            log.warning("cannot embed %s: %s", n, e)
    return out


def prefetch(faces=DEFAULTS, say=print) -> bool:
    """Download the fonts a fresh install needs (used by bootstrap)."""
    if not index():
        return False
    ok = True
    for fam, w in faces:
        n = file_name(fam, w)
        if (FONT_DIR / n).exists():
            continue
        try:
            say(f"  {fam} {w}")
            ensure_file(n)
        except Exception as e:
            say(f"  （{fam} {w} 下載失敗：{e}）")
            ok = False
    return ok
