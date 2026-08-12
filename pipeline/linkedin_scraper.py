"""LinkedIn post and profile scraping via Apify.

Uses two Apify actors:
- Post scraper: extracts post text, author, company
- Profile scraper: extracts followers, bio, website

Actor IDs may need updating if Apify deprecates them — check
https://apify.com/store and search "linkedin post scraper".
"""

import os
import time

import requests

from utils.logger import get_logger

logger = get_logger("pipeline.linkedin_scraper")

# Verify these actor IDs in your Apify store if they stop working
_POST_ACTOR = "apify~playwright-scraper"
_PROFILE_ACTOR = "apify~playwright-scraper"
_BASE_URL = "https://api.apify.com/v2"
_POLL_INTERVAL = 3
_TIMEOUT = 90


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


def _run_actor(actor_id: str, input_data: dict, token: str) -> list:
    run_resp = requests.post(
        f"{_BASE_URL}/acts/{actor_id}/runs",
        params={"token": token},
        json=input_data,
        timeout=30,
    )
    run_resp.raise_for_status()
    data = run_resp.json()["data"]
    run_id = data["id"]
    dataset_id = data["defaultDatasetId"]
    logger.info("Apify LinkedIn run started | run_id=%s | actor=%s", run_id, actor_id)

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
            raise LinkedInScrapeError(f"Apify run {status} for {actor_id}")
    else:
        raise LinkedInScrapeError(f"Apify run timed out for {actor_id}")

    items_resp = requests.get(
        f"{_BASE_URL}/datasets/{dataset_id}/items",
        params={"token": token, "limit": 1},
        timeout=15,
    )
    items_resp.raise_for_status()
    return items_resp.json()


_POST_PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page } = context;
    await page.waitForTimeout(3000);
    return await page.evaluate(() => {
        const post = document.querySelector('.feed-shared-text') ||
                     document.querySelector('.attributed-text-segment-list__content') ||
                     document.querySelector('[data-test-id="main-feed-activity-card__commentary"]');
        const author = document.querySelector('.feed-shared-actor__name') ||
                       document.querySelector('.update-components-actor__name');
        const company = document.querySelector('.feed-shared-actor__sub-description') ||
                        document.querySelector('.update-components-actor__meta');
        const authorLink = document.querySelector('.feed-shared-actor__container-link') ||
                           document.querySelector('.update-components-actor__meta-link');
        return {
            text: post ? post.innerText.trim() : document.title,
            authorName: author ? author.innerText.trim() : '',
            company: company ? company.innerText.trim() : '',
            authorUrl: authorLink ? authorLink.href : '',
        };
    });
}
"""

_PROFILE_PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page } = context;
    await page.waitForTimeout(3000);
    return await page.evaluate(() => {
        const name = document.querySelector('.top-card-layout__title') ||
                     document.querySelector('h1.text-heading-xlarge');
        const headline = document.querySelector('.top-card-layout__headline') ||
                         document.querySelector('.text-body-medium');
        const followers = document.querySelector('.top-card-layout__first-subline') ||
                          document.querySelector('[data-anonymize="followers-count"]') ||
                          document.querySelector('.ph5 span.t-bold');
        const website = document.querySelector('a[data-control-name="contact_see_more"]') ||
                        document.querySelector('.top-card--clickable');
        const bio = document.querySelector('.top-card-layout__card-inner .ph5') ||
                    document.querySelector('[data-anonymize="about-content"]');
        const followersText = followers ? followers.innerText : '';
        const followersNum = parseInt(followersText.replace(/[^0-9]/g, '')) || 0;
        return {
            fullName: name ? name.innerText.trim() : '',
            headline: headline ? headline.innerText.trim() : '',
            followersCount: followersNum,
            website: website ? website.href : '',
            about: bio ? bio.innerText.trim().slice(0, 500) : '',
        };
    });
}
"""


def scrape_post(url: str) -> dict:
    """Scrape a LinkedIn post URL via Apify Playwright. Returns structured post data."""
    token = _api_token()
    input_data = {
        "startUrls": [{"url": url}],
        "pageFunction": _POST_PAGE_FUNCTION,
        "proxyConfiguration": {"useApifyProxy": True},
    }
    items = _run_actor(_POST_ACTOR, input_data, token)
    if not items:
        raise LinkedInScrapeError(f"No post data returned for {url}")
    return items[0]


def scrape_profile(profile_url: str) -> dict:
    """Scrape a LinkedIn profile/company URL. Returns {} on failure."""
    token = _api_token()
    try:
        input_data = {
            "startUrls": [{"url": profile_url}],
            "pageFunction": _PROFILE_PAGE_FUNCTION,
            "proxyConfiguration": {"useApifyProxy": True},
        }
        items = _run_actor(_PROFILE_ACTOR, input_data, token)
        return items[0] if items else {}
    except Exception as exc:
        logger.warning("LinkedIn profile scrape failed: %s", exc)
        return {}
