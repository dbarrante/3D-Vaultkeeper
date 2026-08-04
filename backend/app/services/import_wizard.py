from pathlib import Path

from app.services.scan import SUPPORTED_EXTENSIONS


def build_tree(root: Path) -> dict:
    """Read-only recursive walk of a raw directory for the Import Wizard's
    left pane. No DB writes and no files are touched -- this never
    modifies anything, it only describes what's already there.
    """
    folders = []
    files = []
    for entry in sorted(root.iterdir(), key=lambda e: e.name.lower()):
        if entry.is_dir():
            folders.append(build_tree(entry))
        elif entry.is_file():
            files.append({
                "name": entry.name,
                "path": str(entry),
                "isModel": entry.suffix.lower() in SUPPORTED_EXTENSIONS,
                "size": entry.stat().st_size,
            })
    return {"name": root.name, "path": str(root), "folders": folders, "files": files}
