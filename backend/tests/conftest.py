import sys

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Fresh temp SQLite DB + upload dir per test, fully re-imported app package."""
    db_path = tmp_path / "test.db"
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("FILE_STORAGE", str(upload_dir))
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")  # see app/scheduler.py — never touch the real Downloads folder in tests

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from app import main as app_module

    # raise_server_exceptions=False: an unhandled exception in a route should
    # surface as the HTTP 500 a real deployed server would return, not propagate
    # as a Python exception inside the test — see test_download_missing_model_*.
    with TestClient(app_module.app, raise_server_exceptions=False) as test_client:
        yield test_client
