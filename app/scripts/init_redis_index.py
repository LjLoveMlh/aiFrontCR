"""初始化 Redis 向量索引（阶段1 一次性）.

前置：
    1) 本地启动 Redis Stack：docker run -d -p 6379:6379 redis/redis-stack:latest
    2) 配置 .env 中 REDIS_URL

执行：
    python -m app.scripts.init_redis_index

行为：
    - 加载 embedding 模型（BGE-M3 自动下载）
    - 构造 RedisVectorStore
    - 创建 RediSearch 索引（含向量字段 + TEXT/TAG 字段）
    - 索引为空时不写入任何数据
"""

from __future__ import annotations

import sys

from app.clients.embedding_client import embedding_client
from app.clients.redis_client import redis_vector_client, set_embedding_client
from app.conf.settings import settings
from app.core.log import logger


def main() -> int:
    print("=" * 70)
    print("aiFrontCR · 初始化 Redis 向量索引")
    print("=" * 70)
    print(f"Redis URL: {settings.redis_url}")
    print(f"Index Name: {settings.redis_index_name}")
    print(f"Embedding Backend: {settings.embedding_backend}")
    print(f"Embedding Model: {settings.embedding_model_id}  (dim={settings.embedding_dim})")
    print(f"Rerank Backend: {settings.rerank_backend}  Model: {settings.rerank_model_id}")
    print("-" * 70)

    # 1) 初始化 embedding
    print(f"\n[1/3] 初始化 Embedding（{settings.embedding_backend}）...")
    try:
        embedding_client.init()
    except Exception as e:
        print(f"\n[FAIL] Embedding 加载失败：{e}")
        logger.exception("Embedding init failed")
        return 1
    print(f"   ✓ backend={embedding_client.backend}, dim={embedding_client.VECTOR_DIM}")

    # 2) 注入引用并初始化 Redis
    print("\n[2/3] 连接 Redis Stack ...")
    set_embedding_client(embedding_client)
    try:
        redis_vector_client.init()
    except Exception as e:
        print(f"\n[FAIL] Redis 连接失败：{e}")
        print("请确认 Redis Stack 已启动：")
        print("  docker run -d -p 6379:6379 redis/redis-stack:latest")
        return 1

    # 3) 健康检查
    print("\n[3/3] 健康检查...")
    if not redis_vector_client.health_check():
        print(f"\n[FAIL] Redis ping 失败")
        return 1
    print(f"   ✓ Redis ping OK")

    chunk_count = redis_vector_client.count_chunks()
    doc_count = redis_vector_client.count_documents()

    print("\n" + "=" * 70)
    print(f"[OK] 索引初始化完成")
    print(f"     索引名：{settings.redis_index_name}")
    print(f"     已有 chunks：{chunk_count}")
    print(f"     已有 docs：{doc_count}")
    print("=" * 70)
    print("\n下一步：")
    print("  python -m app.scripts.seed_knowledge    # 灌入 bootstrap 样例")
    print("  或启动 Web 后台自行上传：uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    sys.exit(main())
