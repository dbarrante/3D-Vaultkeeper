from unittest.mock import patch


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
