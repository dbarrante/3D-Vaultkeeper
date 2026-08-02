from fastapi import APIRouter, HTTPException

from app.db import get_setting, set_setting, clear_setting

router = APIRouter()


@router.get("/api/settings/makerworld-token")
def makerworld_token_status():
    return {"configured": bool(get_setting("makerworld_bambu_token"))}


@router.put("/api/settings/makerworld-token")
def update_makerworld_token(payload: dict):
    if payload.get("clear") is True:
        clear_setting("makerworld_bambu_token")
        return {"configured": False}
    token = str(payload.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")
    set_setting("makerworld_bambu_token", token)
    return {"configured": True}


@router.get("/api/settings/openrouter-key")
def openrouter_key_status():
    return {"configured": bool(get_setting("openrouter_api_key"))}


@router.put("/api/settings/openrouter-key")
def update_openrouter_key(payload: dict):
    if payload.get("clear") is True:
        clear_setting("openrouter_api_key")
        return {"configured": False}
    token = str(payload.get("token", "")).strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required")
    set_setting("openrouter_api_key", token)
    return {"configured": True}
