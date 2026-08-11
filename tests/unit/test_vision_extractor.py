import json

import pytest

from pipeline import vision_extractor
from pipeline.vision_extractor import ExtractionError


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModel:
    def __init__(self, text):
        self._text = text

    def generate_content(self, parts):
        return FakeResponse(self._text)


def _brand_payload(overrides=None):
    payload = {
        "brand_name": "Glow Skincare",
        "handle": "@glowskincare",
        "niche": "Skincare",
        "tagline": "Glow from within",
        "email": "hello@glow.com",
        "phone": "+919876543210",
        "website": "https://glow.com",
        "confidence": 0.9,
    }
    if overrides:
        payload.update(overrides)
    return payload


def test_valid_json_parsed_correctly(sample_image):
    raw = json.dumps(_brand_payload())
    result = vision_extractor.extract_brand(sample_image, model=FakeModel(raw))
    assert result["brand_name"] == "Glow Skincare"
    assert result["handle"] == "glowskincare"
    assert result["niche"] == "Skincare"
    assert result["confidence"] == 0.9


def test_handle_at_prefix_stripped(sample_image):
    raw = json.dumps(_brand_payload())
    result = vision_extractor.extract_brand(sample_image, model=FakeModel(raw))
    assert result["handle"] == "glowskincare"


def test_malformed_json_raises_extraction_error(sample_image):
    with pytest.raises(ExtractionError):
        vision_extractor.extract_brand(sample_image, model=FakeModel("this is not json"))


def test_fenced_json_is_parsed(sample_image):
    raw = "```json\n" + json.dumps(_brand_payload()) + "\n```"
    result = vision_extractor.extract_brand(sample_image, model=FakeModel(raw))
    assert result["brand_name"] == "Glow Skincare"


def test_missing_file_raises_file_not_found(tmp_path):
    missing = str(tmp_path / "nope.jpg")
    with pytest.raises(FileNotFoundError):
        vision_extractor.extract_brand(missing, model=FakeModel("{}"))


def test_null_fields_become_none(sample_image):
    payload = _brand_payload({"email": None, "phone": "null", "website": ""})
    result = vision_extractor.extract_brand(sample_image, model=FakeModel(json.dumps(payload)))
    assert result["email"] is None
    assert result["phone"] is None
    assert result["website"] is None


def test_no_brand_or_handle_raises(sample_image):
    payload = {"brand_name": None, "handle": "null", "confidence": 0.1}
    with pytest.raises(ExtractionError):
        vision_extractor.extract_brand(sample_image, model=FakeModel(json.dumps(payload)))


def test_confidence_clamped(sample_image):
    payload = _brand_payload({"confidence": 5.0})
    result = vision_extractor.extract_brand(sample_image, model=FakeModel(json.dumps(payload)))
    assert result["confidence"] == 1.0
    payload2 = _brand_payload({"confidence": -1})
    result2 = vision_extractor.extract_brand(sample_image, model=FakeModel(json.dumps(payload2)))
    assert result2["confidence"] == 0.0


def test_retry_fires_on_retryable_exception(sample_image, monkeypatch):
    calls = {"n": 0}

    class FlakyModel:
        def __init__(self, name):
            pass

        def generate_content(self, parts):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("boom")
            return FakeResponse(json.dumps(_brand_payload()))

    monkeypatch.setattr(vision_extractor.genai, "configure", lambda **k: None)
    monkeypatch.setattr(vision_extractor.genai, "GenerativeModel", FlakyModel)

    from utils import retry as retry_mod

    monkeypatch.setattr(retry_mod.time, "sleep", lambda s: None)

    result = vision_extractor._call_gemini("key", sample_image)
    assert calls["n"] == 3
    assert json.loads(result)["brand_name"] == "Glow Skincare"


def test_empty_response_raises(sample_image):
    with pytest.raises(ExtractionError):
        vision_extractor.extract_brand(sample_image, model=FakeModel(""))
