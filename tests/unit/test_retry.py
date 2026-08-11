import pytest

from utils import retry


def test_succeeds_first_attempt(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    calls = {"n": 0}

    @retry.retry(tries=3, base_delay=0.01)
    def work():
        calls["n"] += 1
        return "ok"

    assert work() == "ok"
    assert calls["n"] == 1


def test_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    calls = {"n": 0}

    @retry.retry(exceptions=(ValueError,), tries=3, base_delay=0.01)
    def work():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "recovered"

    assert work() == "recovered"
    assert calls["n"] == 3


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    calls = {"n": 0}

    @retry.retry(exceptions=(ValueError,), tries=3, base_delay=0.01)
    def work():
        calls["n"] += 1
        raise ValueError("persistent")

    with pytest.raises(ValueError):
        work()
    assert calls["n"] == 3


def test_non_matching_exception_not_retried(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    calls = {"n": 0}

    @retry.retry(exceptions=(ValueError,), tries=3, base_delay=0.01)
    def work():
        calls["n"] += 1
        raise KeyError("nope")

    with pytest.raises(KeyError):
        work()
    assert calls["n"] == 1


def test_retry_decorator_raises_on_bad_tries():
    with pytest.raises(ValueError):
        retry.retry(tries=0)


def test_backoff_increases_delay(monkeypatch):
    sleeps = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    @retry.retry(exceptions=(ValueError,), tries=3, base_delay=1.0)
    def work():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("again")

    work()
    assert sleeps[0] >= 1.0
    assert sleeps[1] >= 2.0


def test_logger_warns_on_retry(monkeypatch):
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    warnings = []

    class FakeLogger:
        def warning(self, *args, **kwargs):
            warnings.append(args)

    @retry.retry(exceptions=(ValueError,), tries=2, base_delay=0.01, logger=FakeLogger())
    def work():
        raise ValueError("boom")

    with pytest.raises(ValueError):
        work()
    assert len(warnings) == 1
    assert "Retrying" in warnings[0][0]
