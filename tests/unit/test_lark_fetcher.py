"""飞书 markdown 清洗单测（不调 lark-cli,只测纯函数）."""

from __future__ import annotations

import pytest

from app.services.lark_fetcher import (
    _extract_content_and_title,
    _html_to_markdown,
    _is_lark_url,
    clean_feishu_markdown,
)


# ============================================================
# _is_lark_url
# ============================================================
def test_is_lark_url_feishu_cn():
    assert _is_lark_url("https://ptac-xadc.feishu.cn/docx/abc")
    assert _is_lark_url("https://example.feishu.cn/wiki/abc")
    assert _is_lark_url("https://x.larksuite.com/docx/abc")
    assert _is_lark_url("https://x.feishu.com/docx/abc")


def test_is_lark_url_negative():
    assert not _is_lark_url("https://github.com/abc.md")
    assert not _is_lark_url("https://example.com/docx/abc")
    assert not _is_lark_url("")


# ============================================================
# clean_feishu_markdown：标签剥离
# ============================================================
def test_clean_strips_self_closing_tags():
    md = """# 总结
正文段落。

<whiteboard token="abc" width="800" height="600"/>

继续正文。

<image token="xyz" width="500" height="300" align="center"/>

<add-ons component-id="" component-type-id="xxx" record="{...}"/>

末尾。"""
    cleaned, removed = clean_feishu_markdown(md)
    assert "<whiteboard" not in cleaned
    assert "<image" not in cleaned
    assert "<add-ons" not in cleaned
    assert "正文段落" in cleaned
    assert "继续正文" in cleaned
    assert "末尾" in cleaned


def test_clean_unwraps_wrap_tags():
    md = """<quote-container>
会议主题：xxx
会议时间：xxx
</quote-container>

<callout emoji="📌" background-color="light-blue" border-color="light-blue">
这是一个 callout。
</callout>

正常段落。"""
    cleaned, _ = clean_feishu_markdown(md)
    assert "<quote-container>" not in cleaned
    assert "</quote-container>" not in cleaned
    assert "会议主题" in cleaned
    assert "<callout" not in cleaned
    assert "这是一个 callout" in cleaned
    assert "正常段落" in cleaned


def test_clean_replaces_mention_user():
    md = "参会人：<mention-user id=\"ou_xxx\"/><mention-user id=\"ou_yyy\"/>"
    cleaned, _ = clean_feishu_markdown(md)
    assert "<mention-user" not in cleaned
    assert "@用户" in cleaned


def test_clean_removes_mention_doc():
    md = """相关链接：[妙记](https://...)

<mention-doc token="abc" type="docx">智能纪要 6月22日</mention-doc>"""
    cleaned, _ = clean_feishu_markdown(md)
    # 包裹标签: 保留内文
    assert "智能纪要 6月22日" in cleaned
    # 自闭合标签: 整段删除
    md2 = '<mention-doc token="abc" type="docx"/>'
    cleaned2, _ = clean_feishu_markdown(md2)
    assert "<mention-doc" not in cleaned2


# ============================================================
# clean_feishu_markdown：噪音段过滤
# ============================================================
def test_clean_filters_noise_sections():
    md = """# 总结

本次会议讨论了 PTUI 组件问题。

# 会议最佳表现成员

王瑶、张三、李四

# 后续计划

继续推进相关功能。

# 相关链接

- 妙记：xxx

# 相关会议纪要

- 6月22日
- 6月21日
"""
    cleaned, removed = clean_feishu_markdown(md)
    assert "总结" in cleaned
    assert "PTUI 组件问题" in cleaned
    assert "后续计划" in cleaned
    assert "会议最佳表现成员" not in cleaned
    assert "王瑶" not in cleaned
    assert "相关链接" not in cleaned
    assert "相关会议纪要" not in cleaned
    assert removed > 0


# ============================================================
# clean_feishu_markdown：折叠空行
# ============================================================
def test_clean_collapses_blank_lines():
    md = "# 标题\n\n\n\n\n正文段落。\n\n\n\n\n\n\n末尾。"
    cleaned, _ = clean_feishu_markdown(md)
    # 连续空行折叠为最多 2 个
    assert "\n\n\n" not in cleaned
    assert "标题" in cleaned
    assert "正文段落" in cleaned


