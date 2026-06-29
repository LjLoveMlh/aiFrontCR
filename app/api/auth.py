"""Web 后台 JSON API 路由 - 鉴权 + 统计."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.repositories.knowledge_repo import knowledge_repo
from app.web.deps import require_admin

router = APIRouter(prefix="/knowledge/api", tags=["web-api"])


@router.get("/stats", dependencies=[Depends(require_admin)], summary="知识库统计")
async def stats():
    knowledge_repo.init()
    return knowledge_repo.stats()


@router.get("/whoami", summary="当前登录信息")
async def whoami(request: Request):
    """未登录返回 200 + is_admin=false；已登录返回 is_admin=true."""
    return {
        "is_admin": bool(request.session.get("is_admin")),
        "app_name": "aiFrontCR",
    }
