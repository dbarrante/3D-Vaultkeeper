import os
from typing import Optional


def find_sidecar_notes(source_path: str) -> Optional[str]:
    """Look for a same-basename .txt or .pdf next to source_path and return its
    text content, or None if neither exists (or both are empty). .txt takes
    priority over .pdf when both are present — a plain-text note is exact,
    while PDF extraction can introduce layout artifacts, so prefer the
    cleaner source when the uploader provided both.
    """
    base, _ext = os.path.splitext(source_path)

    txt_path = base + ".txt"
    if os.path.exists(txt_path):
        with open(txt_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
        if text:
            return text

    pdf_path = base + ".pdf"
    if os.path.exists(pdf_path):
        try:
            from pypdf import PdfReader

            reader = PdfReader(pdf_path)
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except Exception:
            return None
        if text:
            return text

    return None
