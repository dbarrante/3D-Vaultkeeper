import os
from pathlib import Path

from app.db import get_db_conn, UPLOAD_DIR


def ensure_unambiguous_path(path) -> None:
    """Reject any path that isn't absolute, BEFORE .resolve() is allowed to run.

    On Windows a bare drive letter ("C:") is a *drive-relative* path: it
    resolves against the process's current working directory on that drive,
    NOT to "C:\\". The File-mode tree emits exactly that string as the node id
    for a drive-letter node, and because Path("C:").resolve() returns the
    backend's own cwd, `resolved.parent == resolved` never fires for it and
    the containment checks below happily accept it whenever the backend's cwd
    happens to sit under UPLOAD_DIR or a watch root. The same reasoning
    applies to any relative path on any platform.

    Deliberately uses the NATIVE path flavour rather than PureWindowsPath:
    the backend also ships as a Linux container (see .github/workflows), where
    PureWindowsPath("/app/uploads/x").is_absolute() is False and would reject
    every legitimate POSIX path. On Windows the native flavour is the Windows
    flavour, so "C:" is still correctly rejected and "C:\\x" accepted.
    """
    if not Path(str(path)).is_absolute():
        raise ValueError(
            f"{path} is not an absolute path -- refusing to act on an "
            "ambiguous, cwd-relative location"
        )


def path_conflicts_with_watch_root(candidate: Path) -> bool:
    """True if candidate IS a registered watch folder's root, or CONTAINS one
    as a descendant.

    Renaming, moving, or deleting such a path would silently orphan that watch
    folder's `watch_folders.path` row -- it would keep pointing at a directory
    that no longer exists there, and the watcher would silently return 0
    results on every future scan with no visible error, rather than fail
    loudly.

    Note the containment direction: `resolved in root.parents` means "candidate
    CONTAINS root", the inverse of the `root in resolved.parents` test used by
    validate_destination ("candidate is UNDER root"). Getting these backwards
    still passes the exact-match case via `==` while silently permitting the
    orphaning case this helper exists to stop.
    """
    resolved = candidate.resolve()
    conn = get_db_conn()
    try:
        watch_roots = [
            Path(row["path"]).resolve()
            for row in conn.execute("SELECT path FROM watch_folders").fetchall()
        ]
    finally:
        conn.close()
    for root in watch_roots:
        if resolved == root or resolved in root.parents:
            return True
    return False


def is_self_nested_move(source: Path, destination: Path) -> bool:
    """True if destination is the source itself or sits inside the source's own
    subtree.

    Such a move passes both the 409-exists check (the destination doesn't exist
    yet) and containment validation (it still resolves inside the same allowed
    root), and shutil.move would then raise -- surfacing as an unhandled 500.
    Worse, move_folder's destination.parent.mkdir(parents=True) would already
    have created the intermediate directories INSIDE the still-present source
    before that happened, leaving a stray directory behind. Shared by
    rename_folder and move_folder, which enforce the identical rule.
    """
    source_resolved = source.resolve()
    destination_resolved = destination.resolve()
    return (
        destination_resolved == source_resolved
        or source_resolved in destination_resolved.parents
    )


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
    ensure_unambiguous_path(new_path)
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


def resolve_storage_mode_for_path(path: Path) -> str:
    """Determine which containment rule applies to a real directory: "copy"
    if it's under UPLOAD_DIR, "reference" if it's under some configured
    watch_folders.path. Raises ValueError if it's under neither -- this
    should only happen if the path was never a legitimate File-mode node
    to begin with.
    """
    ensure_unambiguous_path(path)
    resolved = path.resolve()
    upload_root = Path(UPLOAD_DIR).resolve()
    if resolved == upload_root or upload_root in resolved.parents:
        return "copy"
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
            return "reference"
    raise ValueError(f"{path} is not inside the managed library or any watched folder")


