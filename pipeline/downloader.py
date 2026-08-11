"""FR-02 — Image downloader.

Downloads the full-resolution image from Slack using the bot token, saves it
to a local ``/tmp`` directory with a UUID filename, and validates that the
downloaded file really is an image before returning its path.
"""

import io
import os
import re
import shutil
import tempfile
import uuid

import requests
from PIL import Image

from utils.logger import get_logger
from utils.retry import retry

_IG_SHORTCODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

logger = get_logger("pipeline.downloader")


class DownloadError(Exception):
    """Raised when the image download fails after retries."""


class ValidationError(Exception):
    """Raised when the downloaded file is not a valid image."""


def _file_is_image(file_info: dict) -> bool:
    """Return True when the Slack file object looks like an image."""
    mime = (file_info.get("mimetype") or "").lower()
    filetype = (file_info.get("filetype") or "").lower()
    if mime in ALLOWED_MIME_TYPES:
        return True
    return filetype in {mt.split("/")[1] for mt in ALLOWED_MIME_TYPES}


def _download_url(url: str, token: str) -> bytes:
    """Download raw bytes from a Slack private URL using the bot token."""
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise DownloadError(f"Slack download returned HTTP {response.status_code}")
    return response.content


def _resolve_download_url(file_info: dict) -> str:
    """Pick the best download URL from the Slack file object."""
    url = (
        file_info.get("url_private_download")
        or file_info.get("url_private")
        or file_info.get("permalink")
    )
    if not url:
        raise DownloadError("No download URL in Slack file payload")
    return url


def validate_image_bytes(data: bytes) -> None:
    """Raise ValidationError when ``data`` is not a decodable image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception as exc:
        raise ValidationError(f"Downloaded file is not a valid image: {exc}") from exc


def download_image(file_info: dict, slack_client) -> str:
    """Download the Slack image and return its absolute temp file path.

    Args:
        file_info: Slack file object from the event payload.
        slack_client: authenticated ``slack_sdk.WebClient`` instance (used
            for its ``token`` and for fetching the file URL).

    Returns:
        Absolute path to the downloaded temp file.

    Raises:
        DownloadError: if the download fails after retries or the file is
            not an image by MIME/type.
        ValidationError: if the bytes cannot be decoded as an image.
    """
    if not _file_is_image(file_info):
        raise ValidationError(
            "Unsupported file type: {} ({})".format(
                file_info.get("mimetype"), file_info.get("filetype")
            )
        )

    file_id = file_info.get("id")
    if not file_id:
        raise DownloadError("File payload has no id")

    file_info = _full_file_info(slack_client, file_id, file_info)
    url = _resolve_download_url(file_info)
    token = slack_client.token

    @retry(exceptions=(DownloadError,), tries=3, base_delay=1.0, logger=logger)
    def _attempt() -> bytes:
        return _download_url(url, token)

    data = _attempt()
    validate_image_bytes(data)

    path = os.path.join(tempfile.gettempdir(), "{}.{}".format(uuid.uuid4().hex, file_info.get("filetype") or "img"))
    with open(path, "wb") as fh:
        fh.write(data)

    logger.info("Downloaded image to %s (%d bytes)", path, len(data))
    return path


def _full_file_info(slack_client, file_id: str, fallback: dict) -> dict:
    """Return the complete Slack file object, enriching with ``files.info``."""
    try:
        response = slack_client.files_info(file=file_id)
    except Exception:
        return fallback
    if response.get("ok"):
        merged = dict(fallback)
        merged.update(response.get("file", {}) or {})
        return merged
    return fallback


def download_from_instagram_url(url: str) -> str:
    """Download the first image from an Instagram post URL via instaloader.

    Args:
        url: public Instagram post/reel URL.

    Returns:
        Absolute path to the downloaded temp image file.

    Raises:
        DownloadError: if the shortcode can't be parsed or instaloader fails.
    """
    import tempfile
    import instaloader

    match = _IG_SHORTCODE_RE.search(url)
    if not match:
        raise DownloadError(f"Could not parse Instagram shortcode from URL: {url}")
    shortcode = match.group(1)
    logger.info("Downloading Instagram post | shortcode=%s", shortcode)

    try:
        L = instaloader.Instaloader(quiet=True)
        post = instaloader.Post.from_shortcode(L.context, shortcode)

        # post.url is the direct CDN image URL (thumbnail for videos, full image for photos)
        img_url = post.url
        logger.info("Resolved image URL | shortcode=%s | is_video=%s", shortcode, post.is_video)

        response = requests.get(img_url, timeout=30)
        if response.status_code >= 400:
            raise DownloadError(f"Image download returned HTTP {response.status_code}")

        dst = os.path.join(tempfile.gettempdir(), f"{uuid.uuid4().hex}.jpg")
        with open(dst, "wb") as fh:
            fh.write(response.content)

        logger.info("Downloaded IG post image | path=%s | bytes=%d", dst, len(response.content))
        return dst
    except instaloader.exceptions.InstaloaderException as exc:
        raise DownloadError(f"Instaloader failed for {shortcode}: {exc}") from exc


def cleanup(path: str) -> None:
    """Best-effort removal of a temp image file."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
            logger.info("Removed temp file %s", path)
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning("Failed to remove temp file %s: %s", path, exc)
