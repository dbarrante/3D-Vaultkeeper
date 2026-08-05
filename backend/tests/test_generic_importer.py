from unittest.mock import MagicMock, patch

from app.importers.generic import GenericImporter

SAMPLE_HTML = """
<html>
<head>
  <meta property="og:title" content="Cool Robot Miniature" />
  <meta property="og:description" content="A fully articulated robot mini, 5 parts." />
</head>
<body>
  <a href="/files/robot-body.stl">Download body</a>
  <a href="/files/robot-arm.stl">Download arm</a>
  <a href="/files/robot-arm.stl">Download arm (again)</a>
  <a href="/images/preview.png">Preview image</a>
</body>
</html>
"""


def test_generic_importer_parses_og_tags_and_file_links():
    fake_response = MagicMock()
    fake_response.text = SAMPLE_HTML
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        result = GenericImporter().getModelOptions("https://example.com/thing/42")

    assert result["title"] == "Cool Robot Miniature"
    assert result["description"] == "A fully articulated robot mini, 5 parts."
    names = {f["name"] for f in result["files"]}
    assert names == {"robot-body.stl", "robot-arm.stl"}
    assert all(f["source"] == "generic" for f in result["files"])


def test_generic_importer_falls_back_to_title_tag_when_no_og_tags():
    html = "<html><head><title>Fallback Title</title></head><body></body></html>"
    fake_response = MagicMock()
    fake_response.text = html
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        result = GenericImporter().getModelOptions("https://example.com/thing/1")

    assert result["title"] == "Fallback Title"
    assert result["description"] == ""
    assert result["files"] == []


def test_generic_importer_download_fetches_file_url_directly():
    fake_response = MagicMock()
    fake_response.content = b"solid fake endsolid"
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response) as mock_get:
        file, thumbnail = GenericImporter().importfromId(
            "https://example.com/files/robot-body.stl", None, ""
        )

    mock_get.assert_called_once()
    assert mock_get.call_args[0][0] == "https://example.com/files/robot-body.stl"
    assert file.content == b"solid fake endsolid"
    assert thumbnail == ""
