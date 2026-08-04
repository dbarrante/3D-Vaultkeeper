from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.import_wizard import build_tree, expand_placement, commit_placement_file

router = APIRouter(prefix="/api/import", tags=["import"])


@router.get("/tree")
def get_import_tree(path: str):
    root = Path(path)
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    return build_tree(root)


class Placement(BaseModel):
    sourcePath: str
    isFolder: bool
    targetFolderId: str


class CommitRequest(BaseModel):
    placements: List[Placement]


@router.post("/commit")
def commit_import(body: CommitRequest):
    results = []
    for placement in body.placements:
        files = expand_placement(placement.sourcePath, placement.isFolder)
        for file_path in files:
            result = commit_placement_file(file_path, placement.targetFolderId)
            result["placementSourcePath"] = placement.sourcePath
            results.append(result)
    return {"results": results}
