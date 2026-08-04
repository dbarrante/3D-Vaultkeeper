import os

import pytest


def test_ingest_file_copies_into_upload_dir_and_creates_model_row(client, tmp_path, monkeypatch):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "incoming.stl"
    source.write_bytes(b"solid source endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="incoming.stl", tags=["watched"])

    assert model["name"] == "incoming.stl"
    assert model["folderId"] == "1"
    assert model["tags"] == ["watched"]
    assert os.path.exists(os.path.join(UPLOAD_DIR, f"{model['id']}.stl"))
    assert source.exists()  # default move=False — the watcher must never delete the user's original


def test_ingest_file_with_move_true_relocates_instead_of_copying(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "scratch.stl"
    source.write_bytes(b"solid scratch endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="scratch.stl", move=True)

    assert os.path.exists(os.path.join(UPLOAD_DIR, f"{model['id']}.stl"))
    assert not source.exists()  # move=True: the scratch file is gone, not duplicated


def test_ingest_file_with_record_source_persists_source_path(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "watched.stl"
    source.write_bytes(b"solid watched endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="watched.stl", record_source=True)

    conn = get_db_conn()
    row = conn.execute("SELECT sourcePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["sourcePath"] == str(source)


def test_ingest_file_without_record_source_leaves_source_path_null(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "uploaded.stl"
    source.write_bytes(b"solid uploaded endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="uploaded.stl")  # record_source defaults False

    conn = get_db_conn()
    row = conn.execute("SELECT sourcePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["sourcePath"] is None


def test_ingest_file_with_pickup_sidecar_notes_sets_description(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "annotated.stl"
    source.write_bytes(b"solid annotated endsolid")
    (tmp_path / "annotated.txt").write_text("Print with supports, 0.16mm layers.")

    model = ingest_file(
        str(source), folder_id="1", original_filename="annotated.stl", pickup_sidecar_notes=True
    )

    assert model["description"] == "Print with supports, 0.16mm layers."


def test_ingest_file_without_pickup_sidecar_notes_ignores_sibling_txt(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "annotated.stl"
    source.write_bytes(b"solid annotated endsolid")
    (tmp_path / "annotated.txt").write_text("Print with supports, 0.16mm layers.")

    model = ingest_file(str(source), folder_id="1", original_filename="annotated.stl")  # default False

    assert model["description"] == ""


def test_ingest_file_with_reference_only_does_not_copy(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "watched" / "model.stl"
    source.parent.mkdir()
    source.write_bytes(b"solid watched endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="model.stl", reference_only=True)

    assert model["name"] == "model.stl"
    assert not any(f.startswith(model["id"]) for f in os.listdir(UPLOAD_DIR))
    assert source.exists()


def test_ingest_file_with_reference_only_records_source_path_and_storage_mode(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "model.stl"
    source.write_bytes(b"solid endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="model.stl", reference_only=True)

    conn = get_db_conn()
    row = conn.execute("SELECT sourcePath, storageMode FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["sourcePath"] == str(source)
    assert row["storageMode"] == "reference"


def test_ingest_file_default_copy_mode_unaffected(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "model.stl"
    source.write_bytes(b"solid endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="model.stl")

    conn = get_db_conn()
    row = conn.execute("SELECT storageMode FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["storageMode"] == "copy"


def test_ingest_file_with_dest_subpath_places_file_in_subdirectory(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR, get_db_conn

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid hull endsolid")

    model = ingest_file(
        str(source),
        folder_id="1",
        original_filename="hull.stl",
        move=True,
        dest_subpath="Vehicles/Tanks",
    )

    expected_dir = UPLOAD_DIR / "Vehicles" / "Tanks"
    assert os.path.isdir(expected_dir)
    assert os.path.exists(os.path.join(expected_dir, f"{model['id']}.stl"))
    assert model["filePath"] == os.path.join(str(expected_dir), f"{model['id']}.stl")
    assert not source.exists()  # move=True

    # Verify filePath is persisted to the database
    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["filePath"] == os.path.join(str(expected_dir), f"{model['id']}.stl")


def test_ingest_file_without_dest_subpath_stays_flat(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR, get_db_conn

    source = tmp_path / "flat.stl"
    source.write_bytes(b"solid flat endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="flat.stl")

    assert os.path.exists(os.path.join(UPLOAD_DIR, f"{model['id']}.stl"))
    assert model["filePath"] == os.path.join(str(UPLOAD_DIR), f"{model['id']}.stl")

    # Verify filePath is persisted to the database
    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["filePath"] == os.path.join(str(UPLOAD_DIR), f"{model['id']}.stl")


def test_ingest_file_reference_only_sets_file_path_to_source(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "linked.stl"
    source.write_bytes(b"solid linked endsolid")

    model = ingest_file(
        str(source), folder_id="1", original_filename="linked.stl", reference_only=True
    )

    assert model["filePath"] == str(source)

    # Verify filePath is persisted to the database
    conn = get_db_conn()
    row = conn.execute("SELECT filePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row["filePath"] == str(source)


def _make_failing_cursor_factory(target_sql_prefix):
    """sqlite3.Cursor is a C extension type and can't be monkeypatched
    directly (its attributes are immutable) -- instead this builds a real
    sqlite3.Cursor subclass to install as a cursor *factory*, which sqlite3
    officially supports via the `factory` argument to .cursor().
    """
    import sqlite3

    class FailingCursor(sqlite3.Cursor):
        def execute(self, sql, *args, **kwargs):
            if sql.strip().startswith(target_sql_prefix):
                raise sqlite3.OperationalError("simulated DB failure")
            return super().execute(sql, *args, **kwargs)

    return FailingCursor


def _make_failing_connection(failing_cursor_factory):
    import sqlite3

    class FailingConnection(sqlite3.Connection):
        def cursor(self, factory=None):
            return super().cursor(factory or failing_cursor_factory)

    return FailingConnection


def test_ingest_file_restores_source_on_db_insert_failure_when_moved(client, tmp_path, monkeypatch):
    """If the DB INSERT fails after move=True already relocated the file (DB
    locked, disk full, etc.), the source file must not be left stranded under
    an opaque <uuid>.<ext> name with zero DB record -- it must be restored to
    its original path, and no row should exist."""
    import sqlite3
    from app.services import ingestion
    from app.db import DB_PATH, get_db_conn

    source = tmp_path / "will_fail.stl"
    source.write_bytes(b"solid endsolid")

    failing_cursor = _make_failing_cursor_factory("INSERT INTO models")
    failing_connection = _make_failing_connection(failing_cursor)

    def fake_get_db_conn():
        conn = sqlite3.connect(DB_PATH, factory=failing_connection)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(ingestion, "get_db_conn", fake_get_db_conn)

    with pytest.raises(sqlite3.OperationalError):
        ingestion.ingest_file(str(source), folder_id="1", original_filename="will_fail.stl", move=True)

    assert source.exists()  # restored to its original location
    assert source.read_bytes() == b"solid endsolid"

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) as c FROM models").fetchone()["c"]
    conn.close()
    assert count == 0


def test_ingest_file_cleans_up_copy_on_db_insert_failure_when_not_moved(client, tmp_path, monkeypatch):
    """Same DB-failure scenario but with the default move=False (copy): the
    source must be untouched (it was never moved), and the orphaned copy in
    UPLOAD_DIR must be cleaned up rather than left behind with no DB row."""
    import sqlite3
    from app.services import ingestion
    from app.db import DB_PATH, get_db_conn, UPLOAD_DIR

    source = tmp_path / "will_fail_copy.stl"
    source.write_bytes(b"solid endsolid")

    failing_cursor = _make_failing_cursor_factory("INSERT INTO models")
    failing_connection = _make_failing_connection(failing_cursor)

    def fake_get_db_conn():
        conn = sqlite3.connect(DB_PATH, factory=failing_connection)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(ingestion, "get_db_conn", fake_get_db_conn)

    with pytest.raises(sqlite3.OperationalError):
        ingestion.ingest_file(str(source), folder_id="1", original_filename="will_fail_copy.stl", move=False)

    assert source.exists()  # copy mode never touches the source
    assert source.read_bytes() == b"solid endsolid"
    # orphaned copy was cleaned up, not left behind (UPLOAD_DIR always contains
    # a "manuals" subdirectory created at startup, so only check for stray files)
    assert [p for p in UPLOAD_DIR.iterdir() if p.is_file()] == []

    conn = get_db_conn()
    count = conn.execute("SELECT COUNT(*) as c FROM models").fetchone()["c"]
    conn.close()
    assert count == 0
