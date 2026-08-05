import os
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

FILE_EXTENSIONS = (".stl", ".3mf", ".step", ".stp", ".zip")

# Content-type families that are never legitimate 3D-file downloads. This is a
# denylist, not an allowlist: real servers commonly serve legitimate binary
# downloads (including real STL/3MF files) as generic "application/octet-stream"
# or with a missing/misconfigured Content-Type, so anything not matched here is
# still accepted rather than rejected.
REJECTED_CONTENT_TYPE_PREFIXES = ("text/", "image/", "video/", "audio/")
REJECTED_CONTENT_TYPES = ("application/pdf", "application/json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

# A 403/503 on a plain, unauthenticated page fetch is essentially always the
# site's own bot-protection (Cloudflare and similar WAFs commonly respond
# this way to a non-browser request) rather than something retrying or
# reformatting the request can fix -- surfaced as a clear, specific error
# instead of letting requests' generic "403 Client Error: Forbidden for
# url: ..." message reach the user.
BLOCKED_STATUS_CODES = (403, 503)


def _raise_for_status_with_clear_message(response):
    if response.status_code in BLOCKED_STATUS_CODES:
        raise ValueError(
            "This site appears to block automated downloads (it responded with "
            f"HTTP {response.status_code}) -- it can't be imported this way. Try "
            "downloading the files manually from the site instead."
        )
    response.raise_for_status()


class GenericImporter:
    """Fallback importer for any site without a dedicated API-backed
    importer (Printables/MakerWorld) -- including Thingiverse, per the
    2026-08-05 design decision to avoid Thingiverse's official API and
    its 90-day-token requirement. Works by parsing the page's own HTML:
    Open Graph tags for title/description, and any link whose target
    ends in a known 3D-file extension as a downloadable file.
    """

    def getModelOptions(self, url):
        session = requests.Session()
        try:
            response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
            _raise_for_status_with_clear_message(response)
            soup = BeautifulSoup(response.text, "html.parser")

            title = self._meta_property(soup, "og:title")
            if not title and soup.title and soup.title.string:
                title = soup.title.string.strip()
            description = self._meta_property(soup, "og:description") or self._meta_name(
                soup, "description"
            )

            files = []
            seen = set()
            for link in soup.find_all("a", href=True):
                href = link["href"]
                href_path = href.lower().split("?")[0]
                has_extension = href_path.endswith(FILE_EXTENSIONS)
                # Many sites (Thingiverse among them) expose downloads via
                # extensionless paths like `/download:12345`, so treat any
                # link whose path mentions "download" as a candidate too.
                looks_like_download = "/download" in href_path
                if not has_extension and not looks_like_download:
                    continue
                file_url = urljoin(url, href)
                if file_url in seen:
                    continue
                seen.add(file_url)
                filename = os.path.basename(urlparse(file_url).path) or "download"
                # Without an extension in the URL there is nothing to derive a
                # real type from at discovery time; default to "stl" (the batch
                # endpoint needs *some* extension to save the file under).
                type_name = filename.rsplit(".", 1)[-1] if has_extension else "stl"
                files.append(
                    {
                        "source": "generic",
                        "parentId": url,
                        "id": file_url,
                        "name": filename,
                        "folder": None,
                        "previewPath": "",
                        "typeName": type_name,
                    }
                )

            return {"title": title or "", "description": description or "", "files": files}
        finally:
            session.close()

    def importfromId(self, fileUrl, parentId, previewPath):
        """`fileUrl` is the file's own absolute download URL, exactly as
        placed into each option's `id` field by getModelOptions above --
        unlike Printables/MakerWorld there is no separate "id vs. real
        download link" resolution step for a generic site.
        """
        session = requests.Session()
        try:
            file = session.get(fileUrl, headers={"User-Agent": USER_AGENT}, allow_redirects=True, timeout=120)
            _raise_for_status_with_clear_message(file)
            # A link that merely *looks* like a file (or a "/download" path that
            # redirects to a landing page, or -- since "/download" matching was
            # broadened to extensionless paths -- an unrelated asset that simply
            # has "download" somewhere in its path, e.g. a preview image under a
            # "/download/" directory) otherwise gets saved verbatim as a corrupt
            # .stl. Raising here lets the batch endpoint report it.
            content_type = file.headers.get("Content-Type", "").lower()
            if content_type.startswith(REJECTED_CONTENT_TYPE_PREFIXES) or content_type.startswith(
                REJECTED_CONTENT_TYPES
            ):
                raise ValueError(
                    f"Expected a 3D file but got {content_type or 'unknown content type'} "
                    "-- likely not a direct download link"
                )
            return file, ""
        finally:
            session.close()

    def _meta_property(self, soup, property_name):
        tag = soup.find("meta", property=property_name)
        return tag["content"].strip() if tag and tag.get("content") else None

    def _meta_name(self, soup, name):
        tag = soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content") else None
