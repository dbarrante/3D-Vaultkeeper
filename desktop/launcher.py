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
