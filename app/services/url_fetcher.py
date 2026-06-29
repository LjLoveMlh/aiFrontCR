"""在线文档链接抓取（飞书公开 / 公共 MD / HTML）.

支持：
- 飞书公开文档（anyone with link can view）— 走 GET + BeautifulSoup
- 公共 MD 链接（raw.githubusercontent.com / *.md / gist）
- 普通 HTML 页面

不支持（阶段1 v1）：
- 飞书内部鉴权文档（需 tenant_access_token；留 TODO v1.1）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx

from app.services.html_cleaner import extract_main_content


@dataclass
class FetchedDoc:
    """抓取结果."""

    title: str
    content: str
    url: str
    content_type: str  # "markdown" | "html"
    source: str  # "feishu_public" | "public_md" | "public_html"


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


async def fetch_url(url: str, timeout: float = 30.0) -> FetchedDoc:
    """抓取 URL 内容并清洗.

    分发策略：
    - 飞书域（feishu.cn / larksuite.com）→ 走 HTML 清洗
    - 以 .md 结尾 / raw.githubusercontent.com / gist → 原样
    - 其余 → HTML 清洗
    """
    url = url.strip()
    if not url:
        raise ValueError("URL 不能为空")

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=_HEADERS) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").lower()
        body = resp.text

    # 飞书公开文档
    if _is_feishu_url(url):
        content = extract_main_content(body)
        title = _extract_title(body) or "飞书文档"
        return FetchedDoc(
            title=title,
            content=content,
            url=url,
            content_type="html",
            source="feishu_public",
        )

    # 公共 MD
    if _is_public_md_url(url) or "markdown" in ctype or url.lower().endswith(".md"):
        return FetchedDoc(
            title=url.split("/")[-1] or "online-md",
            content=body,
            url=url,
            content_type="markdown",
            source="public_md",
        )

    # 普通 HTML
    content = extract_main_content(body)
    title = _extract_title(body) or url
    return FetchedDoc(
        title=title,
        content=content,
        url=url,
        content_type="html",
        source="public_html",
    )


def _is_feishu_url(url: str) -> bool:
    return bool(re.search(r"(feishu\.cn|larksuite\.com|feishu\.com)", url, re.IGNORECASE))


def _is_public_md_url(url: str) -> bool:
    if url.lower().endswith(".md"):
        return True
    if "raw.githubusercontent.com" in url.lower():
        return True
    if "gist.githubusercontent.com" in url.lower():
        return True
    return False


def _extract_title(html: str) -> Optional[str]:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        # 去 - xxx 后缀
        title = re.sub(r"\s*[-_|].*?(飞书|文档|Feishu|Lark)\s*$", "", title, flags=re.IGNORECASE).strip()
        return title[:200]
    return None
