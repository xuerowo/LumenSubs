"""Silero VAD (ONNX) — frame-level speech probability, used to cut audio at
natural pauses and to sanity-check forced-alignment timestamps."""
import logging
from typing import List, Optional, Tuple

import numpy as np

log = logging.getLogger("lumen.vad")

SR = 16000
WIN = 512                 # 32 ms per frame
FRAME_SEC = WIN / SR
_CTX = 64


def _find_model() -> Optional[str]:
    from . import config
    p = config.vad_model_path()          # honours LUMEN_VAD_ONNX, HF_HOME and HF_HUB_CACHE
    if p:
        return str(p)
    try:
        from huggingface_hub import hf_hub_download
        return hf_hub_download(config.VAD_MODEL, "onnx/model.onnx")
    except Exception as e:
        log.warning("Silero VAD model unavailable (%s) — falling back to an energy-based VAD, "
                    "cue timing will be less precise", e)
        return None


class SileroVAD:
    def __init__(self):
        path = _find_model()
        self.sess = None
        if path:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = 1
            self.sess = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])

    def probs(self, wav: np.ndarray) -> np.ndarray:
        """Speech probability per 32 ms frame."""
        n = int(np.ceil(len(wav) / WIN))
        if self.sess is None:
            return self._energy_fallback(wav, n)
        state = np.zeros((2, 1, 128), dtype=np.float32)
        ctx = np.zeros((1, _CTX), dtype=np.float32)
        sr = np.array(SR, dtype=np.int64)
        out = np.zeros(n, dtype=np.float32)
        padded = np.zeros(n * WIN, dtype=np.float32)
        padded[: len(wav)] = wav
        frames = padded.reshape(n, WIN)
        for i in range(n):
            x = np.concatenate([ctx, frames[i : i + 1]], axis=1)
            o, state = self.sess.run(None, {"input": x, "state": state, "sr": sr})
            ctx = x[:, -_CTX:]
            out[i] = o[0, 0]
        return out

    @staticmethod
    def _energy_fallback(wav: np.ndarray, n: int) -> np.ndarray:
        padded = np.zeros(n * WIN, dtype=np.float32)
        padded[: len(wav)] = wav
        rms = np.sqrt((padded.reshape(n, WIN) ** 2).mean(axis=1) + 1e-12)
        db = 20 * np.log10(rms + 1e-9)
        floor = np.percentile(db, 15)
        return np.clip((db - floor - 6) / 18, 0, 1).astype(np.float32)


def speech_regions(p: np.ndarray, on: float = 0.45, off: float = 0.3,
                   min_speech: float = 0.08, min_silence: float = 0.2, pad: float = 0.06) -> List[Tuple[float, float]]:
    """Hysteresis thresholding → [(start_sec, end_sec)]."""
    regs = []
    active, start = False, 0
    for i, v in enumerate(p):
        if not active and v >= on:
            active, start = True, i
        elif active and v < off:
            regs.append([start, i])
            active = False
    if active:
        regs.append([start, len(p)])
    # merge short gaps
    merged = []
    for s, e in regs:
        if merged and (s - merged[-1][1]) * FRAME_SEC < min_silence:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    total = len(p) * FRAME_SEC
    return [(max(0.0, s * FRAME_SEC - pad), min(total, e * FRAME_SEC + pad))
            for s, e in merged if (e - s) * FRAME_SEC >= min_speech]


def plan_chunks(p: np.ndarray, total_sec: float, min_len: float = 40.0, target: float = 100.0,
                max_len: float = 170.0) -> List[Tuple[float, float]]:
    """Split the whole timeline (no gaps) into long chunks — Qwen3-ASR is
    noticeably more accurate with more context — cutting inside the
    longest/quietest pause between min_len and max_len (aligner limit 180 s)."""
    if total_sec <= max_len:
        return [(0.0, total_sec)]
    n = len(p)
    sm = np.convolve(p, np.ones(5) / 5, mode="same")  # ~160 ms smoothing
    quiet = sm < 0.2
    run_start = np.zeros(n, dtype=np.int64)
    run_end = np.zeros(n, dtype=np.int64)
    i = 0
    while i < n:
        j = i
        while j < n and quiet[j] == quiet[i]:
            j += 1
        run_start[i:j], run_end[i:j] = i, j
        i = j
    chunks = []
    start = 0.0
    while total_sec - start > max_len:
        lo = int((start + min_len) / FRAME_SEC)
        hi = min(int((start + max_len) / FRAME_SEC), n - 1)
        tgt = int((start + target) / FRAME_SEC)
        best, best_score = hi, -1e9
        f = lo
        while f < hi:
            if quiet[f]:
                a, b = max(run_start[f], lo), min(run_end[f], hi)
                run_sec = (run_end[f] - run_start[f]) * FRAME_SEC
                cut = min(max((a + b) // 2, a), b - 1) if not (a <= tgt < b) else tgt
                score = min(run_sec, 3.0) * 10 - abs(cut - tgt) * FRAME_SEC * 0.12
                if score > best_score:
                    best, best_score = cut, score
                f = b
            else:
                score = -30 - sm[f] * 20 - abs(f - tgt) * FRAME_SEC * 0.12
                if score > best_score:
                    best, best_score = f, score
                f += 1
        cut = best * FRAME_SEC + FRAME_SEC / 2
        chunks.append((start, cut))
        start = cut
    chunks.append((start, total_sec))
    return chunks


def plan_windows(p: np.ndarray, total_sec: float, max_len: float = 20.0, min_len: float = 1.5,
                 pause: float = 0.6) -> List[Tuple[float, float]]:
    """Partition the timeline into utterance-sized windows cut at every pause
    ≥ `pause` seconds; windows longer than max_len are subdivided."""
    n = len(p)
    if n == 0 or total_sec <= 0:
        return []
    sm = np.convolve(p, np.ones(5) / 5, mode="same")
    quiet = sm < 0.2
    cuts = []
    i = 0
    need = int(pause / FRAME_SEC)
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if j - i >= need and i > 0 and j < n:
                cuts.append(((i + j) / 2) * FRAME_SEC)
            i = j
        else:
            i += 1
    bounds = [0.0] + [c for c in cuts if 0 < c < total_sec] + [total_sec]
    wins = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        if b - a > max_len:
            fa, fb = int(a / FRAME_SEC), int(np.ceil(b / FRAME_SEC))
            for x, y in plan_chunks(p[fa:fb], b - a, min_len=4.0, target=12.0, max_len=max_len):
                wins.append((a + x, min(b, a + y)))
        else:
            wins.append((a, b))
    # merge tiny windows into a neighbour
    out: List[List[float]] = []
    for a, b in wins:
        if out and (b - a < min_len or out[-1][1] - out[-1][0] < min_len) and b - out[-1][0] <= max_len + 5:
            out[-1][1] = b
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def group_windows(wins: List[Tuple[float, float]], target: float = 100.0, max_len: float = 150.0) -> List[Tuple[int, int]]:
    """Group consecutive windows into long-context chunks → [(first, last+1)]."""
    groups = []
    s = 0
    for i in range(len(wins)):
        if i > s and wins[i][1] - wins[s][0] > max_len:
            groups.append((s, i))
            s = i
        if wins[i][1] - wins[s][0] >= target:
            groups.append((s, i + 1))
            s = i + 1
    if s < len(wins):
        groups.append((s, len(wins)))
    return groups
