"""HTML 文本清洗（飞书网页版 / 普通 HTML 文档 → Markdown 文本）."""

from __future__ import annotations

import re
from typing import Optional

import bleach
from bs4 import BeautifulSoup


def html_to_markdown(html: str) -> str:
    """HTML → 简化 Markdown 文本.

    策略：
    - 去 script / style / nav / footer
    - h1-h6 → # / ## ...
    - p → 段间空行
    - pre/code → ``` 块
    - li → "- "
    - 其余 inline 标签去标签
    """
    soup = BeautifulSoup(html, "lxml")

    # 移除噪音标签
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe"]):
        tag.decompose()

    lines: list[str] = []

    def walk(node, depth: int = 0):
        if hasattr(node, "name") and node.name is not None:
            name = node.name.lower()
            if name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
                level = int(name[1])
                text = node.get_text(separator=" ", strip=True)
                if text:
                    lines.append("\n" + "#" * level + " " + text + "\n")
                return
            if name == "p":
                text = node.get_text(separator=" ", strip=True)
                if text:
                    lines.append(text + "\n")
                return
            if name == "br":
                lines.append("\n")
                return
            if name in ["strong", "b"]:
                text = node.get_text(separator="", strip=True)
                if text:
                    lines.append(f"**{text}**")
                return
            if name in ["em", "i"]:
                text = node.get_text(separator="", strip=True)
                if text:
                    lines.append(f"*{text}*")
                return
            if name == "code" and node.parent and node.parent.name != "pre":
                text = node.get_text(separator="", strip=True)
                if text:
                    lines.append(f"`{text}`")
                return
            if name == "pre":
                code_node = node.find("code")
                code_text = code_node.get_text() if code_node else node.get_text()
                lang = ""
                if code_node and code_node.get("class"):
                    for cls in code_node.get("class", []):
                        if cls.startswith("language-"):
                            lang = cls.replace("language-", "")
                            break
                        if cls.startswith("lang-"):
                            lang = cls.replace("lang-", "")
                            break
                lines.append(f"\n```{lang}\n{code_text}\n```\n")
                return
            if name == "li":
                text = node.get_text(separator=" ", strip=True)
                if text:
                    lines.append(f"- {text}\n")
                return
            if name in ["ul", "ol"]:
                lines.append("\n")
                for child in node.children:
                    walk(child, depth + 1)
                return
            if name in ["div", "section", "article", "main"]:
                lines.append("\n")
                for child in node.children:
                    walk(child, depth + 1)
                return
            if name == "a":
                text = node.get_text(separator=" ", strip=True)
                href = node.get("href", "")
                if text:
                    if href and not href.startswith("#"):
                        lines.append(f"[{text}]({href})")
                    else:
                        lines.append(text)
                return
            if name == "img":
                alt = node.get("alt", "")
                src = node.get("src", "")
                if alt or src:
                    lines.append(f"![{alt}]({src})")
                return
            # 默认：递归子节点
            for child in node.children:
                walk(child, depth + 1)
        else:
            # text 节点
            txt = str(node).strip()
            if txt:
                lines.append(txt)

    body = soup.body if soup.body else soup
    walk(body)

    md = "".join(lines)
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = bleach.clean(md, tags=[], strip=True)
    return md.strip()


def extract_main_content(html: str) -> str:
    """提取主体内容（去导航 / 页脚等）."""
    soup = BeautifulSoup(html, "lxml")

    # 飞书 / 掘金 / CSDN 等常见正文选择器
    candidates = [
        ".docx-content",            # 飞书 docx
        ".docx_web_container",       # 飞书新版
        ".wiki-content",             # 飞书 wiki
        ".article-content",          # 掘金 / CSDN
        ".markdown-body",            # GitHub
        "article",
        "main",
        ".main-content",
    ]
    for sel in candidates:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 100:
            return html_to_markdown(str(node))

    # 兜底：整个 body
    return html_to_markdown(html)
