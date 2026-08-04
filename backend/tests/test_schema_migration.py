import sqlite3


def test_models_table_has_phase0_columns(client, monkeypatch):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert {"author", "sourceUrl", "category", "colorCount", "sliceSettings"}.issubset(columns)


def test_migration_is_idempotent_on_already_migrated_db(client):
    from app.db import init_db
    init_db()  # calling it twice must not raise
    init_db()


def test_models_table_has_storage_mode_column_defaulting_to_copy(client):
    from app.db import DB_PATH, get_db_conn

    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert "storageMode" in columns

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("m1", "x.stl", "1", "/api/models/m1/download", 10, 0, "[]", "", None),
    )
    conn.commit()
    row = conn.execute("SELECT storageMode FROM models WHERE id='m1'").fetchone()
    conn.close()
    assert row["storageMode"] == "copy"


def test_models_table_has_file_path_column(client):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert "filePath" in columns


def test_backfill_sets_file_path_for_reference_mode_from_source_path(client, tmp_path):
    from app.db import get_db_conn, init_db

    source = tmp_path / "linked.stl"
    source.write_bytes(b"solid endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,sourcePath,storageMode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m1", "linked.stl", "1", "/api/models/m1/download", 10, 0, "[]", "", None, str(source), "reference"),
    )
    conn.commit()
    conn.close()

    init_db()  # re-run: triggers the backfill pass on the row just inserted

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='m1'").fetchone()
    conn.close()
    assert row["filePath"] == str(source)


def test_backfill_sets_file_path_for_copy_mode_from_upload_dir(client):
    from app.db import get_db_conn, init_db, UPLOAD_DIR

    dest = UPLOAD_DIR / "m2.stl"
    dest.write_bytes(b"solid endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,storageMode) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("m2", "x.stl", "1", "/api/models/m2/download", 10, 0, "[]", "", None, "copy"),
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='m2'").fetchone()
    conn.close()
    assert row["filePath"] == str(dest)


def test_backfill_is_idempotent_and_does_not_overwrite_existing_file_path(client):
    from app.db import get_db_conn, init_db

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m3", "x.stl", "1", "/api/models/m3/download", 10, 0, "[]", "", None, "copy", "/already/set/path.stl"),
    )
    conn.commit()
    conn.close()

    init_db()

    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id='m3'").fetchone()
    conn.close()
    assert row["filePath"] == "/already/set/path.stl"


def test_row_to_model_includes_file_path(client):
    from app.db import get_db_conn, row_to_model

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,storageMode,filePath) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("m4", "x.stl", "1", "/api/models/m4/download", 10, 0, "[]", "", None, "copy", "/some/path.stl"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM models WHERE id='m4'").fetchone()
    conn.close()
    assert row_to_model(row)["filePath"] == "/some/path.stl"
