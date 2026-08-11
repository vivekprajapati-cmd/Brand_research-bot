import os
import sys

import pytest
from slack_bolt import App

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from slack_handlers import events  # noqa: E402

_logger = events.get_logger("test.slack_events")


class _SyncExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


class MockClient:
    def __init__(self, file_info=None):
        self.file_info = file_info or {}
        self.messages = []

    def files_info(self, file):
        return {"ok": True, "file": dict(self.file_info)}

    def chat_getPermalink(self, channel, message_ts):
        return {"ok": True, "permalink": "https://slack.com/permalink"}

    def chat_postMessage(self, channel, thread_ts, text):
        self.messages.append((channel, thread_ts, text))


@pytest.fixture()
def image_file():
    return {
        "id": "F100",
        "filetype": "png",
        "mimetype": "image/png",
        "url_private_download": "https://files.slack.com/x.png",
        "name": "post.png",
    }


@pytest.fixture(autouse=True)
def _sync(monkeypatch):
    monkeypatch.setattr(events, "_executor", _SyncExecutor())


@pytest.fixture()
def handlers():
    app = App(
        token="xoxb-token",
        signing_secret="secret",
        token_verification_enabled=False,
    )
    events.register_handlers(app)
    handlers_by_name = {}
    for listener in app._listeners:
        handlers_by_name[listener.ack_function.__name__] = listener.ack_function
    return {
        "file_shared": [handlers_by_name["on_file_shared"]],
        "message": [handlers_by_name["on_message"]],
    }


def test_is_relevant_file_accepts_image(image_file):
    assert events._is_relevant_file(image_file) is True


def test_is_relevant_file_rejects_pdf():
    assert events._is_relevant_file({"filetype": "pdf", "mimetype": "application/pdf"}) is False


def test_file_shared_triggers_pipeline(monkeypatch, handlers, image_file):
    client = MockClient(image_file)
    event = {"channel_id": "C123", "file_id": "F100", "user_id": "U123", "ts": "1.000"}
    pipeline_calls = []

    def fake_run(client_, channel, file_info, ts, username):
        pipeline_calls.append((channel, file_info["id"], ts, username))

    monkeypatch.setattr(events, "_run_pipeline", fake_run)
    for handler in handlers["file_shared"]:
        handler(client, event, {}, _logger)
    assert pipeline_calls
    assert pipeline_calls[0] == ("C123", "F100", "1.000", "U123")


def test_file_shared_ignores_bot_user(monkeypatch, handlers, image_file):
    client = MockClient(image_file)
    event = {"channel_id": "C123", "file_id": "F100", "user_id": "USLACKBOT", "ts": "1.000"}
    pipeline_calls = []
    monkeypatch.setattr(events, "_run_pipeline", lambda *a, **k: pipeline_calls.append(a))
    for handler in handlers["file_shared"]:
        handler(client, event, {}, _logger)
    assert not pipeline_calls


def test_message_with_image_triggers_only_image(monkeypatch, handlers):
    client = MockClient({})
    event = {
        "user": "U123",
        "channel": "C123",
        "ts": "2.000",
        "files": [
            {"filetype": "jpg", "mimetype": "image/jpeg", "id": "F200"},
            {"filetype": "pdf", "mimetype": "application/pdf", "id": "F201"},
        ],
    }
    pipeline_calls = []
    monkeypatch.setattr(events, "_run_pipeline", lambda *a, **k: pipeline_calls.append(a))
    for handler in handlers["message"]:
        handler(client, event, {}, _logger)
    assert any(call[2]["id"] == "F200" for call in pipeline_calls)
    assert not any(call[2]["id"] == "F201" for call in pipeline_calls)


def test_bot_message_ignored(handlers):
    pipeline_calls = []
    event = {"bot_id": "BOT1", "channel": "C1", "ts": "1", "files": []}
    for handler in handlers["message"]:
        handler(MockClient({}), event, {}, None)
    assert not pipeline_calls
