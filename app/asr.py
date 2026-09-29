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
    (the decoder fell into a loop, e.g. on long moans or music).
    collapse_repeats() shortens what is left of the loop afterwards; the
    local-window pass recovers any speech that followed."""

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


_REPEAT = re.compile(r"(.{1,12}?)\1{3,}", re.S)


def collapse_repeats(text: str, keep: int = 2) -> str:
    """'やめて、やめて、…' ×16 → 'やめて、やめて…'. A unit repeated four or more
    times is kept `keep` times followed by an ellipsis. Units without letters
    (digits, punctuation, spaces) are left alone: "10000" or "!!!!" are fine."""
    def sub(m):
        unit = m.group(1)
        if not any(c.isalpha() for c in unit):
            return m.group(0)
        return (unit * keep).rstrip("、，,。. ") + "…"
    prev = None
    while prev != text:
        prev, text = text, _REPEAT.sub(sub, text)
    return text


def _install_loop_guard(qwen_model) -> bool:
    """Make every generate() call of the ASR model stop runaway repetitions.
    qwen-asr has no public hook for this, so its inner model's generate is
    wrapped; returns False (and logs) if the library changed shape."""
    from transformers import StoppingCriteria, StoppingCriteriaList

    class _SC(StoppingCriteria):
        def __init__(self):
            self.rs = RepeatStop()

        def __call__(self, input_ids, scores, **kw):
            return self.rs(input_ids, scores)

    inner = getattr(qwen_model, "model", None)
    orig = getattr(inner, "generate", None)
    if not callable(orig):
        log.warning("qwen-asr internals changed (no model.generate): the repetition guard is NOT active — "
                    "long music or breathing may make transcription slow")
        return False

    def generate(*a, **kw):
        # add ours even if the caller already passes stopping criteria
        kw["stopping_criteria"] = StoppingCriteriaList([*(kw.get("stopping_criteria") or []), _SC()])
        return orig(*a, **kw)
    inner.generate = generate
    return True


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


def clearly_other_script(text: str, lang: str) -> bool:
    """Text written almost entirely in Latin letters while `lang` uses another
    script (e.g. an English passage inside a Japanese video)."""
    if lang not in _SCRIPT and lang not in CJK_LANGS:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 8:
        return False
    return sum(1 for c in letters if c.isascii()) / len(letters) >= 0.85


def friendly_load_error(e: Exception, device: str) -> str:
    msg = str(e)
    low = msg.lower()
    if "out of memory" in low:
        return ("顯示卡記憶體不足，無法載入語音模型（約需 6.5 GB 可用的顯示卡記憶體）。"
                "請關閉其他使用顯示卡的程式（遊戲、其他 AI 工具）後重試，或到「設定」改用 CPU（需約 16 GB 記憶體，速度較慢）。")
    if isinstance(e, MemoryError) or "not enough memory" in low or "cannot allocate memory" in low:
        return "電腦記憶體不足，無法載入語音模型（CPU 模式約需 13 GB 可用記憶體）。請關閉其他程式後重試。"
    if "no kernel image" in low or "not compatible with the current pytorch" in low:
        return ("這張顯示卡太舊，目前安裝的 PyTorch 已不支援。請執行 start.bat --reinstall 重新挑選版本，"
                "或到「設定」改用 CPU。")
    if "offline" in low or "local_files_only" in low or "couldn't find" in low or "does not appear to have" in low:
        return "語音模型檔案不完整或尚未下載，請關閉程式後重新執行 start.bat（會自動補齊下載）。"
    return f"語音模型載入失敗（{device.upper()}）：{msg[:300]}"


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
                # bfloat16 needs a GPU from 2020 on (Ampere); older cards get float16
                dtype = (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16) if want == "cuda" else torch.float32
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
                self.model = None
                self.status, self.error = "error", friendly_load_error(e, want)
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
                raise RuntimeError(self.error) from e

    def unload(self):
        """Free the GPU/RAM held by the models (they load again on the next job)."""
        with self._lock, self._run_lock:
            self._unload()
            self.status, self.error = "idle", ""

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
                   progress: Callable[[float], None] = lambda p: None,
                   cancelled: Callable[[], bool] = lambda: False,
                   note: Callable[[str], None] = lambda s: None) -> dict:
        """wav: mono float32 16 kHz. language: UI code or None/auto.
        Returns {language, language_name, words:[{text,s,e,lang}], vad, duration, aligned}."""
        from .jobs import Cancelled
        if len(wav) < SR // 10:
            raise ValueError("音訊太短或沒有聲音內容（少於 0.1 秒）")
        for _ in range(2):
            if self.model is None:
                note("載入語音模型中…")
                self.ensure_loaded()
            # only one job uses the GPU at a time; wait for it, but stay cancellable
            while not self._run_lock.acquire(timeout=0.5):
                if cancelled():
                    raise Cancelled()
                note("等待前一個工作完成…")
            try:
                if self.model is not None:      # a device switch may have unloaded it meanwhile
                    note("")
                    return self._transcribe(wav, language, context, progress)
            finally:
                self._run_lock.release()
        raise RuntimeError(self.error or "語音模型尚未載入，請稍候再試")

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
                texts.append(collapse_repeats((r.text or "").strip()))
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
                    and (looks_like(t, main_lang)
                         or (sum(1 for c in t if is_kept_char(c)) < 80 and not clearly_other_script(t, main_lang)))]
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
        n_aligned = 0
        for bi in range(0, len(todo), AB):
            batch = todo[bi: bi + AB]
            al = [i for i in batch if (win_lang.get(i) or "").lower() in self.aligner_langs]
            res = self._align(wav, wins, choice, win_lang, al) if al else {}
            n_aligned += len(res)
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
            # share of windows with word-level alignment (the rest is estimated from speech regions)
            "aligned": round(n_aligned / len(todo), 3) if todo else 1.0,
        }

    def _align(self, wav, wins, choice, win_lang, idx: List[int]) -> dict:
        """Forced alignment of windows `idx` → {window: result}. Out of memory
        halves the batch; windows that still fail are left out, so they fall
        back to estimated timing instead of failing the whole transcription."""
        try:
            ar = self.model.forced_aligner.align(
                audio=[(wav[int(wins[i][0] * SR): int(wins[i][1] * SR)], SR) for i in idx],
                text=[choice[i] for i in idx], language=[win_lang[i] for i in idx])
            return dict(zip(idx, ar))
        except Exception as e:
            if "out of memory" in str(e).lower():
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            if len(idx) > 1:
                h = len(idx) // 2
                log.warning("alignment of %d windows failed (%s), retrying in halves", len(idx), str(e)[:120])
                return {**self._align(wav, wins, choice, win_lang, idx[:h]),
                        **self._align(wav, wins, choice, win_lang, idx[h:])}
            log.warning("alignment failed for one window, using estimated timing: %s", str(e)[:200])
            return {}


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
    sm = difflib.SequenceMatcher(None, lk, sk, autojunk=False)
    if sm.ratio() >= 0.5:
        # the long pass sometimes adds a line the window never heard (a
        # hallucinated "thanks for watching" over music, lyrics in a non-speech
        # stretch): keep only the part both passes agree on
        if len(lk) > 1.4 * len(sk) + 4:
            lp = _trim_to_match(lp, sm)
            if not _kept(lp) or len(_kept(lp)) > 1.4 * len(sk) + 4:
                return sp
        return lp if plausible(_kept(lp)) else sp
    if len(lk) < 0.6 * len(sk):
        return sp
    if len(sk) < 0.6 * len(lk):
        return lp if plausible(lk) else sp
    return lp


def _trim_to_match(lp: str, sm: "difflib.SequenceMatcher") -> str:
    """Cut the long-pass slice `lp` down to the span (of kept characters) that
    matches the local transcript, keeping the punctuation that closes it."""
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return lp
    pos = [i for i, c in enumerate(lp) if is_kept_char(c)]
    a0, a1 = blocks[0].a, blocks[-1].a + blocks[-1].size
    if a0 <= 3:
        a0 = 0
    if len(pos) - a1 <= 3:
        a1 = len(pos)
    start = 0 if a0 == 0 else pos[a0]
    end = len(lp) if a1 >= len(pos) else pos[a1 - 1] + 1
    while end < len(lp) and not is_kept_char(lp[end]) and not lp[end].isspace():
        end += 1
    return lp[start:end].strip()


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
