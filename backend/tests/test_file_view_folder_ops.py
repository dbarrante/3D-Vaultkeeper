import os
from pathlib import Path

import pytest

windows_only = pytest.mark.skipif(
    os.name != "nt",
    reason="bare drive-letter paths ('C:') are a Windows drive-relative-path quirk",
)


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


def test_delete_folder_refuses_upload_dir_itself(client, tmp_path):
    """UPLOAD_DIR is the app's own managed copy-mode storage root. It isn't a
    watch_folders row, so the watch-root exact-match check doesn't cover it, and
    resolve_storage_mode_for_path explicitly ALLOWS resolved == upload_root (that
    check exists to permit copy-mode operations inside the library, not to protect
    the root itself). Without a dedicated guard, deleting UPLOAD_DIR would hard-
    delete every copy-mode model row and rmtree the entire managed library in one
    call. Must be rejected exactly like a watch folder's root.
    """
    upload_dir = Path(os.environ["FILE_STORAGE"])
    survivor = upload_dir / "keep.txt"
    survivor.write_text("do not delete me")

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(upload_dir)})
    assert resp.status_code // 100 == 4
    assert upload_dir.exists()
    assert survivor.exists()
    assert survivor.read_text() == "do not delete me"


def test_delete_folder_reports_partial_failure_when_rmtree_cannot_fully_remove_directory(client, tmp_path, monkeypatch):
    """By the time shutil.rmtree runs, every matched model's DB row is already
    deleted and its file already removed -- that part of the operation genuinely
    succeeded. If rmtree itself then fails (e.g. a file locked by another process,
    realistic for a .stl held open by a slicer on Windows), the endpoint must not
    silently report full success: ignore_errors=True previously swallowed this
    completely. Simulate the failure by monkeypatching shutil.rmtree to raise,
    since there's no cross-platform way to reliably create a genuinely undeletable
    file in a test.
    """
    import app.routers.file_view as file_view_module

    upload_dir = Path(os.environ["FILE_STORAGE"])
    target_dir = upload_dir / "Locked"
    target_dir.mkdir()
    (target_dir / "held.stl").write_text("data")

    def raising_rmtree(path):
        raise OSError("simulated: file in use by another process")

    monkeypatch.setattr(file_view_module.shutil, "rmtree", raising_rmtree)

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(target_dir)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["directoryRemoved"] is False, "must not silently claim success when rmtree failed"
    assert target_dir.exists()  # rmtree never actually ran (patched), directory survives


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


# ---------------------------------------------------------------------------
# Non-absolute / drive-relative source paths
# ---------------------------------------------------------------------------


def _bare_drive(tmp_path):
    """The bare drive-letter form ("C:") of whatever drive tmp_path lives on.

    This is exactly what the File-mode tree emits as the top node id for a
    reference-mode file whose watch root sits at a drive root.
    """
    return tmp_path.anchor.rstrip("\\/")


@windows_only
def test_delete_folder_refuses_bare_drive_letter(client, tmp_path, monkeypatch):
    """Path("C:") does NOT resolve to C:\\ -- Windows treats a bare drive letter
    as *drive-relative*, so it resolves against the process's current working
    directory on that drive. The pre-existing `resolved.parent == resolved`
    drive-root guard therefore never fires for it, and if the backend's cwd
    happens to sit inside UPLOAD_DIR (or a watch root), "C:" sails through the
    containment check and rmtree's the cwd. chdir into a subdirectory of
    UPLOAD_DIR reproduces exactly that arrangement.
    """
    upload_dir = Path(os.environ["FILE_STORAGE"])
    sub = upload_dir / "Sub"
    sub.mkdir()
    (sub / "keep.stl").write_text("do not delete me")
    monkeypatch.chdir(sub)

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": _bare_drive(tmp_path)})
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"].lower()
    assert sub.exists()
    assert (sub / "keep.stl").read_text() == "do not delete me"


