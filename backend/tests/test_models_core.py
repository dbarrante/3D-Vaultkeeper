import os


def _upload(client, name="test.stl", folder_id="1"):
    return client.post(
        "/api/models/upload",
        data={"folderId": folder_id, "tags": "a,b"},
        files={"file": (name, b"solid test endsolid", "application/octet-stream")},
    ).json()


def test_upload_model_persists_and_lists(client):
    created = _upload(client)
    assert created["name"] == "test.stl"
    assert created["tags"] == ["a", "b"]

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["id"] == created["id"] for m in listed)


def test_update_model_allowed_fields_only(client):
    created = _upload(client)
    response = client.patch(
        f"/api/models/{created['id']}",
        json={"description": "new desc", "notAllowedField": "ignored"},
    )
    assert response.status_code == 200
    assert response.json()["description"] == "new desc"


def test_delete_model_removes_row_and_file(client):
    created = _upload(client)
    response = client.delete(f"/api/models/{created['id']}")
    assert response.status_code == 200
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != created["id"] for m in listed)


def test_download_model_returns_file_bytes(client):
    created = _upload(client)
    response = client.get(f"/api/models/{created['id']}/download")
    assert response.status_code == 200
    assert response.content == b"solid test endsolid"


def test_download_missing_model_currently_returns_500_known_bug(client):
    # Upstream get_model_info() calls row_to_model(None) with no null check when the
    # id doesn't match any row, which crashes instead of raising a clean 404. This
    # test locks in *actual* current behavior for the Phase 0 no-behavior-change
    # refactor; fixing the bug is a separate, deliberate follow-up task.
    response = client.get("/api/models/does-not-exist/download")
    assert response.status_code == 500


def test_reference_model_reports_missing_false_when_file_present(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "present.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="present.stl", reference_only=True)

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == model["id"])
    assert found["storageMode"] == "reference"
    assert found["missing"] is False


def test_reference_model_reports_missing_true_when_file_deleted(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "gone.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="gone.stl", reference_only=True)
    os.remove(source)

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == model["id"])
    assert found["missing"] is True


def test_copy_mode_model_never_reports_missing(client):
    created = _upload(client)
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    found = next(m for m in listed if m["id"] == created["id"])
    assert found["storageMode"] == "copy"
    assert found["missing"] is False


