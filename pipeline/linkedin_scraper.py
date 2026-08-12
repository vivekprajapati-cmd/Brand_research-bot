"""LinkedIn scraping.

Post text  → apify~playwright-scraper on the exact post URL
Other data → harvestapi~linkedin-profile-posts on the author profile
"""

import os
import re
import time

import requests

from utils.logger import get_logger

logger = get_logger("pipeline.linkedin_scraper")

_BASE_URL = "https://api.apify.com/v2"
_POST_ACTOR = "apify~playwright-scraper"
_PROFILE_ACTOR = "harvestapi~linkedin-profile-posts"
_POLL_INTERVAL = 5


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


def _run_actor(actor_id: str, input_data: dict, token: str, timeout: int = 180) -> list:
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
    logger.info("Apify run started | actor=%s | run_id=%s", actor_id, run_id)

    deadline = time.time() + timeout
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
            raise LinkedInScrapeError(f"Apify run {status} | actor={actor_id}")
    else:
        raise LinkedInScrapeError(f"Apify run timed out | actor={actor_id}")

    return requests.get(
        f"{_BASE_URL}/datasets/{dataset_id}/items",
        params={"token": token, "limit": 5},
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


# ── Playwright page function (exact post text) ────────────────────────────────

_POST_PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page } = context;

    // Grab text early before login modal covers it
    await page.waitForTimeout(2000);

    // Dismiss any sign-in modal
    try {
        const dismiss = await page.$('button[data-tracking-control-name="guest_homepage-basic_sign-in-modal__dismiss"]') ||
                        await page.$('button.contextual-sign-in-modal__modal-dismiss') ||
                        await page.$('button[aria-label="Dismiss"]');
        if (dismiss) await dismiss.click();
        await page.waitForTimeout(500);
    } catch(e) {}

    // Expand "see more"
    try {
        const seeMore = await page.$('button.feed-shared-inline-show-more-text__see-more-less-toggle') ||
                        await page.$('button.see-more');
        if (seeMore) { await seeMore.click(); await page.waitForTimeout(500); }
    } catch(e) {}

    return await page.evaluate(() => {
        const sel = [
            '.feed-shared-update-v2__description',
            '.feed-shared-text',
            '.attributed-text-segment-list__content',
            '[data-test-id="main-feed-activity-card__commentary"]',
        ];
        let text = '';
        for (const s of sel) {
            const el = document.querySelector(s);
            if (el && el.innerText.trim().length > 5) { text = el.innerText.trim(); break; }
        }

        const authorEl = document.querySelector('.feed-shared-actor__name') ||
                         document.querySelector('.update-components-actor__name');
        const authorLinkEl = document.querySelector('.feed-shared-actor__container-link') ||
                             document.querySelector('.update-components-actor__meta-link');
        const companyEl = document.querySelector('.feed-shared-actor__sub-description') ||
                          document.querySelector('.update-components-actor__meta');

        return {
            text,
            authorName: authorEl ? authorEl.innerText.trim() : '',
            authorUrl:  authorLinkEl ? authorLinkEl.href : '',
            company:    companyEl ? companyEl.innerText.trim() : '',
        };
    });
}
"""


# ── Public API ────────────────────────────────────────────────────────────────

def scrape_post(url: str) -> dict:
    """Scrape exact post text via playwright. Returns {text, authorName, authorUrl, company}."""
    token = _api_token()
    items = _run_actor(_POST_ACTOR, {
        "startUrls": [{"url": url}],
        "pageFunction": _POST_PAGE_FUNCTION,
        "proxyConfiguration": {"useApifyProxy": True},
    }, token, timeout=180)
    if not items:
        raise LinkedInScrapeError(f"No data returned for {url}")
    item = items[0]
    logger.info("Post scraped | author=%s | text_len=%d", item.get("authorName", ""), len(item.get("text") or ""))
    return item


def scrape_profile(profile_url: str) -> dict:
    """Get author details via harvestapi. Returns {followersCount, headline, website}. Never raises."""
    token = _api_token()
    try:
        items = _run_actor(_PROFILE_ACTOR, {
            "targetUrls": [profile_url],
            "maxPosts": 1,
        }, token, timeout=120)
        if not items:
            return {}
        item = items[0]
        author = item.get("author") or {}
        if not isinstance(author, dict):
            author = {}
        followers_raw = (
            author.get("followersCount") or author.get("followers")
            or item.get("followersCount") or 0
        )
        try:
            followers = int(str(followers_raw).replace(",", ""))
        except (TypeError, ValueError):
            followers = 0
        return {
            "followersCount": followers,
            "headline": author.get("headline") or author.get("subtitle") or "",
            "website": author.get("website") or "",
        }
    except Exception as exc:
        logger.warning("Profile scrape failed (non-fatal): %s", exc)
        return {}
