import io


def _make_minimal_pdf(text: str) -> bytes:
    """Hand-built minimal single-page PDF with one text-drawing content stream.
    No reportlab dependency needed just to generate a test fixture — confirmed
    readable by pypdf.PdfReader before writing this test."""
    content = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/MediaBox[0 0 200 200]/Contents 5 0 R>>",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"\nendstream",
    ]
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(buf.tell())
        buf.write(f"{i} 0 obj".encode() + b"\n" + obj + b"\nendobj\n")
    xref_offset = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode())
    buf.write(b"trailer\n")
    buf.write(f"<</Size {len(objects) + 1}/Root 1 0 R>>\n".encode())
    buf.write(b"startxref\n")
    buf.write(f"{xref_offset}\n".encode())
    buf.write(b"%%EOF")
    return buf.getvalue()


def test_find_sidecar_notes_reads_sibling_txt_file(tmp_path):
    from app.services.sidecar_notes import find_sidecar_notes

    (tmp_path / "MyModel.stl").write_bytes(b"solid MyModel endsolid")
    (tmp_path / "MyModel.txt").write_text("Print at 0.2mm, PETG, 3 perimeters.")

    notes = find_sidecar_notes(str(tmp_path / "MyModel.stl"))
    assert notes == "Print at 0.2mm, PETG, 3 perimeters."


def test_find_sidecar_notes_reads_sibling_pdf_file(tmp_path):
    from app.services.sidecar_notes import find_sidecar_notes

    (tmp_path / "MyModel.stl").write_bytes(b"solid MyModel endsolid")
    (tmp_path / "MyModel.pdf").write_bytes(_make_minimal_pdf("Assembly instructions here"))

    notes = find_sidecar_notes(str(tmp_path / "MyModel.stl"))
    assert notes == "Assembly instructions here"


def test_find_sidecar_notes_prefers_txt_over_pdf_when_both_exist(tmp_path):
    from app.services.sidecar_notes import find_sidecar_notes

    (tmp_path / "MyModel.stl").write_bytes(b"solid MyModel endsolid")
    (tmp_path / "MyModel.txt").write_text("From the txt file")
    (tmp_path / "MyModel.pdf").write_bytes(_make_minimal_pdf("From the pdf file"))

    notes = find_sidecar_notes(str(tmp_path / "MyModel.stl"))
    assert notes == "From the txt file"


def test_find_sidecar_notes_returns_none_when_no_sidecar_exists(tmp_path):
    from app.services.sidecar_notes import find_sidecar_notes

    (tmp_path / "MyModel.stl").write_bytes(b"solid MyModel endsolid")

    assert find_sidecar_notes(str(tmp_path / "MyModel.stl")) is None


def test_find_sidecar_notes_returns_none_for_empty_txt_file(tmp_path):
    from app.services.sidecar_notes import find_sidecar_notes

    (tmp_path / "MyModel.stl").write_bytes(b"solid MyModel endsolid")
    (tmp_path / "MyModel.txt").write_text("   \n  ")

    assert find_sidecar_notes(str(tmp_path / "MyModel.stl")) is None
