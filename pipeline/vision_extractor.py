"""FR-03 — Brand extraction via Gemini Flash Vision.

Sends the downloaded image to Google Gemini Flash and asks it to return a
structured JSON payload describing the brand. The response is parsed and
normalised into a fixed dict shape.
"""

import json

from google import genai
from google.genai import types

from utils.logger import get_logger
from utils.retry import retry

logger = get_logger("pipeline.vision_extractor")

_EXTRACTION_PROMPT = (
    "Analyze this Instagram brand post image. Extract the following details "
    "and return ONLY valid JSON (no markdown, no extra text) with exactly "
    "these keys: brand_name (str), handle (str, Instagram username WITHOUT "
    "the @ symbol), niche (str, product/service category), tagline (str, "
    "short brand tagline if visible), email (str or null), phone (str or "
    "null), website (str or null), post_content (str, ALL visible text from "
    "the post including caption, hashtags, offers, prices, slogans, CTAs — "
    "copy it verbatim, nothing omitted), confidence (float between 0 and 1 "
    "indicating how confident you are that this is a real brand and the "
    "handle is correct). If a field is not visible use null."
)

_RETRYABLE_EXCEPTIONS = (RuntimeError, ConnectionError, TimeoutError)


class ExtractionError(Exception):
    """Raised when Gemini fails to extract brand details after retries."""


class _InvalidResponse(ExtractionError):
    """Internal marker for a response that cannot be parsed."""


def _guess_mime(path: str) -> str:
    if path.lower().endswith(".png"):
        return "image/png"
    if path.lower().endswith(".webp"):
        return "image/webp"
    if path.lower().endswith(".gif"):
        return "image/gif"
    return "image/jpeg"


@retry(exceptions=_RETRYABLE_EXCEPTIONS, tries=3, base_delay=1.0, logger=logger)
def _call_gemini(api_key: str, image_path: str) -> str:
    """Call Gemini Flash vision and return the raw text response."""
    client = genai.Client(api_key=api_key)
    with open(image_path, "rb") as fh:
        image_bytes = fh.read()
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=_guess_mime(image_path)),
            _EXTRACTION_PROMPT,
        ],
    )
    return response.text or ""


def _parse_response(raw_text: str) -> dict:
    """Parse and validate the raw Gemini JSON text into a brand dict."""
    text = raw_text.strip()
    if not text:
        raise _InvalidResponse("Empty Gemini response")
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json"):]
        text = text.strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise _InvalidResponse("Gemini returned non-JSON text") from None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise _InvalidResponse(f"Malformed JSON from Gemini: {exc}") from exc

    if not isinstance(payload, dict):
        raise _InvalidResponse("Gemini JSON is not an object")

    brand = {
        "brand_name": _clean_str(payload.get("brand_name")),
        "handle": (_clean_str(payload.get("handle")) or "").lstrip("@"),
        "niche": _clean_str(payload.get("niche")),
        "tagline": _clean_str(payload.get("tagline")),
        "post_content": _clean_str(payload.get("post_content")),
        "email": _clean_str(payload.get("email")),
        "phone": _clean_str(payload.get("phone")),
        "website": _clean_str(payload.get("website")),
        "confidence": _confidence(payload.get("confidence")),
    }
    if not brand["handle"]:
        brand["handle"] = None
    if not brand["brand_name"] and not brand["handle"]:
        raise _InvalidResponse("No brand name or handle extracted")
    return brand


def _clean_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "n/a", "none", "unknown"}:
        return None
    return text


def _confidence(value) -> float:
    try:
        conf = float(value)
        return max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        return 0.0


def extract_brand(image_path: str, api_key: str | None = None, model=None) -> dict:
    """Extract brand identity details from an image.

    Args:
        image_path: path to the downloaded image.
        api_key: Gemini API key; defaults to ``config.CONFIG``.
        model: optional pre-instantiated Gemini model (for testing).

    Returns:
        A dict with keys: brand_name, handle, niche, tagline, email, phone,
        website, confidence.

    Raises:
        FileNotFoundError: if ``image_path`` does not exist.
        ExtractionError: if Gemini fails after retries.
    """
    import os

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file not found: {image_path}")

    if api_key is None:
        from config import CONFIG

        api_key = CONFIG["gemini_api_key"]

    if model is not None:
        with open(image_path, "rb") as fh:
            image_bytes = fh.read()
        response = model.generate_content([_EXTRACTION_PROMPT, {"mime_type": _guess_mime(image_path), "data": image_bytes}])
        raw_text = response.text or ""
    else:
        raw_text = _call_gemini(api_key, image_path)

    return _parse_response(raw_text)
