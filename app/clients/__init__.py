"""外部依赖单例客户端（对齐教程 *ClientManager 模式）.

设计：
- 每个外部依赖一个 ClientManager：模块级单例 + init() 懒加载
- init() 之前任何调用都会抛 RuntimeError（fail-fast）
- 业务模块从 app.clients.llm_client import llm_client 即可
"""

from app.clients.embedding_client import EmbeddingClient, embedding_client
from app.clients.llm_client import LLMClient, llm_client
from app.clients.redis_client import (
    RedisVectorClient,
    redis_vector_client,
    set_embedding_client,
)

__all__ = [
    "LLMClient",
    "llm_client",
    "EmbeddingClient",
    "embedding_client",
    "RedisVectorClient",
    "redis_vector_client",
    "set_embedding_client",
]
