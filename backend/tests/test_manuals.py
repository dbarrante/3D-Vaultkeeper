def _upload(client):
    return client.post(
        "/api/models/upload",
        data={"folderId": "1"},
        files={"file": ("a.stl", b"solid test endsolid", "application/octet-stream")},
    ).json()


def test_manual_round_trip(client):
    model = _upload(client)
    response = client.put(
        f"/api/models/{model['id']}/manual",
        files={"file": ("guide.md", b"# Print settings\n0.2mm layer", "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["manual"] == "guide.md"

    fetched = client.get(f"/api/models/{model['id']}/manual")
    assert fetched.status_code == 200
    assert b"Print settings" in fetched.content

    deleted = client.delete(f"/api/models/{model['id']}/manual")
    assert deleted.status_code == 200
    assert deleted.json()["manual"] is None

    missing = client.get(f"/api/models/{model['id']}/manual")
    assert missing.status_code == 404
