import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TEST_CREDS = {
    "type": "service_account",
    "project_id": "test-project",
    "private_key_id": "test",
    "private_key": "-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----\n",
    "client_email": "test@test-project.iam.gserviceaccount.com",
    "client_id": "1234567890",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}

_ENV_DEFAULTS = {
    "SLACK_BOT_TOKEN": "xoxb-test-token",
    "SLACK_SIGNING_SECRET": "test-signing-secret",
    "SLACK_CHANNEL_ID": "C1234567890",
    "GEMINI_API_KEY": "test-gemini-key",
    "GOOGLE_SHEET_ID": "test-sheet-id",
    "GOOGLE_SHEET_TAB": "Brand Research",
    "GOOGLE_CREDS_JSON": base64.b64encode(json.dumps(_TEST_CREDS).encode()).decode(),
    "LOG_LEVEL": "INFO",
    "MAX_RETRIES": "3",
}

for _key, _value in _ENV_DEFAULTS.items():
    os.environ.setdefault(_key, _value)


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    for _key, _value in _ENV_DEFAULTS.items():
        monkeypatch.setenv(_key, _value)


@pytest.fixture()
def sample_image(tmp_path):
    from PIL import Image

    path = tmp_path / "sample_brand_post.jpg"
    img = Image.new("RGB", (60, 60), color=(200, 100, 50))
    img.save(str(path), format="JPEG")
    return str(path)


@pytest.fixture()
def slack_file_info():
    return {
        "id": "F1234567890",
        "filetype": "jpg",
        "mimetype": "image/jpeg",
        "name": "brand_post.jpg",
        "url_private_download": "https://files.slack.com/files/T123/F123/brand_post.jpg",
    }


@pytest.fixture()
def mock_slack_client(slack_file_info):
    class MockSlackClient:
        token = "xoxb-test-token"

        def files_info(self, file):
            return {"ok": True, "file": dict(slack_file_info)}

    return MockSlackClient()


@pytest.fixture()
def mock_gemini_brand():
    return {
        "brand_name": "Glow Skincare",
        "handle": "glowskincare",
        "niche": "Skincare",
        "tagline": "Glow from within",
        "email": "hello@glowskincare.com",
        "phone": "+91 98765 43210",
        "website": "https://glowskincare.com",
        "confidence": 0.92,
    }