def find_affected_models(dir_path: str) -> list:
    """Every model row whose current file (filePath) lives at or under
    dir_path. Filters in Python rather than SQL LIKE to avoid needing to
    escape "%"/"_" wildcard characters that can legally appear in a real
    folder name.
    """
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT * FROM models").fetchall()
    finally:
        conn.close()
    prefix = os.path.normpath(dir_path)
    affected = []
    for row in rows:
        fp = row["filePath"] if "filePath" in row.keys() else None
        if not fp:
            continue
        norm = os.path.normpath(fp)
        if norm == prefix or norm.startswith(prefix + os.sep):
            affected.append(row)
    return affected


def rewrite_affected_paths(dir_path: str, new_dir_path: str) -> None:
    """After dir_path has already been physically moved/renamed to
    new_dir_path on disk, update every affected model's filePath (and
    sourcePath, for reference-mode rows) to match -- same lock-step
    requirement as the file-level rename/move endpoint, applied per row.
    Call this immediately after the physical move, passing the OLD
    dir_path so find_affected_models still matches what's in the DB.
    """
    old_prefix = os.path.normpath(dir_path)
    conn = get_db_conn()
    cur = conn.cursor()
    try:
        for row in find_affected_models(dir_path):
            old_fp = os.path.normpath(row["filePath"])
            rel = os.path.relpath(old_fp, old_prefix)
            new_fp = os.path.normpath(os.path.join(new_dir_path, rel)) if rel != "." else os.path.normpath(new_dir_path)
            storage_mode = row["storageMode"] if "storageMode" in row.keys() else "copy"
            if storage_mode == "reference":
                cur.execute(
                    "UPDATE models SET filePath=?, sourcePath=? WHERE id=?",
                    (new_fp, new_fp, row["id"]),
                )
            else:
                cur.execute("UPDATE models SET filePath=? WHERE id=?", (new_fp, row["id"]))
        conn.commit()
    finally:
        conn.close()


def find_affected_tracked_folders(dir_path: str) -> list:
    """Every tracked-folder path at or under dir_path. Mirrors
    find_affected_models's Python-side prefix filtering (not SQL LIKE) to
    avoid needing to escape "%"/"_" wildcard characters that can legally
    appear in a real folder name.
    """
    conn = get_db_conn()
    try:
        rows = conn.execute("SELECT path FROM file_view_tracked_folders").fetchall()
    finally:
        conn.close()
    prefix = os.path.normpath(dir_path)
    affected = []
    for row in rows:
        norm = os.path.normpath(row["path"])
        if norm == prefix or norm.startswith(prefix + os.sep):
            affected.append(norm)
    return affected


def rewrite_tracked_folder_paths(dir_path: str, new_dir_path: str) -> None:
    """After dir_path has already been physically moved/renamed to
    new_dir_path on disk, update every tracked-folder row at or under it to
    match -- same purpose as rewrite_affected_paths, applied to
    file_view_tracked_folders instead of models. Call this immediately
    after the physical move, passing the OLD dir_path so
    find_affected_tracked_folders still matches what's in the DB.
    """
    old_prefix = os.path.normpath(dir_path)
    conn = get_db_conn()
    try:
        cur = conn.cursor()
        for old_path in find_affected_tracked_folders(dir_path):
            rel = os.path.relpath(old_path, old_prefix)
            new_path = (
                os.path.normpath(os.path.join(new_dir_path, rel))
                if rel != "."
                else os.path.normpath(new_dir_path)
            )
            # A stale row can already occupy new_path -- e.g. its directory
            # was removed by something other than delete_folder (manual
            # filesystem deletion, external interference), leaving the
            # tracked-folder row behind with nothing on disk. The real
            # directory now being moved to new_path is about to occupy that
            # exact location, so any pre-existing row there no longer refers
            # to anything real and must be cleared before the UPDATE below,
            # or the UPDATE fails on the path column's primary key with an
            # unhandled sqlite3.IntegrityError -- even though the physical
            # shutil.move in rename_folder/move_folder already succeeded,
            # leaving the DB genuinely desynced from disk.
            cur.execute("DELETE FROM file_view_tracked_folders WHERE path=?", (new_path,))
            cur.execute(
                "UPDATE file_view_tracked_folders SET path=? WHERE path=?",
                (new_path, old_path),
            )
        conn.commit()
    finally:
        conn.close()
