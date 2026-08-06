from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def force_windows_platform():
    """reveal_in_explorer now 503s on any sys.platform that doesn't start
    with "win" (Docker/Linux is this project's recommended deployment target
    -- see .github/workflows and watcher.py's own TKINTER_AVAILABLE comment
    for the same Linux-container context). Every test in this file below
    except the dedicated 503 test exercises the Windows behavior and must
    keep passing regardless of which OS pytest itself happens to run on, so
    force sys.platform to "win32" for all of them by default; the 503 test
    overrides it back to "linux" for just its own call.
    """
    with patch("app.routers.file_view.sys.platform", "win32"):
        yield


def test_reveal_returns_503_on_non_windows_platform(client, tmp_path):
    target = tmp_path / "model.stl"
    target.write_text("data")

    with patch("app.routers.file_view.sys.platform", "linux"), \
            patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": str(target)})

    assert resp.status_code == 503
    assert "Windows" in resp.json()["detail"]
    mock_popen.assert_not_called()


def test_reveal_relative_path_returns_400(client):
    with patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": "relative/model.stl"})

    assert resp.status_code == 400
    mock_popen.assert_not_called()


def test_reveal_file_selects_it_in_parent_folder(client, tmp_path):
    target = tmp_path / "model.stl"
    target.write_text("data")

    with patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": str(target)})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_popen.assert_called_once_with(["explorer", "/select,", str(target)])


def test_reveal_directory_opens_it_directly(client, tmp_path):
    target = tmp_path / "SomeFolder"
    target.mkdir()

    with patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": str(target)})

    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mock_popen.assert_called_once_with(["explorer", str(target)])


def test_reveal_nonexistent_path_returns_404(client, tmp_path):
    missing = tmp_path / "does-not-exist.stl"

    with patch("app.routers.file_view.subprocess.Popen") as mock_popen:
        resp = client.post("/api/file-view/reveal", json={"path": str(missing)})

    assert resp.status_code == 404
    mock_popen.assert_not_called()
