import json

from utils.logger import JsonFormatter, get_logger, log_event


class _Record:
    def __init__(self, message, levelname="INFO", name="test", exc_info=None):
        self.msg = message
        self.args = ()
        self.levelname = levelname
        self.name = name
        self.exc_info = exc_info
        self.extra_fields = None

    def getMessage(self):
        return self.msg

    def formatException(self, exc_info):
        return "traceback"


def test_json_formatter_outputs_json():
    fmt = JsonFormatter()
    record = _Record("hello world")
    record.extra_fields = {"stage": "download"}
    out = json.loads(fmt.format(record))
    assert out["message"] == "hello world"
    assert out["level"] == "INFO"
    assert out["stage"] == "download"


def test_get_logger_returns_singleton():
    first = get_logger("test.singleton")
    second = get_logger("test.singleton")
    assert first is second


def test_log_event_emits_with_extra():
    logger = get_logger("test.logevent")
    log_event(logger, "stage complete", stage="scrape", handle="glow")
    assert logger.handlers
