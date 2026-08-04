import os
from pathlib import Path


def test_is_supported_3d_file():
    from app.services.scan import is_supported_3d_file
    assert is_supported_3d_file(Path("model.stl")) is True
    assert is_supported_3d_file(Path("model.3MF")) is True  # case-insensitive
    assert is_supported_3d_file(Path("readme.txt")) is False
    assert is_supported_3d_file(Path("model.stl.zip")) is False


def test_find_new_files_returns_only_unseen_supported_files(tmp_path):
    from app.services.scan import find_new_files

    (tmp_path / "keep.stl").write_bytes(b"solid keep endsolid")
    (tmp_path / "already_ingested.3mf").write_bytes(b"fake 3mf")
    (tmp_path / "notes.txt").write_bytes(b"not a model")
    sub = tmp_path / "subfolder"
    sub.mkdir()
    (sub / "nested.step").write_bytes(b"fake step")

    already_seen = {str(tmp_path / "already_ingested.3mf")}
    found = find_new_files(tmp_path, already_seen)
    found_names = {p.name for p in found}

    assert found_names == {"keep.stl", "nested.step"}  # recursive, excludes seen, excludes non-3D


def test_scan_watch_folder_ingests_new_files_and_updates_last_scan(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn, now_ms

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")
    (watched_dir / "b.3mf").write_bytes(b"fake b")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        ("wf1", str(watched_dir), "1", 60, None, 1),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM watch_folders WHERE id=?", ("wf1",)).fetchone()
    conn.close()

    before = now_ms()
    count = scan_watch_folder(dict(row))
    assert count == 2

    conn = get_db_conn()
    models = conn.execute("SELECT name, sourcePath, folderId FROM models").fetchall()
    updated = conn.execute("SELECT lastScanAt FROM watch_folders WHERE id=?", ("wf1",)).fetchone()
    conn.close()

    names = {m["name"] for m in models}
    assert names == {"a.stl", "b.3mf"}
    assert all(m["folderId"] == "1" for m in models)
    assert all(m["sourcePath"] is not None for m in models)
    assert updated["lastScanAt"] >= before


def test_scan_watch_folder_skips_already_ingested_files(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        ("wf1", str(watched_dir), "1", 60, None, 1),
    )
    conn.commit()
    row = dict(conn.execute("SELECT * FROM watch_folders WHERE id=?", ("wf1",)).fetchone())
    conn.close()

    first_count = scan_watch_folder(row)
    second_count = scan_watch_folder(row)  # nothing new since the first scan

    assert first_count == 1
    assert second_count == 0


def test_scan_watch_folder_picks_up_sidecar_notes(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")
    (watched_dir / "a.txt").write_text("Print settings: 0.2mm, 20% infill.")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        ("wf1", str(watched_dir), "1", 60, None, 1),
    )
    conn.commit()
    row = dict(conn.execute("SELECT * FROM watch_folders WHERE id=?", ("wf1",)).fetchone())
    conn.close()

    scan_watch_folder(row)

    conn = get_db_conn()
    model = conn.execute("SELECT description FROM models WHERE name='a.stl'").fetchone()
    conn.close()
    assert model["description"] == "Print settings: 0.2mm, 20% infill."


def test_scan_downloads_folder_creates_pending_inbox_items(client, tmp_path):
    from app.services.scan import scan_downloads_folder
    from app.db import get_db_conn

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "found_online.stl").write_bytes(b"solid found endsolid")
    (downloads / "not_a_model.pdf").write_bytes(b"%PDF-fake")

    count = scan_downloads_folder(downloads)
    assert count == 1

    conn = get_db_conn()
    items = conn.execute("SELECT path, status FROM inbox_items").fetchall()
    conn.close()
    assert len(items) == 1
    assert items[0]["path"] == str(downloads / "found_online.stl")
    assert items[0]["status"] == "pending"


def test_scan_downloads_folder_does_not_redetect_already_flagged_files(client, tmp_path):
    from app.services.scan import scan_downloads_folder

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "found_online.stl").write_bytes(b"solid found endsolid")

    first = scan_downloads_folder(downloads)
    second = scan_downloads_folder(downloads)

    assert first == 1
    assert second == 0


def test_default_downloads_dir_is_under_home():
    from app.services.scan import default_downloads_dir
    from pathlib import Path
    assert default_downloads_dir() == Path.home() / "Downloads"