def test_delete_folder_refuses_relative_path(client, tmp_path, monkeypatch):
    """Cross-platform half of the same hole: any non-absolute path resolves
    against the process cwd rather than naming an unambiguous location, so it
    is never a legitimate File-mode node regardless of what it happens to
    resolve to.
    """
    upload_dir = Path(os.environ["FILE_STORAGE"])
    sub = upload_dir / "Sub"
    sub.mkdir()
    (sub / "keep.stl").write_text("do not delete me")
    monkeypatch.chdir(upload_dir)

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": "Sub"})
    assert resp.status_code == 400
    assert sub.exists()
    assert (sub / "keep.stl").read_text() == "do not delete me"


@windows_only
def test_rename_folder_refuses_bare_drive_letter_source(client, tmp_path, monkeypatch):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    sub = upload_dir / "Sub"
    sub.mkdir()
    monkeypatch.chdir(sub)

    resp = client.post(
        "/api/file-view/folder/rename",
        json={"path": _bare_drive(tmp_path), "newName": "Renamed"},
    )
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"].lower()
    assert sub.exists()


@windows_only
def test_move_folder_refuses_bare_drive_letter_source(client, tmp_path, monkeypatch):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    sub = upload_dir / "Sub"
    sub.mkdir()
    monkeypatch.chdir(sub)

    resp = client.post(
        "/api/file-view/folder/move",
        json={"sourcePath": _bare_drive(tmp_path), "targetPath": str(upload_dir / "Moved")},
    )
    assert resp.status_code == 400
    assert "absolute" in resp.json()["detail"].lower()
    assert sub.exists()
    assert not (upload_dir / "Moved").exists()


def test_validate_destination_rejects_non_absolute_destination(client, tmp_path, monkeypatch):
    """Destination side of the same guard, covering
    PATCH /api/models/{id}/location as well as the folder endpoints.
    """
    from app.services.file_view_ops import validate_destination

    upload_dir = Path(os.environ["FILE_STORAGE"])
    monkeypatch.chdir(upload_dir)
    with pytest.raises(ValueError, match="absolute"):
        validate_destination("relative_dest.stl", "copy")


# ---------------------------------------------------------------------------
# Watch-root protection for rename / move / delete
# ---------------------------------------------------------------------------


def _nested_watch_roots(tmp_path):
    """A realistic arrangement: the user watches D:\\Models AND, separately,
    D:\\Models\\Projects\\Archive. Returns (outer_root, middle, inner_root).
    """
    outer = tmp_path / "watched"
    middle = outer / "Projects"
    inner = middle / "Archive"
    inner.mkdir(parents=True)
    return outer, middle, inner


def _register_watch_roots(*paths):
    from app.db import get_db_conn

    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    for index, path in enumerate(paths):
        conn.execute(
            "INSERT INTO watch_folders(id,path,folderId) VALUES (?,?,?)",
            (f"wf{index}", str(path), "f1"),
        )
    conn.commit()
    conn.close()


def test_rename_folder_refuses_watch_folder_root(client, tmp_path):
    """Renaming a registered watch folder's own root leaves watch_folders.path
    pointing at a directory that no longer exists -- the watcher then silently
    returns 0 results on every future scan instead of failing loudly.
    """
    outer, _middle, inner = _nested_watch_roots(tmp_path)
    _register_watch_roots(outer, inner)

    resp = client.post("/api/file-view/folder/rename", json={"path": str(inner), "newName": "Renamed"})
    assert resp.status_code == 400
    assert inner.exists()
    assert not (inner.parent / "Renamed").exists()


def test_rename_folder_refuses_folder_containing_watch_root(client, tmp_path):
    outer, middle, inner = _nested_watch_roots(tmp_path)
    _register_watch_roots(outer, inner)

    resp = client.post("/api/file-view/folder/rename", json={"path": str(middle), "newName": "Renamed"})
    assert resp.status_code == 400
    assert middle.exists()
    assert inner.exists()  # nested watch root untouched
    assert not (outer / "Renamed").exists()


