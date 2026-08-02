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
