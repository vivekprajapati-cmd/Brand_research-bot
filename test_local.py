"""Quick local pipeline test — no Slack needed.

Usage:
    python test_local.py --url https://www.instagram.com/p/SHORTCODE/
    python test_local.py --image path/to/screenshot.jpg
"""

import argparse
import sys

from pipeline import downloader, instagram_scraper, sheets_writer, vision_extractor, web_researcher
from pipeline.instagram_scraper import PrivateProfileError, ProfileNotFoundError
from utils.logger import get_logger

logger = get_logger("test_local")
_CONFIDENCE_THRESHOLD = 0.5


def run(image_path: str, source: str) -> None:
    logger.info("[PIPELINE] START | source=%s", source)

    logger.info("[STEP 2/5] Sending image to Gemini Vision | path=%s", image_path)
    brand = vision_extractor.extract_brand(image_path)
    logger.info("[STEP 2/5] Done | brand=%s | handle=%s | confidence=%s",
                brand.get("brand_name"), brand.get("handle"), brand.get("confidence"))

    handle = brand.get("handle") or ""
    profile = {}
    if handle:
        logger.info("[STEP 3/5] Scraping Instagram | handle=@%s", handle)
        try:
            profile = instagram_scraper.get_profile(handle)
            logger.info("[STEP 3/5] Done | followers=%s | verified=%s",
                        profile.get("followers"), profile.get("is_verified"))
        except PrivateProfileError:
            logger.warning("[STEP 3/5] Private profile — continuing")
        except ProfileNotFoundError:
            logger.warning("[STEP 3/5] Handle not found — continuing")
    else:
        logger.warning("[STEP 3/5] No handle — skipping Instagram scrape")

    logger.info("[STEP 4/5] Web research | brand=%s", brand.get("brand_name"))
    research = web_researcher.research_brand(brand.get("brand_name") or "", handle)
    logger.info("[STEP 4/5] Done | sources=%d", len(research.get("sources", [])))

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
        "source_post_url": source,
        "status": status,
    }

    logger.info("[STEP 5/5] Writing to Google Sheets | status=%s", status)
    result = sheets_writer.write_brand(brand_data)
    logger.info("[PIPELINE] DONE | action=%s | row=%s", result["action"], result["row_num"])


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Instagram post URL")
    group.add_argument("--image", help="Local image file path")
    args = parser.parse_args()

    image_path = None
    try:
        if args.url:
            logger.info("[STEP 1/5] Downloading from Instagram URL | url=%s", args.url)
            image_path = downloader.download_from_instagram_url(args.url)
            logger.info("[STEP 1/5] Done | path=%s", image_path)
            run(image_path, args.url)
        else:
            logger.info("[STEP 1/5] Using local image | path=%s", args.image)
            run(args.image, args.image)
    except Exception as exc:
        logger.error("[PIPELINE] FAILED | reason=%s", exc)
        sys.exit(1)
    finally:
        if image_path:
            downloader.cleanup(image_path)


if __name__ == "__main__":
    main()
