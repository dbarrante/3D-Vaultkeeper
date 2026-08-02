import sqlite3


def test_watch_folders_and_inbox_tables_exist(client):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"watch_folders", "inbox_items"}.issubset(tables)


def test_models_table_has_source_path_column(client):
    from app.db import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    conn.close()
    assert "sourcePath" in columns
