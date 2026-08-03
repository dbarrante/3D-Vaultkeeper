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
