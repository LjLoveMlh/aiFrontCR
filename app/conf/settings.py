"""全局配置：env 优先 + pydantic-settings.

设计原则：
- 密钥、URL、模型名等易变 / 敏感项：环境变量（.env 文件）
- 日志格式、路径等不易变项：conf/app_config.yaml（项目根的 conf 目录）
- 教程 ai-agents-from-zero 原用 OmegaConf；本项目改 pydantic-settings 以贴合 Docker / 12-factor
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（aiFrontCR/）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """全局配置：从 .env 读取，所有字段均可由环境变量覆盖."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==================== 阿里通义千问 ====================
    dashscope_api_key: str  # 必填
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_chat_model: str = "qwen3-max"
    qwen_temperature: float = 0.1
    qwen_max_tokens: int = 4096
    dashscope_timeout: int = 30

    # ==================== 应用配置 ====================
    app_name: str = "aiFrontCR"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False

    # ==================== 日志 ====================
    log_level: str = "INFO"
    log_dir: str = "./logs"

    # ==================== 阶段1：知识库 RAG ====================
    redis_url: str = "redis://localhost:6379/0"
    redis_index_name: str = "aiFrontCR_kb"  # 避免 : 在 RediSearch 语法里是特殊字符

    # Embedding 后端：dashscope（API，免下载）| local（BGE-M3 本地推理）
    embedding_backend: str = "dashscope"
    embedding_model_id: str = "text-embedding-v3"  # DashScope: v3 (1024 维) / v2 (1536)
    embedding_dim: int = 1024  # 必须与 embedding_model_id 实际输出一致
    embedding_device: str = "cpu"  # local 后端用
    # Rerank 后端：dashscope（gte-rerank）| local（BGE-Reranker-v2-m3）
    rerank_backend: str = "dashscope"
    rerank_model_id: str = "gte-rerank"
    hf_home: str = "./.cache/huggingface"  # local 后端的模型缓存目录

    chunk_size: int = 600
    chunk_overlap: int = 120
    retrieval_top_k: int = 5
    vector_top_k: int = 30
    keyword_top_k: int = 20
    # 阶段6 · RAG 调优：rerank 分数下限（0~1，低于此值丢弃）
    # 0 表示不过滤；建议 0.3~0.5；gte-rerank 输出约 [0, 1]
    min_rerank_score: float = 0.0

    # ==================== 阶段1：Web 后台 ====================
    admin_password: str = "admin123"  # 简易密码（生产请改）
    session_secret: str = "change-me-in-production-use-openssl-rand-hex-32"

    # ==================== 阶段4：API Key 鉴权 ====================
    # 外部调用方（CloudCode / CLI）需要带 X-API-Key header
    # 多个 key 用逗号分隔；空字符串 = 不鉴权（仅内网部署时）
    api_keys: str = ""  # 例: "key1,key2,key3"
    # API Key 是否强制校验（False=开发模式可跳过；True=生产模式）
    api_key_required: bool = False

    # ==================== 派生属性 ====================
    @property
    def log_dir_abs(self) -> Path:
        path = Path(self.log_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def hf_home_abs(self) -> Path:
        path = Path(self.hf_home)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def data_dir(self) -> Path:
        path = PROJECT_ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def uploads_dir(self) -> Path:
        path = self.data_dir / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def bootstrap_dir(self) -> Path:
        path = self.data_dir / "bootstrap"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def knowledge_base_dir(self) -> Path:
        path = self.data_dir / "knowledge_base"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例配置."""
    return Settings()


settings: Settings = get_settings()
