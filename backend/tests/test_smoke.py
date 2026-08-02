def test_folders_endpoint_is_reachable(client):
    response = client.get("/api/folders")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