def test_move_folder_refuses_watch_folder_root(client, tmp_path):
    outer, _middle, inner = _nested_watch_roots(tmp_path)
    _register_watch_roots(outer, inner)

    target = str(outer / "MovedArchive")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(inner), "targetPath": target})
    assert resp.status_code == 400
    assert inner.exists()
    assert not Path(target).exists()


def test_move_folder_refuses_folder_containing_watch_root(client, tmp_path):
    outer, middle, inner = _nested_watch_roots(tmp_path)
    _register_watch_roots(outer, inner)

    target = str(outer / "MovedProjects")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(middle), "targetPath": target})
    assert resp.status_code == 400
    assert middle.exists()
    assert inner.exists()  # nested watch root untouched
    assert not Path(target).exists()


def test_delete_folder_refuses_folder_containing_watch_root(client, tmp_path):
    """delete_folder's original guard was exact-match only (`resolved in
    watch_roots`), so deleting a PARENT of a registered watch root silently
    orphaned it -- the row survived, its directory did not.
    """
    outer, middle, inner = _nested_watch_roots(tmp_path)
    _register_watch_roots(outer, inner)
    (inner / "hull.stl").write_text("data")

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(middle)})
    assert resp.status_code == 400
    assert middle.exists()
    assert inner.exists()  # nested watch root untouched
    assert (inner / "hull.stl").exists()


def test_rename_folder_ordinary_subfolder_of_watch_root_still_works(client, tmp_path):
    """The watch-root guard must not over-reach: an ordinary subfolder with no
    watch_folders row of its own (and none nested under it) stays renameable.
    """
    outer, _middle, _inner = _nested_watch_roots(tmp_path)
    ordinary = outer / "Ordinary"
    ordinary.mkdir()
    _register_watch_roots(outer)

    resp = client.post("/api/file-view/folder/rename", json={"path": str(ordinary), "newName": "Renamed"})
    assert resp.status_code == 200
    assert not ordinary.exists()
    assert (outer / "Renamed").exists()
    assert outer.exists()


def test_move_folder_ordinary_subfolder_of_watch_root_still_works(client, tmp_path):
    outer, _middle, _inner = _nested_watch_roots(tmp_path)
    ordinary = outer / "Ordinary"
    ordinary.mkdir()
    _register_watch_roots(outer)

    target = str(outer / "Moved" / "Ordinary")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(ordinary), "targetPath": target})
    assert resp.status_code == 200
    assert not ordinary.exists()
    assert Path(target).exists()
    assert outer.exists()


def test_create_folder_under_real_parent_creates_dir_and_tracks_it(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Tanks"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert new_path == parent / "Tanks"
    assert new_path.is_dir()

    conn = get_db_conn()
    row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (str(new_path),)
    ).fetchone()
    conn.close()
    assert row is not None


def test_create_folder_with_no_parent_path_defaults_to_upload_dir(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])

    resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "TopLevel"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert new_path == upload_dir / "TopLevel"
    assert new_path.is_dir()


def test_create_folder_under_watch_root_works(client, tmp_path):
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

    resp = client.post("/api/file-view/folder", json={"parentPath": str(watch_root), "name": "Prints"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert new_path == watch_root / "Prints"
    assert new_path.is_dir()


def test_create_folder_outside_every_allowed_root_rejected(client, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(outside), "name": "New"})
    assert resp.status_code == 400
    assert not (outside / "New").exists()


def test_create_folder_name_is_sanitized(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])

    resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Bad<>Name"})
    assert resp.status_code == 200
    new_path = Path(resp.json()["path"])
    assert "<" not in new_path.name
    assert ">" not in new_path.name
    assert new_path.is_dir()


def test_create_folder_collision_rejected(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    existing = upload_dir / "Existing"
    existing.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Existing"})
    assert resp.status_code == 409


def test_create_folder_missing_parent_404s(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])
    missing_parent = upload_dir / "DoesNotExist"

    resp = client.post("/api/file-view/folder", json={"parentPath": str(missing_parent), "name": "X"})
    assert resp.status_code == 404


