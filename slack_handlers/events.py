"""FR-01 / FR-08 — Slack Bolt event listeners.

Listens for ``file_shared`` events in the configured channel, filters for
image attachments from humans, acknowledges in the thread, and runs the
full research pipeline. Every pipeline error is caught and reported back
into the triggering thread — the bot never crashes silently.
"""

import re
import traceback
from concurrent.futures import ThreadPoolExecutor

from slack_bolt import App

_INSTAGRAM_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+")

from pipeline import downloader, instagram_scraper, sheets_writer, vision_extractor, web_researcher
from pipeline.instagram_scraper import PrivateProfileError, ProfileNotFoundError
from pipeline.vision_extractor import ExtractionError
from utils.logger import get_logger

logger = get_logger("slack_handlers.events")

_IMAGE_FILETYPES = {"jpeg", "jpg", "png", "webp", "gif"}

_executor = ThreadPoolExecutor(max_workers=2)

_CONFIDENCE_THRESHOLD = 0.5


def _is_relevant_file(file_info: dict) -> bool:
    """A file is relevant if it is a supported image."""
    filetype = (file_info.get("filetype") or "").lower()
    return filetype in _IMAGE_FILETYPES


def _permalink_for(client, channel: str, ts: str) -> str:
    try:
        result = client.chat_getPermalink(channel=channel, message_ts=ts)
        if result.get("ok"):
            return result.get("permalink") or ""
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to build permalink: %s", exc)
    return ""


def _post_thread(client, channel: str, ts: str, text: str) -> None:
    client.chat_postMessage(channel=channel, thread_ts=ts, text=text)


def _run_pipeline(
    client, channel: str, file_info: dict, message_ts: str, username: str,
    instagram_url: str | None = None,
) -> None:
    """Execute the research pipeline for a shared image and post results."""
    image_path = None
    trigger = f"url={instagram_url}" if instagram_url else f"file_id={file_info.get('id')}"
    logger.info("[PIPELINE] START | trigger=%s | channel=%s | user=%s", trigger, channel, username)
    try:
        _post_thread(client, channel, message_ts, "Processing your post...")

        # STEP 1 — Download image
        logger.info("[STEP 1/5] Downloading image | trigger=%s", trigger)
        if instagram_url:
            image_path = downloader.download_from_instagram_url(instagram_url)
        else:
            image_path = downloader.download_image(file_info, client)
        logger.info("[STEP 1/5] Download complete | path=%s", image_path)

        # STEP 2 — Gemini Vision extraction
        logger.info("[STEP 2/5] Sending image to Gemini Vision | path=%s", image_path)
        brand = vision_extractor.extract_brand(image_path)
        logger.info(
            "[STEP 2/5] Extraction complete | brand=%s | handle=%s | confidence=%s",
            brand.get("brand_name"), brand.get("handle"), brand.get("confidence"),
        )

        # STEP 3 — Instagram profile scrape
        handle = brand.get("handle") or ""
        profile = {}
        if handle:
            logger.info("[STEP 3/5] Scraping Instagram profile | handle=@%s", handle)
            try:
                profile = instagram_scraper.get_profile(handle)
                logger.info(
                    "[STEP 3/5] Scrape complete | followers=%s | verified=%s | private=%s",
                    profile.get("followers"), profile.get("is_verified"), profile.get("is_private"),
                )
            except PrivateProfileError as exc:
                profile = {
                    "full_name": "", "bio": "", "followers": 0, "following": 0,
                    "post_count": 0, "website": None, "is_verified": False, "is_private": True,
                }
                logger.warning("[STEP 3/5] Private profile, continuing | handle=@%s | reason=%s", handle, exc)
            except (ProfileNotFoundError, Exception) as exc:
                logger.warning("[STEP 3/5] Profile scrape failed, continuing | handle=@%s | reason=%s", handle, exc)
        else:
            logger.warning("[STEP 3/5] No handle extracted — skipping Instagram scrape")

        # STEP 4 — Web research
        logger.info("[STEP 4/5] Starting web research | brand=%s | handle=@%s", brand.get("brand_name"), handle)
        research = web_researcher.research_brand(brand.get("brand_name") or "", handle)
        logger.info(
            "[STEP 4/5] Research complete | sources=%d | notes_length=%d",
            len(research.get("sources", [])), len(research.get("research_notes", "")),
        )

        # STEP 5 — Write to Google Sheets
        status = "Review Needed" if (brand.get("confidence") or 0) < _CONFIDENCE_THRESHOLD else "To Contact"
        logger.info("[STEP 5/5] Writing to Google Sheets | status=%s", status)
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
            "source_post_url": _permalink_for(client, channel, message_ts),
            "status": status,
        }

        result = sheets_writer.write_brand(brand_data)
        logger.info(
            "[STEP 5/5] Sheet write complete | action=%s | row=%s | handle=@%s",
            result["action"], result["row_num"], handle,
        )
        logger.info("[PIPELINE] DONE | handle=@%s | row=%s", handle, result["row_num"])
        _post_thread(
            client,
            channel,
            message_ts,
            f"Done - check the Sheet (row {result['row_num']}, {result['action']}).",
        )
    except Exception as exc:  # FR-08: never crash silently
        logger.error(
            "Pipeline failed: %s\n%s",
            exc,
            traceback.format_exc(),
        )
        try:
            _post_thread(client, channel, message_ts, f"Something went wrong: {exc}")
        except Exception as post_exc:  # pragma: no cover - last resort
            logger.error("Failed to post error to Slack: %s", post_exc)
    finally:
        if image_path:
            downloader.cleanup(image_path)


