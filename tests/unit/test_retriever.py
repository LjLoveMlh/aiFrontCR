"""多路召回单测（mock clients）."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.entities.search import SearchRequest


def test_search_request_validation():
    """SearchRequest 字段校验."""
    req = SearchRequest(query="test")
    assert req.top_k == 5
    assert req.use_rerank is True
    assert req.use_keyword is True

    # top_k 边界
    with pytest.raises(Exception):
        SearchRequest(query="test", top_k=0)
    with pytest.raises(Exception):
        SearchRequest(query="test", top_k=100)


def test_retriever_merge_hits():
    """验证合并去重逻辑."""
    from app.services.retriever import Retriever

    r = Retriever()
    vec = [
        {
            "text": "A",
            "metadata": {"chunk_id": "c1", "doc_id": "d1"},
            "vector_score": 0.1,
        },
        {
            "text": "B",
            "metadata": {"chunk_id": "c2", "doc_id": "d1"},
            "vector_score": 0.2,
        },
    ]
    kw = [
        {
            "text": "A",
            "metadata": {"chunk_id": "c1", "doc_id": "d1"},
            "keyword_score": 1.5,
        },
        {
            "text": "C",
            "metadata": {"chunk_id": "c3", "doc_id": "d2"},
            "keyword_score": 1.0,
        },
    ]
    merged = r._merge_hits(vec, kw)
    assert len(merged) == 3
    # c1 应同时有 vector_score 和 keyword_score
    c1 = next(m for m in merged if m["metadata"]["chunk_id"] == "c1")
    assert c1["vector_score"] == 0.1
    assert c1["keyword_score"] == 1.5
    assert "vector" in c1["recall_path"]
    assert "keyword" in c1["recall_path"]
