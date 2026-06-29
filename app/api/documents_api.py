"""Web 后台 JSON API 路由 - 文档管理."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.repositories.knowledge_repo import knowledge_repo
from app.web.deps import require_admin

router = APIRouter(prefix="/knowledge/api/documents", tags=["web-api"], dependencies=[Depends(require_admin)])


@router.delete("/{doc_id}", summary="删除指定文档")
async def delete_document(doc_id: str):
    n = knowledge_repo.delete_document(doc_id)
    if n == 0:
        raise HTTPException(404, f"文档不存在或无 chunks：{doc_id}")
    return {"deleted": True, "doc_id": doc_id, "chunks_deleted": n}


@router.post("/{doc_id}/reembed", summary="重向量化指定文档")
async def reembed_document(doc_id: str):
    n = knowledge_repo.reembed_document(doc_id)
    if n == 0:
        raise HTTPException(404, f"文档不存在或无 chunks：{doc_id}")
    return {"reembedded": True, "doc_id": doc_id, "chunks": n}


@router.get("/{doc_id}", summary="获取指定文档详情")
async def get_document(doc_id: str):
    doc = knowledge_repo.get_document(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在：{doc_id}")
    return doc
