import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# must be set before app.server is imported
os.environ["LUMEN_NO_PRELOAD"] = "1"
os.environ["LUMEN_TOKEN"] = "test-token"
os.environ.pop("LUMEN_PORT", None)

TOKEN = "test-token"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Point projects and settings at a temporary folder."""
    from app import config
    projects = tmp_path / "projects"
    projects.mkdir()
    monkeypatch.setattr(config, "PROJECTS_DIR", projects)
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "APIKEY_FILE", tmp_path / "apikey.txt")
    for name in ("LOG_DIR", "TMP_DIR", "TRASH_DIR"):
        monkeypatch.setattr(config, name, tmp_path / name.lower().replace("_dir", ""))
    monkeypatch.setattr(config, "USAGE_FILE", tmp_path / "usage.json")
    return tmp_path


@pytest.fixture
def client(workspace):
    from fastapi.testclient import TestClient
    from app import server
    server._written.clear()
    return TestClient(server.app, base_url="http://127.0.0.1:8765")


@pytest.fixture
def auth():
    return {"X-Lumen-Token": TOKEN}


@pytest.fixture(autouse=True)
def no_retry_wait(monkeypatch):
    """Tests simulate failing services; don't sit out the real back-off."""
    from app import translator
    monkeypatch.setattr(translator, "RETRY_WAIT", 0.0)
