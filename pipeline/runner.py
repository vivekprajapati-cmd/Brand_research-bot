"""Pipeline orchestrator.

Platform-specific steps (scrape, extract) stay in each flow function.
Shared tail (web research → outreach → sheets → Slack reply) lives in _finalize.
"""

import traceback

from pipeline import (
    downloader, instagram_scraper, linkedin_scraper,
    outreach_writer, sheets_writer, vision_extractor, web_researcher,
)
from pipeline.instagram_scraper import PrivateProfileError, ProfileNotFoundError
from pipeline.linkedin_scraper import LinkedInScrapeError
from pipeline.vision_extractor import ExtractionError
from utils.logger import get_logger

logger = get_logger("pipeline.runner")

_CONFIDENCE_THRESHOLD = 0.5

_IMAGE_FILETYPES = {"jpeg", "jpg", "png", "webp", "gif"}


def _post_thread(client, channel: str, ts: str, text: str) -> None:
    client.chat_postMessage(channel=channel, thread_ts=ts, text=text)


def _permalink_for(client, channel: str, ts: str) -> str:
    try:
        result = client.chat_getPermalink(channel=channel, message_ts=ts)
        if result.get("ok"):
            return result.get("permalink") or ""
    except Exception as exc:
        logger.warning("Failed to build permalink: %s", exc)
    return ""


def friendly_error(exc: Exception) -> str:
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
    if "Cannot derive a LinkedIn profile URL" in msg:
        return "Couldn't identify the LinkedIn author from that URL. Try a /posts/handle or /in/handle link instead of a feed/update URL."
    if "No post data" in msg or "No posts returned" in msg:
        return "Couldn't find any data for this post. It may be private or the URL may be incorrect."
    if "No image found" in msg or "not a valid image" in msg.lower():
        return "The file doesn't look like a valid image. Try sending a JPG or PNG screenshot."
    if "No brand name or handle" in msg:
        return "Couldn't identify a brand in this post. The image may not contain clear brand info."
    if "403" in msg or "permission" in msg.lower():
        return "Permission denied on Google Sheets. Make sure the service account has Editor access to the sheet."
    return f"Something went wrong: {msg[:200]}"


def _finalize(client, channel: str, message_ts: str, brand_data: dict, display_name: str) -> dict:
    """Shared tail: web research → outreach → sheets write → Slack reply."""
    brand_name = brand_data.get("brand_name") or ""
    logger.info("[FINALIZE] Web research + outreach | brand=%s", brand_name)
    snippets = web_researcher.search_brand(brand_name)
    outreach = outreach_writer.generate_outreach(brand_data, snippets)
    brand_data["linkedin_msg"] = outreach.get("linkedin_msg", "")
    brand_data["outreach_email"] = outreach.get("email", "")
    logger.info("[FINALIZE] Outreach done | linkedin_msg_len=%d", len(brand_data["linkedin_msg"]))

    result = sheets_writer.write_brand(brand_data)
    logger.info("[FINALIZE] Sheet write | action=%s | row=%s", result["action"], result["row_num"])

    if result["action"] == "updated":
        msg = f"Already tracked — updated row {result['row_num']} for {display_name}."
    else:
        msg = f"Done — added to Sheet (row {result['row_num']}) for {display_name}."
    _post_thread(client, channel, message_ts, msg)
    return result


