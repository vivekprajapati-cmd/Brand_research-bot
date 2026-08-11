"""Entry point — wires FastAPI and Slack Bolt together.

FastAPI is the outer web server (owns ``GET /health``), Slack Bolt runs
inside it on ``POST /slack/events`` via the official SlackRequestHandler
adapter. In production this is launched with::

    uvicorn main:api --host 0.0.0.0 --port $PORT
"""

from fastapi import FastAPI, Request
from pydantic import BaseModel
from slack_bolt import App
from slack_bolt.adapter.fastapi import SlackRequestHandler

from config import CONFIG, ConfigError
from pipeline import downloader, instagram_scraper, sheets_writer, vision_extractor, web_researcher
from pipeline.instagram_scraper import PrivateProfileError, ProfileNotFoundError
from slack_handlers.events import register_handlers
from utils.logger import get_logger

logger = get_logger("main")

try:
    _config = CONFIG
except ConfigError as exc:
    logger.error("Invalid configuration: %s", exc)
    raise

bolt_app = App(
    token=_config["slack_bot_token"],
    signing_secret=_config["slack_signing_secret"],
    token_verification_enabled=False,
)
register_handlers(bolt_app)

handler = SlackRequestHandler(bolt_app)
api = FastAPI(title="Brand Research Bot", version="1.0.0")


@api.post("/slack/events")
async def slack_events(req: Request):
    """Slack events endpoint — handed over to Slack Bolt."""
    return await handler.handle(req)


@api.get("/health")
async def health():
    """Native FastAPI keepalive endpoint for cron-job.org (FR-07)."""
    return {"status": "ok"}


class TestRequest(BaseModel):
    instagram_url: str


_CONFIDENCE_THRESHOLD = 0.5


@api.post("/test")
async def test_pipeline(body: TestRequest):
    """Local-only test endpoint — run the full pipeline from an Instagram URL.

    Example:
        curl -X POST http://localhost:8080/test \\
             -H "Content-Type: application/json" \\
             -d '{"instagram_url": "https://www.instagram.com/p/SHORTCODE/"}'
    """
    url = body.instagram_url
    image_path = None
    logger.info("[TEST] Pipeline triggered | url=%s", url)
    try:
        image_path = downloader.download_from_instagram_url(url)

        brand = vision_extractor.extract_brand(image_path)
        logger.info("[TEST] Brand extracted | brand=%s | handle=%s | confidence=%s",
                    brand.get("brand_name"), brand.get("handle"), brand.get("confidence"))

        handle = brand.get("handle") or ""
        profile = {}
        if handle:
            try:
                profile = instagram_scraper.get_profile(handle)
            except PrivateProfileError:
                profile = {"is_private": True}
            except ProfileNotFoundError:
                pass

        research = web_researcher.research_brand(brand.get("brand_name") or "", handle)

        status = "Review Needed" if (brand.get("confidence") or 0) < _CONFIDENCE_THRESHOLD else "To Contact"
        brand_data = {
            "brand_name": brand.get("brand_name"),
            "handle": handle,
            "niche": brand.get("niche"),
            "tagline": brand.get("tagline"),
            "email": brand.get("email"),
            "phone": brand.get("phone"),
            "website": brand.get("website"),
            "profile": profile,
            "research_notes": research.get("research_notes", ""),
            "sources": research.get("sources", []),
            "source_post_url": url,
            "status": status,
        }

        result = sheets_writer.write_brand(brand_data)
        logger.info("[TEST] Done | action=%s | row=%s", result["action"], result["row_num"])
        return {"status": "ok", "action": result["action"], "row": result["row_num"], "brand": brand}

    except Exception as exc:
        logger.error("[TEST] Pipeline failed | reason=%s", exc)
        return {"status": "error", "reason": str(exc)}
    finally:
        if image_path:
            downloader.cleanup(image_path)