def test_get_tracked_folders_returns_created_paths(client, tmp_path):
    upload_dir = Path(os.environ["FILE_STORAGE"])

    client.post("/api/file-view/folder", json={"parentPath": None, "name": "Alpha"})
    client.post("/api/file-view/folder", json={"parentPath": None, "name": "Beta"})

    resp = client.get("/api/file-view/tracked-folders")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert str(upload_dir / "Alpha") in paths
    assert str(upload_dir / "Beta") in paths


def test_create_folder_delete_recreate_sequence_succeeds(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])

    # Create folder "Reusable"
    create_resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Reusable"})
    assert create_resp.status_code == 200
    folder_path = Path(create_resp.json()["path"])
    assert folder_path.is_dir()

    # Verify it's tracked
    conn = get_db_conn()
    row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (str(folder_path),)
    ).fetchone()
    conn.close()
    assert row is not None

    # Delete the folder via the existing delete_folder endpoint
    delete_resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(folder_path)})
    assert delete_resp.status_code == 200
    assert not folder_path.exists()
    # Note: delete_folder doesn't remove the tracked row yet (Task 2 will do that)

    # Re-create the folder at the same path -- should succeed cleanly (200, not 500)
    recreate_resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Reusable"})
    assert recreate_resp.status_code == 200
    new_path = Path(recreate_resp.json()["path"])
    assert new_path == folder_path
    assert new_path.is_dir()

    # Verify the stale row was replaced (INSERT OR REPLACE should have updated it)
    conn = get_db_conn()
    row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (str(new_path),)
    ).fetchone()
    conn.close()
    assert row is not None


def test_rename_folder_updates_tracked_child(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Tanks"})
    tracked_path = resp.json()["path"]

    resp = client.post("/api/file-view/folder/rename", json={"path": str(parent), "newName": "Cars"})
    assert resp.status_code == 200

    conn = get_db_conn()
    old_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (tracked_path,)
    ).fetchone()
    new_expected = str(upload_dir / "Cars" / "Tanks")
    new_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (new_expected,)
    ).fetchone()
    conn.close()
    assert old_row is None
    assert new_row is not None
    assert Path(new_expected).is_dir()


def test_move_folder_updates_tracked_descendant(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    source = upload_dir / "Tanks"
    source.mkdir()
    (upload_dir / "Archive").mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(source), "name": "Old"})
    tracked_path = resp.json()["path"]

    target = str(upload_dir / "Archive" / "Tanks")
    resp = client.post("/api/file-view/folder/move", json={"sourcePath": str(source), "targetPath": target})
    assert resp.status_code == 200

    conn = get_db_conn()
    old_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (tracked_path,)
    ).fetchone()
    new_expected = str(Path(target) / "Old")
    new_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (new_expected,)
    ).fetchone()
    conn.close()
    assert old_row is None
    assert new_row is not None


def test_delete_folder_removes_tracked_descendant(client, tmp_path):
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Empty"})
    tracked_path = resp.json()["path"]

    resp = client.request("DELETE", "/api/file-view/folder", json={"path": str(parent)})
    assert resp.status_code == 200

    conn = get_db_conn()
    row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (tracked_path,)
    ).fetchone()
    conn.close()
    assert row is None
    assert not Path(tracked_path).exists()


