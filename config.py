"""Configuration loader for the Brand Research Bot.

Loads environment variables, validates required ones, and exposes them as
module-level constants. Fails fast (raises ``ValueError``) when any required
variable is missing.
"""

import base64
import json
import os

from dotenv import load_dotenv

load_dotenv()

_REQUIRED_VARS = (
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_CHANNEL_ID",
    "GEMINI_API_KEY",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
)

_OPTIONAL_DEFAULTS = {
    "GOOGLE_SHEET_TAB": "Brand Research",
    "INSTAGRAM_USERNAME": "",
    "INSTAGRAM_PASSWORD": "",
    "LOG_LEVEL": "INFO",
    "MAX_RETRIES": 3,
    "PORT": 8080,
}


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid."""


def _validate_required() -> None:
    missing = [var for var in _REQUIRED_VARS if not os.getenv(var)]
    if missing:
        raise ConfigError(
            "Missing required environment variables: {}".format(", ".join(missing))
        )


def _validate_optional() -> None:
    if not 1 <= _max_retries() <= 10:
        raise ConfigError("MAX_RETRIES must be an integer between 1 and 10")
    if os.getenv("GOOGLE_SHEET_TAB") is None:
        os.environ["GOOGLE_SHEET_TAB"] = _OPTIONAL_DEFAULTS["GOOGLE_SHEET_TAB"]
    if not os.getenv("LOG_LEVEL"):
        os.environ["LOG_LEVEL"] = _OPTIONAL_DEFAULTS["LOG_LEVEL"]
    _google_creds_dict()  # validate base64 JSON parses correctly


def _max_retries() -> int:
    raw = os.getenv("MAX_RETRIES", _OPTIONAL_DEFAULTS["MAX_RETRIES"])
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _OPTIONAL_DEFAULTS["MAX_RETRIES"]


def _google_creds_dict() -> dict:
    raw = os.getenv("GOOGLE_CREDS_JSON", "")
    try:
        decoded = base64.b64decode(raw, validate=True)
        creds = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ConfigError("GOOGLE_CREDS_JSON must be base64-encoded service account JSON") from exc
    if not isinstance(creds, dict):
        raise ConfigError("GOOGLE_CREDS_JSON must decode to a JSON object")
    return creds


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def load_config() -> dict:
    """Validate the environment and return a flat configuration dict.

    Raises:
        ConfigError: if any required variable is missing or malformed.
    """
    _validate_required()
    _validate_optional()
    return {
        "slack_bot_token": os.getenv("SLACK_BOT_TOKEN"),
        "slack_signing_secret": os.getenv("SLACK_SIGNING_SECRET"),
        "slack_channel_id": os.getenv("SLACK_CHANNEL_ID"),
        "gemini_api_key": os.getenv("GEMINI_API_KEY"),
        "google_sheet_id": os.getenv("GOOGLE_SHEET_ID"),
        "google_sheet_tab": os.getenv("GOOGLE_SHEET_TAB"),
        "google_creds_dict": _google_creds_dict(),
        "instagram_username": os.getenv("INSTAGRAM_USERNAME", "") or None,
        "instagram_password": os.getenv("INSTAGRAM_PASSWORD", "") or None,
        "log_level": os.getenv("LOG_LEVEL", "INFO").upper(),
        "max_retries": _max_retries(),
        "port": _int_env("PORT", _OPTIONAL_DEFAULTS["PORT"]),
    }


# Module-level singleton, populated once at import time.
CONFIG = load_config()
