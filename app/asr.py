"""Qwen3-ASR-1.7B transcription + Qwen3-ForcedAligner-0.6B word timestamps.

Pipeline:
  1. Silero VAD → cut the timeline into ≤20 s utterance windows at pauses,
     grouped into ~100 s (≤150 s) long-context chunks
  2. Batched ASR twice: on the long chunks (best wording) and on the windows
     (local precision); the long text is distributed back over the windows
  3. Forced alignment inside each window → token timestamps
  4. Map aligner tokens back onto the punctuated transcript and repair
     implausible spans (aligner swallowing silence) using the VAD curve
"""
import difflib
import gc
import logging
import re
import threading
import time
import unicodedata
from typing import Callable, List, Optional

import numpy as np

from . import config
from .vad import FRAME_SEC, SR, SileroVAD, group_windows, plan_windows, speech_regions

log = logging.getLogger("lumen.asr")

# UI language code → Qwen3-ASR language name
LANG_TO_QWEN = {
    "zh-TW": "Chinese", "zh-CN": "Chinese", "yue": "Cantonese", "en": "English", "ja": "Japanese",
    "ko": "Korean", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
    "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "th": "Thai", "vi": "Vietnamese", "id": "Indonesian",
    "ms": "Malay", "fil": "Filipino", "tr": "Turkish", "nl": "Dutch", "pl": "Polish", "sv": "Swedish",
    "da": "Danish", "fi": "Finnish", "cs": "Czech", "el": "Greek", "hu": "Hungarian", "ro": "Romanian",
    "fa": "Persian", "mk": "Macedonian",
}
QWEN_TO_LANG = {v: k for k, v in LANG_TO_QWEN.items()}
QWEN_TO_LANG["Chinese"] = "zh-CN"

CJK_LANGS = {"Chinese", "Cantonese", "Japanese"}


def is_kept_char(ch: str) -> bool:
    if ch == "'":
        return True
    cat = unicodedata.category(ch)
    return cat.startswith("L") or cat.startswith("N")


def is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF
            or 0xAC00 <= o <= 0xD7AF or 0x20000 <= o <= 0x2FA1F or 0xFF66 <= o <= 0xFF9D)


class RepeatStop:
    """Stop a sequence once its tail is a short pattern repeated many times
    (the decoder fell into a loop, e.g. on long moans or music). The
    repetition fixer collapses the loop afterwards; the local-window pass
    recovers any speech that followed."""

    def __init__(self, max_period: int = 8, min_reps: int = 16):
        self.max_period, self.min_reps = max_period, min_reps

    def __call__(self, input_ids, scores, **kw):
        import torch
        n = input_ids.shape[1]
        out = torch.zeros(input_ids.shape[0], dtype=torch.bool, device=input_ids.device)
        if n < self.min_reps * 2:
            return out
        for k in range(1, self.max_period + 1):
            span = k * self.min_reps
            if span > n:
                break
            tail = input_ids[:, -span:]
            per = tail.view(tail.shape[0], self.min_reps, k)
            out |= (per == per[:, :1, :]).all(dim=2).all(dim=1)
        return out


def _install_loop_guard(qwen_model):
    from transformers import StoppingCriteria, StoppingCriteriaList

    class _SC(StoppingCriteria):
        def __init__(self):
            self.rs = RepeatStop()

        def __call__(self, input_ids, scores, **kw):
            return self.rs(input_ids, scores)

    inner = qwen_model.model
    orig = inner.generate

    def generate(*a, **kw):
        # add ours even if the caller already passes stopping criteria
        kw["stopping_criteria"] = StoppingCriteriaList([*(kw.get("stopping_criteria") or []), _SC()])
        return orig(*a, **kw)
    inner.generate = generate


_SCRIPT = {
    "Japanese": re.compile(r"[぀-ヿ]"),
    "Korean": re.compile(r"[가-힯]"),
    "Thai": re.compile(r"[฀-๿]"),
    "Arabic": re.compile(r"[؀-ۿ]"),
    "Persian": re.compile(r"[؀-ۿ]"),
    "Russian": re.compile(r"[Ѐ-ӿ]"),
    "Hindi": re.compile(r"[ऀ-ॿ]"),
    "Greek": re.compile(r"[Ͱ-Ͽ]"),
}


def looks_like(text: str, lang: str) -> bool:
    rx = _SCRIPT.get(lang)
    return bool(rx and rx.search(text or ""))


