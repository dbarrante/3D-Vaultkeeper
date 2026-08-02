from fastapi import APIRouter, HTTPException

from app.db import get_db_conn, get_setting, row_to_model
from app.services import ai_provider

router = APIRouter()


def _require_openrouter_key() -> str:
    key = get_setting("openrouter_api_key")
    if not key:
        raise HTTPException(
            status_code=400,
            detail="OpenRouter API key not configured — add one in Settings.",
        )
    return key


def _get_model_or_404(model_id: str):
    conn = get_db_conn()
    row = conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Model not found")
    return row_to_model(row)


@router.post("/api/models/{model_id}/suggest-tags")
def suggest_tags(model_id: str):
    api_key = _require_openrouter_key()
    model = _get_model_or_404(model_id)

    prompt = (
        "Suggest 3-6 short, lowercase tags (single words or short phrases) for a "
        "3D-printable model with the following details. Respond with ONLY a JSON "
        "array of strings, nothing else.\n\n"
        f"Name: {model['name']}\n"
        f"Description: {model['description'] or 'none'}\n"
        f"Existing tags: {', '.join(model['tags']) or 'none'}\n"
        f"Category: {model['category'] or 'unknown'}"
    )

    raw = ai_provider.call_openrouter(prompt, api_key)
    tags = ai_provider.extract_json(raw)
    if not isinstance(tags, list):
        raise HTTPException(status_code=502, detail="AI response was not a valid tag list")

    return {"tags": [str(t) for t in tags]}


@router.post("/api/models/{model_id}/suggest-pricing")
def suggest_pricing(model_id: str):
    api_key = _require_openrouter_key()
    model = _get_model_or_404(model_id)

    prompt = (
        "You help 3D-printing hobbyists estimate a fair Etsy resale price for a "
        "printed model. Given the details below, respond with ONLY a JSON object "
        'with exactly these keys: "priceRange" (a short string like "$8-$14"), '
        '"popularity" (one of "Low", "Moderate", "High"), and "reasoning" (one or '
        "two sentences). No other text.\n\n"
        f"Name: {model['name']}\n"
        f"Description: {model['description'] or 'none'}\n"
        f"Category: {model['category'] or 'unknown'}\n"
        f"Tags: {', '.join(model['tags']) or 'none'}\n"
        f"Color count: {model['colorCount'] or 1}"
    )

    raw = ai_provider.call_openrouter(prompt, api_key)
    result = ai_provider.extract_json(raw)
    if not isinstance(result, dict) or "priceRange" not in result:
        raise HTTPException(status_code=502, detail="AI response was not a valid pricing estimate")

    return {
        "priceRange": result.get("priceRange"),
        "popularity": result.get("popularity"),
        "reasoning": result.get("reasoning", ""),
    }
