"""Turn word-level timestamps into readable subtitle cues.

Rules (in priority order):
  * hard breaks at sentence-final punctuation (incl. "." outside abbreviations)
    and long pauses
  * long sentences are split recursively at the best point, scored by
    punctuation, pause length, balance and linguistic "glue" (never start a
    line with a particle / end one on an article)
  * very short fragments are merged into a neighbour when they are close
  * timings get a small linger and a minimum on-screen duration without
    overlapping neighbours
"""
import re
from typing import List

from .asr import is_cjk, is_kept_char

# sentence-final marks, incl. Arabic/Persian "؟", Urdu "۔", Devanagari danda "। ॥"
# and the Greek question mark (U+037E, when it has not been normalised to ";")
STRONG = set("。！？!?…‼⁉؟۔।॥;")
WEAK = set("、，,;；:：—–،؛")
PERIODS = set(".．")
# a "." after these does not end a sentence
ABBREV = {"mr", "mrs", "ms", "dr", "prof", "sr", "sra", "jr", "st", "mt", "vs", "etc", "e.g", "i.e", "cf", "no",
          "vol", "fig", "inc", "ltd", "co", "corp", "dept", "approx", "u.s", "u.k", "a.m", "p.m", "hr", "fr", "hrn"}
CLOSERS = set("」』”’\")）】》〉")
EN_GLUE = {"a", "an", "the", "of", "to", "in", "on", "at", "for", "with", "and", "or", "but", "my", "your",
           "his", "her", "their", "our", "its", "this", "that", "is", "are", "was", "were", "be", "i", "we",
           "you", "they", "he", "she", "it", "not", "no", "very", "so", "as", "from", "by", "de", "la", "le",
           "les", "des", "du", "un", "une", "el", "los", "las", "der", "die", "das", "den", "dem", "ein", "eine"}
HIRA = re.compile(r"^[ぁ-ゟ]+$")
JA_PARTICLE_HEAD = {"は", "が", "を", "に", "へ", "と", "で", "の", "も", "や", "か", "ね", "よ", "な", "て", "た", "だ",
                    "から", "まで", "より", "けど", "けれど", "って", "ば", "し", "わ", "ぞ", "ぜ", "さ", "ん", "です",
                    "ます", "でしょ", "でしょう", "ない", "たい", "てる", "ている", "ちゃう", "じゃ", "ちゃ", "られ", "れる",
                    "られる", "せる", "させる", "う", "よう", "なさい", "かしら", "かな", "ので", "のに", "たら", "なら"}
MAX_DUR = 7.0
VOCAL_CHARS = set("あぁおぉうぅんはふへひほえぇいぃアァオォウゥンハフヘヒホエイーっッ啊哦嗯呃哎唔嘿哈呵喔噢嗚呜哇呀아어오우음흠하")
VOCAL_WORDS = {"oh", "ah", "ahh", "uh", "um", "umm", "mm", "mmm", "hm", "hmm", "huh", "ha", "haha", "ooh", "aah", "eh", "whoa", "wow"}
# short answers written with "vocal" characters that still carry meaning
MEANINGFUL = {"はい", "いいえ", "いえ", "ええ", "うん", "ううん", "いや", "おい", "ほら", "네", "예", "응", "아니", "嗯", "是", "對", "对",
              "好", "yes", "no", "yeah", "yep", "nope", "ok", "okay"}


def _norm_tok(t: str) -> str:
    return re.sub(r"[\W_ーっッ〜~]+", "", t.lower())


def is_vocal(text: str) -> bool:
    """Only interjection / vocalisation characters (moans, 'uh', 'うん'…).
    Anything with a digit is not ("3, 2, 1", "2020.")."""
    if any(c.isdigit() for c in text) or not any(is_kept_char(c) for c in text):
        return False
    words = re.findall(r"[A-Za-z]+", text)
    if words and not all(w.lower() in VOCAL_WORDS for w in words):
        return False
    return all(c in VOCAL_CHARS for c in text if is_kept_char(c) and not c.isascii())


def is_meaningful(text: str) -> bool:
    """A vocal-looking cue that is actually an answer ('はい', 'いいえ', '네'…)."""
    toks = [_norm_tok(x) for x in re.split(r"[\s、。，,.!！?？…・]+", text)]
    return any(t in MEANINGFUL for t in toks if t)


