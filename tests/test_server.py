import json

import pytest
from fastapi.testclient import TestClient

from app import server


def new_project(client, auth):
    return client.post("/api/projects", json={"name": "t"}, headers=auth).json()["id"]


# ---------------------------------------------------------------- access control
def test_api_requires_token(client, auth):
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={"X-Lumen-Token": "wrong"}).status_code == 401
    assert client.get("/api/settings", headers=auth).status_code == 200


def test_foreign_host_is_refused(workspace, auth):
    # DNS rebinding: the browser sends the attacker's host name
    c = TestClient(server.app, base_url="http://evil.example:8765")
    assert c.get("/api/settings", headers=auth).status_code == 403
    assert c.get("/").status_code == 403


def test_cross_origin_is_refused(client, auth):
    r = client.post("/api/projects", json={}, headers={**auth, "Origin": "https://evil.example"})
    assert r.status_code == 403
    r = client.post("/api/projects", json={}, headers={**auth, "Origin": "http://127.0.0.1:8765"})
    assert r.status_code == 200


def test_port_is_enforced_when_known(workspace, auth, monkeypatch):
    monkeypatch.setenv("LUMEN_PORT", "8765")
    assert TestClient(server.app, base_url="http://127.0.0.1:9999").get("/api/settings", headers=auth).status_code == 403
    assert TestClient(server.app, base_url="http://localhost:8765").get("/api/settings", headers=auth).status_code == 200


def test_media_files_need_no_token_but_stay_in_project(client, auth, workspace):
    pid = new_project(client, auth)
    (workspace / "projects" / pid / "a.txt").write_text("hi")
    assert client.get(f"/api/projects/{pid}/files/a.txt").text == "hi"
    for evil in ("../../settings.json", "..%2F..%2Fsettings.json", "..%5C..%5Csettings.json"):
        assert client.get(f"/api/projects/{pid}/files/{evil}").status_code in (401, 404)
    assert client.get(f"/api/projects/{pid}").status_code == 401


def test_beacon_accepts_token_in_query(client, auth):
    pid = new_project(client, auth)
    body = json.dumps({"state": {"segs": [{"start": 0, "end": 1, "src": "a", "tgt": "b"}]}})
    assert client.post(f"/api/projects/{pid}/save", content=body, headers={"Content-Type": "text/plain"}).status_code == 401
    assert client.post(f"/api/projects/{pid}/save?t=test-token", content=body).status_code == 200
    assert client.get(f"/api/projects/{pid}", headers=auth).json()["state"]["segs"][0]["tgt"] == "b"


# ---------------------------------------------------------------- local files
def test_subtitle_export_rules(client, auth, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    r = client.post("/api/export/subtitle", json={"path": str(out / "x.bat"), "content": "calc"}, headers=auth)
    assert r.status_code == 400 and not (out / "x.bat").exists()
    r = client.post("/api/export/subtitle", json={"path": str(out / "x.srt"), "content": "1"}, headers=auth)
    assert r.status_code == 200
    r = client.post("/api/export/subtitle", json={"path": str(out / "x.srt"), "content": "2"}, headers=auth)
    assert r.status_code == 409
    r = client.post("/api/export/subtitle", json={"path": str(out / "x.srt"), "content": "2", "overwrite": True},
                    headers=auth)
    assert r.status_code == 200
    assert client.post("/api/export/subtitle", json={"path": "relative.srt", "content": ""}, headers=auth).status_code == 400


def test_network_paths_are_refused(client, auth):
    r = client.post("/api/path/check", json={"path": "\\\\attacker\\share\\x.srt"}, headers=auth)
    assert r.status_code == 400 and "網路路徑" in r.json()["detail"]


def test_reveal_only_opens_exported_files(client, auth, tmp_path):
    other = tmp_path / "other.srt"
    other.write_text("x")
    assert client.post("/api/reveal", json={"path": str(other), "open": True}, headers=auth).status_code == 403
    server._remember(other)
    r = client.post("/api/reveal", json={"path": str(tmp_path / "nope.srt")}, headers=auth)
    assert r.status_code == 403


# ---------------------------------------------------------------- settings
def test_key_is_never_sent_to_a_new_endpoint(client, auth):
    client.post("/api/settings", json={"api_key": "sk-0123456789abcdef"}, headers=auth)
    r = client.post("/api/settings/test", json={"base_url": "https://evil.example"}, headers=auth)
    assert r.status_code == 400
    r = client.post("/api/settings", json={"base_url": "https://evil.example"}, headers=auth)
    assert r.status_code == 400
    r = client.post("/api/settings", json={"base_url": "http://evil.example", "api_key": "sk-x" * 5}, headers=auth)
    assert r.status_code == 400
    s = client.get("/api/settings", headers=auth).json()
    assert s["base_url"] == "https://api.deepseek.com" and "0123456789" not in s["api_key"]


def test_settings_file_does_not_copy_apikey_txt(client, auth, workspace):
    from app import config
    config.APIKEY_FILE.write_text("sk-from-file-123456")
    client.post("/api/settings", json={"max_chars": 30}, headers=auth)
    saved = json.loads(config.SETTINGS_FILE.read_text("utf-8"))
    assert "sk-from-file" not in json.dumps(saved) and saved["max_chars"] == 30


# ---------------------------------------------------------------- projects / jobs
def test_job_result_is_kept_until_acknowledged(client, auth):
    pid = new_project(client, auth)
    server.save_project(pid, {"job": {"id": "abc", "kind": "generate", "result": {"segs": []}}})
    client.put(f"/api/projects/{pid}", json={"state": {}, "ack_job": "other"}, headers=auth)
    assert client.get(f"/api/projects/{pid}", headers=auth).json()["job"]["id"] == "abc"
    client.put(f"/api/projects/{pid}", json={"state": {}, "ack_job": "abc"}, headers=auth)
    assert client.get(f"/api/projects/{pid}", headers=auth).json()["job"] is None


def test_tracked_job_records_and_clears(client, auth):
    from app import jobs
    pid = new_project(client, auth)
    job = jobs.Job("x", pid)
    assert server._tracked(pid, "generate", lambda j: {"ok": 1})(job) == {"ok": 1}
    assert server.load_project(pid)["job"]["result"] == {"ok": 1}

    def boom(j):
        raise RuntimeError("fail")
    with pytest.raises(RuntimeError):
        server._tracked(pid, "generate", boom)(job)
    assert server.load_project(pid)["job"] is None


def test_export_rejects_unsafe_frames(client, auth):
    pid = new_project(client, auth)
    server.save_project(pid, {"media": {"video": {"file": "v.mp4", "ready": True}}})
    meta = {"mode": "video", "width": 1920, "height": 1080, "duration": 5,
            "cues": [{"start": 0, "end": 1, "frame": "../../x.png"}]}
    r = client.post(f"/api/projects/{pid}/export/video", json={"session": "abc", "meta": meta}, headers=auth)
    assert r.status_code == 400


def test_sweep_removes_leftovers(client, auth, workspace):
    pid = new_project(client, auth)
    d = workspace / "projects" / pid
    server.save_project(pid, {"media": {"video": {"file": "video_src_new.mp4", "work": "video_work_new", "ready": True}}})
    for name in ("video_src_new.mp4", "video_src_old.mp4", "_video_upload.mp4"):
        (d / name).write_text("x")
    for name in ("video_work_new", "video_work_old", "exports/_frames_abc"):
        (d / name).mkdir(parents=True)
    server._sweep_workspace()
    assert sorted(p.name for p in d.iterdir()) == ["exports", "project.json", "video_src_new.mp4", "video_work_new"]
    assert not any((d / "exports").iterdir())
