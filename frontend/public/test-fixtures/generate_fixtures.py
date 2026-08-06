# frontend/public/test-fixtures/generate_fixtures.py -- run once.
# small.3mf, small.stp and corrupt.stl are tiny and committed; the two
# synthetic *-threshold-*.stl files are ~105MB combined and are
# .gitignore'd (see frontend/public/test-fixtures/.gitignore) rather than
# committed -- frontend/public/ is copied verbatim into frontend/dist/ on
# every `bun run build`, and desktop/build.ps1 runs exactly that build to
# produce the shipped desktop app, so committing them would put 105MB of
# synthetic test data inside every real build/release unless separately
# excluded at build time. Regenerate them locally before running the
# integration test suite.
#
# Paths are anchored to this script's own location (not the current working
# directory) so it behaves the same whether invoked as `python
# public/test-fixtures/generate_fixtures.py` from frontend/, or as
# `python frontend/public/test-fixtures/generate_fixtures.py` from the repo
# root.
import struct
import zipfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent


def write_stl(path, triangle_count):
    with open(path, "wb") as f:
        f.write(b"\x00" * 80)
        f.write(struct.pack("<I", triangle_count))
        for i in range(triangle_count):
            x = i * 0.01
            f.write(struct.pack(
                "<12fH",
                0, 0, 1,                 # normal
                x, 0, 0, x + 1, 0, 0, x, 1, 0,  # 3 vertices
                0,                        # attribute byte count
            ))

# ~45MB, under the 50MB threshold -- used to prove the worker path stays
# responsive even close to the boundary.
write_stl(FIXTURES_DIR / "under-threshold-45mb.stl", 900_000)

# ~60MB, over the 50MB threshold -- used to prove the threshold correctly
# skips the worker entirely.
write_stl(FIXTURES_DIR / "over-threshold-60mb.stl", 1_200_000)


def write_minimal_3mf(path):
    # A minimal spec-compliant 3MF (3D Manufacturing Format) package: a ZIP
    # archive containing [Content_Types].xml, _rels/.rels, and
    # 3D/3dmodel.model describing a single 4-vertex/4-triangle tetrahedron.
    # Follows the 3MF Core Specification's documented minimal structure --
    # three.js's ThreeMFLoader (used by both the static-thumbnail pipeline
    # and this worker) parses standard-compliant packages.
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        "</Relationships>"
    )
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
        "<resources><object id=\"1\" type=\"model\"><mesh>"
        "<vertices>"
        '<vertex x="0" y="0" z="0"/>'
        '<vertex x="10" y="0" z="0"/>'
        '<vertex x="0" y="10" z="0"/>'
        '<vertex x="0" y="0" z="10"/>'
        "</vertices>"
        "<triangles>"
        '<triangle v1="0" v2="1" v3="2"/>'
        '<triangle v1="0" v2="1" v3="3"/>'
        '<triangle v1="0" v2="2" v3="3"/>'
        '<triangle v1="1" v2="2" v3="3"/>'
        "</triangles>"
        "</mesh></object></resources>"
        '<build><item objectid="1"/></build>'
        "</model>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)

write_minimal_3mf(FIXTURES_DIR / "small.3mf")


def write_corrupt_stl(path):
    # Deliberately garbage bytes, well under the 84-byte minimum
    # (80-byte header + 4-byte triangle count) that three.js's STLLoader
    # needs before it will even attempt to read a triangle count via
    # DataView.getUint32(80, ...). Uploaded as a real model and hovered in
    # the integration test below to exercise the hover-preview worker's
    # real error path end-to-end (a synchronous RangeError thrown inside
    # STLLoader.parse, caught by hoverPreviewWorker.ts's handleStart try/
    # catch, reported as an "error" message) -- mocking window.fetch on the
    # main thread cannot substitute for this, since the worker performs its
    # own independent fetch in its own global scope, not the main thread's.
    with open(path, "wb") as f:
        f.write(b"NOT A VALID STL FILE")

write_corrupt_stl(FIXTURES_DIR / "corrupt.stl")

print(f"Fixtures written to {FIXTURES_DIR}")
