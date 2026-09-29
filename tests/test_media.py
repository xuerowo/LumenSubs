import sys
import time

import numpy as np
import pytest

from app import media
from app.jobs import Cancelled


def old_peaks(wav, per_sec=50):
    """The previous implementation (padded copy of the whole audio) as a reference."""
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


@pytest.mark.parametrize("n", [0, 1, 319, 320, 321, 16000 * 7 + 13])
def test_peaks_match_the_reference_without_copying(n):
    wav = np.random.default_rng(n).standard_normal(n).astype(np.float32)
    assert media.peaks(wav) == old_peaks(wav)


def test_unsafe_containers_are_refused():
    for fmt in ("hls", "concat", "image2", "tty"):
        with pytest.raises(ValueError):
            media.check_safe({"format": fmt})
    media.check_safe({"format": "mov,mp4,m4a,3gp,3g2,mj2"})


def test_error_tail_keeps_the_cause():
    text = "Cause: No capable devices found\n" + "x" * 5000 + "\nTask finished with error code: -22"
    t = media._tail(text)
    assert t.startswith("Cause: No capable devices found") and t.endswith("-22") and len(t) < 1700


def _py(code):
    return [sys.executable, "-c", code]


def test_run_process_reports_lines_and_exit_code():
    got = []
    rc = media.run_process(_py("print('out_time_us=1000000'); print('progress=end')"), got.append, lambda: False)
    assert rc == 0 and got == ["out_time_us=1000000", "progress=end"]


def test_run_process_cancels_even_while_silent():
    t0 = time.time()
    with pytest.raises(Cancelled):
        media.run_process(_py("import time; time.sleep(30)"), lambda l: None, lambda: time.time() - t0 > 0.6)
    assert time.time() - t0 < 5


def test_run_process_kills_a_stuck_process():
    t0 = time.time()
    with pytest.raises(RuntimeError, match="沒有任何進度"):
        media.run_process(_py("import time; time.sleep(30)"), lambda l: None, lambda: False, stall=1)
    assert time.time() - t0 < 5


def test_nvenc_is_not_used_beyond_its_size_limit(monkeypatch):
    monkeypatch.setattr(media, "nvenc_ok", lambda: True)
    assert media.use_nvenc(3840, 2160) and not media.use_nvenc(5120, 2880) and not media.use_nvenc(0, 0)


def test_hdr_detection():
    assert media.is_hdr({"color_transfer": "smpte2084"}) and media.is_hdr({"color_transfer": "arib-std-b67"})
    assert not media.is_hdr({"color_transfer": "bt709"}) and not media.is_hdr({})
