"""飞书(Lark)私域文档抓取 + markdown 清洗.

链路:
1. subprocess 调 `lark-cli docs +fetch --as bot --format json` 读私域飞书文档
2. 剥离飞书自定义标签(<whiteboard>/<image>/<add-ons>/...)
3. 过滤导航噪音段(会议最佳表现成员/相关会议纪要/相关链接)

不负责鉴权(token 由 lark-cli 内部管理,依赖 ~/.lark-cli/config.json)。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import List, Tuple

from loguru import logger

from app.conf.settings import settings


# ============================================================
# 自定义异常
# ============================================================
class LarkFetcherError(Exception):
    """飞书抓取失败基类."""


class LarkNotInstalledError(LarkFetcherError):
    """lark-cli 未安装或不在 PATH."""


class LarkAuthError(LarkFetcherError):
    """lark-cli 鉴权失败(tenant token 缺失/过期)."""


class LarkDocNotFoundError(LarkFetcherError):
    """飞书文档读不到(权限不足/链接错/已删除)."""


class LarkInvalidResponseError(LarkFetcherError):
    """lark-cli 返回结构异常."""


# ============================================================
# 数据类
# ============================================================
@dataclass
class LarkDoc:
    """飞书文档抓取结果(原始 + 清洗后)."""

    doc_id: str
    title: str
    url: str
    raw_markdown: str       # 含飞书标签的原始 markdown
    cleaned_markdown: str   # 剥离标签 + 过滤噪音后的 markdown
    raw_length: int
    cleaned_length: int
    noise_lines_removed: int  # 被过滤的噪音行数


# ============================================================
# Step 1：调 lark-cli 抓取
# ============================================================
async def fetch_lark_doc(url: str, timeout: float | None = None) -> LarkDoc:
    """调 lark-cli docs +fetch 读飞书文档,返回原始 markdown.

    异常:
        LarkNotInstalledError: lark-cli 不在 PATH
        LarkAuthError: tenant token 获取失败
        LarkDocNotFoundError: 文档读不到(权限/不存在)
        LarkInvalidResponseError: 返回 JSON 解析失败
    """
    if not url or not url.strip():
        raise ValueError("url 不能为空")
    if not _is_lark_url(url):
        raise ValueError(f"非飞书域名 URL: {url}（应包含 feishu.cn / larksuite.com / feishu.com）")

    cli_path = settings.feishu_lark_cli_path or "lark-cli"
    cmd = [cli_path, "docs", "+fetch", "--doc", url.strip(), "--as", "bot", "--format", "json"]
    timeout = timeout or settings.feishu_fetch_timeout

    logger.info(f"[lark] fetch start: url={url}, cli={cli_path}, timeout={timeout}s")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except FileNotFoundError as e:
        raise LarkNotInstalledError(
            f"lark-cli 未找到（{cli_path}），请先安装：npm i -g lark-cli"
        ) from e
    except asyncio.TimeoutError as e:
        raise LarkFetcherError(f"lark-cli 调用超时（>{timeout}s）: {url}") from e

    if proc.returncode != 0:
        err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        raise LarkFetcherError(
            f"lark-cli 退出码 {proc.returncode}：{err_text[:500]}"
        )

    raw_stdout = stdout.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw_stdout)
    except json.JSONDecodeError as e:
        raise LarkInvalidResponseError(
            f"lark-cli 返回非 JSON：{raw_stdout[:300]}"
        ) from e

    # lark-cli 错误格式: {"ok": false, "error": {"type": ..., "message": ...}}
    if not payload.get("ok"):
        err = payload.get("error") or {}
        err_type = err.get("type", "")
        err_msg = err.get("message", "")
        if err_type == "auth":
            raise LarkAuthError(
                f"lark-cli 鉴权失败（bot 身份需 app 配 docx:document:readonly scope）：{err_msg}"
            )
        if "not found" in err_msg.lower() or "permission" in err_msg.lower() or err_type == "permission":
            raise LarkDocNotFoundError(
                f"飞书文档读不到（权限不足/不存在）：{err_msg}"
            )
        # 兜底：按鉴权失败处理
        raise LarkAuthError(
            f"lark-cli 调用失败：type={err_type}, message={err_msg}"
        )

    data = payload.get("data") or {}
    # 兼容 lark-cli 两个版本：
    #   老版（1.0.12）: data.markdown / data.title / data.doc_id
    #   新版（1.0.59+）: data.document.content (HTML) / data.document.document_id
    doc_id = (
        data.get("doc_id")
        or data.get("document_id")
        or (data.get("document") or {}).get("document_id", "")
    )
    raw_content, title, is_html = _extract_content_and_title(data)
    if not raw_content:
        raise LarkInvalidResponseError(
            "lark-cli 返回内容为空（既无 data.markdown 也无 data.document.content）"
        )
    if is_html:
        logger.info(f"[lark] 新版 API: data.document.content (HTML, {len(raw_content)} 字符)")
    else:
        logger.info(f"[lark] 老版 API: data.markdown ({len(raw_content)} 字符)")

    cleaned, removed = clean_feishu_markdown(raw_content)
    if is_html:
        cleaned = _html_to_markdown(cleaned)

    logger.info(
        f"[lark] fetch ok: doc_id={doc_id}, title={title[:40]}, "
        f"raw={len(raw_content)}, cleaned={len(cleaned)}, noise_removed={removed}"
    )

    return LarkDoc(
        doc_id=doc_id,
        title=title,
        url=url,
        raw_markdown=raw_content,
        cleaned_markdown=cleaned,
        raw_length=len(raw_content),
        cleaned_length=len(cleaned),
        noise_lines_removed=removed,
    )


def _is_lark_url(url: str) -> bool:
    return bool(re.search(r"(feishu\.cn|larksuite\.com|feishu\.com)", url, re.IGNORECASE))


# ============================================================
# Step 2：飞书 markdown 清洗
# ============================================================
# 整段需要过滤的章节标题（智能纪要/会议纪要常见尾部模板）
_NOISE_HEADERS = (
    "会议最佳表现成员",
    "相关会议纪要",
    "相关链接",
    "相关文档",
    "相关日程",
)


def clean_feishu_markdown(md: str) -> Tuple[str, int]:
    """剥离飞书自定义标签 + 过滤整段噪音.

    清洗规则:
    1) 完全删除自闭合标签: <whiteboard .../> <image .../> <add-ons .../>
    2) 移除包裹标签但保留内部文字: <quote-container>...</quote-container> 等
    3) 删除整段噪音（从 H1/H2 标题到下一个 H1/H2 标题前）
    4) 折叠连续空行（>2 → 1）

    Returns:
        (cleaned_markdown, noise_lines_removed)
    """
    if not md:
        return "", 0

    removed_lines = 0
    text = md

    # 1) 自闭合标签（无内文）直接整段删除
    self_closing_pat = re.compile(
        r"<(whiteboard|image|add-ons|grid)[^>]*/>",
        re.IGNORECASE | re.DOTALL,
    )
    text, n = self_closing_pat.subn("", text)
    if n:
        logger.debug(f"[lark-clean] 自闭合标签剥离: {n} 个")

    # 2) 包裹标签：保留内文（多行）
    wrap_pat = re.compile(
        r"<(quote-container|callout|grid|column|mention-doc|mention-user)(?:\s[^>]*)?>"
        r"(.*?)"
        r"</\1>",
        re.IGNORECASE | re.DOTALL,
    )
    text = wrap_pat.sub(lambda m: m.group(2), text)

    # 自闭合的 mention-user 标签（如 `<mention-user id="..."/>`）→ 替换为 @用户
    text = re.sub(
        r'<mention-user[^>]*/?>',
        '@用户',
        text,
        flags=re.IGNORECASE,
    )
    # 自闭合的 mention-doc 标签（无内文）→ 删除
    text = re.sub(
        r'<mention-doc[^>]*/?>',
        '',
        text,
        flags=re.IGNORECASE,
    )

    # 3) 整段噪音过滤：定位标题，删该段（到下一个 # / ## 标题前）
    lines = text.split("\n")
    out: List[str] = []
    skip = False
    for line in lines:
        stripped = line.strip()
        # 检测 H1/H2 噪音标题
        m = re.match(r"^#{1,2}\s+(.+?)\s*$", stripped)
        if m and any(h in m.group(1) for h in _NOISE_HEADERS):
            skip = True
            removed_lines += 1
            continue
        # 遇到下一个 H1（## ）则停止 skip
        if skip and re.match(r"^#{1,2}\s+", stripped):
            skip = False
        if skip:
            removed_lines += 1
            continue
        out.append(line)

    text = "\n".join(out)

    # 4) 折叠连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    return text, removed_lines


# ============================================================
# Step 3：API 兼容 + HTML→markdown 转换
# ============================================================
def _extract_content_and_title(data: dict) -> tuple[str, str, bool]:
    """从 lark-cli 返回 data 中提取内容/标题,自动适配 1.0.12 / 1.0.59+ 两种格式.

    Returns:
        (raw_content, title, is_html)
        - 老版: data.markdown (markdown), data.title → (md, title, False)
        - 新版: data.document.content (HTML), title 从 <title>xxx</title> 提取 → (html, title, True)
    """
    # 优先:新版格式 data.document.content
    doc = data.get("document") or {}
    if isinstance(doc, dict) and doc.get("content"):
        content = doc.get("content", "")
        title = _extract_html_title(content) or "飞书文档"
        return content, title, True
    # 降级:老版格式 data.markdown
    md = data.get("markdown", "")
    if md:
        return md, data.get("title", "飞书文档"), False
    return "", "飞书文档", False


def _extract_html_title(html: str) -> str | None:
    """从 HTML 中提取 <title>xxx</title>."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title[:200] or None


