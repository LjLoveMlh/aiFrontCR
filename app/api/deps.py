"""阶段4 · 外部 API 鉴权 + request_id 中间件.

外部调用方（CloudCode 编辑器 / CLI / CI 钩子）通过 `X-API-Key` header 鉴权。
- 配置 `api_keys=key1,key2,key3` 启用
- `api_key_required=False`（默认）→ 不带 key 也能调（开发模式）
- `api_key_required=True` → 必须带合法 key

request_id 中间件：
- 接收 `X-Request-Id` header 或自动生成 UUID
- 注入 loguru context（`request_id_ctx`）
- 回写到响应 header（让客户端能 trace）
"""

from __future__ import annotations

import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from typing import Optional

from fastapi import HTTPException, Request, status
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from app.conf.settings import settings
from app.core.log import clear_request_id, set_request_id


# ============================================================
# 统计（内存版，阶段4 用；阶段5 改 Redis）
# ============================================================
class UsageStats:
    """轻量级业务计数器（单进程内存版）."""

    def __init__(self) -> None:
        self.total_requests: int = 0
        self.by_endpoint: dict = defaultdict(int)
        self.by_status: dict = defaultdict(int)
        self.total_elapsed_ms: float = 0.0
        # LLM 相关
        self.llm_calls: int = 0
        self.llm_elapsed_ms_total: float = 0.0
        # 评审结果
        self.reviews_total: int = 0
        self.reviews_blocking: int = 0
        self.reviews_warning: int = 0
        self.reviews_clean: int = 0
        # 上次重置时间
        self.started_at: float = time.time()

    def record_request(self, path: str, status_code: int, elapsed_ms: float) -> None:
        self.total_requests += 1
        self.by_endpoint[path] += 1
        self.by_status[status_code] += 1
        self.total_elapsed_ms += elapsed_ms

    def record_review(self, blocking: int, warning: int, info: int) -> None:
        self.reviews_total += 1
        if blocking > 0:
            self.reviews_blocking += 1
        elif warning > 0:
            self.reviews_warning += 1
        else:
            self.reviews_clean += 1

    def record_llm(self, elapsed_ms: float) -> None:
        self.llm_calls += 1
        self.llm_elapsed_ms_total += elapsed_ms

    def snapshot(self) -> dict:
        uptime_s = time.time() - self.started_at
        avg_ms = (
            self.total_elapsed_ms / self.total_requests
            if self.total_requests > 0
            else 0.0
        )
        avg_llm_ms = (
            self.llm_elapsed_ms_total / self.llm_calls
            if self.llm_calls > 0
            else 0.0
        )
        block_rate = (
            self.reviews_blocking / self.reviews_total
            if self.reviews_total > 0
            else 0.0
        )
        return {
            "uptime_seconds": round(uptime_s, 1),
            "total_requests": self.total_requests,
            "by_endpoint": dict(self.by_endpoint),
            "by_status": dict(self.by_status),
            "avg_request_ms": round(avg_ms, 1),
            "llm": {
                "calls": self.llm_calls,
                "total_ms": round(self.llm_elapsed_ms_total, 1),
                "avg_ms": round(avg_llm_ms, 1),
            },
            "reviews": {
                "total": self.reviews_total,
                "blocking": self.reviews_blocking,
                "warning_only": self.reviews_warning,
                "clean": self.reviews_clean,
                "blocking_rate": round(block_rate, 3),
            },
        }


usage_stats = UsageStats()


# ============================================================
# API Key 鉴权依赖
# ============================================================
def _parse_api_keys() -> set[str]:
    raw = (settings.api_keys or "").strip()
    if not raw:
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


async def require_api_key(request: Request) -> str:
    """FastAPI 依赖：从 X-API-Key 校验.

    Returns:
        通过鉴权返回 key 字符串；否则 raise 401.

    行为：
    - api_key_required=False 且未配置 keys → 跳过（开发模式）
    - api_key_required=True 或已配置 keys → 必须带合法 key
    """
    if not settings.api_key_required and not _parse_api_keys():
        # 开发模式：未启用鉴权
        return "anonymous"

    key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-API-Key header",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    valid = _parse_api_keys()
    if key not in valid:
        # 安全：不暴露 key 长度 / 前缀
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key 不合法",
        )
    return key


# ============================================================
# request_id + 统计 中间件
# ============================================================
class RequestContextMiddleware(BaseHTTPMiddleware):
    """中间件：注入 request_id 到日志 context + 统计请求耗时."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # 1. 提取或生成 request_id
        rid = request.headers.get("X-Request-Id") or f"req-{uuid.uuid4().hex[:12]}"
        set_request_id(rid)
        request.state.request_id = rid

        # 2. 业务计时
        t0 = time.time()
        try:
            response = await call_next(request)
        except Exception as e:
            # 让 FastAPI 兜底处理（会变成 500）
            logger.exception(f"未捕获异常：{e}")
            clear_request_id()
            raise

        elapsed_ms = (time.time() - t0) * 1000
        # 3. 统计（跳过 /metrics / docs 自身）
        if not request.url.path.startswith(("/docs", "/openapi", "/redoc", "/knowledge/static")):
            usage_stats.record_request(
                path=request.url.path,
                status_code=response.status_code,
                elapsed_ms=elapsed_ms,
            )

        # 4. 写回 header
        response.headers["X-Request-Id"] = rid
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
        clear_request_id()
        return response
