"""知识库仓储集成测试（需要本地 Redis Stack）.

运行：
    # 启动 Redis Stack
    docker run -d -p 6379:6379 redis/redis-stack:latest
    # 跑测试
    pytest tests/integration/test_knowledge_repo_redis.py -v

若 Redis Stack 不可用，测试自动 skip。
"""

from __future__ import annotations

import socket

import pytest


def _redis_available() -> bool:
    """简单判断本地 Redis 是否可达."""
    try:
        s = socket.create_connection(("localhost", 6379), timeout=1)
        s.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _redis_available(),
    reason="需要本地 Redis Stack（docker run -d -p 6379:6379 redis/redis-stack:latest）",
)


@pytest.fixture
def repo():
    from app.conf.settings import settings
    from app.repositories.knowledge_repo import knowledge_repo

    # 用临时索引名隔离
    settings.redis_index_name = "aiFrontCR:test"
    knowledge_repo._initialized = False
    yield knowledge_repo
    # 清理
    try:
        from app.clients.redis_client import redis_vector_client

        if redis_vector_client._initialized:
            redis_vector_client._redis.flushdb()
    except Exception:
        pass


def test_add_and_list_documents(repo):
    from app.entities.document import AssetType

    doc = repo.add_text(
        title="测试规范",
        text="## 规则 1\n\n禁止使用 any。\n\n```ts\nconst x: any = 1;\n```\n",
        asset_type=AssetType.SPEC,
        tags=["ts"],
        level="禁止",
    )
    assert doc.id
    assert doc.chunk_count >= 1

    docs = repo.list_documents()
    assert len(docs) == 1
    assert docs[0]["id"] == doc.id
    assert docs[0]["asset_type"] == "spec"


def test_search_basic(repo):
    from app.entities.document import AssetType
    from app.entities.search import SearchRequest

    repo.add_text(
        title="any 禁止",
        text="## 规则\n\n禁止使用 any 类型，应该使用具体类型或 unknown。",
        asset_type=AssetType.SPEC,
        tags=["ts"],
    )
    req = SearchRequest(query="禁止使用 any", top_k=3, use_rerank=False)
    resp = repo.search(req)
    assert resp.total >= 1
    assert resp.results[0].text  # 有文本


def test_delete_document(repo):
    from app.entities.document import AssetType

    doc = repo.add_text(
        title="to be deleted",
        text="## test\n\ncontent",
        asset_type=AssetType.SPEC,
    )
    n = repo.delete_document(doc.id)
    assert n == doc.chunk_count
    # 列表应为空
    docs = repo.list_documents()
    assert len(docs) == 0
