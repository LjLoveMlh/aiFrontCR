"""Embedding 客户端：支持 DashScope API 或本地 BGE-M3.

默认走 DashScope（无需下载模型，免维护），本地作为可选 fallback。
两个后端都暴露统一的 embed_documents / embed_query 接口，Redis 客户端
无需关心后端差异。
"""

from __future__ import annotations

import os
from typing import List, Optional

from loguru import logger

from app.conf.settings import settings


class _DashScopeEmbeddingsAdapter:
    """轻量适配器，让 langchain-redis 可以把它当 embeddings 对象调用.

    暴露 embed_documents(list[str]) -> list[list[float]]
         embed_query(str) -> list[float]
    """

    def __init__(self, client: "EmbeddingClient") -> None:
        self._client = client

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return self._client.embed_documents(texts)

    def embed_query(self, text: str) -> List[float]:
        return self._client.embed_query(text)


class EmbeddingClient:
    """Embedding 客户端（单例 + 懒加载）."""

    VECTOR_DIM: int = 0  # init 后赋值

    def __init__(self) -> None:
        self._initialized = False
        self._model: object = None
        self._backend: str = "none"  # dashscope | huggingface | flag | none

    # ---------------------------------------------------------------- init
    def init(self) -> "EmbeddingClient":
        if self._initialized:
            return self
        backend = settings.embedding_backend.lower().strip()
        if backend == "dashscope":
            self._init_dashscope()
        elif backend in ("local", "huggingface", "flag"):
            self._init_local()
        else:
            raise ValueError(
                f"未知 embedding_backend: {backend!r}（应为 dashscope | local）"
            )
        return self

    def _init_dashscope(self) -> None:
        """走 DashScope text-embedding-v3（默认 1024 维）。"""
        if not settings.dashscope_api_key:
            raise RuntimeError("EMBEDDING_BACKEND=dashscope 但 DASHSCOPE_API_KEY 未配置")
        import dashscope

        dashscope.api_key = settings.dashscope_api_key
        self._model = _DashScopeEmbeddingsAdapter(self)
        self._backend = "dashscope"
        self.VECTOR_DIM = settings.embedding_dim
        self._initialized = True
        logger.info(
            f"EmbeddingClient [backend=dashscope, model={settings.embedding_model_id}, "
            f"dim={self.VECTOR_DIM}]"
        )

    def _init_local(self) -> None:
        """走本地 BGE-M3（langchain-huggingface → FlagEmbedding fallback）。"""
        os.environ["HF_HOME"] = str(settings.hf_home_abs)
        os.environ.setdefault("HF_HUB_CACHE", str(settings.hf_home_abs / "hub"))
        try:
            from langchain_huggingface import HuggingFaceEmbeddings

            self._model = HuggingFaceEmbeddings(
                model_name=settings.embedding_model_id,
                model_kwargs={
                    "device": settings.embedding_device,
                    "trust_remote_code": True,
                },
                encode_kwargs={"normalize_embeddings": True, "batch_size": 16},
                cache_folder=str(settings.hf_home_abs),
            )
            self._backend = "huggingface"
            self.VECTOR_DIM = 1024  # BGE-M3
            self._initialized = True
            logger.info(
                f"EmbeddingClient [backend=huggingface, model={settings.embedding_model_id}]"
            )
            return
        except Exception as e:
            logger.warning(f"HuggingFaceEmbeddings 失败，尝试 FlagEmbedding：{e}")
        try:
            from FlagEmbedding import FlagModel

            self._model = FlagModel(
                settings.embedding_model_id,
                query_instruction_for_retrieval="",
                use_fp16=False,
                cache_dir=str(settings.hf_home_abs),
            )
            self._backend = "flag"
            self.VECTOR_DIM = 1024
            self._initialized = True
            logger.info(
                f"EmbeddingClient [backend=flag, model={settings.embedding_model_id}]"
            )
            return
        except Exception as e:
            logger.error(f"FlagEmbedding 失败：{e}")
        raise RuntimeError("本地 embedding 初始化失败")

    # ---------------------------------------------------------------- api
    def _ensure_ready(self) -> None:
        if not self._initialized:
            raise RuntimeError("EmbeddingClient 未初始化，请先调用 init()")

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def model_name(self) -> str:
        return settings.embedding_model_id

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """批量向量化文档."""
        self._ensure_ready()
        if not texts:
            return []
        if self._backend == "dashscope":
            return self._dashscope_embed(texts)
        if self._backend == "huggingface":
            return self._model.embed_documents(texts)
        # flag
        return [v.tolist() for v in self._model.encode(texts)]

    def embed_query(self, text: str) -> List[float]:
        """向量化单条查询."""
        self._ensure_ready()
        if self._backend == "dashscope":
            return self._dashscope_embed([text])[0]
        if self._backend == "huggingface":
            return self._model.embed_query(text)
        return self._model.encode_queries([text])[0].tolist()

    def warmup(self) -> None:
        """预热：触发首次推理（避免第一次检索时延过长）."""
        self._ensure_ready()
        self.embed_query("warmup")

    # ---------------------------------------------------------------- dashscope
    def _dashscope_embed(self, texts: List[str]) -> List[List[float]]:
        """调 DashScope text-embedding-v3，按 10 条/批切分（v3 上限）."""
        from dashscope import TextEmbedding

        batch_size = 10  # DashScope v3 单次最多 10 条
        out: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            resp = TextEmbedding.call(
                model=TextEmbedding.Models.text_embedding_v3,
                input=batch,
                dimension=settings.embedding_dim,
            )
            if resp.status_code != 200:
                raise RuntimeError(
                    f"DashScope embed 失败：{resp.code} {resp.message}"
                )
            # resp.output['embeddings'] = [{"embedding": [...], "text_index": 0}, ...]
            embs_sorted = sorted(resp.output["embeddings"], key=lambda x: x["text_index"])
            out.extend([e["embedding"] for e in embs_sorted])
        return out


# 模块级单例
embedding_client = EmbeddingClient()