# ============================================================
# clean_feishu_markdown：边界
# ============================================================
def test_clean_empty_input():
    cleaned, removed = clean_feishu_markdown("")
    assert cleaned == ""
    assert removed == 0


def test_clean_preserves_headings_and_code_blocks():
    md = """# 总结

## PTUI 富文本问题

问题描述：xxx

```typescript
const x = 1;
```

## 其他问题

解决方案：yyy
"""
    cleaned, _ = clean_feishu_markdown(md)
    assert "## PTUI 富文本问题" in cleaned
    assert "## 其他问题" in cleaned
    assert "```typescript" in cleaned
    assert "const x = 1;" in cleaned


# ============================================================
# _extract_content_and_title：兼容老版/新版 lark-cli
# ============================================================
def test_extract_content_old_api():
    """老版 1.0.12: data.markdown + data.title"""
    data = {
        "markdown": "# 总结\n\n富文本问题",
        "title": "Code Review 6月22日",
        "doc_id": "OloEdxxx",
    }
    content, title, is_html = _extract_content_and_title(data)
    assert content == "# 总结\n\n富文本问题"
    assert title == "Code Review 6月22日"
    assert is_html is False


def test_extract_content_new_api():
    """新版 1.0.59+: data.document.content (HTML) + 从 <title> 提取"""
    data = {
        "document": {
            "content": "<title>Code Review 6月18日</title><h1>总结</h1><p>表格合并问题</p>",
            "document_id": "NJxKdkXmEohljgxpZeNcK6qVnSh",
            "revision_id": 28,
        }
    }
    content, title, is_html = _extract_content_and_title(data)
    assert "<title>" in content
    assert is_html is True
    assert title == "Code Review 6月18日"


def test_extract_content_empty():
    """两者都为空,返回空字符串"""
    data = {}
    content, title, is_html = _extract_content_and_title(data)
    assert content == ""
    assert is_html is False


def test_extract_content_new_api_no_title_tag():
    """新版但 HTML 里没 <title>,fallback 默认标题"""
    data = {"document": {"content": "<h1>总结</h1>"}}
    content, title, is_html = _extract_content_and_title(data)
    assert is_html is True
    assert title == "飞书文档"  # fallback


# ============================================================
# _html_to_markdown：轻量 HTML→markdown
# ============================================================
def test_html_to_markdown_basic():
    html = "<h1>总结</h1><p>这是<b>富文本</b>问题</p><p><code>const x = 1</code></p>"
    md = _html_to_markdown(html)
    assert "# 总结" in md
    assert "**富文本**" in md
    assert "`const x = 1`" in md


def test_html_to_markdown_lists():
    html = "<ul><li>第一项</li><li>第二项</li></ul>"
    md = _html_to_markdown(html)
    assert "- 第一项" in md
    assert "- 第二项" in md


def test_html_to_markdown_cite_user():
    """<cite type='user'> 转为 @用户名"""
    html = '<cite type="user" user-id="ou_xxx" user-name="吴朋"></cite>'
    md = _html_to_markdown(html)
    assert "@吴朋" in md


def test_html_to_markdown_cite_doc():
    """<cite type='doc'> 转为 [标题](doc:xxx)"""
    html = '<cite doc-id="abc123def456" file-type="docx" title="Code Review"></cite>'
    md = _html_to_markdown(html)
    assert "Code Review" in md
    assert "[Code Review](doc:abc123de" in md


def test_html_to_markdown_blockquote():
    html = "<blockquote><p>会议时间：xxx</p></blockquote>"
    md = _html_to_markdown(html)
    assert "会议时间" in md
    assert ">" in md  # blockquote 标记


def test_html_to_markdown_strips_unknown_tags():
    """未知标签被剥离但保留内文"""
    html = "<whiteboard token='xxx'></whiteboard><p>内容</p>"
    md = _html_to_markdown(html)
    assert "内容" in md
    assert "<whiteboard" not in md


def test_html_to_markdown_empty():
    assert _html_to_markdown("") == ""
