"""检索请求 / 响应实体."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.entities.document import AssetType, ChunkMeta


class SearchRequest(BaseModel):
    """检索请求."""

    query: str = Field(..., min_length=1, description="查询文本（代码片段 / 关键词 / 评审疑问）")
    top_k: int = Field(5, ge=1, le=50, description="最终返回条数")
    asset_types: Optional[List[AssetType]] = Field(None, description="限定资产类型；None=全部")
    tags: Optional[List[str]] = Field(None, description="限定标签：ts/vue/react/js")
    vector_top_k: Optional[int] = Field(None, ge=1, le=100, description="向量召回数；None=用 settings 默认")
    keyword_top_k: Optional[int] = Field(None, ge=1, le=100, description="关键词召回数；None=用 settings 默认")
    use_rerank: bool = Field(True, description="是否走 BGE 重排")
    use_keyword: bool = Field(True, description="是否走关键词稀疏召回")


class SearchResult(BaseModel):
    """检索响应：单个 chunk + 召回路径."""

    chunk: ChunkMeta
    text: str = Field(..., description="chunk 完整文本")
    vector_score: Optional[float] = None  # 向量距离（越小越相似）
    keyword_score: Optional[float] = None  # 关键词 BM25 分数（越大越相关）
    rerank_score: Optional[float] = None  # BGE rerank 分数（越大越相关）
    recall_path: List[str] = Field(default_factory=list, description="召回路径：vector/keyword/rerank")


class SearchResponse(BaseModel):
    """检索响应包装."""

    query: str
    results: List[SearchResult]
    total: int
    elapsed_ms: float = Field(..., description="总耗时（毫秒）")
    recall_stats: dict = Field(default_factory=dict, description="召回路径统计")
