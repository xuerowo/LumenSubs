"""Audio-reactive bar visualiser shared by the preview and the exporter.

Band energies are computed once per project (VIZ_FPS × VIZ_BANDS uint8),
served to the browser for the live preview, and rendered into an alpha mask
video for export — so both look identical.
"""
import numpy as np

VIZ_FPS = 30
VIZ_BANDS = 28          # mirrored → 56 bars
BAR_W, BAR_GAP = 0.0055, 0.0045   # fractions of frame width   (matches CSS .viz)
BOX_H, CENTER_Y = 0.18, 0.44      # fractions of frame height


def compute_bands(wav: np.ndarray, sr: int = 16000) -> np.ndarray:
    hop = sr / VIZ_FPS
    n_frames = int(len(wav) / hop) + 1
    win = 2048
    window = np.hanning(win).astype(np.float32)
    freqs = np.fft.rfftfreq(win, 1 / sr)
    edges = np.geomspace(90, 7200, VIZ_BANDS + 1)
    bins = [(np.searchsorted(freqs, edges[i]), max(np.searchsorted(freqs, edges[i + 1]), np.searchsorted(freqs, edges[i]) + 1))
            for i in range(VIZ_BANDS)]
    pad = np.concatenate([np.zeros(win // 2, np.float32), wav.astype(np.float32), np.zeros(win, np.float32)])
    out = np.zeros((n_frames, VIZ_BANDS), dtype=np.float32)
    B = 512
    for f0 in range(0, n_frames, B):
        idx = (np.arange(f0, min(n_frames, f0 + B)) * hop).astype(np.int64)
        frames = np.stack([pad[i: i + win] for i in idx]) * window
        mag = np.abs(np.fft.rfft(frames, axis=1))
        for b, (a, z) in enumerate(bins):
            out[f0: f0 + len(idx), b] = mag[:, a:z].mean(axis=1)
    db = 20 * np.log10(out + 1e-6)
    # per-band normalisation keeps highs visible
    hi = np.percentile(db, 99.3, axis=0)
    lo = hi - 48
    v = np.clip((db - lo) / (hi - lo), 0, 1) ** 1.6
    # fast attack, smooth release
    sm = np.zeros_like(v)
    prev = np.zeros(VIZ_BANDS, dtype=np.float32)
    for i in range(len(v)):
        prev = np.maximum(v[i], prev * 0.82)
        sm[i] = prev
    return (sm * 255).astype(np.uint8)


def bar_heights(row: np.ndarray) -> np.ndarray:
    """28 band values → 56 mirrored bar heights (0..1) with a soft centre taper.
    Low frequencies sit in the middle."""
    v = row.astype(np.float32) / 255.0
    bars = np.concatenate([v[::-1], v])
    c = np.abs(np.arange(56) - 27.5) / 28
    return 0.08 + 0.92 * bars * (1 - c * 0.35)


class MaskRenderer:
    """Renders the bars as an 8-bit alpha mask of the viz box for one frame."""

    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        bw = max(2, round(W * BAR_W))
        gap = max(1, round(W * BAR_GAP))
        self.bw, self.gap = bw, gap
        self.box_w = 56 * bw + 55 * gap
        self.box_h = max(8, int(round(H * BOX_H / 2)) * 2)
        self.x0 = [(i * (bw + gap)) for i in range(56)]
        # rounded caps: horizontal alpha profile of a single bar
        self.r = bw / 2

    def render(self, row: np.ndarray) -> np.ndarray:
        m = np.zeros((self.box_h, self.box_w), dtype=np.uint8)
        hs = bar_heights(row)
        cy = self.box_h / 2
        for i, h in enumerate(hs):
            ph = max(self.bw, h * self.box_h)
            y0, y1 = int(round(cy - ph / 2)), int(round(cy + ph / 2))
            x = self.x0[i]
            m[max(0, y0):min(self.box_h, y1), x:x + self.bw] = 255
            # round the caps
            r = int(self.r)
            if r >= 2:
                for k in range(r):
                    inset = r - int(np.sqrt(max(0, r * r - (r - k - 0.5) ** 2)))
                    if inset > 0:
                        for yy in (y0 + k, y1 - 1 - k):
                            if 0 <= yy < self.box_h:
                                m[yy, x:x + inset] = 0
                                m[yy, x + self.bw - inset:x + self.bw] = 0
        return m
