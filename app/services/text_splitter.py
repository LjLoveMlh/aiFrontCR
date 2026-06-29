"""文档切片器（Markdown 规范 + 结构化评审记录）.

策略：
- 编码规范：先 MarkdownHeaderTextSplitter 按 H2/H3 切，再二次回切到 chunk_size
- 评审记录：按「评审点 H3」切，每个评审点一个 chunk
"""

from __future__ import annotations

import re
from typing import List

from langchain_core.documents import Document as LCDocument
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from app.conf.settings import settings


def split_markdown_spec(text: str, doc_id: str, source_metadata: dict) -> List[LCDocument]:
    """编码规范切片.

    1) MarkdownHeaderTextSplitter 按 H2/H3 切（保证一个 chunk 对应一条规则）
    2) 单个 chunk 仍过大时再用 RecursiveCharacterTextSplitter 二次切
    """
    headers_to_split_on = [
        ("##", "h2"),
        ("###", "h3"),
    ]
    md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    md_docs = md_splitter.split_text(text)

    # 二次回切
    fallback_splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", "。", ". ", " ", ""],
    )

    out: List[LCDocument] = []
    for i, d in enumerate(md_docs):
        # 二次切
        if len(d.page_content) > settings.chunk_size:
            sub_docs = fallback_splitter.split_documents([d])
        else:
            sub_docs = [d]
        for j, sub in enumerate(sub_docs):
            meta = dict(source_metadata)
            meta["doc_id"] = doc_id
            meta["chunk_index"] = len(out)
            meta["h2"] = d.metadata.get("h2", "")
            meta["h3"] = d.metadata.get("h3", "")
            out.append(LCDocument(page_content=sub.page_content, metadata=meta))

    return out


def split_review_case(text: str, doc_id: str, source_metadata: dict) -> List[LCDocument]:
    """结构化评审记录切片.

    格式约定（每条评审点）：
        ## 评审点 N - <标题>
        ### 文件
        <文件路径>
        ### 代码（错误）
        ```<lang>
        <code>
        ```
        ### 代码（正确）
        ```<lang>
        <code>
        ```
        ### 评审意见
        <意见>
        ### 级别
        必须 / 禁止 / 建议

    若文本不匹配结构（无 "## 评审点" 头），fallback 到通用切片器。
    """
    # 按 H2 切分
    parts = re.split(r"\n(?=## 评审点 )", text)
    out: List[LCDocument] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 标题提取
        first_line = part.split("\n", 1)[0]
        title_match = re.match(r"##\s+(.+)", first_line)
        # 跳过 H1 / 前言片段（不包含 "## 评审点" 头）
        if not title_match or not title_match.group(1).strip().startswith("评审点"):
            continue
        title = title_match.group(1).strip()

        # 级别提取
        level_match = re.search(r"###\s*级别\s*\n+([^\n]+)", part)
        level = level_match.group(1).strip() if level_match else None

        # 文件路径
        file_match = re.search(r"###\s*文件\s*\n+([^\n]+)", part)
        file_path = file_match.group(1).strip() if file_match else None

        # 评审意见
        opinion_match = re.search(r"###\s*评审意见\s*\n+(.+?)(?=\n###|\Z)", part, re.DOTALL)
        opinion = opinion_match.group(1).strip() if opinion_match else ""

        # 整合为 chunk 文本
        chunk_text = f"{title}\n\n{part}"
        if len(chunk_text) > settings.chunk_size * 2:
            # 过大再切
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            )
            sub_docs = splitter.split_text(chunk_text)
        else:
            sub_docs = [chunk_text]

        for j, sub in enumerate(sub_docs):
            meta = dict(source_metadata)
            meta["doc_id"] = doc_id
            meta["chunk_index"] = len(out)
            meta["h2"] = title
            meta["h3"] = ""
            meta["file_path"] = file_path or ""
            meta["level"] = level or ""
            meta["opinion"] = opinion[:200]
            out.append(LCDocument(page_content=sub, metadata=meta))

    # fallback：没匹配到结构化评审点时，用通用切片器
    if not out:
        return split_generic(text, doc_id, source_metadata)
    return out


def split_generic(text: str, doc_id: str, source_metadata: dict) -> List[LCDocument]:
    """通用切片（fallback）."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=["\n\n", "\n", "。", ". ", " ", ""],
    )
    docs = splitter.create_documents([text], metadatas=[source_metadata])
    for i, d in enumerate(docs):
        d.metadata["doc_id"] = doc_id
        d.metadata["chunk_index"] = i
    return docs
