"""/health 健康检查（阶段0 验收点之一）."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.conf.settings import settings

router = APIRouter(tags=["health"])


@router.get("/health", summary="健康检查")
async def health() -> JSONResponse:
    """返回服务存活状态 + 当前模型名.

    Returns:
        {"status", "app", "model", "version", "timestamp"}
    """
    from app import __version__

    return JSONResponse(
        {
            "status": "ok",
            "app": settings.app_name,
            "model": settings.qwen_chat_model,
            "version": __version__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
