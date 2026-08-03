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
