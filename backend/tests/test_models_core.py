import os


def _upload(client, name="test.stl", folder_id="1"):
    return client.post(
        "/api/models/upload",
        data={"folderId": folder_id, "tags": "a,b"},
        files={"file": (name, b"solid test endsolid", "application/octet-stream")},
    ).json()


def test_upload_model_persists_and_lists(client):
    created = _upload(client)
    assert created["name"] == "test.stl"
    assert created["tags"] == ["a", "b"]

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["id"] == created["id"] for m in listed)


def test_update_model_allowed_fields_only(client):
    created = _upload(client)
    response = client.patch(
        f"/api/models/{created['id']}",
        json={"description": "new desc", "notAllowedField": "ignored"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "new desc"


def test_delete_model_removes_row_and_file(client):
    created = _upload(client)
    response = client.delete(f"/api/models/{created['id']}")
    assert response.status_code == 200
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != created["id"] for m in listed)


def test_download_model_returns_file_bytes(client):
    created = _upload(client)
    response = client.get(f"/api/models/{created['id']}/download")
    assert response.status_code == 200
    assert response.content == b"solid test endsolid"


def test_download_missing_model_currently_returns_500_known_bug(client):
    # Upstream get_model_info() calls row_to_model(None) with no null check when the
    # id doesn't match any row, which crashes instead of raising a clean 404. This
    # test locks in *actual* current behavior for the Phase 0 no-behavior-change
    # refactor; fixing the bug is a separate, deliberate follow-up task.
    response = client.get("/api/models/does-not-exist/download")
    assert response.status_code == 500


def test_reference_model_reports_missing_false_when_file_present(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "present.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="present.stl", reference_only=True)

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == model["id"])
    assert found["storageMode"] == "reference"
    assert found["missing"] is False


def test_reference_model_reports_missing_true_when_file_deleted(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "gone.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="gone.stl", reference_only=True)
    os.remove(source)

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == model["id"])
    assert found["missing"] is True


def test_copy_mode_model_never_reports_missing(client):
    created = _upload(client)
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == created["id"])
    assert found["storageMode"] == "copy"
    assert found["missing"] is False


def test_download_reference_model_serves_from_source_path(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "download_me.stl"
    source.write_bytes(b"solid reference content endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="download_me.stl", reference_only=True)

    response = client.get(f"/api/models/{model['id']}/download")
    assert response.status_code == 200
    assert response.content == b"solid reference content endsolid"


def test_download_reference_model_returns_descriptive_404_when_missing(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "vanish.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="vanish.stl", reference_only=True)
    os.remove(source)

    response = client.get(f"/api/models/{model['id']}/download")
    assert response.status_code == 404
    assert "moved or deleted" in response.json()["detail"]