def test_scan_watch_folder_references_instead_of_copying(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn, UPLOAD_DIR

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")

    row = {"id": "wf1", "path": str(watched_dir), "folderId": "1"}
    ingested = scan_watch_folder(row)

    assert ingested == 1
    conn = get_db_conn()
    model = conn.execute("SELECT storageMode, sourcePath, id FROM models WHERE folderId='1'").fetchone()
    conn.close()
    assert model["storageMode"] == "reference"
    assert model["sourcePath"] == str(watched_dir / "a.stl")
    assert not any(f.startswith(model["id"]) for f in os.listdir(UPLOAD_DIR))


def test_get_or_create_folder_creates_new_folder(client):
    from app.services.scan import get_or_create_folder
    from app.db import get_db_conn

    folder_id = get_or_create_folder("PrintA", None)

    conn = get_db_conn()
    row = conn.execute(
        "SELECT name, parentId FROM folders WHERE id=?", (folder_id,)
    ).fetchone()
    conn.close()
    assert row["name"] == "PrintA"
    assert row["parentId"] is None


def test_get_or_create_folder_reuses_existing_folder(client):
    from app.services.scan import get_or_create_folder
    from app.db import get_db_conn

    first_id = get_or_create_folder("PrintA", "1")
    second_id = get_or_create_folder("PrintA", "1")

    assert first_id == second_id
    conn = get_db_conn()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM folders WHERE name=? AND parentId=?",
        ("PrintA", "1"),
    ).fetchone()["c"]
    conn.close()
    assert count == 1


def test_get_or_create_folder_distinguishes_by_parent(client):
    """Same name under a different parent (or no parent) is a different
    folder — never merged into one."""
    from app.services.scan import get_or_create_folder

    under_none = get_or_create_folder("PrintA", None)
    under_1 = get_or_create_folder("PrintA", "1")

    assert under_none != under_1


def test_get_or_create_folder_reuses_existing_top_level_folder(client):
    """Mirrors test_get_or_create_folder_reuses_existing_folder but for
    parent_id=None -- the one case that actually exercises the NULL-safe
    `IS` comparison (parentId=? would never match a NULL column, silently
    breaking top-level-folder reuse if someone "simplified" IS to =)."""
    from app.services.scan import get_or_create_folder
    from app.db import get_db_conn

    first_id = get_or_create_folder("PrintA", None)
    second_id = get_or_create_folder("PrintA", None)

    assert first_id == second_id
    conn = get_db_conn()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM folders WHERE name=? AND parentId IS NULL",
        ("PrintA",),
    ).fetchone()["c"]
    conn.close()
    assert count == 1


def test_scan_watch_folder_mirrors_subdirectory_structure(client, tmp_path):
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "loose.stl").write_bytes(b"solid loose endsolid")
    print_a = watched_dir / "PrintA"
    print_a.mkdir()
    (print_a / "part1.stl").write_bytes(b"solid part1 endsolid")
    (print_a / "part2.stl").write_bytes(b"solid part2 endsolid")
    supports = print_a / "supports"
    supports.mkdir()
    (supports / "support1.stl").write_bytes(b"solid support1 endsolid")

    row = {"id": "wf1", "path": str(watched_dir), "folderId": "1"}
    ingested = scan_watch_folder(row)
    assert ingested == 4

    conn = get_db_conn()
    folders = {f["name"]: dict(f) for f in conn.execute("SELECT id, name, parentId FROM folders")}
    models = {m["name"]: dict(m) for m in conn.execute("SELECT name, folderId FROM models")}
    conn.close()

    assert "PrintA" in folders
    print_a_folder = folders["PrintA"]
    assert print_a_folder["parentId"] == "1"

    assert "supports" in folders
    supports_folder = folders["supports"]
    assert supports_folder["parentId"] == print_a_folder["id"]

    assert models["loose.stl"]["folderId"] == "1"
    assert models["part1.stl"]["folderId"] == print_a_folder["id"]
    assert models["part2.stl"]["folderId"] == print_a_folder["id"]
    assert models["support1.stl"]["folderId"] == supports_folder["id"]


def test_scan_watch_folder_reuses_existing_folder_on_rescan(client, tmp_path):
    """A second scan tick that finds the same subdirectory again (e.g. a
    new file dropped into an already-mirrored PrintA folder) reuses the
    folder created on the first tick, not "PrintA (2)"."""
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    print_a = watched_dir / "PrintA"
    print_a.mkdir()
    (print_a / "part1.stl").write_bytes(b"solid part1 endsolid")

    row = {"id": "wf1", "path": str(watched_dir), "folderId": "1"}
    scan_watch_folder(row)

    (print_a / "part2.stl").write_bytes(b"solid part2 endsolid")
    scan_watch_folder(row)

    conn = get_db_conn()
    print_a_folders = conn.execute("SELECT id FROM folders WHERE name='PrintA'").fetchall()
    conn.close()
    assert len(print_a_folders) == 1
