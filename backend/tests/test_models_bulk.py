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


def test_bulk_delete_leaves_reference_mode_files_on_disk(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "bulk_keep.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="bulk_keep.stl", reference_only=True)

    response = client.post("/api/models/bulk-delete", json={"ids": [model["id"]]})
    assert response.status_code == 200
    assert source.exists()
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != model["id"] for m in listed)


def test_bulk_delete_mixed_batch_tombstones_reference_and_hard_deletes_copy(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn, UPLOAD_DIR
    import os as _os

    copy_model = _upload(client, "copy_gone.stl")

    source = tmp_path / "ref_kept.stl"
    source.write_bytes(b"solid endsolid")
    ref_model = ingest_file(str(source), folder_id="1", original_filename="ref_kept.stl", reference_only=True)

    response = client.post(
        "/api/models/bulk-delete", json={"ids": [copy_model["id"], ref_model["id"]]}
    )
    assert response.status_code == 200

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    listed_ids = {m["id"] for m in listed}
    assert copy_model["id"] not in listed_ids
    assert ref_model["id"] not in listed_ids

    conn = get_db_conn()
    ref_row = conn.execute("SELECT removedAt, sourcePath FROM models WHERE id=?", (ref_model["id"],)).fetchone()
    copy_row = conn.execute("SELECT * FROM models WHERE id=?", (copy_model["id"],)).fetchone()
    conn.close()

    # reference-mode: row survives, tombstoned, sourcePath intact, real file untouched
    assert ref_row is not None
    assert ref_row["removedAt"] is not None
    assert ref_row["sourcePath"] == str(source)
    assert source.exists()

    # copy-mode: row and file both gone
    assert copy_row is None
    assert not any(f.startswith(copy_model["id"]) for f in _os.listdir(UPLOAD_DIR))


def test_bulk_move_skips_tombstoned_models(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    # Create two models: one active, one tombstoned reference
    active_model = _upload(client, "active.stl")

    source = tmp_path / "tombstoned.stl"
    source.write_bytes(b"solid endsolid")
    tombstoned_model = ingest_file(str(source), folder_id="1", original_filename="tombstoned.stl", reference_only=True)

    # Tombstone the reference-mode model
    client.delete(f"/api/models/{tombstoned_model['id']}")

    # Create destination folder
    dest_folder = client.post("/api/folders", json={"name": "Dest", "parentId": None}).json()

    # Bulk move both models
    response = client.post(
        "/api/models/bulk-move",
        json={"ids": [active_model["id"], tombstoned_model["id"]], "folderId": dest_folder["id"]}
    )
    assert response.status_code == 200

    # Verify: active model moved to dest, tombstoned model's folderId unchanged
    conn = get_db_conn()
    active_row = conn.execute("SELECT folderId FROM models WHERE id=?", (active_model["id"],)).fetchone()
    tombstoned_row = conn.execute("SELECT folderId FROM models WHERE id=?", (tombstoned_model["id"],)).fetchone()
    conn.close()

    assert active_row["folderId"] == dest_folder["id"]
    assert tombstoned_row["folderId"] == "1"  # Unchanged


def test_bulk_tag_skips_tombstoned_models(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    # Create two models: one active, one tombstoned reference
    active_model = _upload(client, "active.stl")

    source = tmp_path / "tombstoned.stl"
    source.write_bytes(b"solid endsolid")
    tombstoned_model = ingest_file(str(source), folder_id="1", original_filename="tombstoned.stl", reference_only=True)

    # Tombstone the reference-mode model
    client.delete(f"/api/models/{tombstoned_model['id']}")

    # Bulk tag both models
    response = client.post(
        "/api/models/bulk-tag",
        json={"ids": [active_model["id"], tombstoned_model["id"]], "tags": ["new-tag"]}
    )
    assert response.status_code == 200

    # Verify: active model got the tag, tombstoned model's tags unchanged
    conn = get_db_conn()
    import json
    active_row = conn.execute("SELECT tags FROM models WHERE id=?", (active_model["id"],)).fetchone()
    tombstoned_row = conn.execute("SELECT tags FROM models WHERE id=?", (tombstoned_model["id"],)).fetchone()
    conn.close()

    active_tags = json.loads(active_row["tags"]) if active_row["tags"] else []
    tombstoned_tags = json.loads(tombstoned_row["tags"]) if tombstoned_row["tags"] else []

    assert "new-tag" in active_tags
    assert "new-tag" not in tombstoned_tags


def test_bulk_delete_removes_subdirectory_file(client, tmp_path):
    """Mirrors test_bulk_delete_mixed_batch_tombstones_reference_and_hard_deletes_copy
    but for a model placed in a real UPLOAD_DIR subdirectory (Import Wizard commit,
    Task 4's dest_subpath) rather than flat — bulk-delete's os.listdir(UPLOAD_DIR)
    scan never finds files a directory down, so this must resolve via filePath."""
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(
        str(source),
        folder_id="1",
        original_filename="hull.stl",
        move=True,
        dest_subpath="Vehicles/Tanks",
    )
    disk_path = UPLOAD_DIR / "Vehicles" / "Tanks" / f"{model['id']}.stl"
    assert disk_path.exists()

    response = client.post("/api/models/bulk-delete", json={"ids": [model["id"]]})
    assert response.status_code == 200
    assert not disk_path.exists()

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != model["id"] for m in listed)


def test_replace_model_file_removes_old_subdirectory_file_and_updates_filepath(client, tmp_path):
    """Replace-file always writes the *new* file flat (pre-existing behavior,
    unchanged), but must still locate and remove the *old* file wherever it
    actually is (a wizard-imported model's file may be in a subdirectory), and
    must update filePath to the new flat location so future lookups don't go
    stale relative to the just-written file."""
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR, get_db_conn

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid original endsolid")
    model = ingest_file(
        str(source),
        folder_id="1",
        original_filename="hull.stl",
        move=True,
        dest_subpath="Vehicles/Tanks",
    )
    old_disk_path = UPLOAD_DIR / "Vehicles" / "Tanks" / f"{model['id']}.stl"
    assert old_disk_path.exists()

    response = client.put(
        f"/api/models/{model['id']}/file",
        files={"file": ("hull2.stl", b"solid replacement content endsolid", "application/octet-stream")},
    )
    assert response.status_code == 200
    assert not old_disk_path.exists()  # old subdirectory file was removed

    new_flat_path = UPLOAD_DIR / f"{model['id']}.stl"
    assert new_flat_path.exists()
    assert new_flat_path.read_bytes() == b"solid replacement content endsolid"

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["filePath"] == str(new_flat_path)

    # Download now serves the new flat file, confirming filePath resolution works
    download = client.get(f"/api/models/{model['id']}/download")
    assert download.content == b"solid replacement content endsolid"


def test_bulk_delete_with_unmatched_id_deletes_nothing_from_upload_dir(client, tmp_path):
    """A bulk-delete id that matches no row (e.g. a stale/empty id from a
    race with another delete) must not fall through to the flat
    os.listdir(UPLOAD_DIR) + startswith("") scan and remove an unrelated
    file -- an empty model_id would match every filename via
    "".startswith(...), deleting the first entry regardless of what it is."""
    from app.db import UPLOAD_DIR
    import os as _os

    kept = _upload(client, "kept.stl")
    before = set(_os.listdir(UPLOAD_DIR))
    assert any(f.startswith(kept["id"]) for f in before)

    response = client.post("/api/models/bulk-delete", json={"ids": [""]})
    assert response.status_code == 200

    after = set(_os.listdir(UPLOAD_DIR))
    assert after == before
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["id"] == kept["id"] for m in listed)


def test_resolve_copy_mode_file_never_returns_a_directory(client, tmp_path):
    """If a real subdirectory (e.g. an Import Wizard logical folder, or
    manuals/) happens to share a name prefix with a model id, the flat
    fallback scan in _resolve_copy_mode_file must never resolve to that
    directory -- only an actual file. Otherwise download_model would hand
    a directory path to FileResponse."""
    from app.routers.models import _resolve_copy_mode_file
    from app.db import UPLOAD_DIR
    import os as _os

    model = _upload(client, "real.stl")
    model_id = model["id"]

    # A directory whose name starts with the same id prefix, placed
    # alongside the real flat file in UPLOAD_DIR.
    conflicting_dir = _os.path.join(UPLOAD_DIR, f"{model_id}-conflict-dir")
    _os.makedirs(conflicting_dir, exist_ok=True)

    resolved = _resolve_copy_mode_file(model_id, None)
    assert resolved is not None
    assert _os.path.isfile(resolved)
    assert not _os.path.isdir(resolved)
