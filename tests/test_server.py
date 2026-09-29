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


def test_media_files_take_the_token_in_the_query_and_stay_in_project(client, auth, workspace):
    pid = new_project(client, auth)
    (workspace / "projects" / pid / "a.txt").write_text("hi")
    assert client.get(f"/api/projects/{pid}/files/a.txt").status_code == 401
    assert client.get(f"/api/projects/{pid}/files/a.txt?t=test-token").text == "hi"
    # the project file itself (transcript) is never served as a media file
    assert client.get(f"/api/projects/{pid}/files/project.json?t=test-token").status_code == 404
    for evil in ("../../settings.json", "..%2F..%2Fsettings.json", "..%5C..%5Csettings.json"):
        assert client.get(f"/api/projects/{pid}/files/{evil}?t=test-token").status_code in (400, 404)
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
    assert sorted(p.name for p in d.iterdir() if p.name != "project.json.bak") ==         ["exports", "project.json", "video_src_new.mp4", "video_work_new"]
    assert not any((d / "exports").iterdir())


# ---------------------------------------------------------------- robustness (assessment fixes)
def test_second_window_cannot_silently_overwrite(client, auth):
    pid = new_project(client, auth)
    r1 = client.put(f"/api/projects/{pid}", json={"state": {"segs": [1]}, "base_rev": 0}, headers=auth)
    assert r1.json()["rev"] == 1
    # a window that still thinks the project is at revision 0
    r2 = client.put(f"/api/projects/{pid}", json={"state": {"segs": [2]}, "base_rev": 0}, headers=auth)
    assert r2.status_code == 409 and r2.json()["code"] == "conflict" and r2.json()["rev"] == 1
    assert client.get(f"/api/projects/{pid}", headers=auth).json()["state"] == {"segs": [1]}
    r3 = client.put(f"/api/projects/{pid}", json={"state": {"segs": [2]}, "base_rev": 0, "force": True}, headers=auth)
    assert r3.status_code == 200 and r3.json()["rev"] == 2
    # job bookkeeping by the server does not count as an edit
    server.save_project(pid, {"job": {"id": "x", "kind": "generate"}})
    assert client.put(f"/api/projects/{pid}", json={"state": {}, "base_rev": 2}, headers=auth).status_code == 200


def test_project_list_has_no_cap_and_shows_broken_projects(client, auth, workspace):
    ids = [new_project(client, auth) for _ in range(55)]
    broken = workspace / "projects" / ids[0] / "project.json"
    broken.write_text("{", encoding="utf-8")
    for f in broken.parent.glob("project.json.bak"):
        f.unlink()
    items = client.get("/api/projects", headers=auth).json()
    assert len(items) == 55
    b = next(x for x in items if x["id"] == ids[0])
    assert b["broken"] and "size" in b


def test_damaged_project_is_recovered_from_backup(client, auth, workspace):
    pid = new_project(client, auth)
    client.put(f"/api/projects/{pid}", json={"name": "keep"}, headers=auth)
    f = workspace / "projects" / pid / "project.json"
    (workspace / "projects" / pid / "project.json.bak").write_text(f.read_text("utf-8"), encoding="utf-8")
    f.write_text("", encoding="utf-8")
    p = client.get(f"/api/projects/{pid}", headers=auth).json()
    assert p["name"] == "keep" and p["recovered"] is True


def test_delete_moves_project_out_and_cancels_its_jobs(client, auth, workspace):
    from app import jobs
    pid = new_project(client, auth)
    (workspace / "projects" / pid / "video_src_x.mp4").write_bytes(b"x" * 10)
    j = jobs.Job("media", pid)
    jobs.JOBS[j.id] = j
    r = client.delete(f"/api/projects/{pid}", headers=auth).json()
    assert r == {"ok": True, "leftover": False}
    assert not (workspace / "projects" / pid).exists() and j.cancelled()
    jobs.JOBS.pop(j.id, None)


def test_upload_space_is_checked_before_uploading(client, auth, monkeypatch):
    pid = new_project(client, auth)
    monkeypatch.setattr(server.shutil, "disk_usage", lambda p: type("U", (), {"free": 1 << 30})())
    r = client.post(f"/api/projects/{pid}/media/check", json={"size": 5 << 30}, headers=auth)
    assert r.status_code == 507 and "磁碟空間不足" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/media/check", json={"size": 1 << 20}, headers=auth).status_code == 200