def _html_to_markdown(html: str) -> str:
    """轻量 HTML→markdown 转换（仅处理飞书场景的常见标签）.

    不引第三方库,避免加 bs4 依赖。规则:
    - <h1>→ # / <h2>→ ## / <h3>→ ###
    - <p>...</p> → 段落（保留内文）
    - <b>/<strong> → **...**
    - <code> → `...`
    - <blockquote> → > ... (引用块)
    - <ul>/<ol>/<li> → - item
    - <cite ...> → @xxx (user-name) / [链接](href)
    - 其余 HTML 标签剥离（保留内文）
    """
    if not html:
        return ""

    text = html

    # 块级标签:转为 markdown
    text = re.sub(r"<h1[^>]*>", "\n# ", text, flags=re.IGNORECASE)
    text = re.sub(r"</h1>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<h2[^>]*>", "\n## ", text, flags=re.IGNORECASE)
    text = re.sub(r"</h2>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<h3[^>]*>", "\n### ", text, flags=re.IGNORECASE)
    text = re.sub(r"</h3>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<h4[^>]*>", "\n#### ", text, flags=re.IGNORECASE)
    text = re.sub(r"</h4>", "\n\n", text, flags=re.IGNORECASE)

    # 段落/换行
    text = re.sub(r"<p[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

    # 列表（简单处理：li 转为 "- "）
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(ul|ol)[^>]*>", "\n", text, flags=re.IGNORECASE)

    # 行内格式
    text = re.sub(r"<(b|strong)[^>]*>(.*?)</\1>", r"**\2**", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<(i|em)[^>]*>(.*?)</\1>", r"*\2*", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.IGNORECASE | re.DOTALL)

    # 引用块
    text = re.sub(r"<blockquote[^>]*>(.*?)</blockquote>",
                  lambda m: "\n> " + m.group(1).strip() + "\n",
                  text, flags=re.IGNORECASE | re.DOTALL)

    # cite 标签：user → @用户名,doc → 文档名
    def _cite_user(m: re.Match) -> str:
        attrs = m.group(0)
        name = re.search(r'user-name="([^"]+)"', attrs)
        return f"@{name.group(1)}" if name else "@用户"

    def _cite_doc(m: re.Match) -> str:
        attrs = m.group(0)
        title = re.search(r'title="([^"]+)"', attrs)
        doc_id = re.search(r'doc-id="([^"]+)"', attrs)
        name = title.group(1) if title else "文档"
        if doc_id:
            return f"[{name}](doc:{doc_id.group(1)[:8]})"
        return name

    text = re.sub(r"<cite[^>]*user-id=[^>]*>", _cite_user, text, flags=re.IGNORECASE)
    text = re.sub(r"<cite[^>]*doc-id=[^>]*>", _cite_doc, text, flags=re.IGNORECASE)
    text = re.sub(r"<cite[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</cite>", "", text, flags=re.IGNORECASE)

    # 链接
    text = re.sub(
        r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        r"[\2](\1)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # 兜底:剥离所有剩余 HTML 标签,保留内文
    text = re.sub(r"<[^>]+>", "", text)

    # 折叠空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