def test_tracked_folder_survives_being_emptied_out(client, tmp_path):
    """The exact behavior the spec's Goals section exists to guarantee: a
    tracked folder must NOT be pruned just because it (temporarily) has a
    model in it and then doesn't. Easy to get wrong by adding 'helpful'
    pruning logic that isn't wanted.
    """
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    parent = upload_dir / "Vehicles"
    parent.mkdir()

    resp = client.post("/api/file-view/folder", json={"parentPath": str(parent), "name": "Tanks"})
    tracked_path = resp.json()["path"]
    tracked_dir = Path(tracked_path)

    # A model "lands" in the tracked folder.
    model_file = tracked_dir / "abc123.stl"
    model_file.write_text("model data")
    conn = get_db_conn()
    _insert_folder(conn, "f1", "Root")
    _insert_model(conn, "abc123", "f1", str(model_file), storage_mode="copy")
    conn.commit()
    conn.close()

    # Confirm the tracked row is still present with a model now inside it.
    resp = client.get("/api/file-view/tracked-folders")
    assert tracked_path in resp.json()["paths"]

    # Move the model back out.
    other_dir = upload_dir / "Elsewhere"
    other_dir.mkdir()
    resp = client.patch(
        "/api/models/abc123/location",
        json={"newPath": str(other_dir / "abc123.stl")},
    )
    assert resp.status_code == 200

    # The tracked folder must still be reported -- it was never pruned.
    resp = client.get("/api/file-view/tracked-folders")
    assert tracked_path in resp.json()["paths"]
    assert tracked_dir.is_dir()


def test_rename_tracked_folder_itself_updates_its_own_row(client, tmp_path):
    """The other three sync tests only cover renaming/moving/deleting an
    ANCESTOR of a tracked folder. This covers renaming the tracked folder's
    own path directly -- the rel == "." branch in
    rewrite_tracked_folder_paths, and the most common real UI flow (create a
    folder, then immediately rename it).
    """
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])

    resp = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Tanks"})
    tracked_path = resp.json()["path"]

    resp = client.post("/api/file-view/folder/rename", json={"path": tracked_path, "newName": "Armor"})
    assert resp.status_code == 200

    new_expected = str(upload_dir / "Armor")
    conn = get_db_conn()
    old_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (tracked_path,)
    ).fetchone()
    new_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (new_expected,)
    ).fetchone()
    conn.close()
    assert old_row is None
    assert new_row is not None
    assert Path(new_expected).is_dir()


def test_create_folder_normalizes_parent_path_with_dotdot_segment(client, tmp_path):
    """A parentPath containing an uncollapsed '..' segment that still resolves
    inside UPLOAD_DIR (e.g. UPLOAD_DIR/Sub/..) must be normalized before the
    tracked-folder row is stored and returned. Without normalization,
    find_affected_tracked_folders' os.path.normpath(row["path"]) comparison
    would never match the un-collapsed stored value, silently orphaning the
    row from every future rename/move/delete sync -- exactly the bug this
    task exists to prevent. Also proves the desync is impossible end-to-end
    by renaming an ancestor afterward and confirming the row actually moves.
    """
    from app.db import get_db_conn
    upload_dir = Path(os.environ["FILE_STORAGE"])
    (upload_dir / "Sub").mkdir()
    dotdot_parent = str(upload_dir / "Sub" / "..")

    resp = client.post("/api/file-view/folder", json={"parentPath": dotdot_parent, "name": "Tanks"})
    assert resp.status_code == 200
    returned_path = resp.json()["path"]
    assert ".." not in Path(returned_path).parts, "returned path must already be normalized"
    expected = str(upload_dir / "Tanks")
    assert returned_path == expected

    conn = get_db_conn()
    row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (expected,)
    ).fetchone()
    conn.close()
    assert row is not None, "row must be stored in normalized form"

    # Close the loop empirically: rename an ancestor and confirm the tracked
    # row's path actually gets updated by find_affected_tracked_folders /
    # rewrite_tracked_folder_paths -- proving the desync is now impossible,
    # not just that the initial INSERT looks right.
    resp = client.post(
        "/api/file-view/folder/rename", json={"path": expected, "newName": "Armor"}
    )
    assert resp.status_code == 200

    conn = get_db_conn()
    old_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?", (expected,)
    ).fetchone()
    new_row = conn.execute(
        "SELECT path FROM file_view_tracked_folders WHERE path=?",
        (str(upload_dir / "Armor"),),
    ).fetchone()
    conn.close()
    assert old_row is None
    assert new_row is not None
