import os


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
