import gspread
import pytest

from pipeline import sheets_writer
from pipeline.sheets_writer import SheetWriteError


class FakeWorksheet:
    def __init__(self, rows=None):
        self._rows = rows or []
        self.row_count = len(self._rows) + 1

    def get_all_values(self):
        return self._rows

    def append_row(self, row):
        self._rows.append(row)
        self.row_count = len(self._rows) + 1

    def update(self, range_str, values):
        row_num = int(range_str.split("A")[1].split(":")[0])
        while len(self._rows) < row_num:
            self._rows.append([""] * 15)
        self._rows[row_num - 1] = values[0]


class FakeSheet:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def worksheet(self, name):
        if name == "missing":
            raise gspread.WorksheetNotFound("no tab")
        return self._worksheet


class FakeClient:
    def __init__(self, worksheet, raise_not_found=False):
        self._sheet = FakeSheet(worksheet)
        self._raise_not_found = raise_not_found

    def open_by_key(self, sheet_id):
        if self._raise_not_found:
            raise gspread.SpreadsheetNotFound("nope")
        return self._sheet


def _brand_data(handle="glowskincare", **overrides):
    data = {
        "brand_name": "Glow Skincare",
        "handle": handle,
        "niche": "Skincare",
        "tagline": "Glow",
        "email": "hello@glow.com",
        "phone": "+919876543210",
        "website": "https://glow.com",
        "profile": {
            "full_name": "Glow Skincare",
            "bio": "Clean skincare.",
            "followers": 125000,
            "following": 345,
            "post_count": 892,
            "website": "https://glow.com",
            "is_verified": True,
            "is_private": False,
        },
        "research_notes": "Brief notes.",
        "source_post_url": "https://slack.com/permalink",
        "status": "To Contact",
    }
    data.update(overrides)
    return data


def test_new_handle_appends_row():
    ws = FakeWorksheet([["Timestamp", "Brand Name", "Instagram Handle", "Niche / Category",
                         "Followers", "Following", "Total Posts", "Website URL", "Email", "Phone",
                         "Bio", "Is Verified", "Research Notes", "Source Post URL", "Status"]])
    client = FakeClient(ws)
    result = sheets_writer.write_brand(_brand_data(), client=client, sheet_id="s", tab_name="Brand Research")
    assert result["action"] == "appended"
    row = ws._rows[-1]
    assert row[2] == "glowskincare"
    assert row[4] == "125,000"
    assert row[11] == "TRUE"
    assert row[14] == "To Contact"


def test_existing_handle_updates_row():
    header = ["Timestamp", "Brand Name", "Instagram Handle", "Niche / Category",
              "Followers", "Following", "Total Posts", "Website URL", "Email", "Phone",
              "Bio", "Is Verified", "Research Notes", "Source Post URL", "Status"]
    existing = ["2026-01-01T00:00:00+00:00", "Old Name", "glowskincare", "Skincare",
                "10", "5", "3", "", "", "", "", "FALSE", "", "", "To Contact"]
    ws = FakeWorksheet([header, existing])
    client = FakeClient(ws)
    result = sheets_writer.write_brand(_brand_data(), client=client, sheet_id="s", tab_name="Brand Research")
    assert result["action"] == "updated"
    assert result["row_num"] == 2
    assert ws._rows[1][1] == "Glow Skincare"
    assert ws._rows[1][11] == "TRUE"


class FakeResponse:
    def __init__(self, text="rate limited"):
        self.text = text

    def json(self):
        return {"error": {"code": 429, "message": self.text, "status": "RESOURCE_EXHAUSTED"}}


def _api_error(text="rate limited"):
    return gspread.exceptions.APIError(FakeResponse(text))


def test_sheet_write_error_on_api_failure():
    class BoomWorksheet:
        def get_all_values(self):
            return []

        def append_row(self, row):
            raise _api_error()

        def update(self, *a):
            raise _api_error()

    client = FakeClient(BoomWorksheet())
    with pytest.raises(SheetWriteError):
        sheets_writer.write_brand(_brand_data(), client=client, sheet_id="s", tab_name="Brand Research")


def test_spreadsheet_not_found_raises():
    client = FakeClient(FakeWorksheet(), raise_not_found=True)
    with pytest.raises(SheetWriteError):
        sheets_writer.write_brand(_brand_data(), client=client, sheet_id="s", tab_name="Brand Research")


def test_status_default_is_to_contact():
    ws = FakeWorksheet([])
    client = FakeClient(ws)
    data = _brand_data()
    data.pop("status")
    result = sheets_writer.write_brand(data, client=client, sheet_id="s", tab_name="Brand Research")
    row = ws._rows[-1]
    assert row[14] == "To Contact"
    assert result["action"] == "appended"


def test_worksheet_not_found_raises():
    ws = FakeWorksheet([])
    client = FakeClient(ws)
    with pytest.raises(SheetWriteError):
        sheets_writer.write_brand(_brand_data(), client=client, sheet_id="s", tab_name="missing")


def test_row_mapping_for_unverified_empty_profile():
    data = _brand_data()
    data["profile"] = {
        "full_name": "",
        "bio": "",
        "followers": None,
        "following": None,
        "post_count": None,
        "website": None,
        "is_verified": False,
        "is_private": True,
    }
    row = sheets_writer._row_from_brand(data)
    assert row[4] == ""
    assert row[11] == "FALSE"


def test_fmt_int_returns_empty_for_non_int():
    assert sheets_writer._fmt_int("nope") == ""
    assert sheets_writer._fmt_int(None) == ""
    assert sheets_writer._fmt_int(1000) == "1,000"


def test_get_all_values_failure_raises_sheet_write_error():
    class ReadFailWorksheet:
        def get_all_values(self):
            raise gspread.exceptions.APIError(FakeResponse("read failed"))

    class ReadFailSheet:
        def worksheet(self, name):
            return ReadFailWorksheet()

    class ReadFailClient:
        def open_by_key(self, sheet_id):
            return ReadFailSheet()

    with pytest.raises(SheetWriteError):
        sheets_writer.write_brand(
            _brand_data(), client=ReadFailClient(), sheet_id="s", tab_name="Brand Research"
        )


def test_refresh_error_on_open_raises():
    from google.auth.exceptions import RefreshError

    class RefreshFailClient:
        def open_by_key(self, sheet_id):
            raise RefreshError("token expired")

    with pytest.raises(SheetWriteError):
        sheets_writer.write_brand(
            _brand_data(), client=RefreshFailClient(), sheet_id="s", tab_name="Brand Research"
        )


def test_client_built_from_creds(monkeypatch):
    captured = {}

    def fake_from_service_account(info, scopes=None):
        captured["info"] = info
        captured["scopes"] = scopes
        return "creds-obj"

    def fake_authorize(creds):
        captured["creds"] = creds
        return "gspread-client"

    monkeypatch.setattr(sheets_writer.Credentials, "from_service_account_info", staticmethod(fake_from_service_account))
    monkeypatch.setattr(sheets_writer.gspread, "authorize", fake_authorize)

    client = sheets_writer._client({"client_email": "a@b.com"})
    assert client == "gspread-client"
    assert captured["info"] == {"client_email": "a@b.com"}
    assert "spreadsheets" in captured["scopes"][0]
