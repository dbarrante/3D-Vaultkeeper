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