class ASREngine:
    def __init__(self):
        self.model = None
        self.vad = None
        self.status = "idle"        # idle | loading | ready | error
        self.error = ""
        self.device = "cuda"
        self._lock = threading.Lock()
        self._run_lock = threading.RLock()
        self.aligner_langs = set()

    # ------------------------------------------------------------ loading
    def load(self, device: Optional[str] = None):
        with self._lock, self._run_lock:      # never swap models under a running job
            want = device or config.load_settings().get("device", "cuda")
            import torch
            if want == "cuda" and not torch.cuda.is_available():
                want = "cpu"
            if self.model is not None and self.device == want:
                return
            self.status, self.error = "loading", ""
            try:
                self._unload()
                from qwen_asr import Qwen3ASRModel
                dtype = torch.bfloat16 if want == "cuda" else torch.float32
                dm = "cuda:0" if want == "cuda" else "cpu"
                t = time.time()
                self.model = Qwen3ASRModel.from_pretrained(
                    config.ASR_MODEL,
                    forced_aligner=config.ALIGNER_MODEL,
                    forced_aligner_kwargs=dict(dtype=dtype, device_map=dm),
                    dtype=dtype, device_map=dm,
                    max_inference_batch_size=8, max_new_tokens=1024,
                )
                try:
                    self.aligner_langs = {x.lower() for x in (self.model.forced_aligner.get_supported_languages() or [])}
                except Exception:
                    self.aligner_langs = set()
                if not self.aligner_langs:
                    self.aligner_langs = {"chinese", "english", "cantonese", "french", "german", "italian",
                                          "japanese", "korean", "portuguese", "russian", "spanish"}
                _install_loop_guard(self.model)
                self.vad = SileroVAD()
                self.device = want
                self.status = "ready"
                log.info("ASR models loaded on %s in %.1fs", want, time.time() - t)
            except Exception as e:  # pragma: no cover
                log.exception("model load failed")
                self.status, self.error = "error", str(e)
                raise

    def _unload(self):
        if self.model is not None:
            self.model = None
            gc.collect()        # the loop-guard closure forms a reference cycle with the model
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass

    def ensure_loaded(self):
        if self.model is None:
            self.load()

    # ------------------------------------------------------------ main entry
    def transcribe(self, wav: np.ndarray, language: Optional[str] = None, context: str = "",
                   progress: Callable[[float], None] = lambda p: None) -> dict:
        """wav: mono float32 16 kHz. language: UI code or None/auto.
        Returns {language, language_name, words:[{text,s,e,lang}], vad, duration}."""
        if len(wav) < SR // 10:
            raise ValueError("音訊太短或沒有聲音內容（少於 0.1 秒）")
        self.ensure_loaded()
        with self._run_lock:
            return self._transcribe(wav, language, context, progress)

    def _decode(self, sub: List[np.ndarray], sub_l, context: str):
        """model.transcribe with an out-of-memory fallback: halve the batch."""
        try:
            return self.model.transcribe(audio=[(s, SR) for s in sub], context=context or "", language=sub_l)
        except Exception as e:
            if "out of memory" not in str(e).lower() or len(sub) == 1:
                raise
            import torch
            torch.cuda.empty_cache()
            h = len(sub) // 2
            log.warning("CUDA out of memory with batch %d, retrying with %d", len(sub), h)
            la, lb = (sub_l[:h], sub_l[h:]) if isinstance(sub_l, list) else (sub_l, sub_l)
            return list(self._decode(sub[:h], la, context)) + list(self._decode(sub[h:], lb, context))

    def _asr(self, segs: List[np.ndarray], langs, context: str, bs: int, prog=None):
        texts, names = [], []
        for bi in range(0, len(segs), bs):
            sub = segs[bi: bi + bs]
            sub_l = langs[bi: bi + bs] if isinstance(langs, list) else langs
            res = self._decode(sub, sub_l, context)
            for k, r in enumerate(res):
                forced = sub_l[k] if isinstance(sub_l, list) else sub_l
                texts.append((r.text or "").strip())
                names.append(forced or (r.language.split(",")[0] if r.language else ""))
            if prog:
                prog(min(len(segs), bi + bs) / max(1, len(segs)))
        return texts, names

    def _transcribe(self, wav, language, context, progress):
        total = len(wav) / SR
        progress(0.01)
        T = [time.time()]
        probs = self.vad.probs(wav)
        T.append(time.time())
        progress(0.05)
        regions = speech_regions(probs)
        forced = LANG_TO_QWEN.get(language or "", None)

        # ---- windows (≤20 s, cut at pauses) grouped into long chunks (≤150 s)
        wins = plan_windows(probs, total)
        chunks = group_windows(wins)
        live = []   # indices of windows that contain anything worth decoding
        for wi, (a, b) in enumerate(wins):
            fa, fb = int(a / FRAME_SEC), max(int(a / FRAME_SEC) + 1, int(b / FRAME_SEC))
            seg = wav[int(a * SR): int(b * SR)]
            if float(np.max(probs[fa:fb])) >= 0.12 and float(np.sqrt(np.mean(seg ** 2))) >= 1e-4:
                live.append(wi)
        live_set = set(live)

        # ---- pass 1: long-context ASR on chunks (best wording)
        ch_items = [(ws, we) for (ws, we) in chunks if any(i in live_set for i in range(ws, we))]
        ch_segs = [wav[int(wins[ws][0] * SR): int(wins[we - 1][1] * SR)] for ws, we in ch_items]
        L_text, L_lang = self._asr(ch_segs, forced, context, 4, lambda f: progress(0.05 + 0.40 * f))
        T.append(time.time())

        langs: dict = {}
        for t, ln in zip(L_text, L_lang):
            if t and ln:
                langs[ln] = langs.get(ln, 0) + sum(1 for c in t if is_kept_char(c))
        main_lang = max(langs, key=langs.get) if langs else (forced or "")
        if not forced and main_lang and langs:
            share = langs[main_lang] / max(1, sum(langs.values()))
            redo = [i for i, (t, ln) in enumerate(zip(L_text, L_lang))
                    if t and ln and ln != main_lang and share >= 0.8
                    and (sum(1 for c in t if is_kept_char(c)) < 80 or looks_like(t, main_lang))]
            if redo:
                t2, _ = self._asr([ch_segs[i] for i in redo], main_lang, context, 4)
                for i, t in zip(redo, t2):
                    L_text[i], L_lang[i] = t, main_lang

        # ---- pass 2: local ASR on each window, language pinned to its chunk
        win_lang = {}
        for (ws, we), ln in zip(ch_items, L_lang):
            for i in range(ws, we):
                win_lang[i] = ln or main_lang or None
        live_l = [win_lang.get(i) or None for i in live]
        S_text, _ = self._asr([wav[int(wins[i][0] * SR): int(wins[i][1] * SR)] for i in live],
                              live_l, context, 12, lambda f: progress(0.45 + 0.30 * f))
        short = dict(zip(live, S_text))

        # ---- recovery: a long chunk whose text is much shorter than its
        # windows' texts lost content (decoder loop / early stop) → re-decode
        # it in ~35 s sub-chunks, still with more context than one window
        for ci, (ws, we) in enumerate(ch_items):
            lk = len(_kept(L_text[ci]))
            sk = sum(len(_kept(short.get(i, ""))) for i in range(ws, we))
            if sk < 20 or lk >= 0.75 * sk:
                continue
            subs = group_windows(wins[ws:we], target=35.0, max_len=60.0)
            segs = [wav[int(wins[ws + a][0] * SR): int(wins[ws + b - 1][1] * SR)] for a, b in subs]
            t2, _ = self._asr(segs, L_lang[ci] or main_lang or None, context, 4)
            sep = "" if (L_lang[ci] or main_lang) in CJK_LANGS else " "
            joined = sep.join(t for t in t2 if t)
            if len(_kept(joined)) > lk:
                L_text[ci] = joined
        T.append(time.time())

        # ---- fuse: distribute each chunk's long text over its windows
        choice = {}
        for (ws, we), L in zip(ch_items, L_text):
            idx = [i for i in range(ws, we) if i in live_set]
            parts = distribute_text(L, [short.get(i, "") for i in idx])
            for i, lp in zip(idx, parts):
                choice[i] = pick_text(lp, short.get(i, ""), wins[i], regions)
        progress(0.78)

        # ---- pass 3: forced alignment inside each window
        words_all: List[dict] = []
        todo = [i for i in live if choice.get(i)]
        AB = 8
        for bi in range(0, len(todo), AB):
            batch = todo[bi: bi + AB]
            al = [i for i in batch if (win_lang.get(i) or "").lower() in self.aligner_langs]
            res = {}
            if al:
                ar = self.model.forced_aligner.align(
                    audio=[(wav[int(wins[i][0] * SR): int(wins[i][1] * SR)], SR) for i in al],
                    text=[choice[i] for i in al], language=[win_lang[i] for i in al])
                res = dict(zip(al, ar))
            for i in batch:
                a, b = wins[i]
                ws = []
                if i in res:
                    ws = map_tokens(choice[i], [(it.text, it.start_time, it.end_time) for it in res[i].items])
                    for w in ws:
                        w["s"] = min(b, w["s"] + a)
                        w["e"] = min(b, w["e"] + a)
                if not ws:
                    ws = proportional_words(choice[i], win_lang.get(i) or "", regions, a, b)
                for w in ws:
                    w["lang"] = win_lang.get(i) or main_lang
                words_all.extend(ws)
            progress(0.78 + 0.22 * min(len(todo), bi + AB) / max(1, len(todo)))

        repair_timings(words_all, probs, total)
        T.append(time.time())
        log.info("transcribe %.0fs audio: vad %.1fs · long-asr %.1fs (%d chunks) · local-asr %.1fs (%d windows) · align %.1fs",
                 total, T[1] - T[0], T[2] - T[1], len(ch_items), T[3] - T[2], len(live), T[4] - T[3])
        return {
            "language": QWEN_TO_LANG.get(main_lang, ""),
            "language_name": main_lang,
            "words": words_all,
            "vad": regions,
            "duration": total,
        }


