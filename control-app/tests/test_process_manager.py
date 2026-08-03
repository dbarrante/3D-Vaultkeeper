import socket
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import process_manager as pm


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Every test gets its own throwaway backend/frontend/dev-data/run tree so
    nothing here can ever touch the user's real repo or real running servers."""
    backend_dir = tmp_path / "backend"
    frontend_dir = tmp_path / "frontend"
    dev_data_dir = tmp_path / "dev-data"
    run_dir = tmp_path / "run"
    (backend_dir / ".venv" / "Scripts").mkdir(parents=True)
    (backend_dir / ".venv" / "Scripts" / "python.exe").write_text("")
    frontend_dir.mkdir()

    monkeypatch.setattr(pm, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(pm, "FRONTEND_DIR", frontend_dir)
    monkeypatch.setattr(pm, "DEV_DATA_DIR", dev_data_dir)
    monkeypatch.setattr(pm, "RUN_DIR", run_dir)
    monkeypatch.setattr(pm, "BACKEND_PIDFILE", run_dir / "backend.pid")
    monkeypatch.setattr(pm, "FRONTEND_PIDFILE", run_dir / "frontend.pid")
    monkeypatch.setattr(pm, "BACKEND_LOG", run_dir / "backend.log")
    monkeypatch.setattr(pm, "FRONTEND_LOG", run_dir / "frontend.log")
    yield


def _tasklist_result(pid_present: bool) -> MagicMock:
    result = MagicMock()
    result.stdout = f'"python.exe","{99999}","Console","1","10,000 K"' if pid_present else "INFO: No tasks are running which match the specified criteria."
    return result


def _netstat_result(*lines: str) -> MagicMock:
    result = MagicMock()
    result.stdout = "\n".join(lines)
    return result


class TestSetupStatus:
    def test_reports_missing_venv(self):
        (pm.BACKEND_DIR / ".venv" / "Scripts" / "python.exe").unlink()
        assert pm.setup_status()["venv"] is False

    def test_reports_present_venv(self):
        assert pm.setup_status()["venv"] is True

    def test_reports_missing_dev_data(self):
        assert pm.setup_status()["dev_data"] is False

    def test_reports_missing_node_modules(self):
        assert pm.setup_status()["node_modules"] is False

    def test_reports_present_node_modules(self):
        (pm.FRONTEND_DIR / "node_modules").mkdir()
        assert pm.setup_status()["node_modules"] is True


class TestEnsureDevDataDirs:
    def test_creates_expected_subdirectories(self):
        pm.ensure_dev_data_dirs()
        assert (pm.DEV_DATA_DIR / "uploads" / "manuals").is_dir()
        assert (pm.DEV_DATA_DIR / "data").is_dir()

    def test_is_idempotent_when_dirs_already_exist(self):
        pm.ensure_dev_data_dirs()
        pm.ensure_dev_data_dirs()  # must not raise
        assert (pm.DEV_DATA_DIR / "data").is_dir()


class TestPidFile:
    def test_write_then_read_round_trips(self):
        pm._write_pid(pm.BACKEND_PIDFILE, 4242)
        assert pm._read_pid(pm.BACKEND_PIDFILE) == 4242

    def test_read_returns_none_when_missing(self):
        assert pm._read_pid(pm.BACKEND_PIDFILE) is None

    def test_read_returns_none_on_garbage_contents(self):
        pm.BACKEND_PIDFILE.parent.mkdir(parents=True, exist_ok=True)
        pm.BACKEND_PIDFILE.write_text("not-a-pid")
        assert pm._read_pid(pm.BACKEND_PIDFILE) is None


class TestIsPidRunning:
    def test_true_when_tasklist_finds_pid(self):
        with patch("process_manager.subprocess.run", return_value=_tasklist_result(True)):
            assert pm.is_pid_running(99999) is True

    def test_false_when_tasklist_reports_no_match(self):
        with patch("process_manager.subprocess.run", return_value=_tasklist_result(False)):
            assert pm.is_pid_running(99999) is False

    def test_false_for_none_pid(self):
        assert pm.is_pid_running(None) is False

    def test_suppresses_console_window(self):
        """Live bug: is_pid_running runs on a 2s poll timer, and without this
        flag each tasklist.exe call flashed a visible console window — the
        'dos box keeps popping up and disappearing' the user saw."""
        with patch("process_manager.subprocess.run", return_value=_tasklist_result(True)) as mock_run:
            pm.is_pid_running(99999)
        assert mock_run.call_args.kwargs.get("creationflags") == pm._NO_WINDOW


class TestPortInUse:
    def test_true_when_connection_succeeds(self):
        with patch("process_manager.socket.create_connection", return_value=MagicMock()):
            assert pm._port_in_use(8998) is True

    def test_false_when_connection_refused(self):
        with patch("process_manager.socket.create_connection", side_effect=OSError()):
            assert pm._port_in_use(8998) is False


class TestFindPidOnPort:
    def test_finds_pid_of_listening_socket(self):
        with patch("process_manager.subprocess.run", return_value=_netstat_result(
            "  TCP    0.0.0.0:8998           0.0.0.0:0              LISTENING       48416",
        )):
            assert pm._find_pid_on_port(8998) == 48416

    def test_ignores_non_listening_states_on_same_port(self):
        with patch("process_manager.subprocess.run", return_value=_netstat_result(
            "  TCP    127.0.0.1:8998         127.0.0.1:55244        CLOSE_WAIT      48416",
            "  TCP    127.0.0.1:55244        127.0.0.1:8998         FIN_WAIT_2      39040",
        )):
            assert pm._find_pid_on_port(8998) is None

    def test_ignores_other_ports(self):
        with patch("process_manager.subprocess.run", return_value=_netstat_result(
            "  TCP    0.0.0.0:5173           0.0.0.0:0              LISTENING       48284",
        )):
            assert pm._find_pid_on_port(8998) is None

    def test_returns_none_on_no_matches(self):
        with patch("process_manager.subprocess.run", return_value=_netstat_result("")):
            assert pm._find_pid_on_port(8998) is None

    def test_suppresses_console_window(self):
        with patch("process_manager.subprocess.run", return_value=_netstat_result("")) as mock_run:
            pm._find_pid_on_port(8998)
        assert mock_run.call_args.kwargs.get("creationflags") == pm._NO_WINDOW


class TestStartBackend:
    def test_launches_venv_python_with_uvicorn_and_records_pid(self):
        fake_proc = MagicMock(pid=1234)
        with patch("process_manager._port_in_use", return_value=False), \
             patch("process_manager.subprocess.Popen", return_value=fake_proc) as mock_popen:
            pid = pm.start_backend()

        assert pid == 1234
        assert pm._read_pid(pm.BACKEND_PIDFILE) == 1234
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert cmd[0] == str(pm.venv_python())
        assert "uvicorn" in cmd
        assert "app.main:app" in cmd
        assert "127.0.0.1" in cmd  # never 0.0.0.0 — that's the exact URL confusion hit earlier
        assert kwargs["cwd"] == str(pm.BACKEND_DIR)
        assert kwargs["env"]["DB_PATH"] == str(pm.DEV_DATA_DIR / "data" / "data.db")
        assert kwargs["env"]["FILE_STORAGE"] == str(pm.DEV_DATA_DIR / "uploads")

    def test_creates_dev_data_dirs_before_launch(self):
        fake_proc = MagicMock(pid=1234)
        with patch("process_manager._port_in_use", return_value=False), \
             patch("process_manager.subprocess.Popen", return_value=fake_proc):
            pm.start_backend()
        assert (pm.DEV_DATA_DIR / "uploads").is_dir()

    def test_raises_clear_error_when_venv_missing(self):
        (pm.BACKEND_DIR / ".venv" / "Scripts" / "python.exe").unlink()
        with pytest.raises(pm.SetupIncomplete, match="venv"):
            pm.start_backend()

    def test_raises_port_in_use_when_backend_port_already_bound(self):
        """The exact bug hit live: the user's backend was already running on
        :8998 in a separate terminal, and clicking Start in the GUI spawned a
        second uvicorn process that never bound (silently stuck at startup)
        while a stale pidfile pointed at it. Must refuse instead of doubling up."""
        with patch("process_manager._port_in_use", return_value=True), \
             patch("process_manager.subprocess.Popen") as mock_popen:
            with pytest.raises(pm.PortInUse, match=str(pm.BACKEND_PORT)):
                pm.start_backend()
        mock_popen.assert_not_called()


class TestStartFrontend:
    def test_launches_bun_dev_and_records_pid(self):
        (pm.FRONTEND_DIR / "node_modules").mkdir()
        fake_proc = MagicMock(pid=5678)
        with patch("process_manager.shutil.which", return_value="C:/bun/bun.exe"), \
             patch("process_manager._port_in_use", return_value=False), \
             patch("process_manager.subprocess.Popen", return_value=fake_proc) as mock_popen:
            pid = pm.start_frontend()

        assert pid == 5678
        assert pm._read_pid(pm.FRONTEND_PIDFILE) == 5678
        args, kwargs = mock_popen.call_args
        cmd = args[0]
        assert cmd == ["C:/bun/bun.exe", "run", "dev"]
        assert kwargs["cwd"] == str(pm.FRONTEND_DIR)

    def test_raises_clear_error_when_node_modules_missing(self):
        with pytest.raises(pm.SetupIncomplete, match="node_modules"):
            pm.start_frontend()

    def test_raises_clear_error_when_bun_not_on_path(self):
        (pm.FRONTEND_DIR / "node_modules").mkdir()
        with patch("process_manager.shutil.which", return_value=None):
            with pytest.raises(pm.SetupIncomplete, match="bun"):
                pm.start_frontend()

    def test_raises_port_in_use_when_frontend_port_already_bound(self):
        """Live bug: vite silently falls back to :5174 when :5173 is taken,
        so this never surfaced as an error — it just quietly ran a second,
        untracked frontend nobody asked for. Refuse instead of guessing."""
        (pm.FRONTEND_DIR / "node_modules").mkdir()
        with patch("process_manager.shutil.which", return_value="C:/bun/bun.exe"), \
             patch("process_manager._port_in_use", return_value=True), \
             patch("process_manager.subprocess.Popen") as mock_popen:
            with pytest.raises(pm.PortInUse, match=str(pm.FRONTEND_PORT)):
                pm.start_frontend()
        mock_popen.assert_not_called()


class TestStopProcess:
    def test_kills_process_tree_and_clears_pidfile(self):
        pm._write_pid(pm.BACKEND_PIDFILE, 4242)
        with patch("process_manager.is_pid_running", return_value=True), \
             patch("process_manager.subprocess.run") as mock_run:
            stopped = pm.stop_process(pm.BACKEND_PIDFILE, pm.BACKEND_PORT, name="backend")

        assert stopped is True
        assert not pm.BACKEND_PIDFILE.exists()
        cmd = mock_run.call_args[0][0]
        assert cmd[:2] == ["taskkill", "/PID"]
        assert "4242" in cmd
        assert "/T" in cmd and "/F" in cmd
        assert mock_run.call_args.kwargs.get("creationflags") == pm._NO_WINDOW

    def test_falls_back_to_whatever_is_listening_on_the_port_when_untracked(self):
        """Live gap: a backend started by hand in a separate terminal has no
        pidfile, so Stop had nothing to kill even though something real was
        listening on :8998. Stop should reset the port, not just our own PID."""
        with patch("process_manager.is_pid_running", return_value=False), \
             patch("process_manager._find_pid_on_port", return_value=48416), \
             patch("process_manager.subprocess.run") as mock_run:
            stopped = pm.stop_process(pm.BACKEND_PIDFILE, pm.BACKEND_PORT, name="backend")

        assert stopped is True
        cmd = mock_run.call_args[0][0]
        assert "48416" in cmd

    def test_returns_false_when_nothing_tracked_and_nothing_on_port(self):
        pm._write_pid(pm.BACKEND_PIDFILE, 4242)
        with patch("process_manager.is_pid_running", return_value=False), \
             patch("process_manager._find_pid_on_port", return_value=None), \
             patch("process_manager.subprocess.run") as mock_run:
            stopped = pm.stop_process(pm.BACKEND_PIDFILE, pm.BACKEND_PORT, name="backend")

        assert stopped is False
        mock_run.assert_not_called()
        assert not pm.BACKEND_PIDFILE.exists()

    def test_returns_false_when_no_pidfile_and_nothing_on_port(self):
        with patch("process_manager._find_pid_on_port", return_value=None), \
             patch("process_manager.subprocess.run") as mock_run:
            stopped = pm.stop_process(pm.BACKEND_PIDFILE, pm.BACKEND_PORT, name="backend")
        assert stopped is False
        mock_run.assert_not_called()


class TestHealthChecks:
    def test_backend_health_true_when_reachable(self):
        with patch("process_manager.urllib.request.urlopen", return_value=MagicMock()):
            assert pm.backend_health() is True

    def test_backend_health_false_when_unreachable(self):
        with patch("process_manager.urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert pm.backend_health() is False

    def test_frontend_health_false_on_os_error(self):
        with patch("process_manager.urllib.request.urlopen", side_effect=OSError()):
            assert pm.frontend_health() is False


class TestStatus:
    def test_reports_stopped_when_no_pidfiles_and_nothing_on_port(self):
        with patch("process_manager._find_pid_on_port", return_value=None), \
             patch("process_manager.backend_health", return_value=False), \
             patch("process_manager.frontend_health", return_value=False):
            result = pm.status()
        assert result["backend"] == {"pid": None, "process_running": False, "healthy": False, "tracked": False}
        assert result["frontend"]["pid"] is None

    def test_reports_running_and_healthy_when_tracked(self):
        pm._write_pid(pm.BACKEND_PIDFILE, 111)
        pm._write_pid(pm.FRONTEND_PIDFILE, 222)
        with patch("process_manager.is_pid_running", return_value=True), \
             patch("process_manager.backend_health", return_value=True), \
             patch("process_manager.frontend_health", return_value=True):
            result = pm.status()
        assert result["backend"] == {"pid": 111, "process_running": True, "healthy": True, "tracked": True}
        assert result["frontend"] == {"pid": 222, "process_running": True, "healthy": True, "tracked": True}

    def test_reports_untracked_process_found_on_port(self):
        """Live case: user's backend was started by hand in a terminal, so
        we have no pidfile for it — but it's really running, and the GUI
        should say so instead of claiming 'stopped'."""
        with patch("process_manager.is_pid_running", return_value=False), \
             patch("process_manager._find_pid_on_port", return_value=48416), \
             patch("process_manager.backend_health", return_value=True), \
             patch("process_manager.frontend_health", return_value=False):
            result = pm.status()
        assert result["backend"] == {"pid": 48416, "process_running": True, "healthy": True, "tracked": False}
