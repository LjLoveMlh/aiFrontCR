"""知识库仓储（KnowledgeRepository）.

业务串联：
- 接收 Document + 文本
- 调用 embedding_client + text_splitter 切片
- 写入 redis_vector_client
- 提供检索 / 文档管理接口
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from app.clients.embedding_client import embedding_client
from app.clients.redis_client import redis_vector_client
from app.conf.settings import settings
from app.entities.document import AssetType, ChunkMeta, Document, SourceType
from app.entities.search import SearchRequest, SearchResponse
from app.services.document_loader import load_and_split, read_text_file
from app.services.retriever import retriever


class KnowledgeRepository:
    """知识库仓储（单例 + 懒加载）."""

    def __init__(self) -> None:
        self._initialized = False

    def init(self) -> "KnowledgeRepository":
        if self._initialized:
            return self
        # 依赖链：embedding → redis → retriever
        if not embedding_client._initialized:
            embedding_client.init()
        if not redis_vector_client._initialized:
            # 注入 embedding 引用（解决循环 import）
            from app.clients.redis_client import set_embedding_client

            set_embedding_client(embedding_client)
            redis_vector_client.init()
        self._initialized = True
        return self

    def _ensure(self) -> None:
        if not self._initialized:
            self.init()

    # ------------------------------------------------------------------
    # 入库
    # ------------------------------------------------------------------
    def add_text(
        self,
        title: str,
        text: str,
        asset_type: AssetType = AssetType.SPEC,
        source: SourceType = SourceType.UPLOAD,
        url: Optional[str] = None,
        tags: Optional[List[str]] = None,
        level: Optional[str] = None,
        doc_id: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Document:
        """入库纯文本（自动切片 + embedding）."""
        self._ensure()
        doc = Document(
            id=doc_id or str(uuid.uuid4()),
            title=title,
            source=source,
            url=url,
            asset_type=asset_type,
            tags=tags or [],
            level=level,
            extra=extra or {},
        )

        # 切片
        lc_docs = load_and_split(text, doc)
        if not lc_docs:
            logger.warning(f"文档 {doc.id} 切片为空，跳过入库")
            return doc

        # 构造 ChunkMeta + texts
        chunks: List[ChunkMeta] = []
        texts: List[str] = []
        for lc in lc_docs:
            cid = f"{doc.id}:{lc.metadata.get('chunk_index', len(chunks))}"
            try:
                at = AssetType(lc.metadata.get("asset_type", doc.asset_type.value))
            except ValueError:
                at = doc.asset_type
            try:
                src = SourceType(lc.metadata.get("source", doc.source.value))
            except ValueError:
                src = doc.source
            chunk = ChunkMeta(
                doc_id=doc.id,
                chunk_id=cid,
                chunk_index=lc.metadata.get("chunk_index", len(chunks)),
                asset_type=at,
                tags=[t for t in (lc.metadata.get("tags", "") or "").split(",") if t] or doc.tags,
                level=lc.metadata.get("level") or doc.level,
                title=doc.title,
                source=src,
                url=doc.url,
            )
            chunks.append(chunk)
            texts.append(lc.page_content)

        # 入库
        redis_vector_client.add_chunks(doc, chunks, texts)
        doc.chunk_count = len(chunks)

        # 同步记录 document 元信息到 Redis Hash（用于文档列表/详情）
        self._save_doc_meta(doc)
        return doc

    def add_file(
        self,
        file_path: str,
        title: Optional[str] = None,
        asset_type: Optional[AssetType] = None,
        tags: Optional[List[str]] = None,
        level: Optional[str] = None,
        source: SourceType = SourceType.UPLOAD,
    ) -> Document:
        """从本地文件入库."""
        self._ensure()
        text = read_text_file(file_path)
        if not title:
            title = file_path.split("/")[-1]
        # 文件级自动判定 asset_type
        if asset_type is None:
            asset_type = self._guess_asset_type(file_path, text)
        return self.add_text(
            title=title,
            text=text,
            asset_type=asset_type,
            source=source,
            tags=tags,
            level=level,
        )

    def add_url(
        self,
        title: str,
        url: str,
        text: str,
        content_type: str,
        source_label: str,
        asset_type: AssetType = AssetType.SPEC,
        tags: Optional[List[str]] = None,
        level: Optional[str] = None,
    ) -> Document:
        """入库在线抓取的内容."""
        # 根据 source_label 映射 SourceType
        source_map = {
            "feishu_public": SourceType.URL_FEISHU,
            "public_md": SourceType.URL_PUBLIC_MD,
            "public_html": SourceType.URL_PUBLIC_MD,
        }
        source = source_map.get(source_label, SourceType.URL_PUBLIC_MD)
        return self.add_text(
            title=title,
            text=text,
            asset_type=asset_type,
            source=source,
            url=url,
            tags=tags,
            level=level,
        )

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(self, req: SearchRequest) -> SearchResponse:
        self._ensure()
        return retriever.search(req)

    # ------------------------------------------------------------------
    # 文档管理
    # ------------------------------------------------------------------
    def list_documents(self) -> List[Dict[str, Any]]:
        self._ensure()
        return redis_vector_client.list_documents()

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure()
        for d in redis_vector_client.list_documents():
            if d["id"] == doc_id:
                d["chunks"] = redis_vector_client.get_document_chunks(doc_id)
                return d
        return None

    def delete_document(self, doc_id: str) -> int:
        self._ensure()
        deleted = redis_vector_client.delete_by_doc_id(doc_id)
        self._delete_doc_meta(doc_id)
        return deleted

    def reembed_document(self, doc_id: str) -> int:
        """重新向量化指定文档的所有 chunks."""
        self._ensure()
        doc_info = self.get_document(doc_id)
        if not doc_info:
            return 0
        chunks = doc_info.get("chunks", [])
        if not chunks:
            return 0
        # 先删除再入库
        redis_vector_client.delete_by_doc_id(doc_id)
        # 重建
        full_text = "\n\n".join(c["text"] for c in chunks)
        try:
            asset_type = AssetType(doc_info["asset_type"])
        except ValueError:
            asset_type = AssetType.UNKNOWN
        try:
            source = SourceType(doc_info["source"])
        except ValueError:
            source = SourceType.UPLOAD
        # 用原始 doc_id 重建
        new_doc = self.add_text(
            title=doc_info["title"],
            text=full_text,
            asset_type=asset_type,
            source=source,
            url=doc_info.get("url") or None,
            tags=doc_info.get("tags", []),
            level=doc_info.get("level") or None,
            doc_id=doc_id,
        )
        return new_doc.chunk_count

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        self._ensure()
        docs = redis_vector_client.list_documents()
        chunk_count = redis_vector_client.count_chunks()
        # 按类型聚合
        by_type: Dict[str, int] = {}
        for d in docs:
            t = d.get("asset_type", "unknown") or "unknown"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "document_count": len(docs),
            "chunk_count": chunk_count,
            "by_type": by_type,
            "index_name": settings.redis_index_name,
            "redis_url": settings.redis_url,
        }

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _save_doc_meta(self, doc: Document) -> None:
        """把 document 元信息存到 Redis Hash（独立于向量索引，方便文档级管理）."""
        from app.clients.redis_client import redis_vector_client as rvc

        rvc._redis.hset(
            f"{rvc._index_name}:docs",
            doc.id,
            json_dumps({
                "id": doc.id,
                "title": doc.title,
                "asset_type": doc.asset_type.value,
                "source": doc.source.value,
                "url": doc.url or "",
                "level": doc.level or "",
                "tags": doc.tags,
                "chunk_count": doc.chunk_count,
                "created_at": int(doc.created_at.timestamp()),
            }),
        )

    def _delete_doc_meta(self, doc_id: str) -> None:
        from app.clients.redis_client import redis_vector_client as rvc

        rvc._redis.hdel(f"{rvc._index_name}:docs", doc_id)

    def _guess_asset_type(self, file_path: str, text: str) -> AssetType:
        """根据文件路径 / 内容启发式判定 asset_type."""
        p = file_path.lower()
        if "/specs/" in p or "/spec/" in p or "claude.md" in p or "rule" in p:
            return AssetType.SPEC
        if "/reviews/" in p or "/review_case/" in p or "cr" in p.split("/")[-1] or "pr_" in p:
            return AssetType.REVIEW_CASE
        # 内容启发
        if "## 评审点" in text or "## 规则" in text:
            if "## 规则" in text:
                return AssetType.SPEC
            return AssetType.REVIEW_CASE
        return AssetType.SPEC


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


# 模块级单例
knowledge_repo = KnowledgeRepository()
