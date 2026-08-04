import os


def test_build_tree_walks_nested_directories_and_flags_model_files(tmp_path):
    from app.services.import_wizard import build_tree

    root = tmp_path / "raw"
    root.mkdir()
    (root / "loose.stl").write_bytes(b"solid loose endsolid")
    (root / "notes.txt").write_bytes(b"just notes")
    subdir = root / "Tank Kit"
    subdir.mkdir()
    (subdir / "hull.stl").write_bytes(b"solid hull endsolid")
    (subdir / "photo.jpg").write_bytes(b"fake jpg bytes")

    tree = build_tree(root)

    assert tree["name"] == "raw"
    assert tree["path"] == str(root)
    assert len(tree["files"]) == 2
    loose = next(f for f in tree["files"] if f["name"] == "loose.stl")
    assert loose["isModel"] is True
    notes = next(f for f in tree["files"] if f["name"] == "notes.txt")
    assert notes["isModel"] is False

    assert len(tree["folders"]) == 1
    kit = tree["folders"][0]
    assert kit["name"] == "Tank Kit"
    hull = next(f for f in kit["files"] if f["name"] == "hull.stl")
    assert hull["isModel"] is True
    photo = next(f for f in kit["files"] if f["name"] == "photo.jpg")
    assert photo["isModel"] is False


def test_build_tree_handles_empty_directory(tmp_path):
    from app.services.import_wizard import build_tree

    root = tmp_path / "empty"
    root.mkdir()

    tree = build_tree(root)

    assert tree["folders"] == []
    assert tree["files"] == []


def test_import_tree_endpoint_returns_walked_structure(client, tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    (root / "part.stl").write_bytes(b"solid part endsolid")

    resp = client.get("/api/import/tree", params={"path": str(root)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["files"][0]["name"] == "part.stl"
    assert body["files"][0]["isModel"] is True


def test_import_tree_endpoint_rejects_nonexistent_path(client, tmp_path):
    resp = client.get("/api/import/tree", params={"path": str(tmp_path / "does_not_exist")})

    assert resp.status_code == 400
