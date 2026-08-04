import os
import sqlite3
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
