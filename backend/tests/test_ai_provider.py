def test_extract_json_parses_clean_array():
    from app.services.ai_provider import extract_json
    assert extract_json('["a", "b"]') == ["a", "b"]


def test_extract_json_parses_prose_wrapped_array():
    from app.services.ai_provider import extract_json
    text = 'Sure, here are some tags:\n["desk", "organizer"]\nHope that helps!'
    assert extract_json(text) == ["desk", "organizer"]


def test_extract_json_parses_prose_wrapped_object():
    from app.services.ai_provider import extract_json
    text = 'Here is my estimate:\n{"priceRange": "$5-$10"}\nLet me know if you need more.'
    assert extract_json(text) == {"priceRange": "$5-$10"}


def test_extract_json_returns_none_for_unparseable_text():
    from app.services.ai_provider import extract_json
    assert extract_json("I couldn't come up with any tags for this one, sorry.") is None