def width(text: str) -> float:
    w = 0.0
    for c in text:
        if is_cjk(c):
            w += 2
        elif is_kept_char(c) or c in "?!.,'":
            w += 1
        elif c == " ":
            w += 0.6
    return w


def _tail(text: str) -> str:
    t = text.rstrip()
    while t and t[-1] in CLOSERS:
        t = t[:-1]
    return t[-1:] if t else ""


def ends_sentence(text: str) -> bool:
    """Sentence-final punctuation; a period counts unless it closes an
    abbreviation ("Mr.", "e.g.") or an initial ("J.")."""
    tail = _tail(text)
    if tail in STRONG:
        return True
    if tail not in PERIODS:
        return False
    t = text.rstrip()
    while t and t[-1] in CLOSERS:
        t = t[:-1]
    if t.endswith(("..", "．．")):
        return True
    m = re.search(r"([^\W\d_]|[.'])+\.$", t)
    word = m.group(0)[:-1].lower() if m else ""
    if word in ABBREV or (len(word) == 1 and word.isalpha()):
        return False
    return True


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def segment(words: List[dict], max_chars: int = 42, duration: float = None) -> List[dict]:
    if not words:
        return []
    limit = max(16, int(max_chars))          # width units (CJK char = 2)
    # ---- 1. sentences
    sents, cur = [], []
    for i, w in enumerate(words):
        if cur and w["s"] - cur[-1]["e"] > 1.2:
            sents.append(cur)
            cur = []
        cur.append(w)
        if ends_sentence(w["text"]):
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)

    # ---- 2. split long sentences
    cues = []
    for s in sents:
        cues.extend(_split(s, limit))

    # ---- 3. merge tiny fragments
    cues = _merge_small(cues, limit)

    # ---- 4. timing polish
    out = []
    for ws in cues:
        text = _clean("".join(w["text"] for w in ws))
        if not any(is_kept_char(c) for c in text):
            continue
        out.append({"start": ws[0]["s"], "end": ws[-1]["e"], "src": text})
    out = _tidy_vocal(out, limit)
    polish_timing(out, duration)
    return out


def _glue(a: str, b: str) -> str:
    return _clean(a + ("" if any(is_cjk(ch) for ch in a + b) else " ") + b)


def _tidy_vocal(cues: List[dict], limit: int = 42) -> List[dict]:
    """Merge runs of vocalisations; a very short one joins a close neighbour
    rather than standing alone. Only an isolated one- or two-character breath
    ('あっ') is dropped — short answers such as 'はい' / 'いいえ' always stay."""
    merged = []
    for c in cues:
        p = merged[-1] if merged else None
        if p and is_vocal(p["src"]) and is_vocal(c["src"]) and c["start"] - p["end"] < 0.35 and c["end"] - p["start"] <= MAX_DUR:
            p["end"] = c["end"]
            if c["src"].rstrip("。、.,!！?？") != p["src"].rstrip("。、.,!！?？"):
                p["src"] = _compress_vocal(_glue(p["src"], c["src"]))
            continue
        merged.append(dict(c))
    out: List[dict] = []
    for i, c in enumerate(merged):
        if not (is_vocal(c["src"]) and c["end"] - c["start"] < 0.5) or is_meaningful(c["src"]):
            out.append(c)
            continue
        prev = out[-1] if out else None
        nxt = merged[i + 1] if i + 1 < len(merged) else None
        gp = c["start"] - prev["end"] if prev else 1e9
        gn = nxt["start"] - c["end"] if nxt else 1e9
        fits_p = prev is not None and gp < 0.35 and width(prev["src"] + c["src"]) <= limit and c["end"] - prev["start"] <= MAX_DUR
        fits_n = nxt is not None and gn < 0.35 and width(c["src"] + nxt["src"]) <= limit and nxt["end"] - c["start"] <= MAX_DUR
        if fits_p and (gp <= gn or not fits_n):
            prev["src"], prev["end"] = _glue(prev["src"], c["src"]), c["end"]
        elif fits_n:
            nxt["src"], nxt["start"] = _glue(c["src"], nxt["src"]), c["start"]
        elif len(_kept(c["src"])) > 2:
            out.append(c)
    return out


def _kept(t: str) -> str:
    return "".join(ch for ch in t if is_kept_char(ch))


