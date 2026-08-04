import os
from pathlib import Path
from unittest.mock import patch

import pytest


def test_build_tree_walks_nested_directories_and_flags_model_files(tmp_path):
    from app.services.import_wizard import build_tree

    root = tmp_path / "raw"
    root.mkdir()
    (root / "loose.stl").write_bytes(b"solid loose endsolid")
    (root / "notes.txt").write_bytes(b"just notes")
    subdir = root / "Tank Kit"
    subdir.mkdir()
    (subdir / "hull.stl").write_bytes(b"solid hull endsolid")
    (subdir / "photo.jpg").write_bytes(b"fake jpg bytes")

    tree = build_tree(root)

    assert tree["name"] == "raw"
    assert tree["path"] == str(root)
    assert len(tree["files"]) == 2
    loose = next(f for f in tree["files"] if f["name"] == "loose.stl")
    assert loose["isModel"] is True
    notes = next(f for f in tree["files"] if f["name"] == "notes.txt")
    assert notes["isModel"] is False

    assert len(tree["folders"]) == 1
    kit = tree["folders"][0]
    assert kit["name"] == "Tank Kit"
    hull = next(f for f in kit["files"] if f["name"] == "hull.stl")
    assert hull["isModel"] is True
    photo = next(f for f in kit["files"] if f["name"] == "photo.jpg")
    assert photo["isModel"] is False


def test_build_tree_handles_empty_directory(tmp_path):
    from app.services.import_wizard import build_tree

    root = tmp_path / "empty"
    root.mkdir()

    tree = build_tree(root)

    assert tree["folders"] == []
    assert tree["files"] == []


def test_import_tree_endpoint_returns_walked_structure(client, tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / "part.stl").write_bytes(b"solid part endsolid")

    resp = client.get("/api/import/tree", params={"path": str(root)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["files"][0]["name"] == "part.stl"
    assert body["files"][0]["isModel"] is True


def test_import_tree_endpoint_rejects_nonexistent_path(client, tmp_path):
    resp = client.get("/api/import/tree", params={"path": str(tmp_path / "does_not_exist")})

    assert resp.status_code == 400


def test_sanitize_path_segment_replaces_illegal_characters():
    from app.services.import_wizard import sanitize_path_segment

    assert sanitize_path_segment('Tanks: "Heavy"') == "Tanks_ _Heavy_"


def test_sanitize_path_segment_strips_trailing_dots_and_spaces():
    from app.services.import_wizard import sanitize_path_segment

    assert sanitize_path_segment("Vehicles.. ") == "Vehicles"


def test_sanitize_path_segment_suffixes_reserved_windows_names():
    from app.services.import_wizard import sanitize_path_segment

    assert sanitize_path_segment("CON") == "CON_"
    assert sanitize_path_segment("com3") == "com3_"


def test_folder_disk_path_walks_parent_chain(client):
    from app.db import get_db_conn
    from app.services.import_wizard import folder_disk_path

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f2", "Tanks", "f1"))
    conn.commit()
    conn.close()

    assert folder_disk_path("f2") == os.path.join("Vehicles", "Tanks")
    assert folder_disk_path("f1") == "Vehicles"


def test_folder_disk_path_sanitizes_each_segment(client):
    from app.db import get_db_conn
    from app.services.import_wizard import folder_disk_path

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f3", "A/B", None))
    conn.commit()
    conn.close()

    assert folder_disk_path("f3") == "A_B"


def test_expand_placement_loose_file_returns_itself(tmp_path):
    from app.services.import_wizard import expand_placement

    f = tmp_path / "loose.stl"
    f.write_bytes(b"solid endsolid")

    result = expand_placement(str(f), is_folder=False)

    assert result == [f]


def test_expand_placement_folder_walks_recursively(tmp_path):
    from app.services.import_wizard import expand_placement

    root = tmp_path / "Tank Kit"
    root.mkdir()
    (root / "hull.stl").write_bytes(b"solid endsolid")
    sub = root / "extras"
    sub.mkdir()
    (sub / "turret.stl").write_bytes(b"solid endsolid")

    result = expand_placement(str(root), is_folder=True)

    names = {p.name for p in result}
    assert names == {"hull.stl", "turret.stl"}


def test_commit_placement_file_moves_model_and_creates_row(client, tmp_path):
    from app.db import get_db_conn
    from app.services.import_wizard import commit_placement_file

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid endsolid")

    result = commit_placement_file(source, "f1")

    assert result["status"] == "ok"
    assert result["isModel"] is True
    assert not source.exists()

    conn = get_db_conn()
    row = conn.execute("SELECT folderId, storageMode, filePath FROM models WHERE filePath LIKE ?", (f"%Vehicles%",)).fetchone()
    conn.close()
    assert row["folderId"] == "f1"
    assert row["storageMode"] == "copy"
    assert "Vehicles" in row["filePath"]


def test_commit_placement_file_moves_non_model_without_db_row(client, tmp_path):
    from app.db import get_db_conn
    from app.services.import_wizard import commit_placement_file

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    source = tmp_path / "photo.jpg"
    source.write_bytes(b"fake jpg")

    result = commit_placement_file(source, "f1")

    assert result["status"] == "ok"
    assert result["isModel"] is False
    assert not source.exists()

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) as c FROM models").fetchone()["c"]
    conn.close()
    assert count == 0


def test_commit_placement_file_reports_error_without_raising(client, tmp_path):
    from app.services.import_wizard import commit_placement_file

    missing = tmp_path / "gone.stl"  # never created

    result = commit_placement_file(missing, "1")

    assert result["status"] == "error"
    assert "error" in result


def test_commit_endpoint_processes_batch_and_isolates_failures(client, tmp_path):
    conn_setup = [
        ("f1", "Vehicles", None),
    ]
    from app.db import get_db_conn
    conn = get_db_conn()
    for row in conn_setup:
        conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", row)
    conn.commit()
    conn.close()

    good = tmp_path / "good.stl"
    good.write_bytes(b"solid endsolid")
    missing = tmp_path / "missing.stl"  # never created, will fail

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(good), "isFolder": False, "targetFolderId": "f1"},
            {"sourcePath": str(missing), "isFolder": False, "targetFolderId": "f1"},
        ]
    })

    assert resp.status_code == 200
    results = resp.json()["results"]
    statuses = {r["sourcePath"]: r["status"] for r in results}
    assert statuses[str(good)] == "ok"
    assert statuses[str(missing)] == "error"


