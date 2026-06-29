"""URL 抓取单测（用 httpx mock，不打外网）."""

from __future__ import annotations

import pytest

from app.services.url_fetcher import (
    _extract_title,
    _is_feishu_url,
    _is_public_md_url,
    fetch_url,
)


def test_is_feishu_url():
    assert _is_feishu_url("https://example.feishu.cn/docx/abc")
    assert _is_feishu_url("https://x.larksuite.com/wiki/abc")
    assert not _is_feishu_url("https://github.com/abc.md")


def test_is_public_md_url():
    assert _is_public_md_url("https://raw.githubusercontent.com/x/y/z.md")
    assert _is_public_md_url("https://gist.github.com/abc.md")
    assert _is_public_md_url("https://example.com/README.md")
    assert not _is_public_md_url("https://example.com/index.html")


def test_extract_title():
    html = "<html><head><title>测试标题 - 飞书文档</title></head></html>"
    assert _extract_title(html) == "测试标题"
    html2 = "<html><head><title>Hello | Feishu</title></head></html>"
    assert _extract_title(html2) == "Hello"


@pytest.mark.asyncio
async def test_fetch_url_public_md(httpx_mock=None):
    """用 respx 或 monkeypatch 模拟 httpx 响应（最小示例）."""
    # 简化：直接测 dispatch 函数
    # 真实环境可加 respx / pytest_httpx
    from app.services import url_fetcher

    called = {"ok": False}

    class FakeAsyncClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def get(self, url):
            class Resp:
                status_code = 200
                headers = {"content-type": "text/markdown"}
                text = "# Hello\n\nWorld"

                def raise_for_status(self):
                    pass

            return Resp()

    # monkey patch httpx.AsyncClient
    import httpx as _httpx

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeAsyncClient
    try:
        result = await url_fetcher.fetch_url("https://example.com/README.md")
        assert result.content_type == "markdown"
        assert "Hello" in result.content
        assert result.source == "public_md"
    finally:
        _httpx.AsyncClient = orig