def test_security_headers_and_readable_errors(client, auth):
    r = client.get("/", headers=auth)
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    r = client.get("/api/jobs/nope", headers=auth)
    assert r.status_code == 404 and "背景工作" in r.json()["detail"]
    assert client.get("/api/projects/bad.id", headers=auth).json()["detail"] == "專案編號不正確"


def test_export_refuses_a_second_export_of_the_same_project(client, auth):
    from app import jobs
    pid = new_project(client, auth)
    server.save_project(pid, {"media": {"video": {"file": "v.mp4", "ready": True}}})
    j = jobs.Job("export", pid)
    jobs.JOBS[j.id] = j
    meta = {"mode": "video", "width": 1920, "height": 1080, "duration": 5, "cues": []}
    r = client.post(f"/api/projects/{pid}/export/video", json={"session": "abc", "meta": meta}, headers=auth)
    assert r.status_code == 409
    j.status = "done"


def test_generate_without_translation_and_with_proofreading(client, auth, workspace, monkeypatch):
    """The whole /generate flow with a fake speech model and translation service."""
    import numpy as np
    from app import jobs, media
    from app import server as srv
    pid = new_project(client, auth)
    d = workspace / "projects" / pid
    (d / "video_work").mkdir()
    np.zeros(16000 * 3, dtype=np.float32).tofile(d / "video_work" / "audio16k.f32")
    srv.save_project(pid, {"media": {"video": {"file": "v.mp4", "ready": True, "info": {"duration": 3}}}})
    words = [{"text": "Hello there. ", "s": 0.1, "e": 1.0}, {"text": "Yes. ", "s": 1.4, "e": 1.7},
             {"text": "Bye now.", "s": 2.0, "e": 2.8}]

    class FakeEngine:
        def transcribe(self, wav, lang, ctx, progress, cancelled, note):
            note("")
            progress(1.0)
            return {"language": "en", "words": words, "duration": 3.0, "aligned": 0.5}
    monkeypatch.setattr(srv, "ENGINE", FakeEngine())

    def wait(jid):
        for _ in range(200):
            j = jobs.get(jid)
            if j.status != "running":
                return j
            import time
            time.sleep(0.02)
    # no API key: translation must be switched off explicitly
    r = client.post(f"/api/projects/{pid}/generate", json={"translate": True}, headers=auth)
    assert r.status_code == 400
    jid = client.post(f"/api/projects/{pid}/generate", json={"translate": False}, headers=auth).json()["job"]
    j = wait(jid)
    assert j.status == "done", j.error
    res = j.result
    assert [c["src"] for c in res["segs"]] == ["Hello there.", "Yes.", "Bye now."]
    assert res["aligned"] == 0.5 and res["usage"] is None and all(c["tgt"] == "" for c in res["segs"])
    assert srv.load_project(pid)["job"]["result"]["segs"]                 # kept until the app acknowledges it

    # with translation: proofread lines carry their original text, usage is reported and recorded
    from app import config

    class FakeTr:
        def __init__(self, settings):
            self.failed, self.fixes = [], {}
            self.usage = {"requests": 1, "prompt": 10, "completion": 5, "cache_hit": 0}

        def translate_all(self, cues, src, tgt, tone, opts, progress, cancelled):
            self.fixes[2] = cues[2]["src"]
            cues[2]["src"] = "Buy now."
            return [f"T{i}" for i in range(len(cues))]
    monkeypatch.setattr(srv, "Translator", FakeTr)
    config.save_settings({"api_key": "sk-test-1234567890"})
    j = wait(client.post(f"/api/projects/{pid}/generate", json={"translate": True}, headers=auth).json()["job"])
    assert j.status == "done", j.error
    segs = j.result["segs"]
    assert segs[2]["src"] == "Buy now." and segs[2]["src_orig"] == "Bye now." and "src_orig" not in segs[0]
    assert [c["tgt"] for c in segs] == ["T0", "T1", "T2"] and j.result["usage"]["prompt"] == 10
    assert config.usage_month()["requests"] == 1
