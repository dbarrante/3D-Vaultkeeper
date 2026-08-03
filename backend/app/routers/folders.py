import uuid
from typing import Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db_conn, row_to_folder

router = APIRouter()


class FolderData(BaseModel):
    name: str
    parentId: Union[str, None] = None


@router.get("/api/folders")
def get_folders():
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT id,name,parentId FROM folders")
    rows = cur.fetchall()
    conn.close()
    return [row_to_folder(r) for r in rows]


@router.post("/api/folders")
def create_folder(item: FolderData):
    fid = str(uuid.uuid4())
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO folders(id,name,parentId) VALUES (?,?,?)",
        (fid, item.name, item.parentId),
    )
    conn.commit()
    conn.close()
    return {"id": fid, "name": item.name, "parentId": item.parentId}


@router.patch("/api/folders/{folder_id}")
def update_folder(folder_id: str, item: FolderData):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("UPDATE folders SET name=? WHERE id=?", (item.name, folder_id))
    if cur.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Folder not found")
    conn.commit()
    cur.execute("SELECT id,name,parentId FROM folders WHERE id=?", (folder_id,))
    row = cur.fetchone()
    conn.close()
    return row_to_folder(row)


@router.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: str):
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM models WHERE folderId=? AND removedAt IS NULL LIMIT 1", (folder_id,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Folder must be empty to delete")
    cur.execute("SELECT 1 FROM folders WHERE parentId=? LIMIT 1", (folder_id,))
    if cur.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Folder must be empty to delete")
    cur.execute("DELETE FROM folders WHERE id=?", (folder_id,))
    conn.commit()
    conn.close()
    return {"ok": True}
