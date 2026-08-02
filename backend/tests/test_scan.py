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