def _compress_vocal(text: str, keep: int = 4) -> str:
    """'うん。うん。うん。…' ×12 → first few + ellipsis (keeps subtitles readable)."""
    toks = re.findall(r"[^、。,.!！?？\s]+[、。,.!！?？]*\s*", text)
    if len(toks) <= keep + 1:
        return text
    return "".join(toks[:keep]).rstrip("、。,. ") + "…"


def _split(ws: List[dict], limit: int) -> List[List[dict]]:
    text = "".join(w["text"] for w in ws)
    W = width(text)
    dur = ws[-1]["e"] - ws[0]["s"]
    if len(ws) < 2 or (W <= limit and dur <= MAX_DUR):
        return [ws]
    widths = [width(w["text"]) for w in ws]
    tot = sum(widths)
    best_k, best = None, -1e9
    left = 0.0
    for k in range(1, len(ws)):
        left += widths[k - 1]
        right = tot - left
        prev, nxt = ws[k - 1], ws[k]
        gap = max(0.0, nxt["s"] - prev["e"])
        tail = _tail(prev["text"])
        score = -abs(left - right) / tot * 3.0
        if ends_sentence(prev["text"]):
            score += 4
        elif tail in WEAK:
            score += 2.2
        score += min(gap, 1.0) * 3.0
        head = nxt["text"].strip()
        prev_word = re.sub(r"[^\w']", "", prev["text"]).lower()
        if prev_word in EN_GLUE:
            score -= 1.5
        if HIRA.match(head.rstrip("、。！？…ー")) and (head.rstrip("、。！？…") in JA_PARTICLE_HEAD or len(head) <= 1):
            score -= 2.5
        minside = min(left, right)
        if minside < 6 and gap < 0.6:
            score -= 3
        if score > best:
            best, best_k = score, k
    return _split(ws[:best_k], limit) + _split(ws[best_k:], limit)


def _merge_small(cues: List[List[dict]], limit: int) -> List[List[dict]]:
    out = []
    i = 0
    while i < len(cues):
        c = cues[i]
        cw = width("".join(w["text"] for w in c))
        d = c[-1]["e"] - c[0]["s"]
        if cw <= 6 and d < 0.9 and i + 1 < len(cues):
            n = cues[i + 1]
            gap = n[0]["s"] - c[-1]["e"]
            nw = width("".join(w["text"] for w in n))
            if gap < 0.3 and cw + nw <= limit and n[-1]["e"] - c[0]["s"] <= MAX_DUR:
                cues[i + 1] = c + n
                i += 1
                continue
        if out and cw <= 6 and d < 0.9:
            p = out[-1]
            gap = c[0]["s"] - p[-1]["e"]
            pw = width("".join(w["text"] for w in p))
            if gap < 0.3 and cw + pw <= limit and c[-1]["e"] - p[0]["s"] <= MAX_DUR:
                out[-1] = p + c
                i += 1
                continue
        out.append(c)
        i += 1
    return out


def polish_timing(cues: List[dict], duration: float = None, linger: float = 0.3, min_dur: float = 0.9, gap: float = 0.06):
    """Add a little linger and a minimum on-screen time without overlaps.
    Guarantees: start < end, starts non-decreasing, no cue overlaps the next."""
    n = len(cues)
    if not n:
        return
    for c in cues:
        c["start"] = max(0.0, c["start"] - 0.05)
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < n else (duration or c["end"] + 5)
        prv = cues[i - 1]["end"] if i > 0 else 0.0
        want = max(c["end"] + linger, c["start"] + min_dur)
        c["end"] = max(c["end"], min(want, nxt - gap))
        if c["end"] - c["start"] < min_dur:
            # still too short: start earlier, into the pause before it (never later)
            c["start"] = min(c["start"], max(prv + gap, c["end"] - min_dur, c["start"] - 0.3))
    # final pass: resolve anything the local adjustments could not
    prev_end = 0.0
    for i, c in enumerate(cues):
        s = round(max(c["start"], prev_end), 3)
        e = round(max(c["end"], s + 0.1), 3)
        if i + 1 < n:
            nxt = cues[i + 1]
            if nxt["start"] < e:
                if nxt["start"] >= s + 0.1:
                    e = round(nxt["start"], 3)          # trim this cue
                else:
                    nxt["start"] = e                   # no room: push the next one
                    nxt["end"] = max(nxt["end"], e + 0.1)
        c["start"], c["end"] = s, e
        prev_end = e
