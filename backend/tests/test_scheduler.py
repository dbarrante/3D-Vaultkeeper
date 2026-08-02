def test_should_run_now_true_when_never_scanned():
    from app.scheduler import should_run_now
    row = {"lastScanAt": None, "frequencyMinutes": 60, "enabled": 1}
    assert should_run_now(row, now_ms_value=1_000_000) is True


def test_should_run_now_false_before_frequency_elapsed():
    from app.scheduler import should_run_now
    row = {"lastScanAt": 1_000_000, "frequencyMinutes": 60, "enabled": 1}
    just_under_an_hour_later = 1_000_000 + (59 * 60 * 1000)
    assert should_run_now(row, now_ms_value=just_under_an_hour_later) is False


def test_should_run_now_true_after_frequency_elapsed():
    from app.scheduler import should_run_now
    row = {"lastScanAt": 1_000_000, "frequencyMinutes": 60, "enabled": 1}
    just_over_an_hour_later = 1_000_000 + (61 * 60 * 1000)
    assert should_run_now(row, now_ms_value=just_over_an_hour_later) is True


def test_should_run_now_false_when_disabled():
    from app.scheduler import should_run_now
    row = {"lastScanAt": None, "frequencyMinutes": 60, "enabled": 0}
    assert should_run_now(row, now_ms_value=1_000_000) is False


def test_scheduler_tick_scans_due_folders_and_downloads(client, tmp_path, monkeypatch):
    from app.scheduler import scheduler_tick
    from app.db import get_db_conn

    watched_dir = tmp_path / "watched"
    watched_dir.mkdir()
    (watched_dir / "a.stl").write_bytes(b"solid a endsolid")

    conn = get_db_conn()
    conn.execute(
        "INSERT INTO watch_folders(id,path,folderId,frequencyMinutes,lastScanAt,enabled) VALUES (?,?,?,?,?,?)",
        ("wf1", str(watched_dir), "1", 60, None, 1),
    )
    conn.commit()
    conn.close()

    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    monkeypatch.setattr("app.scheduler.default_downloads_dir", lambda: downloads)

    summary = scheduler_tick()
    assert summary["watchFoldersScanned"] == 1
    assert summary["totalIngested"] == 1

    models = client.get("/api/models", params={"folderId": "1"}).json()
    assert any(m["name"] == "a.stl" for m in models)
