import io
import os

import pytest

from pipeline import downloader
from pipeline.downloader import DownloadError, ValidationError


def _jpeg_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 40), color=(120, 90, 40)).save(buf, format="JPEG")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


def test_valid_image_downloads_to_tmp(monkeypatch, slack_file_info, mock_slack_client):
    monkeypatch.setattr(downloader.requests, "get", lambda *a, **k: FakeResponse(_jpeg_bytes()))
    path = downloader.download_image(slack_file_info, mock_slack_client)
    try:
        assert os.path.exists(path)
        assert path.startswith("/tmp/")
        assert os.path.getsize(path) > 0
    finally:
        downloader.cleanup(path)
    assert not os.path.exists(path)


def test_non_image_raises_validation_error(slack_file_info, mock_slack_client):
    slack_file_info["filetype"] = "pdf"
    slack_file_info["mimetype"] = "application/pdf"
    with pytest.raises(ValidationError):
        downloader.download_image(slack_file_info, mock_slack_client)


def test_invalid_image_bytes_raise_validation_error(monkeypatch, slack_file_info, mock_slack_client):
    monkeypatch.setattr(
        downloader.requests, "get", lambda *a, **k: FakeResponse(b"not-an-image")
    )
    with pytest.raises(ValidationError):
        downloader.download_image(slack_file_info, mock_slack_client)


def test_slack_http_error_raises_download_error(monkeypatch, slack_file_info, mock_slack_client):
    monkeypatch.setattr(
        downloader.requests, "get", lambda *a, **k: FakeResponse(b"", status_code=403)
    )
    with pytest.raises(DownloadError):
        downloader.download_image(slack_file_info, mock_slack_client)


def test_no_url_raises_download_error(monkeypatch, slack_file_info, mock_slack_client):
    del slack_file_info["url_private_download"]
    monkeypatch.setattr(downloader.requests, "get", lambda *a, **k: FakeResponse(_jpeg_bytes()))
    with pytest.raises(DownloadError):
        downloader.download_image(slack_file_info, mock_slack_client)


def test_missing_file_id_raises_download_error(slack_file_info, mock_slack_client):
    del slack_file_info["id"]
    with pytest.raises(DownloadError):
        downloader.download_image(slack_file_info, mock_slack_client)


def test_validate_image_bytes_rejects_garbage():
    with pytest.raises(ValidationError):
        downloader.validate_image_bytes(b"\x00\x01\x02garbage")
