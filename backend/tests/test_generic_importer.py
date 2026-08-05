import pytest
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
    fake_response.headers = {"Content-Type": "application/octet-stream"}
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response) as mock_get:
        file, thumbnail = GenericImporter().importfromId(
            "https://example.com/files/robot-body.stl", None, ""
        )

    mock_get.assert_called_once()
    assert mock_get.call_args[0][0] == "https://example.com/files/robot-body.stl"
    assert file.content == b"solid fake endsolid"
    assert thumbnail == ""


def test_generic_importer_download_rejects_html_landing_page():
    """A link whose href ends in .stl but which actually serves an HTML
    landing page must fail loudly rather than saving the page as a model."""
    fake_response = MagicMock()
    fake_response.content = b"<!doctype html><html><body>Not a model</body></html>"
    fake_response.headers = {"Content-Type": "text/html; charset=utf-8"}
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        with pytest.raises(ValueError) as excinfo:
            GenericImporter().importfromId(
                "https://example.com/blob/main/robot-body.stl", None, ""
            )

    assert "text/html" in str(excinfo.value)


def test_generic_importer_download_rejects_plain_text_response():
    fake_response = MagicMock()
    fake_response.content = b"404 not found"
    fake_response.headers = {"Content-Type": "text/plain"}
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        with pytest.raises(ValueError):
            GenericImporter().importfromId("https://example.com/files/a.stl", None, "")


def test_generic_importer_download_rejects_image_response():
    """The gap this fix closes: a link that merely has "download" in its path
    (e.g. an asset nested under a /download/ directory, discovered as a file
    candidate by the broadened /download matching) but actually serves an
    image must not be saved as a fake .stl."""
    fake_response = MagicMock()
    fake_response.content = b"\xff\xd8\xff\xe0JFIF fake jpeg bytes"
    fake_response.headers = {"Content-Type": "image/jpeg"}
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        with pytest.raises(ValueError) as excinfo:
            GenericImporter().importfromId(
                "https://example.com/gallery/download/thumb-preview.jpg", None, ""
            )

    assert "image/jpeg" in str(excinfo.value)


def test_generic_importer_download_rejects_pdf_response():
    fake_response = MagicMock()
    fake_response.content = b"%PDF-1.4 fake pdf bytes"
    fake_response.headers = {"Content-Type": "application/pdf"}
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        with pytest.raises(ValueError):
            GenericImporter().importfromId(
                "https://example.com/download/instructions.pdf", None, ""
            )


def test_generic_importer_download_accepts_octet_stream_response():
    """A generic/unrecognized content type -- how many real servers serve real
    binary downloads, including legitimate STL/3MF files -- must still be
    accepted. This guards against the broadened denylist becoming a de facto
    allowlist that rejects real files."""
    fake_response = MagicMock()
    fake_response.content = b"solid fake endsolid"
    fake_response.headers = {"Content-Type": "application/octet-stream"}
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        file, thumbnail = GenericImporter().importfromId(
            "https://example.com/download/robot-body.stl", None, ""
        )

    assert file.content == b"solid fake endsolid"
    assert thumbnail == ""


THINGIVERSE_SHAPED_HTML = """
<html>
<head>
  <meta property="og:title" content="Articulated Dragon" />
  <meta property="og:description" content="Print-in-place dragon." />
</head>
<body>
  <a href="/download:12345">Download dragon-body</a>
  <a href="/download:12346">Download dragon-tail</a>
  <a href="/thing:763622/comments">Comments</a>
  <a href="/images/preview.png">Preview image</a>
</body>
</html>
"""


def test_generic_importer_discovers_extensionless_download_links():
    """Thingiverse-shaped pages link downloads as `/download:NNNN` with no
    file extension at all; those must still be offered as file options."""
    fake_response = MagicMock()
    fake_response.text = THINGIVERSE_SHAPED_HTML
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        result = GenericImporter().getModelOptions("https://www.thingiverse.com/thing:763622")

    assert result["title"] == "Articulated Dragon"
    ids = {f["id"] for f in result["files"]}
    assert ids == {
        "https://www.thingiverse.com/download:12345",
        "https://www.thingiverse.com/download:12346",
    }
    for f in result["files"]:
        assert f["typeName"] == "stl"
        assert f["name"]


def test_generic_importer_ignores_download_only_in_query_string():
    """"download" appearing only in a query param must not make a link a
    file candidate -- matching is done on the path, not the raw href."""
    html = """
    <html><body>
      <a href="/articles/how-to?ref=download">Some article</a>
      <a href="/downloads/robot.stl">Real file</a>
    </body></html>
    """
    fake_response = MagicMock()
    fake_response.text = html
    fake_response.raise_for_status = lambda: None

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        result = GenericImporter().getModelOptions("https://example.com/thing/9")

    assert [f["name"] for f in result["files"]] == ["robot.stl"]


def test_generic_importer_raises_clear_message_on_bot_block():
    """A 403 (or 503) on a plain page fetch is treated as the site's own
    bot-protection (e.g. a Cloudflare challenge page) rather than a normal
    HTTP error -- the resulting message should be specific and actionable,
    not requests' generic "403 Client Error: Forbidden for url: ..." text."""
    fake_response = MagicMock()
    fake_response.status_code = 403

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        with pytest.raises(ValueError, match="appears to block automated downloads"):
            GenericImporter().getModelOptions("https://cults3d.com/en/3d-model/example")


def test_generic_importer_download_raises_clear_message_on_bot_block():
    fake_response = MagicMock()
    fake_response.status_code = 503

    with patch("app.importers.generic.requests.Session.get", return_value=fake_response):
        with pytest.raises(ValueError, match="appears to block automated downloads"):
            GenericImporter().importfromId(
                "https://cults3d.com/files/robot.stl", None, ""
            )