def _handle_file_shared(client, event: dict, body: dict, logger_) -> None:
    """Entry point for a ``file_shared`` event."""
    channel = event.get("channel_id") or event.get("channel")
    if not channel:
        logger_.info("Ignoring file_shared without channel")
        return
    if event.get("user_id") in (None, "USLACKBOT") or (event.get("user") or "").startswith("B"):
        logger_.info("Ignoring bot-shared file")
        return

    message_ts = event.get("message_ts") or event.get("ts")
    username = event.get("user_id") or event.get("user") or ""

    try:
        result = client.files_info(file=event.get("file_id"))
    except Exception as exc:
        logger_.warning("files_info failed for file_shared: %s", exc)
        return
    if not result.get("ok"):
        logger_.warning("files_info not ok: %s", result.get("error"))
        return

    file_info = result.get("file", {}) or {}
    _executor.submit(_run_pipeline, client, channel, file_info, message_ts or "", username)


def register_handlers(app: App) -> None:
    """Register all Bolt event listeners on the provided app."""

    @app.event("file_shared")
    def on_file_shared(client, event, body, logger):
        logger.info("Received file_shared event: %s", event.get("file_id"))
        _handle_file_shared(client, event, body, logger)

    @app.event("message")
    def on_message(client, event, body, logger):
        user = event.get("user") or ""
        bot_id = event.get("bot_id")
        channel = event.get("channel")
        if bot_id or user.startswith("B") or user in ("USLACKBOT",):
            return
        message_ts = event.get("ts") or ""

        # Instagram URL trigger
        text = event.get("text") or ""
        urls = _INSTAGRAM_URL_RE.findall(text)
        if urls:
            logger.info("[TRIGGER] Instagram URL(s) detected | count=%d | user=%s | channel=%s", len(urls), user, channel)
        for url in urls:
            logger.info("[TRIGGER] Dispatching pipeline for URL | url=%s", url)
            _executor.submit(_run_pipeline, client, channel, {}, message_ts, user, url)

        # Screenshot / image file trigger (existing behaviour)
        files = event.get("files") or []
        for file_info in files:
            if _is_relevant_file(file_info):
                logger.info("[TRIGGER] Image file detected | file_id=%s | user=%s", file_info.get("id"), user)
                _executor.submit(_run_pipeline, client, channel, file_info, message_ts, user)
