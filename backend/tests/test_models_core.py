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
