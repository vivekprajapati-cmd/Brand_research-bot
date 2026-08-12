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
_LINKEDIN_URL_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/(?:posts|company|in|feed/update)/[A-Za-z0-9_%:-]+")

from pipeline import downloader, instagram_scraper, linkedin_scraper, sheets_writer, vision_extractor, web_researcher
from pipeline.instagram_scraper import PrivateProfileError, ProfileNotFoundError
from pipeline.linkedin_scraper import LinkedInScrapeError
from pipeline.vision_extractor import ExtractionError
from utils.logger import get_logger

logger = get_logger("slack_handlers.events")

_IMAGE_FILETYPES = {"jpeg", "jpg", "png", "webp", "gif"}


def _friendly_error(exc: Exception) -> str:
    msg = str(exc)
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
        return "Gemini AI hit its daily limit. Try again tomorrow or ask Darshit to upgrade the API plan."
    if "404" in msg and "model" in msg.lower():
        return "Gemini model not found. The AI model name may be outdated — check with the tech team."
    if "Instaloader" in msg or "instaloader" in msg:
        return "Couldn't download the Instagram post. It may be private, deleted, or Instagram is blocking us temporarily."
    if "InstaloaderException" in msg or "shortcode" in msg.lower():
        return "Invalid Instagram URL. Make sure you're sending a post link (instagram.com/p/... or /reel/...)."
    if "Worksheet not found" in msg:
        return "Google Sheet tab 'Brand Research' not found. Check that the tab exists and is named correctly."
    if "Spreadsheet not found" in msg:
        return "Can't access the Google Sheet. Check that the sheet ID is correct in settings."
    if "auth refresh" in msg.lower() or "RefreshError" in msg:
        return "Google authentication expired. The service account credentials may need to be renewed."
    if "APIFY_API_TOKEN" in msg:
        return "Apify API token is missing. Add APIFY_API_TOKEN to the environment variables."
    if "Apify run FAILED" in msg or "Apify run ABORTED" in msg:
        return "The Apify scraper failed. LinkedIn or Instagram may have blocked the request — try again in a few minutes."
    if "timed out" in msg.lower() or "TimeoutError" in msg or "timeout" in msg.lower():
        return "The request timed out. The platform may be slow right now — try again shortly."
    if "No post data" in msg:
        return "Couldn't find any data for this post. It may be private or the URL may be incorrect."
    if "No image found" in msg or "not a valid image" in msg.lower():
        return "The file doesn't look like a valid image. Try sending a JPG or PNG screenshot."
    if "No brand name or handle" in msg:
        return "Couldn't identify a brand in this post. The image may not contain clear brand info."
    if "403" in msg or "permission" in msg.lower():
        return "Permission denied on Google Sheets. Make sure the service account has Editor access to the sheet."
    return f"Something went wrong: {msg[:200]}"

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
        ig_owner = None
        if instagram_url:
            image_path, ig_owner = downloader.download_from_instagram_url(instagram_url)
        else:
            image_path = downloader.download_image(file_info, client)
        logger.info("[STEP 1/5] Download complete | path=%s | ig_owner=@%s", image_path, ig_owner)

        # STEP 2 — Gemini Vision extraction
        logger.info("[STEP 2/5] Sending image to Gemini Vision | path=%s", image_path)
        brand = vision_extractor.extract_brand(image_path)
        logger.info(
            "[STEP 2/5] Extraction complete | brand=%s | handle=%s | confidence=%s",
            brand.get("brand_name"), brand.get("handle"), brand.get("confidence"),
        )

        # STEP 3 — Instagram profile scrape
        # Prefer handle from instaloader (authoritative) over Gemini's OCR guess
        handle = ig_owner or brand.get("handle") or ""
        if ig_owner:
            brand["handle"] = brand.get("handle") or ig_owner
            brand["brand_name"] = brand.get("brand_name") or ig_owner
            logger.info("[STEP 3/5] Using instaloader owner as fallback | handle=@%s", ig_owner)
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
        post_data = brand.get("post_content") or ""
        platform = "Instagram" if instagram_url else "Screenshot"
        brand_data = {
            "platform": platform,
            "brand_name": brand.get("brand_name"),
            "handle": handle,
            "niche": brand.get("niche"),
            "post_data": post_data,
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
        if result["action"] == "updated":
            msg = f"Already tracked — updated row {result['row_num']} for @{handle}."
        else:
            msg = f"Done — added to Sheet (row {result['row_num']}) for @{handle}."
        _post_thread(client, channel, message_ts, msg)
    except Exception as exc:  # FR-08: never crash silently
        logger.error(
            "Pipeline failed: %s\n%s",
            exc,
            traceback.format_exc(),
        )
        try:
            _post_thread(client, channel, message_ts, _friendly_error(exc))
        except Exception as post_exc:  # pragma: no cover - last resort
            logger.error("Failed to post error to Slack: %s", post_exc)
    finally:
        if image_path:
            downloader.cleanup(image_path)


def _run_linkedin_pipeline(
    client, channel: str, message_ts: str, username: str, linkedin_url: str,
) -> None:
    """Execute research pipeline for a LinkedIn post URL."""
    logger.info("[PIPELINE] START | platform=linkedin | url=%s | channel=%s | user=%s", linkedin_url, channel, username)
    try:
        _post_thread(client, channel, message_ts, "Processing LinkedIn post...")

        # STEP 1 — Scrape LinkedIn post via Apify
        logger.info("[STEP 1/4] Scraping LinkedIn post | url=%s", linkedin_url)
        post = linkedin_scraper.scrape_post(linkedin_url)
        author_name = post.get("authorName") or post.get("author") or ""
        company_name = post.get("companyName") or post.get("company") or author_name
        post_text = post.get("text") or post.get("content") or ""
        author_url = post.get("authorUrl") or post.get("profileUrl") or ""
        logger.info("[STEP 1/4] Post scraped | author=%s | text_length=%d", author_name, len(post_text))

        # STEP 2 — Scrape LinkedIn profile via Apify
        profile = {}
        if author_url:
            logger.info("[STEP 2/4] Scraping LinkedIn profile | url=%s", author_url)
            raw = linkedin_scraper.scrape_profile(author_url)
            if raw:
                profile = {
                    "full_name": raw.get("fullName") or author_name,
                    "bio": raw.get("about") or raw.get("headline") or "",
                    "followers": int(raw.get("followersCount") or 0),
                    "following": 0,
                    "post_count": 0,
                    "website": raw.get("website") or None,
                    "is_verified": False,
                    "is_private": False,
                }
                logger.info("[STEP 2/4] Profile scraped | followers=%s", profile.get("followers"))

        # STEP 3 — Web research
        logger.info("[STEP 3/4] Starting web research | brand=%s", company_name)
        research = web_researcher.research_brand(company_name, author_url)
        logger.info("[STEP 3/4] Research complete | sources=%d", len(research.get("sources", [])))

        # STEP 4 — Write to Sheets
        handle = author_url.rstrip("/").split("/")[-1] if author_url else company_name
        brand_data = {
            "platform": "LinkedIn",
            "brand_name": company_name,
            "handle": handle,
            "niche": post.get("industry") or "",
            "post_data": post_text,
            "email": None,
            "phone": None,
            "website": profile.get("website"),
            "profile": profile,
            "research_notes": research.get("research_notes", ""),
            "sources": research.get("sources", []),
            "source_post_url": linkedin_url,
            "status": "To Contact",
        }
        logger.info("[STEP 4/4] Writing to Google Sheets")
        result = sheets_writer.write_brand(brand_data)
        logger.info("[PIPELINE] DONE | platform=linkedin | brand=%s | row=%s", company_name, result["row_num"])

        if result["action"] == "updated":
            msg = f"Already tracked — updated row {result['row_num']} for {company_name} (LinkedIn)."
        else:
            msg = f"Done — added to Sheet (row {result['row_num']}) for {company_name} (LinkedIn)."
        _post_thread(client, channel, message_ts, msg)

    except Exception as exc:
        logger.error("LinkedIn pipeline failed: %s\n%s", exc, traceback.format_exc())
        try:
            _post_thread(client, channel, message_ts, _friendly_error(exc))
        except Exception as post_exc:
            logger.error("Failed to post error to Slack: %s", post_exc)


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

        text = event.get("text") or ""

        # Instagram URL trigger
        ig_urls = _INSTAGRAM_URL_RE.findall(text)
        if ig_urls:
            logger.info("[TRIGGER] Instagram URL(s) detected | count=%d | user=%s", len(ig_urls), user)
        for url in ig_urls:
            _executor.submit(_run_pipeline, client, channel, {}, message_ts, user, url)

        # LinkedIn URL trigger
        li_urls = _LINKEDIN_URL_RE.findall(text)
        if li_urls:
            logger.info("[TRIGGER] LinkedIn URL(s) detected | count=%d | user=%s", len(li_urls), user)
        for url in li_urls:
            _executor.submit(_run_linkedin_pipeline, client, channel, message_ts, user, url)

        # Screenshot / image file trigger (existing behaviour)
        files = event.get("files") or []
        for file_info in files:
            if _is_relevant_file(file_info):
                logger.info("[TRIGGER] Image file detected | file_id=%s | user=%s", file_info.get("id"), user)
                _executor.submit(_run_pipeline, client, channel, file_info, message_ts, user)
