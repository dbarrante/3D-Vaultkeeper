import os
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

FILE_EXTENSIONS = (".stl", ".3mf", ".step", ".stp", ".zip")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)


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
            response.raise_for_status()
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
                if not href.lower().split("?")[0].endswith(FILE_EXTENSIONS):
                    continue
                file_url = urljoin(url, href)
                if file_url in seen:
                    continue
                seen.add(file_url)
                filename = os.path.basename(urlparse(file_url).path)
                files.append(
                    {
                        "source": "generic",
                        "parentId": url,
                        "id": file_url,
                        "name": filename,
                        "folder": None,
                        "previewPath": "",
                        "typeName": filename.rsplit(".", 1)[-1],
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
            file.raise_for_status()
            return file, ""
        finally:
            session.close()

    def _meta_property(self, soup, property_name):
        tag = soup.find("meta", property=property_name)
        return tag["content"].strip() if tag and tag.get("content") else None

    def _meta_name(self, soup, name):
        tag = soup.find("meta", attrs={"name": name})
        return tag["content"].strip() if tag and tag.get("content") else None
