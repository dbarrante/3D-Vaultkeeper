import os
import uuid
import json

from fastapi import APIRouter, HTTPException

from app.db import get_db_conn, get_setting, now_ms, row_to_folder, UPLOAD_DIR
from app.importers import generic, makerworld, printables

router = APIRouter()


def importer_for_url(url: str):
    lowered = url.lower()
    if "makerworld.com" in lowered:
        return makerworld.MakerWorldImporter(), "makerworld"
    if "printables.com" in lowered:
        return printables.PrintablesImporter(), "printables"
    return generic.GenericImporter(), "generic"


def importer_for_source(source: str):
    if source == "makerworld":
        return makerworld.MakerWorldImporter(get_setting("makerworld_bambu_token")), "MakerWorld"
    if source == "generic":
        return generic.GenericImporter(), "Web"
    return printables.PrintablesImporter(), "Printables"


@router.post("/api/import/importid")
def import_model_by_id(payload: dict):
    source = payload.get("source", "printables")
    importer, source_label = importer_for_source(source)
    modelId = payload.get("id")
    modelName = payload.get("name")
    parentId = payload.get("parentId")
    previewPath = payload.get("previewPath")
    folderId = payload.get("folderId", "1")
    typeName = payload.get("typeName")
    mid = str(uuid.uuid4())
    ext = typeName if typeName is not None else ".stl"
    filename = f"{mid}.{ext}"
    path = os.path.join(UPLOAD_DIR, filename)

    try:
        if modelId is not None:
            file, thumbnail = importer.importfromId(modelId, parentId, previewPath)
            if file is not None:
                with open(path, "wb") as fh:
                    fh.write(file.content)
                size = os.path.getsize(path)
            else:
                raise ValueError("File Is Empty")
        else:
            raise ValueError("URL is None")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    model = {
        "id": mid, "name": modelName, "folderId": folderId if folderId != "all" else "1",
        "url": f"/api/models/{mid}/download", "size": size, "dateAdded": now_ms(),
        "tags": ["imported"], "description": f"Imported from {source_label}", "thumbnail": thumbnail,
        "filePath": path,
    }
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,filePath) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (model["id"], model["name"], model["folderId"], model["url"], model["size"],
         model["dateAdded"], json.dumps(model["tags"]), model["description"], model["thumbnail"], model["filePath"]),
    )
    conn.commit()
    conn.close()
    return model


@router.post("/api/import/batch")
def import_batch(payload: dict):
    source = payload.get("source", "printables")
    folder_name = (payload.get("folderName") or "").strip()
    description = payload.get("description") or ""
    files = payload.get("files") or []
    folder_resolution = payload.get("folderResolution")

    if not folder_name:
        raise HTTPException(status_code=400, detail="folderName is required")
    if not files:
        raise HTTPException(status_code=400, detail="No files selected")

    importer, source_label = importer_for_source(source)

    conn = get_db_conn()
    cur = conn.cursor()

    existing = cur.execute(
        "SELECT id, name FROM folders WHERE name=? AND parentId IS NULL",
        (folder_name,),
    ).fetchone()

    if existing and folder_resolution is None:
        conn.close()
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "folder_name_collision",
                "existingFolderId": existing["id"],
                "existingFolderName": existing["name"],
            },
        )

    if existing and folder_resolution == "reuse":
        folder_id = existing["id"]
    else:
        folder_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO folders(id,name,parentId,description) VALUES (?,?,?,?)",
            (folder_id, folder_name, None, description),
        )
        conn.commit()

    created_models = []
    failed = []
    for f in files:
        model_id = f.get("id")
        model_name = f.get("name")
        try:
            file_resp, thumbnail = importer.importfromId(
                model_id, f.get("parentId"), f.get("previewPath")
            )
            if file_resp is None:
                raise ValueError("File is empty")
            ext = f.get("typeName") or "stl"
            mid = str(uuid.uuid4())
            filename = f"{mid}.{ext}"
            path = os.path.join(UPLOAD_DIR, filename)
            with open(path, "wb") as fh:
                fh.write(file_resp.content)
            size = os.path.getsize(path)
            model = {
                "id": mid,
                "name": model_name,
                "folderId": folder_id,
                "url": f"/api/models/{mid}/download",
                "size": size,
                "dateAdded": now_ms(),
                "tags": ["imported"],
                "description": f"Imported from {source_label}",
                "thumbnail": thumbnail,
                "filePath": path,
            }
            cur.execute(
                "INSERT INTO models(id,name,folderId,url,size,dateAdded,tags,description,thumbnail,filePath) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    model["id"], model["name"], model["folderId"], model["url"], model["size"],
                    model["dateAdded"], json.dumps(model["tags"]), model["description"],
                    model["thumbnail"], model["filePath"],
                ),
            )
            conn.commit()
            created_models.append(model)
        except Exception as e:
            failed.append({"name": model_name, "error": str(e)})

    cur.execute("SELECT id,name,parentId,description FROM folders WHERE id=?", (folder_id,))
    folder_row = cur.fetchone()
    conn.close()

    return {
        "folder": row_to_folder(folder_row),
        "models": created_models,
        "failed": failed,
    }


@router.post("/api/import/options")
def import_model_options(payload: dict):
    url = payload.get("url")
    try:
        if url is not None:
            importer, _source_label = importer_for_url(url)
            modelData = importer.getModelOptions(url)
            if modelData is not None and modelData.get("files"):
                return modelData
            raise ValueError("No importable files found at that URL")
        raise ValueError("URL is None")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/printables/importid")
def import_printables_model_by_id(payload: dict):
    payload["source"] = "printables"
    return import_model_by_id(payload)


@router.post("/api/printables/options")
def import_printables_model_options(payload: dict):
    return import_model_options(payload)
