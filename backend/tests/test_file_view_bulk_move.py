import os
import sqlite3
from pathlib import Path


def _make_model(tmp_path, name, subpath, storageMode_kwargs=None):
    from app.services.ingestion import ingest_file

    source = tmp_path / f"{name}_source.stl"
    source.write_bytes(b"solid endsolid")
    kwargs = {"move": True, "dest_subpath": subpath}
    if storageMode_kwargs:
        kwargs.update(storageMode_kwargs)
    return ingest_file(str(source), folder_id="1", original_filename=f"{name}.stl", **kwargs)


def test_bulk_move_moves_multiple_files_successfully(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    b = _make_model(tmp_path, "b", "Source")

    target = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Dest"}).json()["path"]

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"], b["id"]], "targetPath": target},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == []
    moved_ids = {m["id"] for m in body["moved"]}
    assert moved_ids == {a["id"], b["id"]}

    for model, original in [(a, a["filePath"]), (b, b["filePath"])]:
        assert not Path(original).exists()
        moved_entry = next(m for m in body["moved"] if m["id"] == model["id"])
        assert Path(moved_entry["filePath"]).exists()
        assert Path(moved_entry["filePath"]).parent == Path(target)


def test_bulk_move_null_target_path_moves_to_library_root(client, tmp_path):
    from app.db import UPLOAD_DIR

    a = _make_model(tmp_path, "a", "Source")

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": None},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == []
    moved_entry = body["moved"][0]
    assert Path(moved_entry["filePath"]).parent == Path(UPLOAD_DIR).resolve()


def test_bulk_move_partial_failure_continues_other_files(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    b = _make_model(tmp_path, "b", "Source")

    target = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Dest"}).json()["path"]

    # Pre-create a collision at the destination for "a" only.
    # The actual filename is the UUID filename from a["filePath"], not "a.stl"
    (Path(target) / Path(a["filePath"]).name).write_bytes(b"already here")

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"], b["id"]], "targetPath": target},
    )
    assert response.status_code == 200
    body = response.json()

    assert len(body["failed"]) == 1
    assert body["failed"][0]["id"] == a["id"]
    assert "already exists" in body["failed"][0]["reason"]

    assert len(body["moved"]) == 1
    assert body["moved"][0]["id"] == b["id"]
    assert Path(body["moved"][0]["filePath"]).exists()
    # "a" was left untouched at its original location since it was never moved
    assert Path(a["filePath"]).exists()


def test_bulk_move_rejects_relative_target_path(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": "relative/path"},
    )
    assert response.status_code == 400


def test_bulk_move_404s_on_missing_target_folder(client, tmp_path):
    a = _make_model(tmp_path, "a", "Source")
    missing = str(tmp_path / "does-not-exist")
    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": missing},
    )
    assert response.status_code == 404


def test_bulk_move_rolls_back_file_on_db_failure(client, tmp_path, monkeypatch):
    from app.db import get_db_conn as original_get_db_conn

    a = _make_model(tmp_path, "a", "Source")
    original_path = a["filePath"]

    target = client.post("/api/file-view/folder", json={"parentPath": None, "name": "Dest"}).json()["path"]

    # Wrapper class for sqlite3.Connection that intercepts execute on the cursor
    class FailingConnection:
        def __init__(self, real_conn):
            self._real = real_conn
            self._fail_next_update = False

        def cursor(self):
            real_cursor = self._real.cursor()

            class InterceptingCursor:
                def __init__(self, real_cursor, parent):
                    self._real = real_cursor
                    self._parent = parent

                def execute(self, sql, params=()):
                    if (
                        isinstance(sql, str)
                        and "UPDATE models SET filePath" in sql
                        and self._parent._fail_next_update
                    ):
                        raise sqlite3.OperationalError("simulated failure")
                    return self._real.execute(sql, params)

                def fetchone(self):
                    return self._real.fetchone()

                def fetchall(self):
                    return self._real.fetchall()

                def __getattr__(self, name):
                    return getattr(self._real, name)

            return InterceptingCursor(real_cursor, self)

        def commit(self):
            return self._real.commit()

        def rollback(self):
            return self._real.rollback()

        def close(self):
            return self._real.close()

        def execute(self, *args, **kwargs):
            return self._real.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self._real, name)

    # Mock get_db_conn to return our failing connection wrapper
    def mock_get_db_conn():
        real_conn = original_get_db_conn()
        failing_conn = FailingConnection(real_conn)
        failing_conn._fail_next_update = True
        return failing_conn

    monkeypatch.setattr("app.routers.file_view.get_db_conn", mock_get_db_conn)

    response = client.post(
        "/api/file-view/models/bulk-move",
        json={"ids": [a["id"]], "targetPath": target},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["moved"] == []
    assert body["failed"][0]["id"] == a["id"]

    # File was moved back to its original location by the rollback.
    assert Path(original_path).exists()
