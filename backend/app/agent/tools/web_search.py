"""Web search tools — DuckDuckGo search and webpage content extraction.

Rate limited to 10 requests/minute per user via Redis counter.
"""

import logging
from typing import Any, Dict, List, Optional

from app.agent.registry import register_tool

logger = logging.getLogger(__name__)

RATE_LIMIT_RPM = 10  # max web requests per minute per user


async def _check_rate_limit(user_id: int, limit: int = RATE_LIMIT_RPM) -> bool:
    """Check if the user has exceeded the rate limit. Returns True if allowed."""
    try:
        from app.core.redis import redis_client
        if not redis_client:
            return True  # no Redis = no rate limit
        key = f"agent:rate_limit:web:{user_id}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 60)
        return count <= limit
    except Exception:
        return True


@register_tool(
    name="web_search",
    description=(
        "Search the web using DuckDuckGo. Returns top 10 results with titles, "
        "URLs, and snippets. Use this to find current information not in the "
        "knowledge base. Best for fact-checking, current events, and external research."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "max_results": {
                "type": "integer",
                "description": "Number of results (default 5, max 10)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    tags=["web", "external", "search"],
)
async def web_search(
    query: str,
    max_results: int = 5,
    user_id: int = 0,
    **_: Any,
) -> str:
    if not await _check_rate_limit(user_id):
        return "Rate limit exceeded. Please wait before making more web searches."

    max_results = min(max_results, 10)

    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        if not results:
            return f"No web results found for: {query}"

        output = [f"Web search results for: {query}"]
        for i, r in enumerate(results, 1):
            output.append(f"[{i}] {r['title']}\n    {r['snippet'][:200]}\n    {r['url']}")
        return "\n\n".join(output)
    except ImportError:
        return "Web search unavailable: duckduckgo-search package not installed. Try: pip install duckduckgo-search"
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return f"Web search error: {type(e).__name__}: {e}"


@register_tool(
    name="fetch_webpage",
    description=(
        "Fetch and extract the text content of a webpage. Uses readability-style "
        "extraction to get the main article content. Max 5000 characters returned. "
        "Use this when you need to read the details of a page found by web_search."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The full URL of the webpage to fetch",
            },
        },
        "required": ["url"],
    },
    tags=["web", "external"],
)
async def fetch_webpage(
    url: str,
    user_id: int = 0,
    **_: Any,
) -> str:
    if not await _check_rate_limit(user_id):
        return "Rate limit exceeded."

    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        return f"Invalid URL: {url}. URL must start with http:// or https://"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; DocMind/1.0; +https://docmind.ai)",
                },
            )
            if response.status_code != 200:
                return f"Failed to fetch {url}: HTTP {response.status_code}"

            html = response.text

            # Try readability extraction
            text = _extract_text(html)
            if not text:
                return f"Could not extract text content from {url}"

            if len(text) > 5000:
                text = text[:5000] + "\n...[truncated]"

            return f"Content from {url}:\n\n{text}"
    except ImportError:
        return "Webpage fetch unavailable: httpx package not installed."
    except Exception as e:
        logger.error(f"Fetch webpage failed: {e}")
        return f"Fetch error: {type(e).__name__}: {e}"


def _extract_text(html: str) -> str:
    """Extract readable text from HTML using trafilatura or BeautifulSoup."""
    # Try trafilatura first (best readability extraction)
    try:
        import trafilatura
        text = trafilatura.extract(html)
        if text:
            return text.strip()
    except ImportError:
        pass

    # Fallback: BeautifulSoup
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        # Get text
        text = soup.get_text(separator="\n", strip=True)

        # Clean up empty lines
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n".join(lines)
    except ImportError:
        # Last resort: basic HTML tag stripping
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()[:5000]
