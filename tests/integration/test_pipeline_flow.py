import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slack_handlers import events  # noqa: E402


class MockClient:
    def __init__(self, file_info):
        self.file_info = file_info
        self.messages = []

    def files_info(self, file):
        return {"ok": True, "file": dict(self.file_info)}

    def chat_getPermalink(self, channel, message_ts):
        return {"ok": True, "permalink": "https://slack.com/archives/C123/p1000"}

    def chat_postMessage(self, channel, thread_ts, text):
        self.messages.append(text)


@pytest.fixture(autouse=True)
def _sync(monkeypatch):
    monkeypatch.setattr(events, "_executor", type("Sync", (), {"submit": lambda self, fn, *a, **k: fn(*a, **k)})())


@pytest.fixture()
def file_info():
    return {
        "id": "F100",
        "filetype": "jpg",
        "mimetype": "image/jpeg",
        "url_private_download": "https://files.slack.com/brand.jpg",
        "name": "brand.jpg",
    }


def _mock_download(monkeypatch, tmp_path):
    from PIL import Image

    img_path = str(tmp_path / "img.jpg")
    Image.new("RGB", (40, 40), color=(10, 200, 100)).save(img_path, format="JPEG")
    monkeypatch.setattr(events.downloader, "download_image", lambda *a, **k: img_path)
    monkeypatch.setattr(events.downloader, "cleanup", lambda *a, **k: None)
    return img_path


def _mock_vision(monkeypatch, brand=None):
    from pipeline import vision_extractor

    monkeypatch.setattr(
        events.vision_extractor,
        "extract_brand",
        lambda *a, **k: brand
        or {
            "brand_name": "Glow Skincare",
            "handle": "glowskincare",
            "niche": "Skincare",
            "tagline": "Glow",
            "email": "hello@glow.com",
            "phone": "+919876543210",
            "website": "https://glow.com",
            "confidence": 0.9,
        },
    )


def _mock_instagram(monkeypatch, profile=None):
    monkeypatch.setattr(
        events.instagram_scraper,
        "get_profile",
        lambda handle, *a, **k: profile
        or {
            "full_name": "Glow Skincare",
            "bio": "Clean skincare.",
            "followers": 125000,
            "following": 345,
            "post_count": 892,
            "website": "https://glow.com",
            "is_verified": True,
            "is_private": False,
        },
    )


def _mock_research(monkeypatch, notes="Synthesised brief."):
    monkeypatch.setattr(
        events.web_researcher,
        "research_brand",
        lambda *a, **k: {"research_notes": notes, "sources": ["https://x.com/1"]},
    )


def test_full_pipeline_writes_sheet_row(monkeypatch, tmp_path, file_info):
    _mock_download(monkeypatch, tmp_path)
    _mock_vision(monkeypatch)
    _mock_instagram(monkeypatch)
    _mock_research(monkeypatch)

    written = {}

    def fake_write(brand_data):
        written.update(brand_data)
        return {"action": "appended", "row_num": 3}

    monkeypatch.setattr(events.sheets_writer, "write_brand", fake_write)

    client = MockClient(file_info)
    events._run_pipeline(client, "C123", file_info, "1.000", "U123")

    assert written["brand_name"] == "Glow Skincare"
    assert written["handle"] == "glowskincare"
    assert written["status"] == "To Contact"
    assert written["profile"]["followers"] == 125000
    assert written["source_post_url"] == "https://slack.com/archives/C123/p1000"
    assert any("Done" in m for m in client.messages)


def test_pipeline_private_profile_continues(monkeypatch, tmp_path, file_info):
    _mock_download(monkeypatch, tmp_path)
    _mock_vision(monkeypatch)

    def private_profile(handle, *a, **k):
        raise events.instagram_scraper.PrivateProfileError("private")

    monkeypatch.setattr(events.instagram_scraper, "get_profile", private_profile)
    _mock_research(monkeypatch)

    written = {}
    monkeypatch.setattr(
        events.sheets_writer,
        "write_brand",
        lambda data: written.update(data) or {"action": "appended", "row_num": 4},
    )

    client = MockClient(file_info)
    events._run_pipeline(client, "C123", file_info, "1.000", "U123")

    assert written["profile"]["is_private"] is True
    assert written["profile"]["followers"] == 0
    assert written["status"] == "To Contact"


def test_pipeline_low_confidence_sets_review_needed(monkeypatch, tmp_path, file_info):
    _mock_download(monkeypatch, tmp_path)
    _mock_vision(
        monkeypatch,
        brand={
            "brand_name": "Unknown",
            "handle": "",
            "niche": "",
            "tagline": "",
            "email": None,
            "phone": None,
            "website": None,
            "confidence": 0.2,
        },
    )
    _mock_instagram(monkeypatch)
    _mock_research(monkeypatch)

    written = {}
    monkeypatch.setattr(
        events.sheets_writer,
        "write_brand",
        lambda data: written.update(data) or {"action": "appended", "row_num": 5},
    )

    client = MockClient(file_info)
    events._run_pipeline(client, "C123", file_info, "1.000", "U123")

    assert written["status"] == "Review Needed"
    assert written["handle"] == ""


def test_pipeline_error_posts_to_slack(monkeypatch, tmp_path, file_info):
    _mock_download(monkeypatch, tmp_path)

    def boom(*a, **k):
        raise RuntimeError("vision failed")

    monkeypatch.setattr(events.vision_extractor, "extract_brand", boom)
    _mock_research(monkeypatch)

    client = MockClient(file_info)
    events._run_pipeline(client, "C123", file_info, "1.000", "U123")

    assert any("Something went wrong" in m for m in client.messages)
