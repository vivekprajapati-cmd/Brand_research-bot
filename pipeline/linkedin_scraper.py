"""LinkedIn post and profile scraping via Apify playwright-scraper.

Navigates to the exact URL provided — no profile-based guessing.
"""

import os
import re
import time

import requests

from utils.logger import get_logger

logger = get_logger("pipeline.linkedin_scraper")

_POST_ACTOR = "apify~playwright-scraper"
_PROFILE_ACTOR = "apify~playwright-scraper"
_BASE_URL = "https://api.apify.com/v2"
_POLL_INTERVAL = 5
_TIMEOUT = 120


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
    logger.info("Apify run started | run_id=%s | actor=%s", run_id, actor_id)

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

    items = requests.get(
        f"{_BASE_URL}/datasets/{dataset_id}/items",
        params={"token": token, "limit": 1},
        timeout=15,
    ).json()
    return items


_POST_PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page } = context;
    await page.waitForTimeout(4000);

    // Expand truncated post text
    const seeMore = await page.$('button.feed-shared-inline-show-more-text__see-more-less-toggle') ||
                    await page.$('.see-more');
    if (seeMore) {
        await seeMore.click();
        await page.waitForTimeout(1000);
    }

    return await page.evaluate(() => {
        const textEl =
            document.querySelector('.feed-shared-update-v2__description') ||
            document.querySelector('.feed-shared-text') ||
            document.querySelector('.attributed-text-segment-list__content') ||
            document.querySelector('[data-test-id="main-feed-activity-card__commentary"]');

        const authorEl =
            document.querySelector('.feed-shared-actor__name') ||
            document.querySelector('.update-components-actor__name');

        const companyEl =
            document.querySelector('.feed-shared-actor__sub-description') ||
            document.querySelector('.update-components-actor__meta');

        const authorLinkEl =
            document.querySelector('.feed-shared-actor__container-link') ||
            document.querySelector('.update-components-actor__meta-link');

        return {
            text: textEl ? textEl.innerText.trim() : '',
            authorName: authorEl ? authorEl.innerText.trim() : '',
            company: companyEl ? companyEl.innerText.trim() : '',
            authorUrl: authorLinkEl ? authorLinkEl.href : '',
        };
    });
}
"""

_PROFILE_PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page } = context;
    await page.waitForTimeout(4000);
    return await page.evaluate(() => {
        const nameEl =
            document.querySelector('.top-card-layout__title') ||
            document.querySelector('h1.text-heading-xlarge');
        const headlineEl =
            document.querySelector('.top-card-layout__headline') ||
            document.querySelector('.text-body-medium');
        const followersEl =
            document.querySelector('[data-anonymize="followers-count"]') ||
            document.querySelector('.top-card-layout__first-subline');
        const bioEl =
            document.querySelector('[data-anonymize="about-content"]') ||
            document.querySelector('.top-card-layout__card-inner .ph5');

        const followersText = followersEl ? followersEl.innerText : '';
        const followersNum = parseInt(followersText.replace(/[^0-9]/g, '')) || 0;

        return {
            fullName: nameEl ? nameEl.innerText.trim() : '',
            headline: headlineEl ? headlineEl.innerText.trim() : '',
            followersCount: followersNum,
            about: bioEl ? bioEl.innerText.trim().slice(0, 500) : '',
        };
    });
}
"""

# Patterns to derive a profile URL from any LinkedIn URL (used for handle extraction)
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


def scrape_post(url: str) -> dict:
    """Scrape the exact LinkedIn post URL. Returns post text + author info."""
    token = _api_token()
    input_data = {
        "startUrls": [{"url": url}],
        "pageFunction": _POST_PAGE_FUNCTION,
        "proxyConfiguration": {"useApifyProxy": True},
    }
    items = _run_actor(_POST_ACTOR, input_data, token)
    if not items:
        raise LinkedInScrapeError(f"No data returned for {url}")
    item = items[0]
    logger.info(
        "Post scraped | author=%s | text_len=%d",
        item.get("authorName", ""), len(item.get("text") or ""),
    )
    return item


def scrape_profile(profile_url: str) -> dict:
    """Scrape a LinkedIn profile page for follower count and bio. Returns {} on failure."""
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
