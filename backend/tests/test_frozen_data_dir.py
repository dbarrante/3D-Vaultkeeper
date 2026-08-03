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


def test_frozen_data_dir_and_upload_dir_are_created(monkeypatch, tmp_path):
    """DB_PATH's parent directory and UPLOAD_DIR must both exist after
    import, even for a completely fresh LOCALAPPDATA that had nothing in
    it beforehand — sqlite3.connect() does not create parent directories
    itself, so relying on MANUAL_DIR's mkdir as an accidental side effect
    is fragile (see db.py comment history).

    MANUAL_STORAGE is deliberately pointed at a sibling location
    (tmp_path/"manuals_elsewhere") rather than left to its default of
    UPLOAD_DIR / "manuals". If MANUAL_STORAGE were unset, MANUAL_DIR would
    nest under UPLOAD_DIR, and MANUAL_DIR's own pre-existing
    mkdir(parents=True) call would incidentally create DB_PATH's parent
    and UPLOAD_DIR as intermediate directories — making this test pass
    even without the fix under test. Pointing MANUAL_STORAGE at a sibling
    directory instead means MANUAL_DIR's mkdir cannot reach DB_PATH's
    parent or UPLOAD_DIR at all, so this test only passes if db.py
    explicitly creates them itself.
    """
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.delenv("FILE_STORAGE", raising=False)
    manual_dir = tmp_path / "manuals_elsewhere"
    monkeypatch.setenv("MANUAL_STORAGE", str(manual_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert list(tmp_path.iterdir()) == []  # fresh LOCALAPPDATA, nothing pre-existing

    db = _reimport_db()

    db_path_parent = Path(db.DB_PATH).parent
    # Sanity-check the isolation this test relies on: MANUAL_DIR must not
    # be, or contain, DB_PATH's parent or UPLOAD_DIR, otherwise its own
    # mkdir could incidentally satisfy the assertions below for the wrong
    # reason.
    assert db_path_parent != manual_dir
    assert db.UPLOAD_DIR != manual_dir

    assert db_path_parent.exists()
    assert db.UPLOAD_DIR.exists()
