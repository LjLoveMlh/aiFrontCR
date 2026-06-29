"""多路召回服务：向量 + 关键词 + BGE 重排序."""

from __future__ import annotations

import time
from typing import List, Optional

from loguru import logger

from app.clients.embedding_client import embedding_client
from app.clients.redis_client import redis_vector_client
from app.conf.settings import settings
from app.entities.document import AssetType, ChunkMeta
from app.entities.search import SearchRequest, SearchResponse, SearchResult


class Retriever:
    """多路召回器（单例 + 懒加载）."""

    def __init__(self) -> None:
        self._initialized = False
        self._rerank_model: object = None

    def init(self) -> "Retriever":
        if self._initialized:
            return self
        if not redis_vector_client._initialized:
            redis_vector_client.init()
        if not embedding_client._initialized:
            embedding_client.init()
        # 懒加载 rerank（首次 search 才真正加载）
        self._initialized = True
        return self

    def _ensure_rerank(self):
        if self._rerank_model is not None:
            return
        backend = settings.rerank_backend.lower().strip()
        if backend == "dashscope":
            if not settings.dashscope_api_key:
                logger.warning("RERANK_BACKEND=dashscope 但 DASHSCOPE_API_KEY 未配置，禁用 rerank")
                self._rerank_model = False
                return
            import dashscope

            dashscope.api_key = settings.dashscope_api_key
            self._rerank_model = {"backend": "dashscope"}
            logger.info(f"Reranker [backend=dashscope, model={settings.rerank_model_id}]")
            return
        # local（默认）：FlagReranker
        try:
            from FlagEmbedding import FlagReranker

            self._rerank_model = FlagReranker(
                settings.rerank_model_id,
                use_fp16=False,
                cache_dir=str(settings.hf_home_abs),
            )
            # FlagReranker 不带 backend 字段，用 .compute_score 接口识别
            self._rerank_model = {"backend": "local", "model": self._rerank_model}
            logger.info(f"Reranker [backend=local, model={settings.rerank_model_id}]")
        except Exception as e:
            logger.warning(f"FlagReranker 加载失败，禁用 rerank：{e}")
            self._rerank_model = False

    def search(self, req: SearchRequest) -> SearchResponse:
        """执行多路召回."""
        if not self._initialized:
            self.init()
        self._ensure_rerank()
        start = time.time()

        vec_top_k = req.vector_top_k or settings.vector_top_k
        kw_top_k = req.keyword_top_k or settings.keyword_top_k
        top_k = req.top_k

        vec_hits: List[dict] = []
        kw_hits: List[dict] = []

        # 1) 向量召回
        try:
            vec_hits = redis_vector_client.similarity_search(
                query=req.query,
                k=vec_top_k,
                asset_types=req.asset_types,
                tags=req.tags,
            )
        except Exception as e:
            logger.warning(f"向量召回失败：{e}")

        # 2) 关键词召回
        if req.use_keyword:
            try:
                kw_hits = redis_vector_client.keyword_search(
                    query=req.query,
                    k=kw_top_k,
                    asset_types=req.asset_types,
                    tags=req.tags,
                )
            except Exception as e:
                logger.warning(f"关键词召回失败：{e}")

        # 3) 合并去重
        merged = self._merge_hits(vec_hits, kw_hits)
        logger.info(f"召回合并: vector={len(vec_hits)}, keyword={len(kw_hits)}, merged={len(merged)}")

        # 4) BGE 重排
        if req.use_rerank and self._rerank_model and self._rerank_model is not False and merged:
            try:
                merged = self._rerank(req.query, merged, top_n=vec_top_k)  # rerank top-K 后再取 top_n
            except Exception as e:
                logger.warning(f"rerank 失败：{e}")

        # 4.5) 阶段6：按 rerank 分数过滤低质量召回
        min_rerank = settings.min_rerank_score
        if min_rerank > 0 and merged and any(h.get("rerank_score") is not None for h in merged):
            before = len(merged)
            merged = [h for h in merged if (h.get("rerank_score") or 0) >= min_rerank]
            if before != len(merged):
                logger.info(f"rerank 分数过滤: {before} -> {len(merged)} (min_rerank_score={min_rerank})")

        # 5) 取 top_n
        final = merged[:top_k]

        # 6) 构造响应
        results: List[SearchResult] = []
        for h in final:
            meta_dict = h.get("metadata", {})
            try:
                chunk = ChunkMeta(
                    doc_id=meta_dict.get("doc_id", ""),
                    chunk_id=meta_dict.get("chunk_id", ""),
                    chunk_index=int(meta_dict.get("chunk_index", 0)),
                    asset_type=AssetType(meta_dict.get("asset_type", "unknown")),
                    tags=[t for t in (meta_dict.get("tags", "") or "").split(",") if t],
                    level=meta_dict.get("level") or None,
                    title=meta_dict.get("title", ""),
                    source=meta_dict.get("source", "upload"),
                    url=meta_dict.get("url") or None,
                    score=h.get("rerank_score") or h.get("vector_score") or h.get("keyword_score"),
                    text=h.get("text", "")[:200],
                )
            except Exception as e:
                logger.warning(f"ChunkMeta 构造失败：{e}, meta={meta_dict}")
                continue
            results.append(
                SearchResult(
                    chunk=chunk,
                    text=h.get("text", ""),
                    vector_score=h.get("vector_score"),
                    keyword_score=h.get("keyword_score"),
                    rerank_score=h.get("rerank_score"),
                    recall_path=h.get("recall_path", []),
                )
            )

        elapsed_ms = (time.time() - start) * 1000
        return SearchResponse(
            query=req.query,
            results=results,
            total=len(results),
            elapsed_ms=elapsed_ms,
            recall_stats={
                "vector_hits": len(vec_hits),
                "keyword_hits": len(kw_hits),
                "merged_before_rerank": len(merged),
                "final": len(final),
            },
        )

    def _merge_hits(self, vec_hits: List[dict], kw_hits: List[dict]) -> List[dict]:
        """按 chunk_id 合并去重，保留各路 score 和 recall_path."""
        merged: dict = {}
        for h in vec_hits:
            cid = h["metadata"].get("chunk_id") or h["metadata"].get("doc_id", "")
            h["recall_path"] = ["vector"]
            merged[cid] = h
        for h in kw_hits:
            cid = h["metadata"].get("chunk_id") or h["metadata"].get("doc_id", "")
            if cid in merged:
                merged[cid]["keyword_score"] = h.get("keyword_score")
                merged[cid]["recall_path"].append("keyword")
            else:
                h["recall_path"] = ["keyword"]
                merged[cid] = h
        return list(merged.values())

    def _rerank(self, query: str, hits: List[dict], top_n: int) -> List[dict]:
        """按 backend 分发重排."""
        if not hits:
            return []
        if isinstance(self._rerank_model, dict):
            backend = self._rerank_model.get("backend")
            if backend == "dashscope":
                return self._dashscope_rerank(query, hits, top_n)
            if backend == "local":
                return self._local_rerank(query, hits, top_n)
        return hits[:top_n]

    def _dashscope_rerank(self, query: str, hits: List[dict], top_n: int) -> List[dict]:
        """走 DashScope gte-rerank."""
        from dashscope import TextReRank

        docs = [h.get("text", "") for h in hits]
        try:
            resp = TextReRank.call(
                model=TextReRank.Models.gte_rerank,
                query=query,
                documents=docs,
                top_n=min(top_n, len(docs)),
                return_documents=False,
            )
        except Exception as e:
            logger.warning(f"DashScope rerank 异常，fallback 原顺序：{e}")
            return hits[:top_n]
        if resp.status_code != 200:
            logger.warning(f"DashScope rerank 失败：{resp.code} {resp.message}，fallback 原顺序")
            return hits[:top_n]
        out: List[dict] = []
        for r in resp.output.results:
            idx = int(r["index"])
            if 0 <= idx < len(hits):
                h = dict(hits[idx])
                h["rerank_score"] = float(r["relevance_score"])
                out.append(h)
        return out if out else hits[:top_n]

    def _local_rerank(self, query: str, hits: List[dict], top_n: int) -> List[dict]:
        """本地 BGE FlagReranker."""
        model = (
            self._rerank_model.get("model")
            if isinstance(self._rerank_model, dict)
            else self._rerank_model
        )
        if model is None:
            return hits[:top_n]
        pairs = [[query, h.get("text", "")] for h in hits]
        scores = model.compute_score(pairs, normalize=True)
        if not isinstance(scores, list):
            scores = [scores]
        for h, s in zip(hits, scores):
            h["rerank_score"] = float(s)
        hits.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        return hits[:top_n]


# 模块级单例
retriever = Retriever()
