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


# ---------------------------------------------------------------- fallbacks
from types import SimpleNamespace  # noqa: E402

from app import exporter  # noqa: E402


def _job():
    return SimpleNamespace(progress=0.0, cancelled=lambda: False)


def test_hardware_encoder_failure_falls_back_to_x264(monkeypatch):
    monkeypatch.setattr(exporter, "use_nvenc", lambda w, h: True)
    calls = []

    def fake_run(job, args, dur, feeder=None, log_path=None):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("h264_nvenc: No capable devices found")
    monkeypatch.setattr(exporter, "run_ffmpeg", fake_run)
    exporter._encode(_job(), lambda nv: ["h264_nvenc" if nv else "libx264"], 1920, 1080, 5)
    assert calls == [["h264_nvenc"], ["libx264"]]


def test_locked_target_keeps_the_encode_under_a_new_name(tmp_path, monkeypatch):
    tmp, final = tmp_path / ".x.part.mp4", tmp_path / "x.mp4"
    tmp.write_bytes(b"video")
    final.write_bytes(b"old")

    def locked(src, dst, tries=15):
        raise PermissionError(13, "in use")
    monkeypatch.setattr(exporter, "replace_retry", locked)
    res = exporter._finish(tmp, final)
    assert res["renamed"] and res["file"] == "x (新).mp4" and (tmp_path / "x (新).mp4").read_bytes() == b"video"
    assert final.read_bytes() == b"old"


def test_unwritable_folder_is_reported_before_encoding(tmp_path):
    exporter.check_writable(tmp_path / "ok.mp4")
    with pytest.raises(PermissionError):
        exporter.check_writable(tmp_path / "missing-folder" / "x.mp4")