def test_commit_endpoint_groups_folder_results_under_placement_source(client, tmp_path):
    from app.db import get_db_conn
    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    kit = tmp_path / "Tank Kit"
    kit.mkdir()
    (kit / "hull.stl").write_bytes(b"solid endsolid")
    (kit / "turret.stl").write_bytes(b"solid endsolid")

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(kit), "isFolder": True, "targetFolderId": "f1"},
        ]
    })

    results = resp.json()["results"]
    assert len(results) == 2
    assert all(r["placementSourcePath"] == str(kit) for r in results)
    assert all(r["status"] == "ok" for r in results)


def test_folder_disk_path_raises_for_unresolvable_folder_id(client):
    from app.services.import_wizard import folder_disk_path

    with pytest.raises(ValueError):
        folder_disk_path("does-not-exist")


def test_commit_placement_file_reports_error_for_unresolvable_target_folder_and_does_not_move_source(client, tmp_path):
    from app.services.import_wizard import commit_placement_file

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid endsolid")

    result = commit_placement_file(source, "does-not-exist")

    assert result["status"] == "error"
    assert source.exists()  # a bogus targetFolderId must not delete/relocate the source


def test_expand_placement_raises_for_vanished_folder(tmp_path):
    from app.services.import_wizard import expand_placement

    gone = tmp_path / "never_existed"  # never created

    with pytest.raises(Exception):
        expand_placement(str(gone), is_folder=True)


def test_expand_placement_returns_empty_list_for_genuinely_empty_folder(tmp_path):
    from app.services.import_wizard import expand_placement

    empty = tmp_path / "Empty Kit"
    empty.mkdir()

    result = expand_placement(str(empty), is_folder=True)

    assert result == []


