"""Web 后台 JSON API 路由 - 检索."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.entities.search import SearchRequest
from app.repositories.knowledge_repo import knowledge_repo
from app.web.deps import require_admin

router = APIRouter(prefix="/knowledge/api", tags=["web-api"], dependencies=[Depends(require_admin)])


@router.post("/search", summary="多路检索（向量+关键词+BGE重排）")
async def search(req: SearchRequest):
    """阶段4 业务系统调用的统一检索入口."""
    knowledge_repo.init()
    return knowledge_repo.search(req).model_dump()
