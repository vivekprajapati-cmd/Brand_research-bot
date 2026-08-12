"""LinkedIn post and profile scraping via Apify harvestapi.

harvestapi~linkedin-profile-posts works without LinkedIn login.
We derive the author profile URL from the post URL, scrape up to 20
recent posts, and match the specific post by its activity ID.
"""

import os
import re
import time

import requests

from utils.logger import get_logger

logger = get_logger("pipeline.linkedin_scraper")

_ACTOR = "harvestapi~linkedin-profile-posts"
_BASE_URL = "https://api.apify.com/v2"
_POLL_INTERVAL = 5
_TIMEOUT = 180


class LinkedInScrapeError(Exception):
    pass


def _api_token() -> str:
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        from config import CONFIG
        token = CONFIG.get("apify_api_token")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN not set")
    return token


def _run_actor(input_data: dict, token: str) -> list:
    run_resp = requests.post(
        f"{_BASE_URL}/acts/{_ACTOR}/runs",
        params={"token": token},
        json=input_data,
        timeout=30,
    )
    run_resp.raise_for_status()
    data = run_resp.json()["data"]
    run_id = data["id"]
    dataset_id = data["defaultDatasetId"]
    logger.info("Apify harvestapi run started | run_id=%s", run_id)

    deadline = time.time() + _TIMEOUT
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        status = requests.get(
            f"{_BASE_URL}/actor-runs/{run_id}",
            params={"token": token},
            timeout=15,
        ).json()["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise LinkedInScrapeError(f"Apify run {status}")
    else:
        raise LinkedInScrapeError("Apify run timed out")

    return requests.get(
        f"{_BASE_URL}/datasets/{dataset_id}/items",
        params={"token": token, "limit": 20},
        timeout=15,
    ).json()


# ── URL helpers ───────────────────────────────────────────────────────────────

_PROFILE_PATTERNS = [
    (re.compile(r"linkedin\.com/(in/[A-Za-z0-9_%-]+)"), "https://www.linkedin.com/{}"),
    (re.compile(r"linkedin\.com/(company/[A-Za-z0-9_%-]+)"), "https://www.linkedin.com/{}"),
    (re.compile(r"linkedin\.com/posts/([A-Za-z0-9_%-]+?)_"), "https://www.linkedin.com/in/{}"),
]


def profile_url_from(url: str) -> str | None:
    for pattern, template in _PROFILE_PATTERNS:
        m = pattern.search(url)
        if m:
            return template.format(m.group(1))
    return None


def _activity_id(url: str) -> str | None:
    """Extract the numeric activity ID from a LinkedIn post URL."""
    m = re.search(r'[-_](\d{15,})', url)
    return m.group(1) if m else None


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_post(item: dict) -> dict:
    author = item.get("author") or {}
    if not isinstance(author, dict):
        author = {}

    author_name = (
        author.get("name") or author.get("fullName")
        or item.get("authorName") or ""
    )
    author_url = (
        author.get("url") or author.get("profileUrl") or author.get("linkedinUrl")
        or item.get("authorUrl") or ""
    )
    followers_raw = (
        author.get("followersCount") or author.get("followers")
        or author.get("followerCount") or item.get("followersCount") or 0
    )
    try:
        followers = int(str(followers_raw).replace(",", ""))
    except (TypeError, ValueError):
        followers = 0

    bio = (
        author.get("headline") or author.get("subtitle") or author.get("tagline")
        or item.get("authorHeadline") or ""
    )
    text = (
        item.get("content") or item.get("text") or item.get("commentary")
        or item.get("postContent") or item.get("fullText") or item.get("description") or ""
    )
    website = author.get("website") or ""

    return {
        "authorName": author_name,
        "authorUrl": author_url,
        "company": bio,
        "text": text,
        "followersCount": followers,
        "website": website,
        "_itemId": str(item.get("id") or item.get("entityId") or ""),
        "_itemUrl": item.get("linkedinUrl") or item.get("shareLinkedinUrl") or "",
    }


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_post(url: str) -> dict:
    """Scrape a LinkedIn post URL. Matches the specific post by activity ID."""
    token = _api_token()
    profile_url = profile_url_from(url)
    if not profile_url:
        raise LinkedInScrapeError(
            f"Cannot derive a LinkedIn profile URL from: {url}. "
            "Supported formats: /in/handle, /company/name, /posts/handle_id."
        )

    logger.info("Fetching posts for profile | profile_url=%s", profile_url)
    items = _run_actor({"targetUrls": [profile_url], "maxPosts": 20}, token)
    if not items:
        raise LinkedInScrapeError(f"No posts returned for {profile_url}")

    target = _activity_id(url)
    matched = None
    if target:
        for item in items:
            item_id = str(item.get("id") or item.get("entityId") or "")
            item_url = item.get("linkedinUrl") or item.get("shareLinkedinUrl") or ""
            if target in item_id or target in item_url:
                matched = item
                logger.info("Matched post by activity ID %s", target)
                break

    if matched is None:
        matched = items[0]
        logger.warning("No activity ID match for %s — using most recent post", target)

    parsed = _parse_post(matched)
    logger.info(
        "Post scraped | author=%s | followers=%d | text_len=%d",
        parsed["authorName"], parsed["followersCount"], len(parsed["text"]),
    )
    return parsed


def scrape_profile(profile_url: str) -> dict:
    """Unused — profile data is returned inline with posts by harvestapi."""
    return {}
