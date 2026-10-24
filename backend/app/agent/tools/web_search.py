"""Web search tools — DuckDuckGo search and webpage content extraction.

Rate limited to 10 requests/minute per user via Redis counter.
"""

import asyncio
import ipaddress
import logging
from typing import Any

from app.agent.registry import ToolResult, register_tool

logger = logging.getLogger(__name__)

RATE_LIMIT_RPM = 10  # max web requests per minute per user

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def _validate_public_url(url: str) -> str:
    """Block SSRF targets: private, loopback, link-local, and cloud metadata ranges."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL 必须使用 http:// 或 https://")
    if not parsed.hostname:
        raise ValueError("URL 缺少主机名")

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise ValueError(f"无法解析主机名: {parsed.hostname}") from e

    for _, _, _, _, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or any(ip in net for net in _PRIVATE_NETWORKS)
        ):
            raise ValueError(f"不允许访问内网地址: {ip}")
    return url


async def _check_rate_limit(user_id: int, limit: int = RATE_LIMIT_RPM) -> bool:
    """Check if the user has exceeded the rate limit. Returns True if allowed.

    安全加固：Redis 不可用/异常时 fail-closed（拒绝），外部网络调用宁可拒绝不可放开。
    """
    try:
        from app.core.redis import redis_client

        if not redis_client:
            return False  # no Redis = fail closed
        key = f"agent:rate_limit:web:{user_id}"
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, 60)
        return count <= limit
    except Exception:
        return False


@register_tool(
    name="web_search",
    description=(
        "Web search (action=search) or fetch a webpage's text (action=fetch, max 5000 chars)."
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
                "description": "Max results (default 5)",
                "default": 5,
            },
            "action": {
                "type": "string",
                "enum": ["search", "fetch"],
                "description": "search|fetch",
                "default": "search",
            },
            "url": {
                "type": "string",
                "description": "URL (fetch)",
            },
        },
    },
    tags=["web", "external", "search"],
)
async def web_search(
    query: str = "",
    max_results: int = 5,
    action: str = "search",
    url: str = "",
    user_id: int = 0,
    **_: Any,
) -> str | ToolResult:
    # issue #509：fetch_webpage 合并进本工具（action=fetch）。SSRF 每跳重验、
    # 2MB 上限、5000 字截断、禁自动跳转等安全语义原样保留在 _fetch_webpage_impl。
    if str(action or "").strip().lower() == "fetch":
        if not url:
            return ToolResult.fail(code="validation_error", message="action=fetch 需要 url 参数")
        return await _fetch_webpage_impl(url, user_id)
    if str(action or "").strip().lower() != "search":
        return ToolResult.fail(
            code="validation_error", message=f"未知 action: {action}（支持 search/fetch）"
        )
    if not query:
        return ToolResult.fail(code="validation_error", message="action=search 需要 query 参数")

    if not await _check_rate_limit(user_id):
        # issue #293：失败必须结构化 —— 返回错误字符串会被注册表归一为
        # success=True，SSE/指标/run_report/findings 全绿（见该 issue 证据链）。
        return ToolResult.fail(
            code="rate_limited",
            message="Rate limit exceeded. Please wait before making more web searches.",
        )

    max_results = min(max_results, 10)

    try:
        # 上游 2025 年由 duckduckgo-search 改名 ddgs（旧名冻结在 8.1.1，见
        # requirements.txt 注释）。新名优先、旧名兜底——环境里装的是哪个都能跑；
        # 两个都没有时 ImportError 穿透到下方分支（结构化 invalid_configuration）。
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        def _run_search() -> list[dict[str, str]]:
            # DDGS is a blocking HTTP client; running it on the event loop would
            # stall every other request served by this worker.
            found: list[dict[str, str]] = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    found.append(
                        {
                            "title": r.get("title", ""),
                            "url": r.get("href", ""),
                            "snippet": r.get("body", ""),
                        }
                    )
            return found

        results = await asyncio.to_thread(_run_search)
        if not results:
            return f"No web results found for: {query}"

        output = [f"Web search results for: {query}"]
        for i, r in enumerate(results, 1):
            output.append(f"[{i}] {r['title']}\n    {r['snippet'][:200]}\n    {r['url']}")
        return "\n\n".join(output)
    except ImportError:
        return ToolResult.fail(
            code="invalid_configuration",
            message=(
                "Web search unavailable: neither 'ddgs' nor 'duckduckgo-search' "
                "package installed. Try: pip install ddgs"
            ),
        )
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        # raw=e 交给注册表内建 error_mapper 再分类（连接类错误 → 可重试码）。
        return ToolResult.fail(
            code="unknown",
            message=f"Web search error: {type(e).__name__}: {e}",
            raw=e,
        )


async def _fetch_webpage_impl(url: str, user_id: int = 0) -> str | ToolResult:
    """网页正文抓取（原 fetch_webpage 实现，issue #509 原样迁入）。"""
    if not await _check_rate_limit(user_id):
        return ToolResult.fail(code="rate_limited", message="Rate limit exceeded.")

    try:
        from urllib.parse import urljoin

        import httpx

        current_url = _validate_public_url(url)

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = None
            for _ in range(5):
                current_url = _validate_public_url(current_url)
                response = await client.get(
                    current_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; DocMind/1.0; +https://docmind.ai)",
                    },
                )
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location", "")
                    if not location:
                        break
                    current_url = urljoin(str(response.url), location)
                    continue
                break

            if response is None:
                return ToolResult.fail(
                    code="unknown", message=f"Failed to fetch {url}: no response"
                )
            if response.status_code != 200:
                return ToolResult.fail(
                    code="api_error", message=f"Failed to fetch {url}: HTTP {response.status_code}"
                )

            # 安全加固：限制响应体大小（2MB），防止恶意服务器返回超大响应拖垮内存
            raw = response.content
            if len(raw) > 2 * 1024 * 1024:
                return ToolResult.fail(
                    code="unknown", message=f"Failed to fetch {url}: response too large (>2MB)"
                )
            html = raw.decode("utf-8", errors="replace")

            # Try readability extraction (CPU-bound parsing — keep it off the loop)
            text = await asyncio.to_thread(_extract_text, html)
            if not text:
                return ToolResult.fail(
                    code="empty_response", message=f"Could not extract text content from {url}"
                )

            if len(text) > 5000:
                text = text[:5000] + "\n...[truncated]"

            return f"Content from {current_url}:\n\n{text}"
    except ImportError:
        return ToolResult.fail(
            code="invalid_configuration",
            message="Webpage fetch unavailable: httpx package not installed.",
        )
    except Exception as e:
        logger.error(f"Fetch webpage failed: {e}")
        return ToolResult.fail(
            code="unknown",
            message=f"Fetch error: {type(e).__name__}: {e}",
            raw=e,
        )


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
    # issue #509：能力已合并进 web_search(action=fetch)；保留注册与 handler
    # （不删工具、旧名可用），但不再进 schema；旧名经 TOOL_ALIASES 路由。
    hidden=True,
    superseded_by="web_search",
)
async def fetch_webpage(
    url: str,
    user_id: int = 0,
    **_: Any,
) -> str | ToolResult:
    return await _fetch_webpage_impl(url, user_id)


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
