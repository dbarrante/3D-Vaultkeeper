from unittest.mock import patch


def _ok_worker(result_queue):
    result_queue.put(("ok", "C:/Users/dkbar/Downloads"))


def _cancelled_worker(result_queue):
    result_queue.put(("ok", None))  # tkinter's askdirectory() returns "" on Cancel -> normalized to None


def _crashing_worker(result_queue):
    # os._exit bypasses normal interpreter shutdown/exception handling entirely —
    # the closest a test can get to simulating a hard native Tcl/Tk crash that
    # a plain try/except in the parent process could never catch.
    import os
    os._exit(1)


def _hanging_worker(result_queue):
    import time
    time.sleep(30)


def test_run_folder_dialog_isolated_returns_selected_path():
    from app.routers.watcher import run_folder_dialog_isolated

    result = run_folder_dialog_isolated(timeout_seconds=10, worker=_ok_worker)
    assert result == {"path": "C:/Users/dkbar/Downloads"}


def test_run_folder_dialog_isolated_returns_null_on_cancel():
    from app.routers.watcher import run_folder_dialog_isolated

    result = run_folder_dialog_isolated(timeout_seconds=10, worker=_cancelled_worker)
    assert result == {"path": None}


def test_run_folder_dialog_isolated_survives_child_process_crash():
    """The whole point of running the dialog in a subprocess: a hard crash in
    it (simulated here with os._exit, standing in for a native Tcl/Tk fault)
    must not raise an uncatchable error in — or kill — the calling process."""
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    try:
        run_folder_dialog_isolated(timeout_seconds=10, worker=_crashing_worker)
        assert False, "expected an HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 503


def test_run_folder_dialog_isolated_times_out_cleanly():
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    try:
        run_folder_dialog_isolated(timeout_seconds=1, worker=_hanging_worker)
        assert False, "expected an HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 504


def test_run_folder_dialog_isolated_returns_503_when_tkinter_unavailable():
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    with patch("app.routers.watcher.TKINTER_AVAILABLE", False):
        try:
            run_folder_dialog_isolated(timeout_seconds=10, worker=_ok_worker)
            assert False, "expected an HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 503
            assert "manually" in exc.detail


def test_browse_folder_route_returns_isolated_result(client):
    with patch(
        "app.routers.watcher.run_folder_dialog_isolated",
        return_value={"path": "C:/picked/path"},
    ):
        response = client.post("/api/browse-folder")
    assert response.status_code == 200
    assert response.json() == {"path": "C:/picked/path"}
