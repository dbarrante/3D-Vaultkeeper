def _upload(client, name="test.stl", folder_id="1"):
    return client.post(
        "/api/models/upload",
        data={"folderId": folder_id},
        files={"file": (name, b"solid test endsolid", "application/octet-stream")},
    ).json()


def test_bulk_delete(client):
    a = _upload(client, "a.stl")
    b = _upload(client, "b.stl")
    response = client.post("/api/models/bulk-delete", json={"ids": [a["id"], b["id"]]})
    assert response.status_code == 200
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    remaining_ids = {m["id"] for m in listed}
    assert a["id"] not in remaining_ids and b["id"] not in remaining_ids


def test_bulk_move(client):
    folder = client.post("/api/folders", json={"name": "Dest", "parentId": None}).json()
    a = _upload(client, "a.stl")
    response = client.post("/api/models/bulk-move", json={"ids": [a["id"]], "folderId": folder["id"]})
    assert response.status_code == 200
    moved = client.get("/api/models", params={"folderId": folder["id"]}).json()
    assert any(m["id"] == a["id"] for m in moved)


def test_bulk_tag_merges_without_duplicates(client):
    a = _upload(client, "a.stl")
    client.post("/api/models/bulk-tag", json={"ids": [a["id"]], "tags": ["red"]})
    response = client.post("/api/models/bulk-tag", json={"ids": [a["id"]], "tags": ["red", "blue"]})
    assert response.status_code == 200
    updated = [m for m in client.get("/api/models", params={"folderId": "1"}).json() if m["id"] == a["id"]][0]
    assert updated["tags"] == ["red", "blue"]


def test_replace_model_file_updates_size(client):
    a = _upload(client, "a.stl")
    response = client.put(
        f"/api/models/{a['id']}/file",
        files={"file": ("a2.stl", b"solid bigger file content endsolid", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert response.json()["size"] == len(b"solid bigger file content endsolid")


def test_replace_model_thumbnail_stores_base64_data_uri(client):
    a = _upload(client, "a.stl")
    response = client.put(
        f"/api/models/{a['id']}/thumbnail",
        files={"file": ("thumb.png", b"\x89PNG fake bytes", "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["thumbnail"].startswith("data:image/png;base64,")


def test_storage_stats_reports_used_bytes(client):
    _upload(client, "a.stl")
    response = client.get("/api/storage-stats")
    assert response.status_code == 200
    body = response.json()
    assert body["used"] > 0
    assert body["total"] == 5 * 1024 * 1024 * 1024