# ---------------------------------------------------------------- helpers
def _kept(s: str) -> str:
    return "".join(c for c in s if is_kept_char(c))


def distribute_text(L: str, shorts: List[str]) -> List[str]:
    """Split the long-context transcript L into len(shorts) consecutive slices
    whose boundaries follow the per-window transcripts (char-level alignment)."""
    n = len(shorts)
    if n == 0:
        return []
    if n == 1:
        return [L]
    pos = [i for i, c in enumerate(L) if is_kept_char(c)]
    Lk = "".join(L[i] for i in pos)
    sk = [_kept(s) for s in shorts]
    Sk = "".join(sk)
    if not Lk:
        return [""] * n
    if not Sk:
        return [L] + [""] * (n - 1)
    blocks = difflib.SequenceMatcher(None, Lk, Sk, autojunk=False).get_matching_blocks()

    def f(j):
        prev = (0, 0)
        for bl in blocks:
            if bl.b <= j < bl.b + bl.size:
                return bl.a + (j - bl.b)
            if bl.b > j:
                na, nb = bl.a, bl.b
                pa, pb = prev
                if nb == pb:
                    return pa
                return int(round(pa + (j - pb) * (na - pa) / (nb - pb)))
            prev = (bl.a + bl.size, bl.b + bl.size)
        return len(Lk)

    cuts, acc = [], 0
    for k in range(n - 1):
        acc += len(sk[k])
        c = f(acc)
        cuts.append(max(c, cuts[-1] if cuts else 0))
    idx = []
    for c in cuts:
        p = pos[c] if c < len(pos) else len(L)
        # prefer a nearby punctuation boundary (window edges sit in pauses,
        # which the transcript usually marks with punctuation)
        best = None
        for q in range(max(1, p - 5), min(len(L), p + 6)):
            if not is_kept_char(L[q - 1]) and is_kept_char(L[q]):
                if best is None or abs(q - p) < abs(best - p):
                    best = q
        if best is not None:
            p = best
        # never cut inside a space-delimited word
        if 0 < p < len(L) and L[p - 1].isalpha() and L[p].isalpha() and not is_cjk(L[p]):
            left = L.rfind(" ", max(0, p - 15), p)
            right = L.find(" ", p, p + 15)
            cands = [x for x in (left, right) if x >= 0]
            if cands:
                p = min(cands, key=lambda x: abs(x - p)) + 1
        idx.append(max(p, idx[-1] if idx else 0))
    bounds = [0] + idx + [len(L)]
    return [L[a:b].strip() for a, b in zip(bounds[:-1], bounds[1:])]


