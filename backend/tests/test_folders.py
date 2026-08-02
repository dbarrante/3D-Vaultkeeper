def test_get_folders_returns_seeded_defaults(client):
    response = client.get("/api/folders")
    names = {f["name"] for f in response.json()}
    assert {"Characters", "Vehicles", "Terrain", "Tanks"}.issubset(names)


def test_create_folder(client):
    response = client.post("/api/folders", json={"name": "Minis", "parentId": None})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Minis"
    assert body["parentId"] is None
    assert "id" in body


def test_update_folder_name(client):
    created = client.post("/api/folders", json={"name": "Old", "parentId": None}).json()
    response = client.patch(f"/api/folders/{created['id']}", json={"name": "New"})
    assert response.status_code == 200
    assert response.json()["name"] == "New"


def test_update_missing_folder_returns_404(client):
    response = client.patch("/api/folders/does-not-exist", json={"name": "X"})
    assert response.status_code == 404


def test_delete_empty_folder(client):
    created = client.post("/api/folders", json={"name": "Temp", "parentId": None}).json()
    response = client.delete(f"/api/folders/{created['id']}")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_delete_folder_with_models_is_rejected(client):
    folder = client.post("/api/folders", json={"name": "HasModel", "parentId": None}).json()
    client.post(
        "/api/models/upload",
        data={"folderId": folder["id"]},
        files={"file": ("test.stl", b"solid test endsolid", "application/octet-stream")},
    )
    response = client.delete(f"/api/folders/{folder['id']}")
    assert response.status_code == 400
