import pytest

from app.exporter import write_concat


def durations(text):
    return [float(l.split()[1]) for l in text.splitlines() if l.startswith("duration")]


def test_concat_covers_duration(tmp_path):
    cues = [{"start": 1.0, "end": 2.0, "frame": "c00000.png"}, {"start": 2.5, "end": 4.0, "frame": "c00001.png"}]
    txt = write_concat(tmp_path, cues, 10.0).read_text("utf-8")
    assert "file 'c00000.png'" in txt and "file 'c00001.png'" in txt
    # blank 1s, cue 1s, blank .5s, cue 1.5s, blank tail (≥ remaining time)
    d = durations(txt)
    assert d[:4] == [1.0, 1.0, 0.5, 1.5] and d[4] >= 6.0


def test_overlapping_cues_are_clipped(tmp_path):
    cues = [{"start": 0.0, "end": 3.0, "frame": "c00000.png"}, {"start": 2.0, "end": 4.0, "frame": "c00001.png"}]
    d = durations(write_concat(tmp_path, cues, 5.0).read_text("utf-8"))
    assert d[0] == 3.0 and d[1] == 1.0


@pytest.mark.parametrize("bad", ["../x.png", "C:\\x.png", "c1.png", "c00000.png'\nfile 'secret.png", "http://x/c00000.png"])
def test_rejects_unsafe_frame_names(tmp_path, bad):
    with pytest.raises(ValueError):
        write_concat(tmp_path, [{"start": 0, "end": 1, "frame": bad}], 2.0)