def run_instagram_pipeline(
    client, channel: str, file_info: dict, message_ts: str, username: str,
    instagram_url: str | None = None,
) -> None:
    """Instagram URL, screenshot, or Slack image file pipeline."""
    image_path = None
    trigger = f"url={instagram_url}" if instagram_url else f"file_id={file_info.get('id')}"
    logger.info("[PIPELINE] START | trigger=%s | channel=%s | user=%s", trigger, channel, username)
    try:
        _post_thread(client, channel, message_ts, "Processing your post...")

        logger.info("[STEP 1/4] Downloading image | trigger=%s", trigger)
        ig_owner = None
        if instagram_url:
            image_path, ig_owner = downloader.download_from_instagram_url(instagram_url)
        else:
            image_path = downloader.download_image(file_info, client)
        logger.info("[STEP 1/4] Download complete | path=%s | ig_owner=@%s", image_path, ig_owner)

        logger.info("[STEP 2/4] Sending image to Gemini Vision | path=%s", image_path)
        brand = vision_extractor.extract_brand(image_path)
        logger.info(
            "[STEP 2/4] Extraction complete | brand=%s | handle=%s | confidence=%s",
            brand.get("brand_name"), brand.get("handle"), brand.get("confidence"),
        )

        handle = ig_owner or brand.get("handle") or ""
        if ig_owner:
            brand["handle"] = brand.get("handle") or ig_owner
            brand["brand_name"] = brand.get("brand_name") or ig_owner
            logger.info("[STEP 3/4] Using instaloader owner as fallback | handle=@%s", ig_owner)
        profile = {}
        if handle:
            logger.info("[STEP 3/4] Scraping Instagram profile | handle=@%s", handle)
            try:
                profile = instagram_scraper.get_profile(handle)
                logger.info(
                    "[STEP 3/4] Scrape complete | followers=%s | verified=%s | private=%s",
                    profile.get("followers"), profile.get("is_verified"), profile.get("is_private"),
                )
            except PrivateProfileError as exc:
                profile = {
                    "full_name": "", "bio": "", "followers": 0, "following": 0,
                    "post_count": 0, "website": None, "is_verified": False, "is_private": True,
                }
                logger.warning("[STEP 3/4] Private profile, continuing | handle=@%s | reason=%s", handle, exc)
            except (ProfileNotFoundError, Exception) as exc:
                logger.warning("[STEP 3/4] Profile scrape failed, continuing | handle=@%s | reason=%s", handle, exc)
        else:
            logger.warning("[STEP 3/4] No handle extracted — skipping Instagram scrape")

        status = "Review Needed" if (brand.get("confidence") or 0) < _CONFIDENCE_THRESHOLD else "To Contact"
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
            "source_post_url": _permalink_for(client, channel, message_ts),
            "status": status,
        }

        result = _finalize(client, channel, message_ts, brand_data, f"@{handle}")
        logger.info("[PIPELINE] DONE | handle=@%s | row=%s", handle, result["row_num"])

    except Exception as exc:
        logger.error("Pipeline failed: %s\n%s", exc, traceback.format_exc())
        try:
            _post_thread(client, channel, message_ts, friendly_error(exc))
        except Exception as post_exc:
            logger.error("Failed to post error to Slack: %s", post_exc)
    finally:
        if image_path:
            downloader.cleanup(image_path)


def run_linkedin_pipeline(
    client, channel: str, message_ts: str, username: str, linkedin_url: str,
) -> None:
    """LinkedIn post URL pipeline."""
    logger.info("[PIPELINE] START | platform=linkedin | url=%s | channel=%s | user=%s", linkedin_url, channel, username)
    try:
        _post_thread(client, channel, message_ts, "Processing LinkedIn post...")

        logger.info("[STEP 1/4] Scraping LinkedIn post | url=%s", linkedin_url)
        post = linkedin_scraper.scrape_post(linkedin_url)
        author_name = post.get("authorName") or ""
        post_text = post.get("text") or ""
        author_url = post.get("authorUrl") or ""
        logger.info("[STEP 1/4] Post scraped | author=%s | text_len=%d", author_name, len(post_text))

        profile_url = author_url or linkedin_scraper.profile_url_from(linkedin_url) or ""
        profile_data = {}
        if profile_url:
            logger.info("[STEP 2/4] Fetching profile details | url=%s", profile_url)
            profile_data = linkedin_scraper.scrape_profile(profile_url)
            logger.info("[STEP 2/4] Profile | followers=%s", profile_data.get("followersCount"))

        logger.info("[STEP 3/4] Extracting brand fields from post text")
        brand = vision_extractor.extract_brand_from_text(post_text)
        brand_name = brand.get("brand_name") or post.get("company") or author_name
        niche = brand.get("niche") or ""
        handle = author_url.rstrip("/").split("/")[-1] if author_url else ""
        if not handle or handle.startswith("http"):
            derived = linkedin_scraper.profile_url_from(linkedin_url)
            handle = derived.rstrip("/").split("/")[-1] if derived else author_name
        logger.info("[STEP 3/4] Extracted | brand=%s | handle=%s | email=%s", brand_name, handle, brand.get("email"))

        profile = {
            "full_name": author_name,
            "bio": profile_data.get("headline") or post.get("company") or "",
            "followers": int(profile_data.get("followersCount") or 0),
            "following": 0,
            "post_count": 0,
            "website": profile_data.get("website") or brand.get("website"),
            "is_verified": False,
            "is_private": False,
        }

        brand_data = {
            "platform": "LinkedIn",
            "brand_name": brand_name,
            "handle": handle,
            "niche": niche,
            "post_data": post_text,
            "email": brand.get("email"),
            "phone": brand.get("phone"),
            "website": brand.get("website"),
            "profile": profile,
            "source_post_url": linkedin_url,
            "status": "To Contact",
        }

        result = _finalize(client, channel, message_ts, brand_data, f"{brand_name} (LinkedIn)")
        logger.info("[PIPELINE] DONE | platform=linkedin | brand=%s | row=%s", brand_name, result["row_num"])

    except Exception as exc:
        logger.error("LinkedIn pipeline failed: %s\n%s", exc, traceback.format_exc())
        try:
            _post_thread(client, channel, message_ts, friendly_error(exc))
        except Exception as post_exc:
            logger.error("Failed to post error to Slack: %s", post_exc)
