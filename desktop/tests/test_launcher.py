import http.server
import os
import socket
import sys
import tempfile
import threading
from unittest.mock import patch, MagicMock


def test_find_free_port_returns_a_bindable_port():
    from launcher import find_free_port

    port = find_free_port()
    assert 1024 <= port <= 65535
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))  # raises OSError if somehow still held


def test_wait_for_health_returns_true_once_server_responds():
    from launcher import find_free_port, wait_for_health

    port = find_free_port()

    class OKHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass  # keep test output quiet

    server = http.server.HTTPServer(("127.0.0.1", port), OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert wait_for_health(f"http://127.0.0.1:{port}/", timeout_seconds=5) is True
    finally:
        server.shutdown()


def test_wait_for_health_returns_false_when_nothing_is_listening():
    from launcher import find_free_port, wait_for_health

    port = find_free_port()  # guaranteed free — nothing listens on it
    assert wait_for_health(f"http://127.0.0.1:{port}/", timeout_seconds=1) is False


def test_run_browse_folder_worker_writes_selected_path():
    from launcher import run_browse_folder_worker

    fd, result_path = tempfile.mkstemp()
    os.close(fd)
    try:
        with patch("tkinter.Tk", return_value=MagicMock()), \
             patch("tkinter.filedialog.askdirectory", return_value="C:/Users/dkbar/Downloads"):
            run_browse_folder_worker(result_path)
        with open(result_path, encoding="utf-8") as f:
            assert f.read() == "OK:C:/Users/dkbar/Downloads"
    finally:
        os.remove(result_path)


def test_run_browse_folder_worker_writes_ok_empty_on_cancel():
    from launcher import run_browse_folder_worker

    fd, result_path = tempfile.mkstemp()
    os.close(fd)
    try:
        # tkinter's askdirectory() returns "" on Cancel
        with patch("tkinter.Tk", return_value=MagicMock()), \
             patch("tkinter.filedialog.askdirectory", return_value=""):
            run_browse_folder_worker(result_path)
        with open(result_path, encoding="utf-8") as f:
            assert f.read() == "OK:"
    finally:
        os.remove(result_path)


def test_run_browse_folder_worker_writes_error_on_exception():
    """Whatever goes wrong inside this throwaway worker process (a crash, a
    native fault) must land in the result file as a reportable error, not
    propagate and crash the worker process before it can write anything —
    the parent (backend/app/routers/watcher.py) only knows the dialog
    failed if the file says so."""
    from launcher import run_browse_folder_worker

    fd, result_path = tempfile.mkstemp()
    os.close(fd)
    try:
        with patch("tkinter.Tk", side_effect=RuntimeError("no display")):
            run_browse_folder_worker(result_path)
        with open(result_path, encoding="utf-8") as f:
            content = f.read()
        assert content.startswith("ERROR:")
        assert "no display" in content
    finally:
        os.remove(result_path)


def test_main_dispatches_to_worker_before_importing_webview():
    """The whole point of the --browse-folder-worker flag: the re-invoked
    .exe must branch to the worker before main() imports webview or starts
    the server, so it never becomes a second copy of the running app.
    Blocking the webview import (rather than just asserting the worker was
    called) makes this test fail loudly if that ordering ever regresses."""
    import launcher

    called = {}
    with patch.object(launcher, "run_browse_folder_worker", lambda p: called.setdefault("path", p)), \
         patch.object(sys, "argv", ["3D Vaultkeeper.exe", "--browse-folder-worker", "C:/temp/result.txt"]), \
         patch.dict(sys.modules, {"webview": None}):
        launcher.main()  # would raise ImportError here if it reached `import webview`

    assert called["path"] == "C:/temp/result.txt"
