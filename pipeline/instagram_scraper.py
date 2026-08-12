"""FR-04 — Instagram profile scraping via Apify.

Fetches a public Instagram profile using the Apify Instagram Profile Scraper
actor. Same external interface as before — callers see no change.
"""

import os
import time

import requests

from utils.logger import get_logger

logger = get_logger("pipeline.instagram_scraper")

_ACTOR_ID = "apify~instagram-profile-scraper"
_BASE_URL = "https://api.apify.com/v2"
_POLL_INTERVAL = 3
_TIMEOUT = 60


class ProfileNotFoundError(Exception):
    """Raised when the Instagram handle does not exist."""


class PrivateProfileError(Exception):
    """Raised when the profile exists but is private."""


def _api_token() -> str:
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        from config import CONFIG
        token = CONFIG.get("apify_api_token")
    if not token:
        raise RuntimeError("APIFY_API_TOKEN not set")
    return token


def _run_actor(handle: str, token: str) -> dict:
    """Start the Apify actor and poll until done, return first result."""
    run_resp = requests.post(
        f"{_BASE_URL}/acts/{_ACTOR_ID}/runs",
        params={"token": token},
        json={"usernames": [handle]},
        timeout=30,
    )
    run_resp.raise_for_status()
    run_id = run_resp.json()["data"]["id"]
    dataset_id = run_resp.json()["data"]["defaultDatasetId"]
    logger.info("Apify run started | run_id=%s | handle=@%s", run_id, handle)

    # Poll until run finishes
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
            raise RuntimeError(f"Apify run {status} for @{handle}")
    else:
        raise RuntimeError(f"Apify run timed out for @{handle}")

    items_resp = requests.get(
        f"{_BASE_URL}/datasets/{dataset_id}/items",
        params={"token": token, "limit": 1},
        timeout=15,
    )
    items_resp.raise_for_status()
    items = items_resp.json()
    if not items:
        raise ProfileNotFoundError(f"No data returned for @{handle}")
    return items[0]


def get_profile(handle: str, loader=None) -> dict:
    """Fetch and return Instagram profile data for ``handle`` via Apify.

    Args:
        handle: Instagram username without the ``@`` symbol.
        loader: ignored (kept for interface compatibility with old instaloader code).

    Returns:
        A dict with keys: full_name, bio, followers, following, post_count,
        website, is_verified, is_private.

    Raises:
        ProfileNotFoundError: if the handle does not exist or returns no data.
        PrivateProfileError: if the profile is private.
    """
    handle = handle.strip().lstrip("@")
    if not handle:
        raise ProfileNotFoundError("Empty Instagram handle")

    token = _api_token()
    data = _run_actor(handle, token)

    if data.get("private"):
        raise PrivateProfileError(f"Profile is private: @{handle}")
    if not data.get("username"):
        raise ProfileNotFoundError(f"Handle not found: @{handle}")

    return {
        "full_name": data.get("fullName") or "",
        "bio": data.get("biography") or "",
        "followers": int(data.get("followersCount") or 0),
        "following": int(data.get("followsCount") or 0),
        "post_count": int(data.get("postsCount") or 0),
        "website": data.get("externalUrl") or None,
        "is_verified": bool(data.get("verified")),
        "is_private": bool(data.get("private")),
    }
