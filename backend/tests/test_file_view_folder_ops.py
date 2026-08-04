import os
from pathlib import Path


def _insert_folder(conn, folder_id, name, parent_id=None):
    conn.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (folder_id, name, parent_id),
    )


def _insert_model(conn, model_id, folder_id, file_path, storage_mode="copy", source_path=None):
    conn.execute(
        "INSERT INTO models "
        "(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,manual,"
        " sourcePath,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            model_id, os.path.basename(file_path), folder_id,
            f"/api/models/{model_id}/download", 100, 0, "[]", "", None, None,
            source_path, storage_mode, file_path,
        ),
    )


def test_rename_folder_moves_directory_and_rewrites_paths(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Vehicles"
    src_dir.mkdir()
    f1 = src_dir / "abc.stl"
    f1.write_text("data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc", "f1", str(f1), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.post("/api/file-view/folder/rename", json={"path": str(src_dir), "newName": "Cars"})
    assert resp.status_code == 200
    new_dir = upload_dir / "Cars"
    assert new_dir.exists()
    assert not src_dir.exists()
    assert (new_dir / "abc.stl").exists()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='abc'").fetchone()
    conn.close()
    assert row["filePath"] == str(new_dir / "abc.stl")


def test_rename_folder_updates_nested_reference_mode_source_path(client, tmp_path):
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    sub = watch_root / "Prints"
    sub.mkdir(parents=True)
    f1 = sub / "hull.stl"
    f1.write_text("data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(f1), storage_mode="reference", source_path=str(f1))
    conn.commit()
    conn.close()

    resp = client.post("/api/file-view/folder/rename", json={"path": str(sub), "newName": "Archive"})
    assert resp.status_code == 200
    new_path = str(watch_root / "Archive" / "hull.stl")

    conn = get_db_conn()
    row = conn.execute("SELECT filePath, sourcePath FROM models WHERE id='m1'").fetchone()
    conn.close()
    assert row["filePath"] == new_path
    assert row["sourcePath"] == new_path


def test_rename_folder_destination_exists_conflicts(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Vehicles"
    src_dir.mkdir()
    (upload_dir / "Cars").mkdir()

    resp = client.post("/api/file-view/folder/rename", json={"path": str(src_dir), "newName": "Cars"})
    assert resp.status_code == 409
    assert src_dir.exists()


def test_rename_nonexistent_folder_404s(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    resp = client.post(
        "/api/file-view/folder/rename",
        json={"path": str(upload_dir / "DoesNotExist"), "newName": "New"},
    )
    assert resp.status_code == 404


def test_move_folder_into_another_directory(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Tanks"
    src_dir.mkdir()
    f1 = src_dir / "abc.stl"
    f1.write_text("data")
    (upload_dir / "Vehicles").mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc", "f1", str(f1), storage_mode="copy")
    conn.commit()
    conn.close()

    target = str(upload_dir / "Vehicles" / "Tanks")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(src_dir), "targetPath": target})
    assert resp.status_code == 200
    assert Path(target).exists()
    assert not src_dir.exists()
    assert (Path(target) / "abc.stl").exists()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='abc'").fetchone()
    conn.close()
    assert row["filePath"] == str(Path(target) / "abc.stl")


def test_move_folder_nested_subfolders_all_rewritten(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Tanks"
    nested = src_dir / "Supports"
    nested.mkdir(parents=True)
    f1 = src_dir / "hull.stl"
    f1.write_text("data")
    f2 = nested / "support1.stl"
    f2.write_text("data")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "hull", "f1", str(f1), storage_mode="copy")
    _insert_model(conn, "sup1", "f1", str(f2), storage_mode="copy")
    conn.commit()
    conn.close()

    target = str(upload_dir / "Archive" / "Tanks")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(src_dir), "targetPath": target})
    assert resp.status_code == 200

    conn = get_db_conn()
    hull_row = conn.execute("SELECT filePath FROM models WHERE id='hull'").fetchone()
    sup_row = conn.execute("SELECT filePath FROM models WHERE id='sup1'").fetchone()
    conn.close()
    assert hull_row["filePath"] == str(Path(target) / "hull.stl")
    assert sup_row["filePath"] == str(Path(target) / "Supports" / "support1.stl")
