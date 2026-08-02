from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import FileResponse

from app.db import get_db_conn, row_to_model, save_upload_file, MANUAL_DIR

router = APIRouter()


@router.get("/api/models/{model_id}/manual")
def get_model_manual(model_id: str):
    path = MANUAL_DIR / f"{model_id}.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Manual not found")
    return FileResponse(path, media_type="text/markdown")


@router.put("/api/models/{model_id}/manual")
def upload_model_manual(model_id: str, file: UploadFile = File(...)):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    path = MANUAL_DIR / f"{model_id}.md"
    save_upload_file(file, str(path))
    cur.execute("UPDATE models SET manual=? WHERE id=?", (file.filename, model_id))
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)


@router.delete("/api/models/{model_id}/manual")
def delete_model_manual(model_id: str):
    conn = get_db_conn()
    cur = conn.cursor()
    m = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    if not m:
        conn.close()
        raise HTTPException(status_code=404, detail="Model not found")
    path = MANUAL_DIR / f"{model_id}.md"
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    cur.execute("UPDATE models SET manual=NULL WHERE id=?", (model_id,))
    conn.commit()
    row = cur.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    return row_to_model(row)