def test_commit_endpoint_reports_error_for_vanished_folder_placement_without_crashing_batch(client, tmp_path):
    from app.db import get_db_conn
    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    gone = tmp_path / "never_existed"  # never created

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(gone), "isFolder": True, "targetFolderId": "f1"},
        ]
    })

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["status"] == "error"
    assert results[0]["placementSourcePath"] == str(gone)


def test_commit_endpoint_one_bad_placement_does_not_block_a_sibling_placement(client, tmp_path):
    from app.db import get_db_conn
    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    gone = tmp_path / "never_existed"  # never created
    good = tmp_path / "good.stl"
    good.write_bytes(b"solid endsolid")

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(gone), "isFolder": True, "targetFolderId": "f1"},
            {"sourcePath": str(good), "isFolder": False, "targetFolderId": "f1"},
        ]
    })

    assert resp.status_code == 200
    results = resp.json()["results"]
    statuses = {r["placementSourcePath"]: r["status"] for r in results}
    assert statuses[str(gone)] == "error"
    assert statuses[str(good)] == "ok"


def test_commit_endpoint_empty_folder_placement_produces_no_results_and_no_error(client, tmp_path):
    from app.db import get_db_conn
    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    empty = tmp_path / "Empty Kit"
    empty.mkdir()
    good = tmp_path / "good.stl"
    good.write_bytes(b"solid endsolid")

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(empty), "isFolder": True, "targetFolderId": "f1"},
            {"sourcePath": str(good), "isFolder": False, "targetFolderId": "f1"},
        ]
    })

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    assert results[0]["sourcePath"] == str(good)
    assert results[0]["status"] == "ok"


def test_build_tree_skips_unreadable_subdirectory_and_still_lists_siblings(tmp_path, monkeypatch):
    """A picked directory (e.g. a drive root) commonly contains at least
    one inaccessible system folder (System Volume Information,
    $RECYCLE.BIN on Windows) -- one PermissionError anywhere in the
    recursion must not 500 the whole request. Real permission-denied
    directories are awkward to construct portably in a test, so this
    follows test_scan.py's flaky-monkeypatch convention (see
    test_scan_watch_folder_survives_folder_resolution_failure) and
    monkeypatches Path.iterdir to raise for one specific directory name."""
    from app.services.import_wizard import build_tree

    root = tmp_path / "raw"
    root.mkdir()
    (root / "readable.stl").write_bytes(b"solid endsolid")
    blocked = root / "System Volume Information"
    blocked.mkdir()
    (blocked / "hidden.stl").write_bytes(b"solid endsolid")

    real_iterdir = Path.iterdir

    def flaky_iterdir(self):
        if self.name == "System Volume Information":
            raise PermissionError(f"Access denied: {self}")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", flaky_iterdir)

    tree = build_tree(root)  # must not raise

    assert any(f["name"] == "readable.stl" for f in tree["files"])
    blocked_folder = next(f for f in tree["folders"] if f["name"] == "System Volume Information")
    assert blocked_folder["folders"] == []
    assert blocked_folder["files"] == []


def test_build_tree_guards_against_symlink_cycle(tmp_path, monkeypatch):
    """A symlink cycle must not cause unbounded recursion. Directory
    symlinks require elevated privileges to create on Windows, so a real
    symlink loop isn't portable to construct here; instead this
    monkeypatches Path.resolve so a subdirectory's resolved identity
    matches an already-visited ancestor, the same condition a real
    symlink-back-to-a-parent would produce, and verifies build_tree's
    _visited tracking stops the walk there rather than recursing.
    Note: because the on-disk "loop" directory here is otherwise empty,
    this proves the cycle-detection short-circuits on a repeated resolved
    path (the actual mechanism guarding against unbounded recursion) --
    it doesn't reproduce true unbounded recursion via a live filesystem
    symlink loop, which wasn't reliably constructible in this
    environment."""
    from app.services.import_wizard import build_tree

    root = tmp_path / "raw"
    root.mkdir()
    (root / "file.stl").write_bytes(b"solid endsolid")
    loop = root / "loop"
    loop.mkdir()

    root_real = root.resolve()
    real_resolve = Path.resolve

    def fake_resolve(self, *args, **kwargs):
        if self.name == "loop":
            return root_real  # simulate a symlink pointing back to an ancestor
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    tree = build_tree(root)  # must terminate

    loop_folder = next(f for f in tree["folders"] if f["name"] == "loop")
    assert loop_folder["folders"] == []
    assert loop_folder["files"] == []


