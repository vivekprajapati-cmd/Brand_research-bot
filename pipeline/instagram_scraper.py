"""FR-04 — Instagram profile scraping via Instaloader.

Fetches a public Instagram profile for a given handle and extracts a fixed
set of fields. Private profiles and invalid handles are handled gracefully
by raising specific exceptions so the pipeline can continue.
"""

import random
import time

from instaloader import Instaloader, Profile, ProfileNotExistsException

from utils.logger import get_logger

logger = get_logger("pipeline.instagram_scraper")


class ProfileNotFoundError(Exception):
    """Raised when the Instagram handle does not exist."""


class PrivateProfileError(Exception):
    """Raised when the profile exists but is private."""


def _delay(min_seconds: int = 2, max_seconds: int = 5) -> None:
    """Add a random delay to avoid Instagram rate limits (FR-04)."""
    time.sleep(random.uniform(min_seconds, max_seconds))


def _loader(username: str | None = None, password: str | None = None) -> Instaloader:
    loader = Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        compress_json=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
        max_connection_attempts=1,
    )
    if username and password:
        loader.login(username, password)
    return loader


def _public_profile(loader: Instaloader, handle: str):
    """Return the Profile object, raising the documented exceptions."""
    if not handle:
        raise ProfileNotFoundError("Empty Instagram handle")
    try:
        return Profile.from_username(loader.context, handle)
    except ProfileNotExistsException as exc:
        raise ProfileNotFoundError(f"Handle not found: @{handle}") from exc


def get_profile(handle: str, loader: Instaloader | None = None) -> dict:
    """Fetch and return Instagram profile data for ``handle``.

    Args:
        handle: Instagram username without the ``@`` symbol.
        loader: optional pre-configured Instaloader (for testing); when None
            one is created using the configured optional credentials.

    Returns:
        A dict with keys: full_name, bio, followers, following, post_count,
        website, is_verified, is_private.

    Raises:
        ProfileNotFoundError: if the handle does not exist.
        PrivateProfileError: if the profile exists but is private.
    """
    handle = handle.strip().lstrip("@")
    _delay()

    if loader is None:
        from config import CONFIG

        loader = _loader(CONFIG.get("instagram_username"), CONFIG.get("instagram_password"))

    profile = _public_profile(loader, handle)

    if profile.is_private:
        logger.warning("Profile is private: @%s", handle)
        raise PrivateProfileError(f"Profile is private: @{handle}")

    return {
        "full_name": profile.full_name or "",
        "bio": profile.biography or "",
        "followers": int(profile.followers),
        "following": int(profile.followees),
        "post_count": int(profile.mediacount),
        "website": profile.external_url or None,
        "is_verified": bool(profile.is_verified),
        "is_private": False,
    }
