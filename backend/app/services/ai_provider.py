import json
import re
from typing import Optional

import requests

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-3.5-haiku"


def call_openrouter(prompt: str, api_key: str, model: str = DEFAULT_MODEL) -> str:
    """Single-turn chat completion via OpenRouter's OpenAI-compatible API.
    Callers (routers/ai.py) always patch this function directly in tests —
    it's the one place that makes a real network call, so it's the seam.
    """
    response = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def extract_json(text: str) -> Optional[object]:
    """LLMs asked for JSON often wrap it in prose ("Sure, here you go:\n[...]\nHope
    that helps!") or markdown code fences — pull out the first {...} or [...]
    substring and parse that, rather than requiring an exact-JSON response.
    Returns None if nothing parses, so callers can produce a clear error instead
    of a raw JSONDecodeError leaking to the API response.
    """
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
