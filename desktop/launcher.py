"""Desktop entry point for the packaged (PyInstaller) build: starts the
existing FastAPI app on a background thread and shows it in a native
window via pywebview, instead of requiring a browser tab.
"""
import socket
import sys
import threading
import time
import urllib.error
import urllib.request


def find_free_port() -> int:
    """Binds to port 0 so the OS assigns a free ephemeral port, then
    releases it immediately — avoids colliding with anything else already
    running on the user's machine, which is unknown for a packaged app."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_health(url: str, timeout_seconds: float = 15.0) -> bool:
    """Polls `url` until it responds or `timeout_seconds` elapses. Holds
    the pywebview window open until uvicorn (started on a separate
    thread) is actually ready to serve requests."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.2)
    return False


def run_browse_folder_worker(result_path: str) -> None:
    """Entry point for this same .exe re-invoked with
    `--browse-folder-worker <result-file>` (see
    backend/app/routers/watcher.py's _run_frozen_folder_dialog). Opens a
    native folder picker and writes the result to `result_path` instead of
    stdout — see that function's docstring for why. Deliberately imports
    nothing from uvicorn/webview/app.main: this must stay a fast, isolated
    worker, never a second copy of the running app.
    """
    import tkinter
    from tkinter import filedialog

    try:
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory()
        root.destroy()
        result = "OK:" + (selected or "")
    except Exception as e:
        result = "ERROR:" + str(e)

    with open(result_path, "w", encoding="utf-8") as f:
        f.write(result)


def _run_server(port: int) -> None:
    # Imported here, not at module level: this keeps find_free_port/
    # wait_for_health importable and testable (desktop/tests/test_launcher.py)
    # without needing the whole backend app package (and its dependencies)
    # importable in that test environment.
    import uvicorn

    backend_dir = str((__import__("pathlib").Path(__file__).resolve().parent.parent / "backend"))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main() -> None:
    # Checked before anything else, including the webview import below:
    # this branch must stay a lightweight, fast-exiting worker, not a
    # second copy of the full app. See run_browse_folder_worker's docstring.
    if len(sys.argv) >= 3 and sys.argv[1] == "--browse-folder-worker":
        run_browse_folder_worker(sys.argv[2])
        return

    import webview

    port = find_free_port()
    server_thread = threading.Thread(target=_run_server, args=(port,), daemon=True)
    server_thread.start()

    url = f"http://127.0.0.1:{port}/"
    if not wait_for_health(url):
        raise RuntimeError(f"Backend did not become ready at {url} within the timeout")

    webview.create_window("3D Vaultkeeper", url, width=1400, height=900)
    webview.start()


if __name__ == "__main__":
    main()
