import base64
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_VALID_CREDS = {
    "type": "service_account",
    "project_id": "test-project",
    "client_email": "test@test-project.iam.gserviceaccount.com",
}

_REQUIRED = {
    "SLACK_BOT_TOKEN",
    "SLACK_SIGNING_SECRET",
    "SLACK_CHANNEL_ID",
    "GEMINI_API_KEY",
    "GOOGLE_SHEET_ID",
    "GOOGLE_CREDS_JSON",
}


def _set_all_env(monkeypatch, **overrides):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C123")
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    monkeypatch.setenv("GOOGLE_SHEET_ID", "sheet")
    monkeypatch.setenv(
        "GOOGLE_CREDS_JSON",
        base64.b64encode(json.dumps(_VALID_CREDS).encode()).decode(),
    )
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _reload_config():
    import config

    for name in list(sys.modules):
        if name == "config" or name.startswith("config."):
            del sys.modules[name]
    return __import__("config")


def test_missing_required_vars_raise(monkeypatch):
    _set_all_env(monkeypatch)
    for var in _REQUIRED:
        cfg = _reload_config()
        monkeypatch.delenv(var, raising=False)
        with pytest.raises(ValueError):
            cfg.load_config()
        monkeypatch.setenv(var, {"GOOGLE_CREDS_JSON": "x"}.get(var, "dummy"))
        if var == "GOOGLE_CREDS_JSON":
            monkeypatch.setenv(
                "GOOGLE_CREDS_JSON",
                base64.b64encode(json.dumps(_VALID_CREDS).encode()).decode(),
            )
    _set_all_env(monkeypatch)


def test_load_config_populates_defaults(monkeypatch):
    _set_all_env(monkeypatch)
    cfg = _reload_config()
    result = cfg.load_config()
    assert result["google_sheet_tab"] == "Brand Research"
    assert result["log_level"] == "INFO"
    assert result["max_retries"] == 3
    assert result["instagram_username"] is None


def test_load_config_respects_overrides(monkeypatch):
    _set_all_env(monkeypatch, GOOGLE_SHEET_TAB="Outreach", MAX_RETRIES="5", LOG_LEVEL="DEBUG")
    cfg = _reload_config()
    result = cfg.load_config()
    assert result["google_sheet_tab"] == "Outreach"
    assert result["max_retries"] == 5
    assert result["log_level"] == "DEBUG"


def test_invalid_creds_json_raises(monkeypatch):
    _set_all_env(monkeypatch)
    cfg = _reload_config()
    monkeypatch.setenv("GOOGLE_CREDS_JSON", "not-base64-json!!!")
    with pytest.raises(ValueError):
        cfg.load_config()


def test_max_retries_bounds_raise(monkeypatch):
    _set_all_env(monkeypatch)
    cfg = _reload_config()
    monkeypatch.setenv("MAX_RETRIES", "0")
    with pytest.raises(ValueError):
        cfg.load_config()
    monkeypatch.setenv("MAX_RETRIES", "11")
    with pytest.raises(ValueError):
        cfg.load_config()
