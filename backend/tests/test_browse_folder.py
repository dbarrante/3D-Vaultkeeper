from unittest.mock import patch, MagicMock
import subprocess
import sys


def _completed(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


def test_run_folder_dialog_isolated_returns_selected_path():
    from app.routers.watcher import run_folder_dialog_isolated

    with patch("app.routers.watcher.subprocess.run", return_value=_completed("OK:C:/Users/dkbar/Downloads")):
        result = run_folder_dialog_isolated()
    assert result == {"path": "C:/Users/dkbar/Downloads"}


def test_run_folder_dialog_isolated_returns_null_on_cancel():
    from app.routers.watcher import run_folder_dialog_isolated

    # tkinter's askdirectory() returns "" on Cancel -> the dialog script emits "OK:" with nothing after it
    with patch("app.routers.watcher.subprocess.run", return_value=_completed("OK:")):
        result = run_folder_dialog_isolated()
    assert result == {"path": None}


def test_run_folder_dialog_isolated_survives_child_process_crash():
    """The whole point of running the dialog in a subprocess: a non-zero exit
    (crash, uncaught native fault, anything) must not raise an uncatchable
    error in — or kill — the calling process."""
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    with patch("app.routers.watcher.subprocess.run", return_value=_completed("", returncode=1)):
        try:
            run_folder_dialog_isolated()
            assert False, "expected an HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 503


def test_run_folder_dialog_isolated_times_out_cleanly():
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    with patch(
        "app.routers.watcher.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="python", timeout=1),
    ):
        try:
            run_folder_dialog_isolated(timeout_seconds=1)
            assert False, "expected an HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 504


def test_run_folder_dialog_isolated_returns_503_when_tkinter_unavailable():
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    with patch("app.routers.watcher.TKINTER_AVAILABLE", False):
        try:
            run_folder_dialog_isolated()
            assert False, "expected an HTTPException"
        except HTTPException as exc:
            assert exc.status_code == 503
            assert "manually" in exc.detail


def test_run_folder_dialog_isolated_does_not_reinvoke_uvicorn_entrypoint():
    """Confirmed root cause of a real crash: multiprocessing's Windows spawn
    start method, when the parent was launched via `python -m uvicorn ...`,
    re-executes that entire command as a bootstrap step — which tries to
    rebind the same port and crashes the real server. The fix is to shell
    out via subprocess.run with an explicit, self-contained -c script that
    has no relation to the app package at all, so there is nothing for a
    child process to "re-invoke". This test locks in that the dialog script
    passed to subprocess never references app.main, uvicorn, or -m.
    """
    from app.routers.watcher import DIALOG_SCRIPT

    assert "app.main" not in DIALOG_SCRIPT
    assert "uvicorn" not in DIALOG_SCRIPT


def _fake_frozen_run(result_text: str, returncode: int = 0):
    """Builds a subprocess.run replacement for the frozen path: real
    _run_frozen_folder_dialog invokes [sys.executable,
    "--browse-folder-worker", <result-file>] and expects the *file* to
    hold the result (see that function's docstring for why stdout isn't
    used) — so the fake, like the real worker process, must write to
    cmd[2] rather than return stdout."""

    def fake_run(cmd, timeout=None):
        with open(cmd[2], "w", encoding="utf-8") as f:
            f.write(result_text)
        result = MagicMock()
        result.returncode = returncode
        return result

    return fake_run


def test_run_frozen_folder_dialog_returns_selected_path():
    from app.routers.watcher import run_folder_dialog_isolated

    with patch.object(sys, "frozen", True, create=True):
        with patch(
            "app.routers.watcher.subprocess.run",
            side_effect=_fake_frozen_run("OK:C:/Users/dkbar/Downloads"),
        ):
            result = run_folder_dialog_isolated()
    assert result == {"path": "C:/Users/dkbar/Downloads"}


def test_run_frozen_folder_dialog_invokes_worker_flag_not_dash_c():
    """The whole point of the frozen-build fix: sys.executable is this
    app's own .exe, which ignores -c entirely, so the frozen path must
    invoke the --browse-folder-worker flag instead."""
    from app.routers.watcher import run_folder_dialog_isolated

    captured = {}

    def fake_run(cmd, timeout=None):
        captured["cmd"] = cmd
        with open(cmd[2], "w", encoding="utf-8") as f:
            f.write("OK:")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch.object(sys, "frozen", True, create=True):
        with patch("app.routers.watcher.subprocess.run", side_effect=fake_run):
            run_folder_dialog_isolated()

    assert captured["cmd"][0] == sys.executable
    assert captured["cmd"][1] == "--browse-folder-worker"
    assert "-c" not in captured["cmd"]


def test_run_frozen_folder_dialog_survives_child_process_crash():
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    with patch.object(sys, "frozen", True, create=True):
        with patch(
            "app.routers.watcher.subprocess.run",
            side_effect=_fake_frozen_run("", returncode=1),
        ):
            try:
                run_folder_dialog_isolated()
                assert False, "expected an HTTPException"
            except HTTPException as exc:
                assert exc.status_code == 503


def test_run_frozen_folder_dialog_times_out_cleanly():
    from app.routers.watcher import run_folder_dialog_isolated
    from fastapi import HTTPException

    with patch.object(sys, "frozen", True, create=True):
        with patch(
            "app.routers.watcher.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="worker", timeout=1),
        ):
            try:
                run_folder_dialog_isolated(timeout_seconds=1)
                assert False, "expected an HTTPException"
            except HTTPException as exc:
                assert exc.status_code == 504


def test_run_frozen_folder_dialog_cleans_up_result_file():
    import os

    from app.routers.watcher import run_folder_dialog_isolated

    captured = {}

    def fake_run(cmd, timeout=None):
        captured["result_path"] = cmd[2]
        with open(cmd[2], "w", encoding="utf-8") as f:
            f.write("OK:")
        result = MagicMock()
        result.returncode = 0
        return result

    with patch.object(sys, "frozen", True, create=True):
        with patch("app.routers.watcher.subprocess.run", side_effect=fake_run):
            run_folder_dialog_isolated()

    assert not os.path.exists(captured["result_path"])


def test_browse_folder_route_returns_isolated_result(client):
    with patch(
        "app.routers.watcher.run_folder_dialog_isolated",
        return_value={"path": "C:/picked/path"},
    ):
        response = client.post("/api/browse-folder")
    assert response.status_code == 200
    assert response.json() == {"path": "C:/picked/path"}
