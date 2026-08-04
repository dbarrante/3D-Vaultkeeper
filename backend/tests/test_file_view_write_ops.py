import os
from pathlib import Path


def _insert_folder(conn, folder_id, name, parent_id=None):
    conn.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (folder_id, name, parent_id),
    )


def _insert_model(conn, model_id, folder_id, file_path, storage_mode="copy", source_path=None):
    if source_path and storage_mode == "reference":
        conn.execute(
            "INSERT INTO models "
            "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,"
            " sourcePath,storageMode,filePath) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                model_id, os.path.basename(file_path), folder_id,
                f"/api/models/{model_id}/download", 100, 0, "[]", "", None,
                source_path, storage_mode, file_path,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO models "
            "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,"
            " storageMode,filePath) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                model_id, os.path.basename(file_path), folder_id,
                f"/api/models/{model_id}/download", 100, 0, "[]", "", None,
                storage_mode, file_path,
            ),
        )


def test_rename_copy_mode_file_updates_path_and_moves_file(client, tmp_path, monkeypatch):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    new_path = str(upload_dir / "abc123_renamed.stl")
    resp = client.patch("/api/models/abc123/location", json={"newPath": new_path})
    assert resp.status_code == 200
    body = resp.json()
    assert body["filePath"] == new_path
    assert not src.exists()
    assert os.path.exists(new_path)

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='abc123'").fetchone()
    conn.close()
    assert row["filePath"] == new_path


def test_move_copy_mode_file_into_subdirectory(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    dest_dir = upload_dir / "Vehicles" / "Tanks"
    dest_dir.mkdir(parents=True)
    new_path = str(dest_dir / "abc123.stl")
    resp = client.patch("/api/models/abc123/location", json={"newPath": new_path})
    assert resp.status_code == 200
    assert os.path.exists(new_path)
    assert not src.exists()


def test_rename_reference_mode_file_syncs_source_path(client, tmp_path):
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    new_path = str(watch_root / "hull_v2.stl")
    resp = client.patch("/api/models/m1/location", json={"newPath": new_path})
    assert resp.status_code == 200
    body = resp.json()
    assert body["filePath"] == new_path
    assert body["sourcePath"] == new_path
    assert body["missing"] is False
    assert not src.exists()
    assert os.path.exists(new_path)


def test_move_reference_mode_file_outside_every_watch_root_rejected(client, tmp_path):
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    new_path = str(outside / "hull.stl")
    resp = client.patch("/api/models/m1/location", json={"newPath": new_path})
    assert resp.status_code == 400
    assert src.exists()  # untouched


def test_move_reference_mode_then_rescan_does_not_duplicate(client, tmp_path):
    """The exact bug the spec's reference-mode section exists to prevent:
    a rename/move that updated filePath but not sourcePath would make the
    watcher re-ingest the file at its new location as a brand-new row.
    """
    from app.db import get_db_conn
    from app.services.scan import scan_watch_folder

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,enabled) VALUES (?,?,?,?,?)",
        ("wf1", str(watch_root), "f1", 60, 1),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    watch_row = dict(conn.execute("SELECT * FROM watch_folders WHERE id='wf1'").fetchone())
    conn.close()

    new_path = str(watch_root / "hull_v2.stl")
    resp = client.patch("/api/models/m1/location", json={"newPath": new_path})
    assert resp.status_code == 200

    scan_watch_folder(watch_row)

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models").fetchone()["c"]
    conn.close()
    assert count == 1, "rescan created a duplicate row instead of recognizing the file at its new location"


