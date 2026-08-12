"""Generate LinkedIn outreach message and cold email from brand data via Gemini."""

import json

from google import genai
from google.genai import types

from utils.logger import get_logger

logger = get_logger("pipeline.outreach_writer")

_PROMPT_TEMPLATE = """You are writing outreach on behalf of a full-service marketing agency (Chord / 1702 Marketing Agency). You are reaching out to potential clients — brands or individuals who may need marketing services.

Brand info:
- Name: {brand_name}
- Handle: @{handle}
- Niche: {niche}
- Followers: {followers}
- Bio: {bio}
- Recent post content: {post_data}
{snippets_block}

Generate two outreach messages and return ONLY valid JSON (no markdown):
{{
  "linkedin_msg": "...",
  "email": "..."
}}

Rules:
- linkedin_msg: max 400 characters. Casual, direct. Reference something specific from their post or niche. End with a soft CTA to connect or chat. Do NOT mention AM:PM.
- email: Full cold email. Include Subject line as first line ("Subject: ..."). Reference the brand's post or content specifically. Pitch marketing services relevant to what they need. No filler phrases. No "hope you are doing well". 3-4 short paragraphs max.
- Both must feel personalised, not templated.
- If brand info is sparse, write generic but still specific to their niche and what was in the post."""


def generate_outreach(brand_data: dict, snippets: str = "", api_key: str | None = None) -> dict:
    """Generate LinkedIn outreach msg (≤400 chars) and cold email via Gemini.

    Returns:
        {"linkedin_msg": str, "email": str}
    """
    if api_key is None:
        from config import CONFIG
        api_key = CONFIG["gemini_api_key"]

    profile = brand_data.get("profile") or {}
    snippets_block = f"Web search results:\n{snippets[:800]}" if snippets else ""
    prompt = _PROMPT_TEMPLATE.format(
        brand_name=brand_data.get("brand_name") or "Unknown Brand",
        handle=brand_data.get("handle") or "",
        niche=brand_data.get("niche") or "Unknown",
        followers=profile.get("followers") or brand_data.get("followers") or "Unknown",
        bio=profile.get("bio") or "",
        post_data=(brand_data.get("post_data") or "")[:500],
        snippets_block=snippets_block,
    )

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=[types.Part.from_text(text=prompt)],
    )
    raw = (response.text or "").strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                payload = {}
        else:
            payload = {}

    linkedin_msg = str(payload.get("linkedin_msg") or "")[:400]
    email = str(payload.get("email") or "")

    logger.info(
        "Outreach generated | linkedin_msg_len=%d | email_len=%d",
        len(linkedin_msg), len(email),
    )
    return {"linkedin_msg": linkedin_msg, "email": email}
