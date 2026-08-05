import os
from unittest.mock import patch


def test_makerworld_token_status_defaults_unconfigured(client):
    response = client.get("/api/settings/makerworld-token")
    assert response.status_code == 200
    assert response.json() == {"configured": False}


def test_makerworld_token_set_then_clear(client):
    set_resp = client.put("/api/settings/makerworld-token", json={"token": "abc123"})
    assert set_resp.json() == {"configured": True}

    status_resp = client.get("/api/settings/makerworld-token")
    assert status_resp.json() == {"configured": True}

    clear_resp = client.put("/api/settings/makerworld-token", json={"clear": True})
    assert clear_resp.json() == {"configured": False}


def test_makerworld_token_rejects_empty(client):
    response = client.put("/api/settings/makerworld-token", json={"token": "   "})
    assert response.status_code == 400


class _FakeImporter:
    def importfromId(self, model_id, parent_id, preview_path):
        class _Resp:
            content = b"solid fake endsolid"
        return _Resp(), "data:image/png;base64,fake"

    def getModelOptions(self, url):
        return {"files": [{"id": "123", "name": "Fake Model"}]}


def test_import_model_by_id_uses_printables_importer(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeImporter()):
        response = client.post(
            "/api/import/importid",
            json={"source": "printables", "id": "123", "name": "Fake Model", "folderId": "1", "typeName": "stl"},
        )
    assert response.status_code == 200
    assert response.json()["name"] == "Fake Model"


def test_import_options_rejects_missing_url(client):
    response = client.post("/api/import/options", json={})
    assert response.status_code == 400


def test_import_model_by_id_sets_file_path(client):
    from app.db import get_db_conn, UPLOAD_DIR

    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeImporter()):
        response = client.post(
            "/api/import/importid",
            json={"source": "printables", "id": "123", "name": "Fake Model", "folderId": "1", "typeName": "stl"},
        )
    assert response.status_code == 200
    model = response.json()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()

    assert row["filePath"] is not None
    assert row["filePath"] == os.path.join(str(UPLOAD_DIR), f"{model['id']}.stl")
    assert os.path.exists(row["filePath"])


def test_import_options_returns_title_description_and_files(client):
    class _FakeImporterWithMeta:
        def getModelOptions(self, url):
            return {"title": "My Print", "description": "A neat thing", "files": [{"id": "1", "name": "part.stl"}]}

    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeImporterWithMeta()):
        response = client.post("/api/import/options", json={"url": "https://www.printables.com/model/1-x"})
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "My Print"
    assert body["description"] == "A neat thing"
    assert body["files"] == [{"id": "1", "name": "part.stl"}]


def test_import_options_errors_when_no_files_found(client):
    class _EmptyImporter:
        def getModelOptions(self, url):
            return {"title": "Empty", "description": "", "files": []}

    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_EmptyImporter()):
        response = client.post("/api/import/options", json={"url": "https://www.printables.com/model/1-x"})
    assert response.status_code == 400
