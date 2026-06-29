"""Redis Vector 单库多类型客户端（实装）.

设计：
- 单例 + init() 懒加载
- 单向量库（aiFrontCR:kb），依靠 metadata.asset_type 区分 spec/review_case/feedback
- 向量检索走 langchain_redis.RedisVectorStore
- 关键词稀疏走原生 redis.Redis FT.SEARCH on content TEXT
- 支持按 doc_id / asset_type / tags 过滤
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from app.conf.settings import settings
from app.entities.document import AssetType, ChunkMeta, Document, SourceType

# 占位（import 在 init() 内部做，避免启动即建连）
_embedding_client = None


def set_embedding_client(client) -> None:
    """注入 embedding client（解决循环 import：redis_client 依赖 embedding_client）."""
    global _embedding_client
    _embedding_client = client


class RedisVectorClient:
    """Redis 向量库 + 关键词稀疏召回 + 元数据管理."""

    # 字段名（RediSearch schema）
    F_CONTENT = "content"
    F_VECTOR = "content_vector"
    F_DOC_ID = "doc_id"
    F_CHUNK_ID = "chunk_id"
    F_CHUNK_INDEX = "chunk_index"
    F_ASSET_TYPE = "asset_type"
    F_TAGS = "tags"
    F_LEVEL = "level"
    F_TITLE = "title"
    F_SOURCE = "source"
    F_URL = "url"
    F_CREATED_AT = "created_at"

    def __init__(self) -> None:
        self._initialized = False
        self._vs: object = None  # RedisVectorStore
        self._redis: object = None  # redis.Redis
        self._index_name: str = ""

    def init(self) -> "RedisVectorClient":
        if self._initialized:
            return self

        # 0) 先连 redis，再确保自定义 schema 存在（langchain-redis 在
        # RedisVectorStore.__init__ 里会抢先创建默认 schema，会冲掉我们的 TEXT 字段）
        import redis as redis_lib

        self._redis = redis_lib.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
        )
        self._redis.ping()

        self._index_name = settings.redis_index_name

        # 1) 先建/校验 schema（含 TEXT content + TAG 元数据）
        self._ensure_index()

        # 2) 再构造 RedisVectorStore（此时索引已存在，langchain-redis 不会再覆盖）
        from langchain_redis import RedisConfig, RedisVectorStore

        cfg = RedisConfig(
            index_name=self._index_name,
            redis_url=settings.redis_url,
            embedding_dim=settings.embedding_dim,
            distance_metric="COSINE",
            embedding_field=self.F_VECTOR,  # schema 里叫 content_vector
            vector_index_config={
                "algorithm": "HNSW",
                "ef_construction": 200,
                "M": 16,
            },
        )
        if _embedding_client is None or not _embedding_client._initialized:
            raise RuntimeError(
                "RedisVectorClient 依赖 embedding_client，"
                "请先调用 embedding_client.init()"
            )

        self._vs = RedisVectorStore(
            embeddings=_embedding_client._model,
            config=cfg,
        )

        self._initialized = True
        logger.info(
            f"RedisVectorClient 初始化成功 [index={self._index_name}, "
            f"redis_url={settings.redis_url}]"
        )
        return self

    def _ensure_ready(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "RedisVectorClient 未初始化，请先调用 redis_vector_client.init()"
            )

    def _ensure_index(self) -> None:
        """如果 RediSearch 索引不存在则创建（含 TEXT/TAG 字段）.

        使用 redisvl（redis 5.x 把 search 模块移到了 redisvl 包里）。
        """
        # 1) 已存在就直接返回
        try:
            self._redis.ft(self._index_name).info()
            logger.debug(f"RediSearch 索引 {self._index_name} 已存在")
            return
        except Exception:
            pass

        # 2) 用 redisvl 创建带 TEXT/TAG 的复合 schema
        try:
            from redisvl.schema import IndexSchema
            from redisvl.index import SearchIndex

            schema_dict = {
                "index": {
                    "name": self._index_name,
                    "prefix": f"{self._index_name}:",
                    "storage_type": "hash",
                },
                "fields": [
                    # langchain-redis 内部字段（必须存在且类型正确）
                    {"name": "text", "type": "text", "attrs": {"weight": 1.0}},
                    {"name": "_index_name", "type": "text"},  # langchain-redis 用 Text filter 查询
                    {"name": "_metadata_json", "type": "text"},
                    # 业务字段
                    {
                        "name": self.F_VECTOR,
                        "type": "vector",
                        "attrs": {
                            "algorithm": "hnsw",
                            "dims": settings.embedding_dim,
                            "distance_metric": "cosine",
                            "m": 16,
                            "ef_construction": 200,
                            "ef_runtime": 10,
                            "type": "float32",
                        },
                    },
                    {"name": self.F_CONTENT, "type": "text", "attrs": {"weight": 1.0}},
                    {"name": self.F_DOC_ID, "type": "tag"},
                    {"name": self.F_CHUNK_ID, "type": "tag"},
                    {"name": self.F_CHUNK_INDEX, "type": "numeric"},
                    {"name": self.F_ASSET_TYPE, "type": "tag"},
                    {"name": self.F_TAGS, "type": "tag"},
                    {"name": self.F_LEVEL, "type": "text"},
                    {"name": self.F_TITLE, "type": "text", "attrs": {"weight": 0.5}},
                    {"name": self.F_SOURCE, "type": "tag"},
                    {"name": self.F_URL, "type": "text"},
                    {"name": self.F_CREATED_AT, "type": "numeric", "attrs": {"sortable": True}},
                ],
            }
            schema = IndexSchema.from_dict(schema_dict)
            idx = SearchIndex(schema=schema, redis_client=self._redis)
            idx.create(overwrite=False)
            logger.info(f"已用 redisvl 创建 RediSearch 索引 {self._index_name}（含 TEXT/TAG）")
        except Exception as e:
            logger.warning(f"创建 RediSearch 索引失败（可能 langchain-redis 已自动创建）：{e}")

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    def add_chunks(
        self,
        doc: Document,
        chunks: List[ChunkMeta],
        texts: List[str],
    ) -> List[str]:
        """添加文档的所有 chunks 入库.

        Args:
            doc: 文档元信息
            chunks: 每个 chunk 的元信息（与 texts 一一对应）
            texts: 每个 chunk 的文本内容

        Returns:
            生成的 chunk_id 列表
        """
        self._ensure_ready()
        if not texts:
            return []

        ids = [c.chunk_id for c in chunks]
        metadatas = []
        for c, t in zip(chunks, texts):
            meta = c.to_redis_metadata()
            meta[self.F_CONTENT] = t  # 索引 TEXT 字段
            meta[self.F_CREATED_AT] = int(doc.created_at.timestamp())
            metadatas.append(meta)

        new_ids = self._vs.add_texts(texts, metadatas, ids=ids)
        logger.info(f"已入库 {len(new_ids)} 个 chunks to doc_id={doc.id}")
        return new_ids

    # ------------------------------------------------------------------
    # 向量检索
    # ------------------------------------------------------------------
    def similarity_search(
        self,
        query: str,
        k: int = 30,
        asset_types: Optional[List[AssetType]] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """向量相似度检索.

        Returns:
            [{"text", "metadata", "vector_score"}] 列表
        """
        self._ensure_ready()
        filter_expr = self._build_filter(asset_types, tags)
        try:
            results = self._vs.similarity_search_with_score(
                query, k=k, filter=filter_expr
            )
        except TypeError:
            # 某些版本签名不同
            results = self._vs.similarity_search_with_score(query, k=k)

        out: List[Dict[str, Any]] = []
        for doc, score in results:
            out.append({
                "text": doc.page_content,
                "metadata": dict(doc.metadata),
                "vector_score": float(score) if score is not None else None,
            })
        return out

    # ------------------------------------------------------------------
    # 关键词稀疏检索（RediSearch FT.SEARCH on content/title）
    # ------------------------------------------------------------------
    def keyword_search(
        self,
        query: str,
        k: int = 20,
        asset_types: Optional[List[AssetType]] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """关键词稀疏召回（RediSearch MATCH on content + title）."""
        self._ensure_ready()
        from redis.commands.search.query import Query

        # 简化：query 用 MATCH 做全文，对 query 字符串做最小清洗
        safe_query = _escape_redis_query(query.strip())
        if not safe_query:
            return []

        filter_expr = self._build_filter(asset_types, tags)
        if filter_expr:
            redis_query = f"({safe_query}) {filter_expr}"
        else:
            redis_query = safe_query

        try:
            q = Query(redis_query).paging(0, k).sort_by(self.F_CREATED_AT, asc=False)
            res = self._redis.ft(self._index_name).search(q)
        except Exception as e:
            logger.warning(f"keyword_search 失败：{e}")
            return []

        out: List[Dict[str, Any]] = []
        for doc in res.docs:
            meta = {
                self.F_DOC_ID: getattr(doc, self.F_DOC_ID, ""),
                self.F_CHUNK_ID: getattr(doc, self.F_CHUNK_ID, ""),
                self.F_CHUNK_INDEX: getattr(doc, self.F_CHUNK_INDEX, 0),
                self.F_ASSET_TYPE: getattr(doc, self.F_ASSET_TYPE, ""),
                self.F_TAGS: getattr(doc, self.F_TAGS, ""),
                self.F_LEVEL: getattr(doc, self.F_LEVEL, ""),
                self.F_TITLE: getattr(doc, self.F_TITLE, ""),
                self.F_SOURCE: getattr(doc, self.F_SOURCE, ""),
                self.F_URL: getattr(doc, self.F_URL, ""),
            }
            out.append({
                "text": getattr(doc, self.F_CONTENT, ""),
                "metadata": meta,
                "keyword_score": float(doc.score) if hasattr(doc, "score") else None,
            })
        return out

    # ------------------------------------------------------------------
    # 文档管理
    # ------------------------------------------------------------------
    def delete_by_doc_id(self, doc_id: str) -> int:
        """删除指定 doc_id 的所有 chunks（同步从 RediSearch + 哈希表删除）."""
        self._ensure_ready()
        deleted = 0
        # 1) 从 RediSearch 删除
        try:
            from redis.commands.search.query import Query

            q = Query(f"@{self.F_DOC_ID}:{{{_escape_tag(doc_id)}}}")
            res = self._redis.ft(self._index_name).search(q)
            for doc in res.docs:
                self._redis.delete(doc.id)
                deleted += 1
        except Exception as e:
            logger.warning(f"FT.SEARCH 删除失败，尝试散列表兜底：{e}")
            # 兜底：扫描所有 key
            pattern = f"{self._index_name}:*"
            for key in self._redis.scan_iter(match=pattern, count=100):
                try:
                    hash_doc_id = self._redis.hget(key, self.F_DOC_ID)
                    if hash_doc_id == doc_id:
                        self._redis.delete(key)
                        deleted += 1
                except Exception:
                    pass
        logger.info(f"已删除 doc_id={doc_id} 的 {deleted} 个 chunks")
        return deleted

    def list_documents(self) -> List[Dict[str, Any]]:
        """列出所有文档（按 doc_id 聚合）."""
        self._ensure_ready()
        from redis.commands.search.query import Query

        try:
            q = Query("*").paging(0, 10000)
            res = self._redis.ft(self._index_name).search(q)
        except Exception as e:
            logger.warning(f"list_documents 失败：{e}")
            return []

        # 聚合
        docs_map: Dict[str, Dict[str, Any]] = {}
        for d in res.docs:
            doc_id = getattr(d, self.F_DOC_ID, "")
            if not doc_id:
                continue
            if doc_id not in docs_map:
                docs_map[doc_id] = {
                    "id": doc_id,
                    "title": getattr(d, self.F_TITLE, ""),
                    "asset_type": getattr(d, self.F_ASSET_TYPE, ""),
                    "source": getattr(d, self.F_SOURCE, ""),
                    "url": getattr(d, self.F_URL, ""),
                    "level": getattr(d, self.F_LEVEL, ""),
                    "tags": [t for t in (getattr(d, self.F_TAGS, "") or "").split(",") if t],
                    "chunk_count": 0,
                    "created_at": int(getattr(d, self.F_CREATED_AT, 0) or 0),
                }
            docs_map[doc_id]["chunk_count"] += 1
        return sorted(docs_map.values(), key=lambda x: x["created_at"], reverse=True)

    def get_document_chunks(self, doc_id: str) -> List[Dict[str, Any]]:
        """获取指定文档的所有 chunks."""
        self._ensure_ready()
        from redis.commands.search.query import Query

        try:
            q = Query(f"@{self.F_DOC_ID}:{{{_escape_tag(doc_id)}}}").paging(0, 1000).sort_by("__chunk_index", asc=True)
            res = self._redis.ft(self._index_name).search(q)
        except Exception as e:
            logger.warning(f"get_document_chunks 失败：{e}")
            return []

        out: List[Dict[str, Any]] = []
        for d in res.docs:
            meta = {
                self.F_DOC_ID: getattr(d, self.F_DOC_ID, ""),
                self.F_CHUNK_ID: getattr(d, self.F_CHUNK_ID, ""),
                self.F_CHUNK_INDEX: getattr(d, self.F_CHUNK_INDEX, 0),
                self.F_ASSET_TYPE: getattr(d, self.F_ASSET_TYPE, ""),
                self.F_TAGS: getattr(d, self.F_TAGS, ""),
                self.F_LEVEL: getattr(d, self.F_LEVEL, ""),
                self.F_TITLE: getattr(d, self.F_TITLE, ""),
                self.F_SOURCE: getattr(d, self.F_SOURCE, ""),
                self.F_URL: getattr(d, self.F_URL, ""),
            }
            out.append({
                "text": getattr(d, self.F_CONTENT, ""),
                "metadata": meta,
            })
        return out

    def count_chunks(self) -> int:
        """总 chunk 数."""
        self._ensure_ready()
        try:
            return int(self._redis.ft(self._index_name).info().get("num_docs", 0))
        except Exception:
            return 0

    def count_documents(self) -> int:
        """不同 doc_id 数量."""
        return len(self.list_documents())

    # ------------------------------------------------------------------
    # 备份 / 恢复
    # ------------------------------------------------------------------
    def backup(self, output_path: str) -> int:
        """全量备份到 JSON（不含向量，仅元数据 + 文本）."""
        self._ensure_ready()
        docs = self.list_documents()
        backup_data = {
            "index_name": self._index_name,
            "created_at": datetime.now().isoformat(),
            "documents": [],
        }
        for d in docs:
            chunks = self.get_document_chunks(d["id"])
            backup_data["documents"].append({
                "id": d["id"],
                "title": d["title"],
                "asset_type": d["asset_type"],
                "source": d["source"],
                "url": d["url"],
                "level": d["level"],
                "tags": d["tags"],
                "created_at": d["created_at"],
                "chunks": chunks,
            })
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        logger.info(f"备份完成: {len(docs)} 个文档 → {output_path}")
        return len(docs)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _build_filter(
        self,
        asset_types: Optional[List[AssetType]],
        tags: Optional[List[str]],
    ) -> Optional[str]:
        """构造 RediSearch 过滤表达式（仅返回 None 或表达式，不含查询）."""
        clauses = []
        if asset_types:
            values = "|".join(at.value for at in asset_types)
            clauses.append(f"@{self.F_ASSET_TYPE}:{{{values}}}")
        if tags:
            # tag 字段包含任一即命中（OR 语义）
            tag_clauses = [f"@{self.F_TAGS}:{{{_escape_tag(t)}}}" for t in tags]
            clauses.append("(" + " | ".join(tag_clauses) + ")")
        return " ".join(clauses) if clauses else None

    def health_check(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:
            return False


# 模块级辅助
def EmbeddingClient_VDIM() -> int:
    """兼容旧调用：返回向量维度."""
    return settings.embedding_dim


def _escape_redis_query(q: str) -> str:
    """最小化清洗：去除控制字符 / 标点（RediSearch 简单查询可保留空格和字母）."""
    # 把中文 / 英文 / 数字 / 下划线 / 空格 保留，其他替换为空格
    import re

    cleaned = re.sub(r"[^\w\s\u4e00-\u9fff]", " ", q, flags=re.UNICODE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _escape_tag(t: str) -> str:
    """RediSearch tag 字段转义：去掉 | { } ( ) 等保留字符."""
    return t.replace("|", " ").replace("{", " ").replace("}", " ").replace("(", " ").replace(")", " ")


# 模块级单例
redis_vector_client = RedisVectorClient()
