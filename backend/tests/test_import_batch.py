from unittest.mock import patch


class _FakeBatchImporter:
    def importfromId(self, model_id, parent_id, preview_path):
        class _Resp:
            content = b"solid fake endsolid"

        return _Resp(), ""


class _PartialFailImporter:
    def __init__(self):
        self.calls = 0

    def importfromId(self, model_id, parent_id, preview_path):
        self.calls += 1
        if self.calls == 2:
            raise ValueError("download failed")

        class _Resp:
            content = b"solid fake endsolid"

        return _Resp(), ""


def _files_payload():
    return [
        {"id": "1", "name": "body.stl", "parentId": "p", "previewPath": "", "typeName": "stl"},
        {"id": "2", "name": "arm.stl", "parentId": "p", "previewPath": "", "typeName": "stl"},
    ]


def test_import_batch_creates_folder_and_models(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        response = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Cool Robot",
                "description": "A robot mini",
                "files": _files_payload(),
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["folder"]["name"] == "Cool Robot"
    assert body["folder"]["description"] == "A robot mini"
    assert len(body["models"]) == 2
    assert body["failed"] == []


def test_import_batch_reports_name_collision(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Dup Project", "description": "", "files": _files_payload()},
        )
        response = client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Dup Project", "description": "", "files": _files_payload()},
        )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "folder_name_collision"


def test_import_batch_reuse_adds_to_existing_folder(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        first = client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Reuse Me", "description": "", "files": _files_payload()},
        ).json()
        second = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Reuse Me",
                "description": "",
                "files": _files_payload(),
                "folderResolution": "reuse",
            },
        ).json()
    assert second["folder"]["id"] == first["folder"]["id"]


def test_import_batch_create_new_makes_a_second_distinct_folder(client):
    with patch("app.routers.importers.printables.PrintablesImporter", return_value=_FakeBatchImporter()):
        first = client.post(
            "/api/import/batch",
            json={"source": "printables", "folderName": "Twice Named", "description": "", "files": _files_payload()},
        ).json()
        second = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Twice Named",
                "description": "",
                "files": _files_payload(),
                "folderResolution": "createNew",
            },
        ).json()
    assert second["folder"]["id"] != first["folder"]["id"]
    assert second["folder"]["name"] == "Twice Named"


def test_import_batch_partial_failure_still_creates_folder_and_succeeding_models(client):
    with patch(
        "app.routers.importers.printables.PrintablesImporter",
        return_value=_PartialFailImporter(),
    ):
        response = client.post(
            "/api/import/batch",
            json={
                "source": "printables",
                "folderName": "Partial Project",
                "description": "",
                "files": _files_payload(),
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert len(body["models"]) == 1
    assert len(body["failed"]) == 1
    assert body["failed"][0]["name"] == "arm.stl"
    assert body["folder"]["name"] == "Partial Project"


def test_import_batch_requires_folder_name(client):
    response = client.post(
        "/api/import/batch",
        json={"source": "printables", "folderName": "", "description": "", "files": _files_payload()},
    )
    assert response.status_code == 400


def test_import_batch_requires_at_least_one_file(client):
    response = client.post(
        "/api/import/batch",
        json={"source": "printables", "folderName": "No Files", "description": "", "files": []},
    )
    assert response.status_code == 400
