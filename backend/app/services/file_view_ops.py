from pathlib import Path

from app.db import get_db_conn, UPLOAD_DIR


def validate_destination(new_path: str, storage_mode: str) -> Path:
    """Ensure a rename/move destination resolves inside an allowed root.

    Copy-mode files may only land under UPLOAD_DIR (the app's managed
    storage) -- .resolve() neutralizes any ".." traversal in new_path
    before the containment check runs, so a crafted destination can't
    escape UPLOAD_DIR even if a caller doesn't sanitize it first.

    Reference-mode (watch-folder) files may land under any currently
    configured watch_folders.path. Moving one outside every watched root
    would mean the app permanently loses track of it -- nothing would
    ever scan that location again -- so that's rejected rather than
    silently allowed.
    """
    resolved = Path(new_path).resolve()

    if storage_mode == "copy":
        upload_root = Path(UPLOAD_DIR).resolve()
        if resolved == upload_root or upload_root in resolved.parents:
            return resolved
        raise ValueError(
            f"Destination must be inside the managed library folder: {upload_root}"
        )

    conn = get_db_conn()
    try:
        watch_roots = [
            Path(row["path"]).resolve()
            for row in conn.execute("SELECT path FROM watch_folders").fetchall()
        ]
    finally:
        conn.close()
    for root in watch_roots:
        if resolved == root or root in resolved.parents:
            return resolved
    raise ValueError(
        "A linked file has to stay somewhere the app is watching -- "
        "this destination isn't inside any watched folder."
    )