def test_download_reference_model_serves_from_source_path(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "download_me.stl"
    source.write_bytes(b"solid reference content endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="download_me.stl", reference_only=True)

    response = client.get(f"/api/models/{model['id']}/download")
    assert response.status_code == 200
    assert response.content == b"solid reference content endsolid"


def test_download_reference_model_returns_descriptive_404_when_missing(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "vanish.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="vanish.stl", reference_only=True)
    os.remove(source)

    response = client.get(f"/api/models/{model['id']}/download")
    assert response.status_code == 404
    assert "moved or deleted" in response.json()["detail"]


def test_delete_reference_model_default_leaves_file_on_disk(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "keep.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="keep.stl", reference_only=True)

    response = client.delete(f"/api/models/{model['id']}")
    assert response.status_code == 200
    assert source.exists()
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != model["id"] for m in listed)


def test_delete_reference_model_with_delete_file_true_removes_it(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "remove_me.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="remove_me.stl", reference_only=True)

    response = client.delete(f"/api/models/{model['id']}", params={"deleteFile": "true"})
    assert response.status_code == 200
    assert not source.exists()

    # deleteFile=True hard-deletes the row (no tombstone) — nothing left to re-detect.
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row is None


def test_delete_copy_mode_model_ignores_delete_file_param(client):
    created = _upload(client)

    response = client.delete(f"/api/models/{created['id']}", params={"deleteFile": "false"})
    assert response.status_code == 200
    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != created["id"] for m in listed)


def test_delete_reference_model_default_tombstones_row_instead_of_hard_deleting(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import get_db_conn

    source = tmp_path / "tombstone_me.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="tombstone_me.stl", reference_only=True)

    response = client.delete(f"/api/models/{model['id']}")
    assert response.status_code == 200

    conn = get_db_conn()
    row = conn.execute("SELECT removedAt, sourcePath FROM models WHERE id=?", (model["id"],)).fetchone()
    conn.close()
    assert row is not None  # row survives — tombstoned, not hard-deleted
    assert row["removedAt"] is not None
    assert row["sourcePath"] == str(source)


def test_get_models_excludes_tombstoned_row_folder_filtered_and_unfiltered(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "excluded.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="excluded.stl", reference_only=True)

    client.delete(f"/api/models/{model['id']}")

    filtered = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != model["id"] for m in filtered)

    unfiltered_all = client.get("/api/models", params={"folderId": "all"}).json()
    assert all(m["id"] != model["id"] for m in unfiltered_all)

    unfiltered_none = client.get("/api/models").json()
    assert all(m["id"] != model["id"] for m in unfiltered_none)


def test_tombstoned_reference_model_is_not_reingested_on_next_scan(client, tmp_path):
    """The core regression the reviewer found: 'Remove from library' on a reference-mode
    model used to hard-delete the row, so the next watch-folder scan would re-add the
    still-present file as a brand-new entry. Tombstoning must prevent that."""
    from app.services.ingestion import ingest_file
    from app.services.scan import scan_watch_folder
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    source = watched_dir / "keeper.stl"
    source.write_bytes(b"solid keeper endsolid")

    model = ingest_file(str(source), folder_id="1", original_filename="keeper.stl", reference_only=True)

    response = client.delete(f"/api/models/{model['id']}")
    assert response.status_code == 200

    watch_row = {"id": "wf1", "path": str(watched_dir), "folderId": "1"}
    rescanned_count = scan_watch_folder(watch_row)
    assert rescanned_count == 0  # file must NOT be re-ingested as a new model

    conn = get_db_conn()
    matching_rows = conn.execute(
        "SELECT id FROM models WHERE sourcePath=?", (str(source),)
    ).fetchall()
    conn.close()
    assert len(matching_rows) == 1  # still just the original (tombstoned) row, no duplicate


def test_replace_file_on_reference_model_returns_400_and_does_not_write(client, tmp_path):
    from app.services.ingestion import ingest_file

    source = tmp_path / "no_replace.stl"
    source.write_bytes(b"solid original endsolid")
    model = ingest_file(str(source), folder_id="1", original_filename="no_replace.stl", reference_only=True)

    response = client.put(
        f"/api/models/{model['id']}/file",
        files={"file": ("replacement.stl", b"solid replacement endsolid", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert "references a file on disk" in response.json()["detail"]

    # Content served on download is still the untouched original — nothing was written.
    download = client.get(f"/api/models/{model['id']}/download")
    assert download.content == b"solid original endsolid"

    # Copy-mode behavior is unchanged (still 200, still replaces the file) —
    # see test_replace_model_file_updates_size in test_models_bulk.py.


def test_download_subdirectory_model_returns_file_bytes(client, tmp_path):
    """A model ingested via the Import Wizard's commit endpoint (Task 4) lands
    in a real UPLOAD_DIR subdirectory via dest_subpath, not flat in UPLOAD_DIR's
    root. Download must resolve it via filePath, not the flat os.listdir scan."""
    from app.services.ingestion import ingest_file

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid subdir content endsolid")
    model = ingest_file(
        str(source),
        folder_id="1",
        original_filename="hull.stl",
        move=True,
        dest_subpath="Vehicles/Tanks",
    )

    response = client.get(f"/api/models/{model['id']}/download")
    assert response.status_code == 200
    assert response.content == b"solid subdir content endsolid"


def test_delete_subdirectory_model_removes_the_actual_file(client, tmp_path):
    from app.services.ingestion import ingest_file
    from app.db import UPLOAD_DIR

    source = tmp_path / "hull.stl"
    source.write_bytes(b"solid endsolid")
    model = ingest_file(
        str(source),
        folder_id="1",
        original_filename="hull.stl",
        move=True,
        dest_subpath="Vehicles/Tanks",
    )
    disk_path = UPLOAD_DIR / "Vehicles" / "Tanks" / f"{model['id']}.stl"
    assert disk_path.exists()

    response = client.delete(f"/api/models/{model['id']}")
    assert response.status_code == 200
    assert not disk_path.exists()  # actually removed, not just a silent no-op

    listed = client.get("/api/models", params={"folderId": "1"}).json()
    assert all(m["id"] != model["id"] for m in listed)


def test_thumbnail_queue_returns_models_missing_thumbnail(client):
    _upload(client, name="needs_thumb.stl")
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "needs_thumb.stl"
    assert body[0]["thumbnail"] is None


def test_thumbnail_queue_excludes_models_with_a_thumbnail(client):
    model = _upload(client, name="has_thumb.stl")
    client.patch(f"/api/models/{model['id']}", json={"thumbnail": "data:image/png;base64,fake"})
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_thumbnail_queue_excludes_permanently_failed_models(client):
    model = _upload(client, name="broken.stl")
    client.patch(f"/api/models/{model['id']}", json={"thumbnailFailed": True})
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_thumbnail_queue_excludes_unsupported_extensions(client):
    _upload(client, name="notes.pdf")
    resp = client.get("/api/models/thumbnail-queue")
    assert resp.status_code == 200
    assert resp.json() == []


def test_thumbnail_queue_respects_limit(client):
    _upload(client, name="a.stl")
    _upload(client, name="b.stl")
    _upload(client, name="c.stl")
    resp = client.get("/api/models/thumbnail-queue?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_thumbnail_queue_with_multiple_eligible_models_returns_a_valid_eligible_one(client):
    # ORDER BY RANDOM() means which row comes back isn't deterministic, but
    # every filter criterion (missing thumbnail, not permanently failed,
    # supported extension) must still hold -- this guards against the
    # ordering change accidentally loosening the existing WHERE clause.
    # Not asserting on randomness itself, just that filtering still works
    # and a single call still returns one genuinely eligible model.
    eligible_ids = {
        _upload(client, name=f"eligible_{i}.stl")["id"] for i in range(5)
    }
    ineligible_thumbnail = _upload(client, name="has_thumb.stl")
    client.patch(
        f"/api/models/{ineligible_thumbnail['id']}",
        json={"thumbnail": "data:image/png;base64,fake"},
    )
    ineligible_failed = _upload(client, name="failed.stl")
    client.patch(f"/api/models/{ineligible_failed['id']}", json={"thumbnailFailed": True})
    _upload(client, name="unsupported.pdf")

    resp = client.get("/api/models/thumbnail-queue?limit=1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    returned = body[0]
    assert returned["id"] in eligible_ids
    assert returned["thumbnail"] is None
    assert not returned["thumbnailFailed"]
    assert returned["name"].lower().endswith((".stl", ".3mf", ".step", ".stp"))


def test_update_model_can_set_thumbnail_failed(client):
    model = _upload(client, name="fails_to_render.stl")
    resp = client.patch(f"/api/models/{model['id']}", json={"thumbnailFailed": True})
    assert resp.status_code == 200
    assert resp.json()["thumbnailFailed"] is True