def pick_text(lp: str, sp: str, win, regions) -> str:
    """Choose between the long-context slice and the local transcript."""
    lk, sk = _kept(lp), _kept(sp)
    dur = max(0.3, win[1] - win[0])
    plausible = lambda k: len(k) / dur <= 16
    if not lk and not sk:
        return ""
    if not sk:
        return lp if plausible(lk) else ""
    if not lk:
        return sp
    sim = difflib.SequenceMatcher(None, lk, sk, autojunk=False).ratio()
    if sim >= 0.5:
        return lp
    if len(lk) < 0.6 * len(sk):
        return sp
    if len(sk) < 0.6 * len(lk):
        return lp if plausible(lk) else sp
    return lp


def map_tokens(text: str, toks: list) -> List[dict]:
    """Attach aligner tokens (punctuation stripped) back to the original text.
    Each returned word owns the original text from its first char up to the
    next word's first char (so trailing punctuation/spaces stay attached)."""
    kept_pos = [i for i, c in enumerate(text) if is_kept_char(c)]
    kept = "".join(text[i] for i in kept_pos)
    out = []
    p = 0
    for (tt, s, e) in toks:
        tt = "".join(c for c in tt if is_kept_char(c))
        if not tt:
            continue
        # tokens should appear in order; search a small window for robustness
        j = kept.find(tt, p, p + len(tt) + 12)
        if j < 0:
            j = p
        if j >= len(kept):
            break
        out.append({"k0": j, "k1": min(len(kept), j + len(tt)), "s": float(s), "e": float(e)})
        p = min(len(kept), j + len(tt))
    if not out:
        return []
    words = []
    for n, w in enumerate(out):
        c0 = kept_pos[w["k0"]] if n > 0 else 0
        c1 = kept_pos[out[n + 1]["k0"]] if n + 1 < len(out) else len(text)
        seg = text[c0:c1]
        if not seg.strip():
            continue
        words.append({"text": seg, "s": w["s"], "e": w["e"]})
    # any kept chars not covered by tokens are merged into the last word already
    return words


