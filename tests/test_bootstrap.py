"""The installer's decisions, with pip / nvidia-smi / the GPU check faked."""
import pytest

import bootstrap


@pytest.mark.parametrize("out,v", [("| NVIDIA-SMI 570.1  Driver Version: 570.1  CUDA Version: 12.8 |", 12.8),
                                    ("CUDA UMD Version: 13.3", 13.3), ("no cuda here", 0.0)])
def test_parse_cuda(out, v):
    assert bootstrap.parse_cuda(out) == v


def test_cuda_for_driver():
    assert bootstrap.cuda_for_driver(581) == 13.0 and bootstrap.cuda_for_driver(551) == 12.4
    assert bootstrap.cuda_for_driver(470) == 11.8 and bootstrap.cuda_for_driver(0) == 0.0


@pytest.mark.parametrize("out,env,word", [
    ("ReadTimeoutError: HTTPSConnectionPool(host='download.pytorch.org'): Read timed out.", True, "網路"),
    ("OSError: [Errno 28] No space left on device", True, "磁碟"),
    ("SSL: CERTIFICATE_VERIFY_FAILED", True, "SSL"),
    ("ERROR: No matching distribution found for torch", False, "Python"),
])
def test_errors_are_explained(out, env, word):
    assert word in bootstrap.explain(out) and bootstrap.is_env_error(out) is env


def test_unknown_option_is_refused(capsys):
    with pytest.raises(SystemExit):
        bootstrap.check_args(["--reinstal"])
    assert "--reinstall" in capsys.readouterr().out
    bootstrap.check_args(["--cpu", "--no-browser"])


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    state = {}
    monkeypatch.setattr(bootstrap, "load_state", lambda: dict(state))
    monkeypatch.setattr(bootstrap, "save_state", lambda **kw: [state.pop(k, None) if v is None else state.update({k: v}) for k, v in kw.items()])
    monkeypatch.setattr(bootstrap, "need_space", lambda *a: None)
    monkeypatch.setattr(bootstrap, "LOG", tmp_path / "setup.log")
    return state


def test_network_failure_is_not_mistaken_for_an_old_gpu(fake_env, monkeypatch):
    monkeypatch.setattr(bootstrap, "torch_status", lambda: (None, False))
    monkeypatch.setattr(bootstrap, "driver_cuda", lambda: 12.8)
    tried = []
    monkeypatch.setattr(bootstrap, "install_torch", lambda tag, replace: (tried.append(tag), (1, "Read timed out."))[1])
    with pytest.raises(SystemExit):
        bootstrap.ensure_torch(False, False)
    assert tried == ["cu128"] and "cuda_failed" not in fake_env      # stopped, nothing remembered


def test_gpu_that_cannot_compute_tries_older_builds(fake_env, monkeypatch):
    monkeypatch.setattr(bootstrap, "torch_status", lambda: ("2.9.0+cu128", True))
    monkeypatch.setattr(bootstrap, "driver_cuda", lambda: 12.8)
    tried = []
    monkeypatch.setattr(bootstrap, "install_torch", lambda tag, replace: (tried.append(tag), (0, ""))[1])
    monkeypatch.setattr(bootstrap, "gpu_works", lambda: len(tried) >= 2)
    bootstrap.ensure_torch(False, True)
    assert tried == ["cu128", "cu126"] and "cuda_failed" not in fake_env


def test_no_working_cuda_build_falls_back_to_cpu_and_remembers(fake_env, monkeypatch):
    monkeypatch.setattr(bootstrap, "torch_status", lambda: (None, False))
    monkeypatch.setattr(bootstrap, "driver_cuda", lambda: 12.1)
    tried = []
    monkeypatch.setattr(bootstrap, "install_torch", lambda tag, replace: (tried.append(tag), (0, ""))[1])
    monkeypatch.setattr(bootstrap, "gpu_works", lambda: False)
    bootstrap.ensure_torch(False, False)
    assert tried == ["cu121", "cu118", "cpu"] and fake_env["cuda_failed"] == 12.1