def test_rename_traversal_attempt_is_neutralized(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    escape_attempt = str(upload_dir / ".." / ".." / "escaped.stl")
    resp = client.patch("/api/models/abc123/location", json={"newPath": escape_attempt})
    assert resp.status_code == 400
    assert src.exists()


def test_move_destination_already_exists_conflicts(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")
    existing = upload_dir / "taken.stl"
    existing.write_text("already here")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.patch("/api/models/abc123/location", json={"newPath": str(existing)})
    assert resp.status_code == 409
    assert src.exists()
    assert existing.read_text() == "already here"


def test_rename_missing_model_404s(client):
    resp = client.patch("/api/models/doesnotexist/location", json={"newPath": "/tmp/x.stl"})
    assert resp.status_code == 404


def test_duplicate_copy_mode_file_creates_new_row_and_copy(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO models "
        "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,manual,author,"
        " sourceUrl,category,colorCount,sliceSettings,sourcePath,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "abc123", "abc123.stl", "f1", "/api/models/abc123/download", 100, 0,
            '["red"]', "a description", None, None, "some author",
            None, "vehicles", None, None, None, "copy", str(src),
        ),
    )
    conn.commit()
    conn.close()

    resp = client.post("/api/models/abc123/duplicate")
    assert resp.status_code == 200
    new_model = resp.json()
    assert new_model["id"] != "abc123"
    assert new_model["folderId"] == "f1"
    assert new_model["tags"] == ["red"]
    assert new_model["description"] == "a description"
    assert new_model["author"] == "some author"
    assert new_model["storageMode"] == "copy"
    assert new_model["sourcePath"] is None
    assert os.path.exists(new_model["filePath"])
    assert new_model["filePath"] != str(src)
    assert src.exists(), "original file must be untouched"

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models").fetchone()["c"]
    conn.close()
    assert count == 2


def test_duplicate_reference_mode_file_becomes_copy_mode(client, tmp_path):
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    upload_dir = Path(os.environ["FILE_STORAGE"])
    resp = client.post("/api/models/m1/duplicate")
    assert resp.status_code == 200
    new_model = resp.json()
    assert new_model["storageMode"] == "copy"
    assert new_model["sourcePath"] is None
    assert Path(new_model["filePath"]).resolve().is_relative_to(upload_dir.resolve())
    # Pin the actual destination, not just "somewhere under UPLOAD_DIR": the
    # reference branch is the one that still mirrors folder_disk_path, so this
    # asserts the "Root" segment really is applied rather than the copy landing
    # at the bare UPLOAD_DIR root and passing the containment check vacuously.
    assert Path(new_model["filePath"]).parent == upload_dir / "Root"
    assert os.path.exists(new_model["filePath"])
    assert src.exists(), "original watched file must be untouched"


def test_duplicate_copy_mode_lands_beside_current_file_not_folder_disk_path(client, tmp_path):
    """File view is deliberately a distinct lens from Logical view: after a
    File-mode move, a copy-mode file's real filePath diverges from what
    folder_disk_path(folderId) implies. Duplicating must follow the FILE
    (land beside where it actually lives right now), not the logical folder
    assignment -- otherwise the copy silently appears in a directory the user
    wasn't even looking at.
    """
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    # Physically parked somewhere that does NOT match folderId's logical chain
    # (which would be UPLOAD_DIR/Root/Vehicles) -- exactly the divergence a
    # File-mode move produces.
    actual_dir = upload_dir / "Elsewhere"
    actual_dir.mkdir()
    src = actual_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "root", "Root")
    _insert_folder(conn, "vehicles", "Vehicles", parent_id="root")
    _insert_model(conn, "abc123", "vehicles", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.post("/api/models/abc123/duplicate")
    assert resp.status_code == 200
    new_path = Path(resp.json()["filePath"])
    assert new_path.parent == actual_dir
    assert new_path != src
    assert new_path.exists()
    assert src.exists(), "original file must be untouched"
    assert not (upload_dir / "Root" / "Vehicles").exists(), "must not mirror the logical folder chain"


def test_duplicate_reference_mode_mirrors_folder_disk_path(client, tmp_path):
    """Unchanged behavior for reference-mode: the source lives outside the
    managed library, so there is no legal "beside the original" for a
    copy-mode duplicate -- mirroring the logical folder under UPLOAD_DIR
    remains the only sensible default.
    """
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "root", "Root")
    _insert_folder(conn, "vehicles", "Vehicles", parent_id="root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "root"),
    )
    _insert_model(conn, "m1", "vehicles", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    resp = client.post("/api/models/m1/duplicate")
    assert resp.status_code == 200
    new_path = Path(resp.json()["filePath"])
    assert new_path.parent == upload_dir / "Root" / "Vehicles"
    assert src.exists(), "original watched file must be untouched"


def test_duplicate_missing_model_404s(client):
    resp = client.post("/api/models/doesnotexist/duplicate")
    assert resp.status_code == 404


def test_duplicate_deep_folder_hierarchy_mirrors_all_segments(client, tmp_path):
    """Reference-mode source: this is the branch that still consults
    folder_disk_path, so it's where deep-hierarchy mirroring is now tested.
    (It was previously written against a copy-mode source whose physical
    location already diverged from its folderId chain -- that setup no longer
    exercises mirroring at all, since copy-mode duplicates now follow the
    file's real directory.)
    """
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "root", "Root")
    _insert_folder(conn, "vehicles", "Vehicles", parent_id="root")
    _insert_folder(conn, "tanks", "Tanks", parent_id="vehicles")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "root"),
    )
    _insert_model(conn, "m1", "tanks", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    resp = client.post("/api/models/m1/duplicate")
    assert resp.status_code == 200
    new_path = Path(resp.json()["filePath"])
    # Verify all three hierarchy levels are reflected in the path
    assert new_path.parent == upload_dir / "Root" / "Vehicles" / "Tanks"


def test_duplicate_missing_folder_404s(client, tmp_path):
    """An orphaned folderId (folder row deleted out from under the model) must
    still 404 rather than blow up inside folder_disk_path. Written against a
    reference-mode source because that is now the only branch that calls
    folder_disk_path -- copy-mode duplicates derive their destination from the
    file's own directory and so cannot reach this error path at all (see
    test_duplicate_copy_mode_survives_orphaned_folder_id below).
    """
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    src = watch_root / "hull.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    # Model's folderId points at a non-existent folder
    _insert_model(conn, "m1", "nonexistent", str(src), storage_mode="reference", source_path=str(src))
    conn.commit()
    conn.close()

    resp = client.post("/api/models/m1/duplicate")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_duplicate_copy_mode_survives_orphaned_folder_id(client, tmp_path):
    """The flip side: a copy-mode duplicate no longer consults folder_disk_path,
    so an orphaned folderId must not fail it -- the file's own directory is
    all the destination information needed.
    """
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src = upload_dir / "abc123.stl"
    src.write_text("model data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "nonexistent", str(src), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.post("/api/models/abc123/duplicate")
    assert resp.status_code == 200
    new_path = Path(resp.json()["filePath"])
    assert new_path.parent == upload_dir
    assert new_path.exists()
