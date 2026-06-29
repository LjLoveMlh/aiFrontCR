"""文档加载器：按文件类型 dispatch 到对应切片器."""

from __future__ import annotations

from pathlib import Path
from typing import List

from langchain_core.documents import Document as LCDocument

from app.entities.document import AssetType, Document as DomainDocument
from app.services.text_splitter import (
    split_generic,
    split_markdown_spec,
    split_review_case,
)


def load_and_split(
    text: str,
    doc: DomainDocument,
) -> List[LCDocument]:
    """根据 doc.asset_type 选择切片策略."""
    base_meta = {
        "doc_id": doc.id,
        "asset_type": doc.asset_type.value,
        "tags": ",".join(doc.tags),
        "level": doc.level or "",
        "title": doc.title,
        "source": doc.source.value,
        "url": doc.url or "",
    }
    if doc.asset_type == AssetType.SPEC:
        return split_markdown_spec(text, doc.id, base_meta)
    elif doc.asset_type == AssetType.REVIEW_CASE:
        return split_review_case(text, doc.id, base_meta)
    else:
        return split_generic(text, doc.id, base_meta)


def read_text_file(path: str | Path) -> str:
    """读取本地文件为文本."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in [".md", ".markdown", ".txt"]:
        return p.read_text(encoding="utf-8")
    if suffix == ".json":
        import json

        return json.dumps(json.loads(p.read_text(encoding="utf-8")), ensure_ascii=False, indent=2)
    # 兜底
    return p.read_text(encoding="utf-8")
