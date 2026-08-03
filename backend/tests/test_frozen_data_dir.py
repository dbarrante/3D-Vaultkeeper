import sys
from pathlib import Path


def _reimport_db():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app import db
    return db


def test_db_path_defaults_to_relative_when_not_frozen(monkeypatch):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("FILE_STORAGE", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)

    db = _reimport_db()

    assert db.DB_PATH == "data.db"
    assert db.UPLOAD_DIR == Path("./app/uploads")


def test_db_path_defaults_to_localappdata_when_frozen(monkeypatch, tmp_path):
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("FILE_STORAGE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    db = _reimport_db()

    assert db.DB_PATH == str(tmp_path / "3D Vaultkeeper" / "data.db")
    assert db.UPLOAD_DIR == tmp_path / "3D Vaultkeeper" / "uploads"


def test_env_override_wins_even_when_frozen(monkeypatch, tmp_path):
    custom_db = str(tmp_path / "custom.db")
    custom_uploads = str(tmp_path / "custom_uploads")
    monkeypatch.setenv("DB_PATH", custom_db)
    monkeypatch.setenv("FILE_STORAGE", custom_uploads)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    db = _reimport_db()

    assert db.DB_PATH == custom_db
    assert db.UPLOAD_DIR == Path(custom_uploads)


def test_env_override_wins_even_without_localappdata(monkeypatch, tmp_path):
    custom_db = str(tmp_path / "custom.db")
    custom_uploads = str(tmp_path / "custom_uploads")
    monkeypatch.setenv("DB_PATH", custom_db)
    monkeypatch.setenv("FILE_STORAGE", custom_uploads)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)  # LOCALAPPDATA is NOT set
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    db = _reimport_db()

    assert db.DB_PATH == custom_db
    assert db.UPLOAD_DIR == Path(custom_uploads)