def test_commit_endpoint_per_file_failure_in_folder_placement_does_not_block_sibling(client, tmp_path):
    """Existing coverage (test_commit_endpoint_one_bad_placement_does_not_block_a_sibling_placement)
    only proves isolation across separate *placements*. This proves the
    narrower, spec-named case: two files inside the *same* folder
    placement, one fails, the other must still succeed -- both tagged
    with the same placementSourcePath."""
    from app.db import get_db_conn
    from app.services import import_wizard as iw_module

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "Vehicles", None))
    conn.commit()
    conn.close()

    kit = tmp_path / "Tank Kit"
    kit.mkdir()
    good = kit / "good.stl"
    good.write_bytes(b"solid endsolid")
    bad = kit / "bad.stl"
    bad.write_bytes(b"solid endsolid")

    real_commit_placement_file = iw_module.commit_placement_file

    def flaky_commit_placement_file(file_path, target_folder_id):
        if file_path.name == "bad.stl":
            return {"sourcePath": str(file_path), "status": "error", "error": "simulated failure", "isModel": True}
        return real_commit_placement_file(file_path, target_folder_id)

    with patch("app.routers.import_wizard.commit_placement_file", side_effect=flaky_commit_placement_file):
        resp = client.post("/api/import/commit", json={
            "placements": [
                {"sourcePath": str(kit), "isFolder": True, "targetFolderId": "f1"},
            ]
        })

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 2
    statuses = {r["sourcePath"]: r["status"] for r in results}
    assert statuses[str(good)] == "ok"
    assert statuses[str(bad)] == "error"
    assert all(r["placementSourcePath"] == str(kit) for r in results)


def test_commit_endpoint_traversal_shaped_folder_name_stays_inside_upload_dir(client, tmp_path):
    """String-level coverage (test_sanitize_path_segment_*) only checks
    sanitize_path_segment's output in isolation. This goes through the
    real POST /api/import/commit endpoint with a folder literally named
    ".." (and a slash-bearing "../../etc") and confirms the resulting
    filePath -- read back from the DB -- still resolves to a location
    inside UPLOAD_DIR, not somewhere it escaped to via path traversal.
    ".." alone exercises sanitize_path_segment's strip(" ."); the
    slash-bearing name additionally exercises its illegal-character
    substitution (a bare strip(" .") would leave the slashes intact and
    let os.path.join treat them as extra path segments)."""
    from app.db import get_db_conn, UPLOAD_DIR

    conn = get_db_conn()
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f1", "..", None))
    conn.execute("INSERT INTO folders(id,name,parentId) VALUES (?,?,?)", ("f2", "../../etc", None))
    conn.commit()
    conn.close()

    source1 = tmp_path / "evil.stl"
    source1.write_bytes(b"solid endsolid")
    source2 = tmp_path / "evil2.stl"
    source2.write_bytes(b"solid endsolid")

    resp = client.post("/api/import/commit", json={
        "placements": [
            {"sourcePath": str(source1), "isFolder": False, "targetFolderId": "f1"},
            {"sourcePath": str(source2), "isFolder": False, "targetFolderId": "f2"},
        ]
    })

    assert resp.status_code == 200
    results = resp.json()["results"]
    assert all(r["status"] == "ok" for r in results)

    conn = get_db_conn()
    row1 = conn.execute("SELECT filePath FROM models WHERE folderId=?", ("f1",)).fetchone()
    row2 = conn.execute("SELECT filePath FROM models WHERE folderId=?", ("f2",)).fetchone()
    conn.close()

    upload_root = Path(UPLOAD_DIR).resolve()
    assert Path(row1["filePath"]).resolve().is_relative_to(upload_root)
    assert Path(row2["filePath"]).resolve().is_relative_to(upload_root)
