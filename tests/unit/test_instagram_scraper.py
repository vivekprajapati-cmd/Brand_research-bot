from instaloader import ProfileNotExistsException
import pytest

from pipeline import instagram_scraper
from pipeline.instagram_scraper import PrivateProfileError, ProfileNotFoundError


class MockProfile:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockContext:
    pass


class MockLoader:
    def __init__(self, profile=None, exc=None):
        self._profile = profile
        self._exc = exc

    @property
    def context(self):
        return MockContext()

    def login(self, username, password):
        pass


def _profile(**overrides):
    defaults = {
        "full_name": "Glow Skincare",
        "biography": "Clean skincare.",
        "followers": 125000,
        "followees": 345,
        "mediacount": 892,
        "external_url": "https://glowskincare.com",
        "is_verified": True,
        "is_private": False,
    }
    defaults.update(overrides)
    return MockProfile(**defaults)


def _public_profile(monkeypatch, profile):
    monkeypatch.setattr(instagram_scraper, "_delay", lambda: None)
    loader = MockLoader(profile=profile)

    def fake_from_username(ctx, handle):
        return profile

    monkeypatch.setattr(instagram_scraper.Profile, "from_username", staticmethod(fake_from_username))
    return loader


def test_public_profile_returns_expected_dict(monkeypatch):
    loader = _public_profile(monkeypatch, _profile())
    result = instagram_scraper.get_profile("glowskincare", loader=loader)
    assert result["full_name"] == "Glow Skincare"
    assert result["followers"] == 125000
    assert result["following"] == 345
    assert result["post_count"] == 892
    assert result["website"] == "https://glowskincare.com"
    assert result["is_verified"] is True
    assert result["is_private"] is False


def test_private_profile_raises_private_profile_error(monkeypatch):
    monkeypatch.setattr(instagram_scraper, "_delay", lambda: None)
    profile = _profile(is_private=True)
    loader = MockLoader(profile=profile)
    monkeypatch.setattr(
        instagram_scraper.Profile, "from_username", staticmethod(lambda ctx, handle: profile)
    )
    with pytest.raises(PrivateProfileError):
        instagram_scraper.get_profile("@privatebrand", loader=loader)


def test_unknown_handle_raises_profile_not_found(monkeypatch):
    monkeypatch.setattr(instagram_scraper, "_delay", lambda: None)
    loader = MockLoader(exc=ProfileNotExistsException())

    def fake_from_username(ctx, handle):
        raise ProfileNotExistsException()

    monkeypatch.setattr(
        instagram_scraper.Profile, "from_username", staticmethod(fake_from_username)
    )
    with pytest.raises(ProfileNotFoundError):
        instagram_scraper.get_profile("ghostbrand", loader=loader)


def test_at_prefix_stripped(monkeypatch):
    loader = _public_profile(monkeypatch, _profile())
    result = instagram_scraper.get_profile("@glowskincare", loader=loader)
    assert result["full_name"] == "Glow Skincare"


def test_empty_handle_raises_profile_not_found(monkeypatch):
    monkeypatch.setattr(instagram_scraper, "_delay", lambda: None)
    loader = MockLoader(profile=_profile())
    monkeypatch.setattr(
        instagram_scraper.Profile, "from_username", staticmethod(lambda ctx, handle: _profile())
    )
    with pytest.raises(ProfileNotFoundError):
        instagram_scraper.get_profile("", loader=loader)


def test_loader_logs_in_with_credentials(monkeypatch):
    class _MockLoaderLike:
        def __init__(self, **kwargs):
            self.logged_in = None

        def login(self, username, password):
            self.logged_in = (username, password)

    monkeypatch.setattr(instagram_scraper, "Instaloader", _MockLoaderLike)
    from pipeline.instagram_scraper import _loader

    loader = _loader("user", "pass")
    assert loader.logged_in == ("user", "pass")


def test_delay_pauses(monkeypatch):
    slept = []
    monkeypatch.setattr(instagram_scraper.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(instagram_scraper.random, "uniform", lambda a, b: 2.5)
    instagram_scraper._delay(2, 5)
    assert slept == [2.5]


def test_connection_error_propagates(monkeypatch):
    monkeypatch.setattr(instagram_scraper, "_delay", lambda: None)
    loader = MockLoader()

    def boom(ctx, handle):
        raise ConnectionError("network")

    monkeypatch.setattr(instagram_scraper.Profile, "from_username", staticmethod(boom))
    with pytest.raises(ConnectionError):
        instagram_scraper.get_profile("brand", loader=loader)
