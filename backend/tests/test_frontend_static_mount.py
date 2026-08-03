import sys


def test_frontend_static_mount_serves_index_when_present(tmp_path, monkeypatch):
    frontend_dist = tmp_path / "frontend_dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<html><body>Vaultkeeper</body></html>")

    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("FILE_STORAGE", str(tmp_path / "uploads"))
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from app import main as app_module
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as test_client:
        response = test_client.get("/")
        assert response.status_code == 200
        assert "Vaultkeeper" in response.text

        # the mount must not shadow existing API routes
        api_response = test_client.get("/api/folders")
        assert api_response.status_code == 200
