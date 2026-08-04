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


def test_rename_folder_copy_mode_destination_outside_upload_dir_rejected(client, tmp_path):
    """A copy-mode folder (one that lives under UPLOAD_DIR) must not be
    renameable to a destination that resolves outside UPLOAD_DIR -- e.g. a
    newName containing ".." traversal segments. Mirrors the containment
    guarantee Tasks 1-2 already enforce at the file level via
    validate_destination.
    """
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Vehicles"
    src_dir.mkdir()
    f1 = src_dir / "abc.stl"
    f1.write_text("data")

    resp = client.post(
        "/api/file-view/folder/rename",
        json={"path": str(src_dir), "newName": "../../Escaped"},
    )
    assert resp.status_code == 400
    assert src_dir.exists()  # untouched
    assert f1.exists()
    assert not (upload_dir.parent.parent / "Escaped").exists()


def test_move_folder_reference_mode_outside_every_watch_root_rejected(client, tmp_path):
    """A reference-mode folder (one that lives under a configured
    watch_folders.path) must not be movable to a targetPath outside every
    watched root -- the app would permanently lose track of it. Mirrors
    test_move_reference_mode_file_outside_every_watch_root_rejected from
    the file-level endpoint tests (test_file_view_write_ops.py).
    """
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    sub = watch_root / "Prints"
    sub.mkdir(parents=True)
    f1 = sub / "hull.stl"
    f1.write_text("data")
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    _insert_model(conn, "m1", "f1", str(f1), storage_mode="reference", source_path=str(f1))
    conn.commit()
    conn.close()

    target = str(outside / "Prints")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(sub), "targetPath": target})
    assert resp.status_code == 400
    assert sub.exists()  # untouched
    assert f1.exists()
    assert not Path(target).exists()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath, sourcePath FROM models WHERE id='m1'").fetchone()
    conn.close()
    assert row["filePath"] == str(f1)  # DB not rewritten either
    assert row["sourcePath"] == str(f1)


def test_rename_folder_into_own_subfolder_rejected(client, tmp_path):
    """Renaming "Tanks" with newName="Tanks/Sub" resolves to a destination
    nested inside the source itself. This passes the 409-exists check
    (destination doesn't exist yet) and containment validation (still
    resolves inside UPLOAD_DIR) -- shutil.move would then raise on a
    self-nested move, and the router must reject this before ever calling
    it rather than let that surface as an unhandled 500.
    """
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Tanks"
    src_dir.mkdir()
    f1 = src_dir / "hull.stl"
    f1.write_text("data")

    resp = client.post(
        "/api/file-view/folder/rename",
        json={"path": str(src_dir), "newName": "Tanks/Sub"},
    )
    assert resp.status_code == 400
    assert src_dir.exists()  # untouched
    assert f1.exists()
    assert sorted(p.name for p in src_dir.iterdir()) == ["hull.stl"]  # no stray "Sub" dir


def test_delete_folder_removes_tracked_and_untracked_files(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    target_dir = upload_dir / "Vehicles"
    target_dir.mkdir()
    tracked = target_dir / "abc.stl"
    tracked.write_text("data")
    untracked = target_dir / "notes.txt"
    untracked.write_text("notes")

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc", "f1", str(tracked), storage_mode="copy")
    conn.commit()
    conn.close()

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(target_dir)})
    assert resp.status_code == 200
    assert resp.json()["deletedModels"] == 1
    assert not target_dir.exists()

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models WHERE id='abc'").fetchone()["c"]
    conn.close()
    assert count == 0


def test_delete_folder_reference_mode_hard_deletes_not_tombstones(client, tmp_path):
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

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(sub)})
    assert resp.status_code == 200

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) c FROM models WHERE id='m1'").fetchone()["c"]
    conn.close()
    assert count == 0, "reference-mode row must be hard-deleted, not tombstoned, from File view"
    assert not f1.exists()


def test_delete_folder_refuses_drive_root(client, tmp_path):
    drive_root = Path(tmp_path.anchor)
    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(drive_root)})
    assert resp.status_code == 400
    assert drive_root.exists()


def test_delete_folder_refuses_watch_folder_root(client, tmp_path):
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    watch_root.mkdir()

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    conn.commit()
    conn.close()

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(watch_root)})
    assert resp.status_code == 400
    assert watch_root.exists()


def test_delete_folder_allows_subfolder_of_watch_root(client, tmp_path):
    from app.db import get_db_conn
    watch_root = tmp_path / "watched"
    sub = watch_root / "Old"
    sub.mkdir(parents=True)

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
        ("wf1", str(watch_root), "f1"),
    )
    conn.commit()
    conn.close()

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(sub)})
    assert resp.status_code == 200
    assert not sub.exists()
    assert watch_root.exists()


def test_delete_nonexistent_folder_404s(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(upload_dir / "Nope")})
    assert resp.status_code == 404


def test_delete_folder_refuses_path_outside_managed_library_and_watch_roots(client, tmp_path):
    """A directory that lives neither under UPLOAD_DIR nor under any configured
    watch_folders.path is not a File-view node at all -- delete_folder must reject
    it with a clean 4xx via resolve_storage_mode_for_path's containment check,
    the same guard rename/move already apply, rather than recursively rmtree
    an arbitrary directory anywhere on the filesystem.
    """
    outside_dir = tmp_path / "unrelated"
    outside_dir.mkdir()
    survivor = outside_dir / "keep.txt"
    survivor.write_text("do not delete me")

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(outside_dir)})
    assert resp.status_code == 400
    assert outside_dir.exists()
    assert survivor.exists()
    assert survivor.read_text() == "do not delete me"


def test_move_folder_into_own_subtree_rejected(client, tmp_path):
    """Moving "Tanks" to a targetPath nested inside "Tanks" itself
    (e.g. UPLOAD_DIR/Tanks/Sub/Tanks) must be rejected before
    destination.parent.mkdir(parents=True, exist_ok=True) runs -- otherwise
    that call creates the intermediate "Sub" directory *inside the still
    -present source* right before shutil.move blows up, leaving a stray
    directory behind even though the whole operation ultimately failed.
    """
    upload_dir = Path(os.environ["FILE_STORAGE"])
    src_dir = upload_dir / "Tanks"
    src_dir.mkdir()
    f1 = src_dir / "hull.stl"
    f1.write_text("data")

    target = str(src_dir / "Sub" / "Tanks")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(src_dir), "targetPath": target})
    assert resp.status_code == 400
    assert src_dir.exists()  # untouched
    assert f1.exists()
    assert sorted(p.name for p in src_dir.iterdir()) == ["hull.stl"]  # no stray "Sub" dir created
