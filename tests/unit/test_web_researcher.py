import pytest

from pipeline import web_researcher
from pipeline.web_researcher import ResearchError


def _ddg_result(title, body, url):
    return {"title": title, "body": body, "href": url}


def test_research_returns_notes_and_sources(monkeypatch):
    results = [
        _ddg_result("Glow Skincare overview", "Series A funding", "https://x.com/1"),
        _ddg_result("Glow Skincare ads", "running IG ads", "https://x.com/2"),
    ]
    monkeypatch.setattr(web_researcher, "_ddg_search", lambda query, max_results=5: results)
    monkeypatch.setattr(web_researcher, "_synthesise", lambda *a, **k: "Synthesised brief.")

    out = web_researcher.research_brand("Glow Skincare", "glowskincare", api_key="key")
    assert out["research_notes"] == "Synthesised brief."
    assert out["sources"] == ["https://x.com/1", "https://x.com/2"]


def test_empty_results_generate_fallback(monkeypatch):
    monkeypatch.setattr(web_researcher, "_ddg_search", lambda query, max_results=5: [])
    monkeypatch.setattr(web_researcher.time, "sleep", lambda s: None)

    out = web_researcher.research_brand("Ghost Brand", "ghost", api_key="key")
    assert "no web data found" in out["research_notes"].lower()
    assert out["sources"] == []


def test_synthesis_failure_uses_fallback_brief(monkeypatch):
    results = [_ddg_result("Glow", "body", "https://x.com/1")]
    monkeypatch.setattr(web_researcher, "_ddg_search", lambda query, max_results=5: results)
    monkeypatch.setattr(web_researcher, "_synthesise", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))

    out = web_researcher.research_brand("Glow", "glow", api_key="key")
    assert "unavailable" in out["research_notes"]


def test_all_searches_fail_returns_fallback(monkeypatch):
    def boom(query, max_results=5):
        raise RuntimeError("network down")

    monkeypatch.setattr(web_researcher, "_ddg_search", boom)
    monkeypatch.setattr(web_researcher.time, "sleep", lambda s: None)

    out = web_researcher.research_brand("Down Brand", "down", api_key="key")
    assert "no web data found" in out["research_notes"].lower()


def test_collect_search_results_failure_raises(monkeypatch):
    monkeypatch.setattr(web_researcher, "_ddg_search", lambda query, max_results=5: [])
    monkeypatch.setattr(web_researcher.time, "sleep", lambda s: None)
    with pytest.raises(ResearchError):
        web_researcher.collect_search_results("No Results Brand")
