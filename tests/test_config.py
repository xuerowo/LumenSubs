import json

import pytest

from app import config


def test_write_json_keeps_a_backup_and_recovers_from_it(tmp_path):
    f = tmp_path / "project.json"
    config.write_json(f, {"v": 1})
    config.write_json(f, {"v": 2}, backup_every=0)
    assert json.loads(f.read_text("utf-8")) == {"v": 2}
    assert json.loads(config.bak_path(f).read_text("utf-8")) == {"v": 1}
    f.write_text("{ half a file", encoding="utf-8")          # e.g. after a power cut
    assert config.read_json(f) == ({"v": 1}, True)
    assert not list(tmp_path.glob("*.tmp"))                  # no temp files left behind


def test_read_json_missing_and_unrecoverable(tmp_path):
    f = tmp_path / "x.json"
    assert config.read_json(f) == (None, False)
    f.write_text("garbage", encoding="utf-8")
    with pytest.raises(ValueError):
        config.read_json(f)


def test_unreadable_settings_are_never_overwritten(workspace):
    config.save_settings({"api_key": "sk-keep-me-123456", "glossary": "A = B"})
    config.SETTINGS_FILE.write_text("{ broken", encoding="utf-8")
    config.bak_path(config.SETTINGS_FILE).unlink(missing_ok=True)
    with pytest.raises(config.SettingsError):
        config.save_settings({"export_dir": "C:\\x"})
    assert config.SETTINGS_FILE.read_text("utf-8") == "{ broken"


def test_settings_fall_back_to_backup(workspace):
    config.save_settings({"glossary": "one"})
    config.save_settings({"glossary": "two"})
    config.SETTINGS_FILE.write_text("", encoding="utf-8")
    assert config.load_settings()["glossary"] == "one"


def test_replace_retry_survives_a_transient_lock(tmp_path, monkeypatch):
    src, dst = tmp_path / "a", tmp_path / "b"
    src.write_text("x")
    calls = []
    real = config.os.replace

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(13, "sharing violation")
        return real(a, b)
    monkeypatch.setattr(config.os, "replace", flaky)
    config.replace_retry(src, dst)
    assert dst.read_text() == "x" and len(calls) == 3


def _snapshot(hub, repo, files, rev="abc"):
    base = hub / ("models--" + repo.replace("/", "--"))
    (base / "refs").mkdir(parents=True)
    (base / "refs" / "main").write_text(rev)
    snap = base / "snapshots" / rev
    snap.mkdir(parents=True)
    for name, data in files.items():
        (snap / name).write_bytes(data)
    return snap


def test_model_snapshot_detects_interrupted_downloads(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    index = json.dumps({"weight_map": {"a": "model-1.safetensors", "b": "model-2.safetensors"}}).encode()
    _snapshot(tmp_path, "Q/partial", {"config.json": b"{}", "model.safetensors.index.json": index,
                                      "model-1.safetensors": b"x"})            # second shard missing
    _snapshot(tmp_path, "Q/full", {"config.json": b"{}", "model.safetensors.index.json": index,
                                   "model-1.safetensors": b"x", "model-2.safetensors": b"y"})
    _snapshot(tmp_path, "Q/single", {"config.json": b"{}", "model.safetensors": b"w"})
    _snapshot(tmp_path, "Q/empty", {"config.json": b"{}", "model.safetensors": b""})
    assert config.model_snapshot("Q/partial") is None
    assert config.model_snapshot("Q/full") is not None
    assert config.model_snapshot("Q/single") is not None
    assert config.model_snapshot("Q/empty") is None
    assert config.model_snapshot("Q/missing") is None


def test_usage_is_summed_per_month(workspace):
    config.add_usage({"requests": 2, "prompt": 100, "completion": 10, "cache_hit": 50})
    config.add_usage({"requests": 1, "prompt": 5, "completion": 1, "cache_hit": 0})
    config.add_usage({"requests": 0})                          # nothing happened → not recorded
    assert config.usage_month() == {"requests": 3, "prompt": 105, "completion": 11, "cache_hit": 50}
