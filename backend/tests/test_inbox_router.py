def _seed_inbox_item(client, tmp_path, name="found.stl"):
    from app.db import get_db_conn, now_ms
    import uuid

    path = tmp_path / name
    path.write_bytes(b"solid found endsolid")
    item_id = str(uuid.uuid4())
    conn = get_db_conn()
    conn.execute(
        "INSERT INTO inbox_items(id,path,detectedAt,status) VALUES (?,?,?,?)",
        (item_id, str(path), now_ms(), "pending"),
    )
    conn.commit()
    conn.close()
    return item_id, path


def test_list_inbox_returns_only_pending(client, tmp_path):
    item_id, _ = _seed_inbox_item(client, tmp_path)
    listed = client.get("/api/inbox").json()
    assert any(i["id"] == item_id for i in listed)
    assert all(i["status"] == "pending" for i in listed)


def test_file_inbox_item_ingests_and_marks_filed(client, tmp_path):
    item_id, path = _seed_inbox_item(client, tmp_path)

    response = client.post(f"/api/inbox/{item_id}/file", json={"folderId": "1"})
    assert response.status_code == 200
    assert response.json()["name"] == "found.stl"

    listed = client.get("/api/inbox").json()
    assert all(i["id"] != item_id for i in listed)  # no longer pending
    assert path.exists()  # move=False — original file in "Downloads" untouched

    models = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["name"] == "found.stl" for m in models)


def test_dismiss_inbox_item(client, tmp_path):
    item_id, path = _seed_inbox_item(client, tmp_path)

    response = client.post(f"/api/inbox/{item_id}/dismiss")
    assert response.status_code == 200

    listed = client.get("/api/inbox").json()
    assert all(i["id"] != item_id for i in listed)
    assert path.exists()  # dismiss never touches the file


def test_file_missing_inbox_item_is_404(client):
    response = client.post("/api/inbox/does-not-exist/file", json={"folderId": "1"})
    assert response.status_code == 404
