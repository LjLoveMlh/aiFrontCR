"""端到端集成测试：上传 → 检索 → 反馈 → 删除.

需要本地 Redis Stack + 真实 BGE 模型（首次会下载 ~2.3GB）。
"""

from __future__ import annotations

import socket

import pytest


def _redis_available() -> bool:
    try:
        s = socket.create_connection(("localhost", 6379), timeout=1)
        s.close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(
        not _redis_available(),
        reason="需要本地 Redis Stack",
    ),
    pytest.mark.skipif(
        True,  # 默认 skip，避免 CI 拉 BGE
        reason="端到端测试需要拉 BGE-M3 (~2.3GB)，默认 skip；本地显式 -k e2e 可跑",
    ),
]


def test_e2e_ingest_search_feedback_delete():
    from app.conf.settings import settings
    from app.entities.document import AssetType
    from app.entities.search import SearchRequest
    from app.repositories.knowledge_repo import knowledge_repo

    settings.redis_index_name = "aiFrontCR:e2e_test"
    knowledge_repo._initialized = False

    # 1) 上传
    doc = knowledge_repo.add_text(
        title="e2e 测试规范",
        text="## 规则\n\n禁止使用 any 类型。\n\n```ts\nconst x: any = 1;\n```\n",
        asset_type=AssetType.SPEC,
        tags=["ts"],
        level="禁止",
    )
    assert doc.chunk_count >= 1

    # 2) 检索
    resp = knowledge_repo.search(SearchRequest(query="禁止使用 any", top_k=3, use_rerank=False))
    assert resp.total >= 1

    # 3) 反馈沉淀
    feedback_doc = knowledge_repo.add_text(
        title="e2e 反馈案例",
        text="## 评审点\n\n不应该用 any。\n\n```ts\nconst x: any = 1;\n```\n",
        asset_type=AssetType.FEEDBACK,
        source="feedback_form",
    )
    assert feedback_doc.id != doc.id

    # 4) 删除
    n = knowledge_repo.delete_document(doc.id)
    assert n >= 1
