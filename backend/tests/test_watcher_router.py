def test_create_and_list_watch_folder(client, tmp_path):
    response = client.post(
        "/api/watch-folders",
        json={"path": str(tmp_path), "folderId": "1", "frequencyMinutes": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(tmp_path)
    assert body["frequencyMinutes"] == 30

    listed = client.get("/api/watch-folders").json()
    assert any(w["id"] == body["id"] for w in listed)


def test_create_watch_folder_rejects_missing_path(client):
    response = client.post("/api/watch-folders", json={"path": "/does/not/exist", "folderId": "1"})
    assert response.status_code == 400


def test_delete_watch_folder(client, tmp_path):
    created = client.post("/api/watch-folders", json={"path": str(tmp_path), "folderId": "1"}).json()
    response = client.delete(f"/api/watch-folders/{created['id']}")
    assert response.status_code == 200
    listed = client.get("/api/watch-folders").json()
    assert all(w["id"] != created["id"] for w in listed)


def test_scan_now_ingests_and_returns_count(client, tmp_path):
    (tmp_path / "a.stl").write_bytes(b"solid a endsolid")
    created = client.post("/api/watch-folders", json={"path": str(tmp_path), "folderId": "1"}).json()

    response = client.post(f"/api/watch-folders/{created['id']}/scan-now")
    assert response.status_code == 200
    assert response.json() == {"ingested": 1}

    models = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["name"] == "a.stl" for m in models)


def test_drive_scan_ingests_from_multiple_arbitrary_paths(client, tmp_path):
    root_a = tmp_path / "old_downloads"
    root_a.mkdir()
    (root_a / "a.stl").write_bytes(b"solid a endsolid")

    root_b = tmp_path / "external_drive_stash"
    root_b.mkdir()
    (root_b / "b.3mf").write_bytes(b"fake b")

    response = client.post(
        "/api/drive-scan",
        json={"paths": [str(root_a), str(root_b)], "folderId": "1"},
    )
    assert response.status_code == 200
    assert response.json() == {"ingested": 2}

    models = client.get("/api/models", params={"folderId": "1"}).json()
    names = {m["name"] for m in models}
    assert {"a.stl", "b.3mf"}.issubset(names)


def test_drive_scan_picks_up_sidecar_notes(client, tmp_path):
    root = tmp_path / "old_downloads"
    root.mkdir()
    (root / "a.stl").write_bytes(b"solid a endsolid")
    (root / "a.txt").write_text("Consolidated from an old backup, PLA only.")

    client.post("/api/drive-scan", json={"paths": [str(root)], "folderId": "1"})

    models = client.get("/api/models", params={"folderId": "1"}).json()
    model = next(m for m in models if m["name"] == "a.stl")
    assert model["description"] == "Consolidated from an old backup, PLA only."


def test_drive_scan_skips_nonexistent_paths_without_failing(client, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "c.stl").write_bytes(b"solid c endsolid")

    response = client.post(
        "/api/drive-scan",
        json={"paths": [str(real), str(tmp_path / "does_not_exist")], "folderId": "1"},
    )
    assert response.status_code == 200
    assert response.json() == {"ingested": 1}
