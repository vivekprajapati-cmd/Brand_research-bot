"""FR-06 — Google Sheets logging.

Appends one row per brand to the configured Google Sheet, deduplicating by
Instagram handle: if the handle already exists, the existing row is updated
instead of appending a duplicate.
"""

from datetime import datetime, timezone

import gspread
from google.auth.exceptions import RefreshError
from google.oauth2.service_account import Credentials

from utils.logger import get_logger

logger = get_logger("pipeline.sheets_writer")

_COLUMNS = [
    "Timestamp",
    "Platform",
    "Brand Name",
    "Handle",
    "Niche / Category",
    "Post Data",
    "Followers",
    "Following",
    "Total Posts",
    "Website URL",
    "Email",
    "Phone",
    "Bio",
    "Is Verified",
    "LinkedIn Outreach Msg",
    "Outreach Email",
    "Source Post URL",
    "Status",
]

_LAST_COL = "R"  # update if columns change

_DEFAULT_STATUS = "To Contact"


class SheetWriteError(Exception):
    """Raised when the Sheets API call fails after refresh attempts."""


def _fmt_int(value) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return ""


def _row_from_brand(brand_data: dict) -> list:
    """Convert a merged brand dict into a full sheet row (fixed column order)."""
    profile = brand_data.get("profile", {}) or {}
    return [
        datetime.now(timezone.utc).isoformat(),
        brand_data.get("platform") or "Instagram",
        brand_data.get("brand_name") or "",
        (brand_data.get("handle") or "").lstrip("@"),
        brand_data.get("niche") or "",
        brand_data.get("post_data") or "",
        _fmt_int(profile.get("followers")),
        _fmt_int(profile.get("following")),
        _fmt_int(profile.get("post_count")),
        profile.get("website") or brand_data.get("website") or "",
        brand_data.get("email") or "",
        brand_data.get("phone") or "",
        profile.get("bio") or "",
        "TRUE" if profile.get("is_verified") else "FALSE",
        brand_data.get("linkedin_msg") or "",
        brand_data.get("outreach_email") or "",
        brand_data.get("source_post_url") or "",
        brand_data.get("status") or _DEFAULT_STATUS,
    ]


def _client(creds_dict: dict) -> gspread.Client:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)


def _find_existing_row(sheet, handle: str, source_url: str) -> int | None:
    """Return the 1-based row index of a duplicate, or None.

    Matches on source post URL first (exact same post URL = same event),
    then falls back to handle (same brand, different post).
    """
    handle_norm = handle.lstrip("@").lower()
    source_url_norm = (source_url or "").strip().rstrip("/")
    try:
        values = sheet.get_all_values()
    except Exception as exc:
        raise SheetWriteError(f"Failed to read sheet: {exc}") from exc

    # col indices (0-based): Handle=3, Source Post URL=16
    url_col = _COLUMNS.index("Source Post URL")
    handle_col = _COLUMNS.index("Handle")

    url_match = None
    handle_match = None
    for idx, row in enumerate(values, start=1):
        if idx == 1:
            continue
        row_url = (row[url_col] if len(row) > url_col else "").strip().rstrip("/")
        row_handle = (row[handle_col] if len(row) > handle_col else "").lstrip("@").lower()
        if source_url_norm and row_url == source_url_norm:
            url_match = idx
            break  # exact post URL match wins immediately
        if handle_norm and row_handle == handle_norm and handle_match is None:
            handle_match = idx
    return url_match or handle_match


def write_brand(
    brand_data: dict,
    client=None,
    sheet_id: str | None = None,
    tab_name: str | None = None,
) -> dict:
    """Append or update a brand row in the Google Sheet.

    Args:
        brand_data: merged output of all pipeline stages.
        client: optional pre-authenticated ``gspread.Client`` (for testing);
            when None one is created from ``config.CONFIG``.
        sheet_id: Google Sheet ID; defaults to ``config.CONFIG``.
        tab_name: worksheet name; defaults to ``config.CONFIG``.

    Returns:
        A dict with keys: action ("appended" | "updated") and row_num.

    Raises:
        SheetWriteError: if the Sheets API call fails.
    """
    if client is None or sheet_id is None or tab_name is None:
        from config import CONFIG

        if client is None:
            client = _client(CONFIG["google_creds_dict"])
        if sheet_id is None:
            sheet_id = CONFIG["google_sheet_id"]
        if tab_name is None:
            tab_name = CONFIG["google_sheet_tab"]

    try:
        spreadsheet = client.open_by_key(sheet_id)
    except gspread.SpreadsheetNotFound as exc:
        raise SheetWriteError(f"Spreadsheet not found: {sheet_id}") from exc
    except RefreshError as exc:
        raise SheetWriteError(f"Google auth refresh failed: {exc}") from exc

    try:
        sheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(_COLUMNS))
        sheet.append_row(_COLUMNS)
        logger.info("Created worksheet %r with headers", tab_name)

    # Auto-fix headers if stale (e.g. after adding/renaming columns)
    existing_headers = sheet.row_values(1) if sheet.row_count > 0 else []
    if existing_headers != _COLUMNS:
        sheet.update("A1", [_COLUMNS])
        logger.info("Updated worksheet headers to match current schema")

    handle = brand_data.get("handle") or ""
    source_url = brand_data.get("source_post_url") or ""
    row = _row_from_brand(brand_data)

    existing = _find_existing_row(sheet, handle, source_url)
    try:
        if existing is not None:
            sheet.update(f"A{existing}:{_LAST_COL}{existing}", [row])
            action = "updated"
            logger.info("Updated row %d for @%s", existing, handle)
        else:
            sheet.append_row(row)
            action = "appended"
            existing = sheet.row_count
            logger.info("Appended new row for @%s", handle)
    except gspread.exceptions.APIError as exc:
        raise SheetWriteError(f"Sheets API call failed: {exc}") from exc

    return {"action": action, "row_num": existing}
