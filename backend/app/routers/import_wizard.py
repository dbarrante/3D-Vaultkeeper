from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.services.import_wizard import build_tree

router = APIRouter(prefix="/api/import", tags=["import"])


@router.get("/tree")
def get_import_tree(path: str):
    root = Path(path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return build_tree(root)
