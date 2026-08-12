"""LinkedIn post and profile scraping via Apify.

Uses harvestapi~linkedin-profile-posts — no LinkedIn cookies needed.
Input: LinkedIn profile/company URL. Returns posts + author info.
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
_TIMEOUT = 120


class LinkedInScrapeError(Exception):
    """Raised when Apify LinkedIn scrape fails."""


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
    logger.info("Apify LinkedIn run started | run_id=%s", run_id)

    deadline = time.time() + _TIMEOUT
    while time.time() < deadline:
        time.sleep(_POLL_INTERVAL)
        status_resp = requests.get(
            f"{_BASE_URL}/actor-runs/{run_id}",
            params={"token": token},
            timeout=15,
        )
        status_resp.raise_for_status()
        status = status_resp.json()["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise LinkedInScrapeError(f"Apify run {status}")
    else:
        raise LinkedInScrapeError("Apify run timed out")

    items_resp = requests.get(
        f"{_BASE_URL}/datasets/{dataset_id}/items",
        params={"token": token, "limit": 5},
        timeout=15,
    )
    items_resp.raise_for_status()
    return items_resp.json()


# Patterns ordered most-specific first
_PROFILE_PATTERNS = [
    # /in/handle or /company/name → already a profile URL
    (re.compile(r"linkedin\.com/(in/[A-Za-z0-9_%-]+)"), "https://www.linkedin.com/{}"),
    (re.compile(r"linkedin\.com/(company/[A-Za-z0-9_%-]+)"), "https://www.linkedin.com/{}"),
    # /posts/handle_activityid → extract handle, build /in/ URL
    (re.compile(r"linkedin\.com/posts/([A-Za-z0-9_%-]+?)_"), "https://www.linkedin.com/in/{}"),
]


def profile_url_from(url: str) -> str | None:
    """Derive a LinkedIn profile URL from any LinkedIn URL we might receive."""
    for pattern, template in _PROFILE_PATTERNS:
        m = pattern.search(url)
        if m:
            return template.format(m.group(1))
    return None


def _parse_post(item: dict) -> dict:
    """Normalise one harvestapi result item into our internal shape."""
    author = item.get("author") or {}
    # harvestapi nests author data; field names vary across actor versions
    author_name = (
        author.get("name") or author.get("fullName")
        or item.get("authorName") or item.get("actorName") or ""
    )
    author_url = (
        author.get("url") or author.get("profileUrl")
        or item.get("authorUrl") or item.get("actorUrl") or ""
    )
    followers = (
        author.get("followersCount") or author.get("followers")
        or item.get("followersCount") or 0
    )
    try:
        followers = int(followers)
    except (TypeError, ValueError):
        followers = 0

    company = (
        author.get("headline") or author.get("subtitle")
        or item.get("authorHeadline") or item.get("actorSubLine") or author_name
    )
    text = (
        item.get("text") or item.get("content") or item.get("commentary")
        or item.get("postContent") or item.get("fullText") or item.get("rawText")
        or item.get("description") or item.get("body") or item.get("article") or ""
    )
    website = author.get("website") or author.get("url") or ""

    return {
        "authorName": author_name,
        "authorUrl": author_url,
        "company": company,
        "text": text,
        "followersCount": followers,
        "website": website,
    }


def scrape_post(url: str) -> dict:
    """Scrape a LinkedIn post/profile URL. Returns normalised post + author data.

    Derives the author profile URL, then fetches their recent posts via
    harvestapi~linkedin-profile-posts (no login required).
    """
    token = _api_token()
    profile_url = profile_url_from(url)
    if not profile_url:
        raise LinkedInScrapeError(
            f"Cannot derive a LinkedIn profile URL from: {url}. "
            "Supported formats: /in/handle, /company/name, /posts/handle_id."
        )

    logger.info("Scraping LinkedIn profile posts | profile_url=%s", profile_url)
    items = _run_actor({"targetUrls": [profile_url], "maxPosts": 3}, token)
    if not items:
        raise LinkedInScrapeError(f"No posts returned for {profile_url}")

    raw = items[0]
    logger.info("Harvestapi raw item keys: %s", list(raw.keys()))
    for k, v in raw.items():
        if isinstance(v, str):
            logger.info("  [%s] = %r", k, v[:200])
    return _parse_post(raw)


def scrape_profile(profile_url: str) -> dict:
    """Return profile data for a LinkedIn URL.

    Re-uses scrape_post since harvestapi returns author info alongside posts.
    Returns empty dict on failure so the pipeline continues.
    """
    try:
        post = scrape_post(profile_url)
        return {
            "fullName": post.get("authorName", ""),
            "headline": post.get("company", ""),
            "followersCount": post.get("followersCount", 0),
            "website": post.get("website", ""),
            "about": "",
        }
    except Exception as exc:
        logger.warning("LinkedIn profile scrape failed: %s", exc)
        return {}