def proportional_words(text: str, lang: str, regions, a: float, b: float) -> List[dict]:
    """Fallback timing when the aligner does not support the language:
    split into words and spread them over the chunk's speech regions."""
    parts = re.findall(r"\S+\s*", text) if " " in text.strip() else list(text)
    parts = [x for x in parts if x]
    if not parts:
        return []
    regs = [(max(a, s), min(b, e)) for s, e in regions if e > a and s < b] or [(a, b)]
    tot = sum(e - s for s, e in regs)
    weights = [max(1, sum(1 for c in x if is_kept_char(c))) for x in parts]
    W = sum(weights)
    words, acc = [], 0.0

    def at(x):
        for s, e in regs:
            if x <= e - s:
                return s + x
            x -= e - s
        return regs[-1][1]
    for x, w in zip(parts, weights):
        s = at(acc / W * tot)
        acc += w
        e = at(acc / W * tot)
        words.append({"text": x, "s": s, "e": e})
    return words


def _expected_dur(text: str) -> float:
    n = 0.0
    for c in text:
        if is_cjk(c):
            n += 0.19
        elif is_kept_char(c):
            n += 0.075
    return n + 0.25


def repair_timings(words: List[dict], probs: np.ndarray, total: float):
    """Fix aligner artefacts: spans that swallow long silences, zero-length
    spans and non-monotonic times."""
    if not words:
        return
    sm = np.convolve(probs, np.ones(3) / 3, mode="same")
    speech = sm >= 0.3

    def runs_in(s, e):
        fs, fe = max(0, int(s / FRAME_SEC)), min(len(speech), int(np.ceil(e / FRAME_SEC)))
        rs, i = [], fs
        while i < fe:
            if speech[i]:
                j = i
                while j < fe and speech[j]:
                    j += 1
                rs.append((i * FRAME_SEC, j * FRAME_SEC))
                i = j
            else:
                i += 1
        return rs

    n = len(words)
    for i, w in enumerate(words):
        s, e = w["s"], max(w["e"], w["s"])
        exp = _expected_dur(w["text"])
        if e - s > exp * 1.6 + 0.6:
            rs = runs_in(s, e)
            if rs:
                prev_e = words[i - 1]["e"] if i > 0 else -1e9
                next_s = words[i + 1]["s"] if i + 1 < n else 1e9
                if next_s - e < 0.35 and not (s - prev_e < 0.35):
                    r = rs[-1]
                    s2, e2 = max(r[0], min(r[1], e) - exp * 1.3), min(r[1], e)
                elif s - prev_e < 0.35 and not (next_s - e < 0.35):
                    r = rs[0]
                    s2, e2 = max(s, r[0]), min(r[1], max(s, r[0]) + exp * 1.3)
                else:
                    r = max(rs, key=lambda x: x[1] - x[0])
                    s2, e2 = r[0], min(r[1], r[0] + exp * 1.5)
                s, e = max(s, s2), min(e, max(e2, s2 + 0.08))
            else:
                e = s + exp
        w["s"], w["e"] = s, e
    # monotonic + non-zero
    for i, w in enumerate(words):
        if i > 0 and w["s"] < words[i - 1]["s"]:
            w["s"] = words[i - 1]["s"]
        if w["e"] <= w["s"]:
            nxt = words[i + 1]["s"] if i + 1 < n else total
            w["e"] = min(max(nxt, w["s"] + 0.05), w["s"] + max(0.12, _expected_dur(w["text"]) * 0.6))
        w["s"] = round(max(0.0, w["s"]), 3)
        w["e"] = round(min(total, w["e"]), 3)


ENGINE = ASREngine()
