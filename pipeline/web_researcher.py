"""FR-05 — Web research via DuckDuckGo + Gemini synthesis.

Runs three DuckDuckGo searches for a brand, collects the top results, and
asks Gemini Flash to synthesise a 150-200 word research brief.
"""

import time

from ddgs import DDGS
from google import genai

from utils.logger import get_logger
from utils.retry import retry

logger = get_logger("pipeline.web_researcher")

_SEARCH_QUERIES = (
    "{brand} company overview funding",
    "{brand} Instagram ads marketing agency",
    "{brand} contact email press",
)

_SYNTHESIS_PROMPT = (
    "You are a brand researcher. Below are search result snippets for the "
    'brand "{brand}" (@{handle}). Write a concise research brief of 150-200 '
    "words covering: what the company does, funding status if mentioned, "
    "marketing/advertising activity, and any public contact information. "
    "Return only the brief text, no preamble.\n\n"
    "SNIPPETS:\n{snippets}"
)

_RETRYABLE_EXCEPTIONS = (RuntimeError, ConnectionError, TimeoutError)


class ResearchError(Exception):
    """Raised when all searches fail."""


def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """Run a single DuckDuckGo search and return result dicts."""
    with DDGS() as ddgs:
        results = list(ddgs.text(query, max_results=max_results))
    return results


def collect_search_results(brand_name: str, max_results: int = 5) -> list[dict]:
    """Run the three brand searches and return a flat result list.

    Raises:
        ResearchError: if every search raises or returns no results.
    """
    all_results: list[dict] = []
    failures = 0
    for template in _SEARCH_QUERIES:
        query = template.format(brand=brand_name)
        try:
            results = _ddg_search(query, max_results)
            if results:
                all_results.extend(results)
            else:
                failures += 1
            time.sleep(1.0)  # rate-limit guard between queries
        except Exception as exc:
            failures += 1
            logger.warning("Search failed for %r: %s", query, exc)
    if not all_results:
        raise ResearchError("All DuckDuckGo searches failed or returned no results")
    return all_results


def _format_snippets(results: list[dict], max_per_result: int = 5) -> str:
    lines = []
    for result in results[:max_per_result]:
        title = result.get("title") or result.get("name") or ""
        body = result.get("body") or result.get("snippet") or result.get("description") or ""
        url = result.get("href") or result.get("url") or ""
        lines.append(f"- {title} | {body} | {url}")
    return "\n".join(lines)


@retry(exceptions=_RETRYABLE_EXCEPTIONS, tries=3, base_delay=1.0, logger=logger)
def _synthesise(api_key: str, brand_name: str, handle: str, snippets: str) -> str:
    client = genai.Client(api_key=api_key)
    prompt = _SYNTHESIS_PROMPT.format(brand=brand_name, handle=handle, snippets=snippets)
    response = client.models.generate_content(model="gemini-3.1-flash-lite", contents=prompt)
    return (response.text or "").strip()


def research_brand(
    brand_name: str,
    handle: str,
    api_key: str | None = None,
    max_results: int = 5,
) -> dict:
    """Research a brand online and synthesise a research brief.

    Args:
        brand_name: extracted brand name.
        handle: Instagram handle (without ``@``).
        api_key: Gemini API key; defaults to ``config.CONFIG``.
        max_results: results to collect per search.

    Returns:
        A dict with keys: research_notes (str) and sources (list[str]).

    Raises:
        ResearchError: if all searches fail and no fallback is possible.
    """
    if api_key is None:
        from config import CONFIG

        api_key = CONFIG["gemini_api_key"]

    try:
        results = collect_search_results(brand_name, max_results)
    except ResearchError as exc:
        logger.error("Research failed for %s: %s", brand_name, exc)
        return {
            "research_notes": (
                f"{brand_name} — no web data found. Verify manually on Instagram "
                f"(@{handle}) before outreach. Details from the post image only."
            ),
            "sources": [],
        }

    sources: list[str] = []
    for r in results:
        url = r.get("href") or r.get("url") or ""
        if url and url not in sources:
            sources.append(url)
    snippets = _format_snippets(results)

    # Check if any result actually mentions the brand — skip synthesis if not
    brand_lower = brand_name.lower()
    relevant = any(
        brand_lower in (r.get("title") or "").lower()
        or brand_lower in (r.get("body") or r.get("snippet") or r.get("description") or "").lower()
        for r in results
    )
    if not relevant:
        logger.info("No relevant web results for %r — skipping synthesis", brand_name)
        return {
            "research_notes": f"No public web data found. Research manually via Instagram (@{handle}).",
            "sources": [],
        }

    try:
        notes = _synthesise(api_key, brand_name, handle, snippets)
    except Exception as exc:
        logger.warning("Synthesis failed, using fallback brief: %s", exc)
        notes = f"No public web data found. Research manually via Instagram (@{handle})."

    return {"research_notes": notes, "sources": sources}
