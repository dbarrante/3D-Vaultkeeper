import sqlite3


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE models (id TEXT PRIMARY KEY, name TEXT, sourcePath TEXT, "
        "storageMode TEXT NOT NULL DEFAULT 'copy')"
    )
    return conn


def test_migrate_converts_and_deletes_redundant_copy(tmp_path):
    from scripts.migrate_watch_folder_references import migrate_watch_folder_to_references

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    real_file = watch_root / "a.stl"
    real_file.write_bytes(b"solid endsolid")

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    copy_path = uploads_dir / "m1.stl"
    copy_path.write_bytes(b"solid endsolid")

    conn = _make_db()
    conn.execute(
        "INSERT INTO models(id,name,sourcePath,storageMode) VALUES (?,?,?,?)",
        ("m1", "a.stl", str(real_file), "copy"),
    )
    conn.commit()

    result = migrate_watch_folder_to_references(conn, str(uploads_dir), str(watch_root))

    assert result == {"migrated": 1, "skipped_missing": []}
    row = conn.execute("SELECT storageMode FROM models WHERE id='m1'").fetchone()
    assert row["storageMode"] == "reference"
    assert not copy_path.exists()
    conn.close()


def test_migrate_skips_row_whose_source_file_is_missing(tmp_path):
    from scripts.migrate_watch_folder_references import migrate_watch_folder_to_references

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    missing_source = watch_root / "gone.stl"

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    copy_path = uploads_dir / "m2.stl"
    copy_path.write_bytes(b"solid endsolid")

    conn = _make_db()
    conn.execute(
        "INSERT INTO models(id,name,sourcePath,storageMode) VALUES (?,?,?,?)",
        ("m2", "gone.stl", str(missing_source), "copy"),
    )
    conn.commit()

    result = migrate_watch_folder_to_references(conn, str(uploads_dir), str(watch_root))

    assert result == {"migrated": 0, "skipped_missing": ["m2"]}
    row = conn.execute("SELECT storageMode FROM models WHERE id='m2'").fetchone()
    assert row["storageMode"] == "copy"
    assert copy_path.exists()
    conn.close()


def test_migrate_ignores_rows_outside_the_watch_folder(tmp_path):
    from scripts.migrate_watch_folder_references import migrate_watch_folder_to_references

    watch_root = tmp_path / "watched"
    watch_root.mkdir()
    other_root = tmp_path / "elsewhere"
    other_root.mkdir()
    other_file = other_root / "b.stl"
    other_file.write_bytes(b"solid endsolid")

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    copy_path = uploads_dir / "m3.stl"
    copy_path.write_bytes(b"solid endsolid")

    conn = _make_db()
    conn.execute(
        "INSERT INTO models(id,name,sourcePath,storageMode) VALUES (?,?,?,?)",
        ("m3", "b.stl", str(other_file), "copy"),
    )
    conn.commit()

    result = migrate_watch_folder_to_references(conn, str(uploads_dir), str(watch_root))

    assert result == {"migrated": 0, "skipped_missing": []}
    row = conn.execute("SELECT storageMode FROM models WHERE id='m3'").fetchone()
    assert row["storageMode"] == "copy"
    assert copy_path.exists()
    conn.close()
