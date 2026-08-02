from unittest.mock import patch


def test_openrouter_key_status_defaults_unconfigured(client):
    response = client.get("/api/settings/openrouter-key")
    assert response.status_code == 200
    assert response.json() == {"configured": False}


def test_openrouter_key_set_then_clear(client):
    set_resp = client.put("/api/settings/openrouter-key", json={"token": "sk-or-abc123"})
    assert set_resp.json() == {"configured": True}

    status_resp = client.get("/api/settings/openrouter-key")
    assert status_resp.json() == {"configured": True}

    clear_resp = client.put("/api/settings/openrouter-key", json={"clear": True})
    assert clear_resp.json() == {"configured": False}


def test_openrouter_key_rejects_empty(client):
    response = client.put("/api/settings/openrouter-key", json={"token": "   "})
    assert response.status_code == 400


def _upload(client, name="Widget.stl"):
    return client.post(
        "/api/models/upload",
        data={"folderId": "1", "tags": "printed"},
        files={"file": (name, b"solid Widget endsolid", "application/octet-stream")},
    ).json()


def test_suggest_tags_without_configured_key_returns_400(client):
    model = _upload(client)
    response = client.post(f"/api/models/{model['id']}/suggest-tags")
    assert response.status_code == 400
    assert "OpenRouter" in response.json()["detail"]


def test_suggest_tags_calls_openrouter_and_returns_parsed_tags(client):
    model = _upload(client)
    client.put("/api/settings/openrouter-key", json={"token": "sk-or-abc123"})

    with patch("app.services.ai_provider.call_openrouter", return_value='["miniature", "fantasy", "tabletop"]'):
        response = client.post(f"/api/models/{model['id']}/suggest-tags")

    assert response.status_code == 200
    assert response.json() == {"tags": ["miniature", "fantasy", "tabletop"]}


def test_suggest_tags_handles_prose_wrapped_json(client):
    model = _upload(client)
    client.put("/api/settings/openrouter-key", json={"token": "sk-or-abc123"})

    with patch(
        "app.services.ai_provider.call_openrouter",
        return_value='Sure, here are some tags:\n["desk", "organizer"]\nHope that helps!',
    ):
        response = client.post(f"/api/models/{model['id']}/suggest-tags")

    assert response.status_code == 200
    assert response.json() == {"tags": ["desk", "organizer"]}


def test_suggest_tags_for_missing_model_is_404(client):
    client.put("/api/settings/openrouter-key", json={"token": "sk-or-abc123"})
    response = client.post("/api/models/does-not-exist/suggest-tags")
    assert response.status_code == 404


def test_suggest_pricing_without_configured_key_returns_400(client):
    model = _upload(client)
    response = client.post(f"/api/models/{model['id']}/suggest-pricing")
    assert response.status_code == 400


def test_suggest_pricing_calls_openrouter_and_returns_parsed_result(client):
    model = _upload(client)
    client.put("/api/settings/openrouter-key", json={"token": "sk-or-abc123"})

    fake_response = (
        '{"priceRange": "$8-$14", "popularity": "Moderate", '
        '"reasoning": "Simple functional print, common category on Etsy."}'
    )
    with patch("app.services.ai_provider.call_openrouter", return_value=fake_response):
        response = client.post(f"/api/models/{model['id']}/suggest-pricing")

    assert response.status_code == 200
    body = response.json()
    assert body["priceRange"] == "$8-$14"
    assert body["popularity"] == "Moderate"
    assert "functional print" in body["reasoning"]
