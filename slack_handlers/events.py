"""FR-01 / FR-08 — Slack Bolt event listeners.

Listens for file_shared events and messages in the configured channel,
detects Instagram/LinkedIn URLs and image uploads, then dispatches to
the pipeline runner. Every pipeline error is caught inside the runner —
the bot never crashes silently.
"""

import re
from concurrent.futures import ThreadPoolExecutor

from slack_bolt import App

from pipeline.runner import run_instagram_pipeline, run_linkedin_pipeline
from utils.logger import get_logger

logger = get_logger("slack_handlers.events")

_INSTAGRAM_URL_RE = re.compile(r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+")
_LINKEDIN_URL_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/(?:posts|company|in|feed/update)/[A-Za-z0-9_%:-]+")
_IMAGE_FILETYPES = {"jpeg", "jpg", "png", "webp", "gif"}

_executor = ThreadPoolExecutor(max_workers=2)


def _is_relevant_file(file_info: dict) -> bool:
    return (file_info.get("filetype") or "").lower() in _IMAGE_FILETYPES


def _handle_file_shared(client, event: dict, body: dict, logger_) -> None:
    channel = event.get("channel_id") or event.get("channel")
    if not channel:
        logger_.info("Ignoring file_shared without channel")
        return
    if event.get("user_id") in (None, "USLACKBOT") or (event.get("user") or "").startswith("B"):
        logger_.info("Ignoring bot-shared file")
        return

    message_ts = event.get("message_ts") or event.get("ts")
    username = event.get("user_id") or event.get("user") or ""

    try:
        result = client.files_info(file=event.get("file_id"))
    except Exception as exc:
        logger_.warning("files_info failed for file_shared: %s", exc)
        return
    if not result.get("ok"):
        logger_.warning("files_info not ok: %s", result.get("error"))
        return

    file_info = result.get("file", {}) or {}
    _executor.submit(run_instagram_pipeline, client, channel, file_info, message_ts or "", username)


def register_handlers(app: App) -> None:
    """Register all Bolt event listeners on the provided app."""

    @app.event("file_shared")
    def on_file_shared(client, event, body, logger):
        logger.info("Received file_shared event: %s", event.get("file_id"))
        _handle_file_shared(client, event, body, logger)

    @app.event("message")
    def on_message(client, event, body, logger):
        user = event.get("user") or ""
        bot_id = event.get("bot_id")
        channel = event.get("channel")
        if bot_id or user.startswith("B") or user in ("USLACKBOT",):
            return
        message_ts = event.get("ts") or ""
        text = event.get("text") or ""

        ig_urls = _INSTAGRAM_URL_RE.findall(text)
        if ig_urls:
            logger.info("[TRIGGER] Instagram URL(s) detected | count=%d | user=%s", len(ig_urls), user)
        for url in ig_urls:
            _executor.submit(run_instagram_pipeline, client, channel, {}, message_ts, user, url)

        li_urls = _LINKEDIN_URL_RE.findall(text)
        if li_urls:
            logger.info("[TRIGGER] LinkedIn URL(s) detected | count=%d | user=%s", len(li_urls), user)
        for url in li_urls:
            _executor.submit(run_linkedin_pipeline, client, channel, message_ts, user, url)

        files = event.get("files") or []
        for file_info in files:
            if _is_relevant_file(file_info):
                logger.info("[TRIGGER] Image file detected | file_id=%s | user=%s", file_info.get("id"), user)
                _executor.submit(run_instagram_pipeline, client, channel, file_info, message_ts, user)
