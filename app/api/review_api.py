"""阶段2/3 · 代码评审 JSON API.

路由：
    POST /review/code          - 单文件评审
    POST /review/code/batch    - 批量评审（串行，复用同一工作流）
    POST /review/git           - Git 评审（pre-commit 钩子 / CloudCode 用）
    GET  /review/health        - 工作流健康检查
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.agents.workflow import run_git_review, run_review
from app.entities.review import (
    GitReviewFile,
    GitReviewRequest,
    GitReviewResponse,
    ReviewRequest,
    ReviewReport,
)

router = APIRouter(prefix="/review", tags=["review"])


# ============================================================
# POST /review/code
# ============================================================
class ReviewResponse(BaseModel):
    """评审响应（API 出参）."""

    review_report: ReviewReport | None = Field(None, description="结构化评审报告")
    feedback_doc_id: str | None = Field(None, description="自动沉淀的 feedback 文档 ID")
    error: str | None = Field(None, description="整个工作流的异常信息")
    llm_error: str | None = Field(None, description="LLM 调用异常")
    elapsed_ms: float = Field(0.0, description="总耗时（毫秒）")


@router.post(
    "/code",
    response_model=ReviewResponse,
    summary="单文件代码评审",
    description="接收代码片段，返回结构化评审报告；可选自动沉淀到 feedback 知识库",
)
async def review_code(req: ReviewRequest) -> ReviewResponse:
    """运行 LangGraph 评审工作流.

    - 内部走完整 5 节点链
    - LLM 失败 / RAG 召回失败都不会阻断，最多产出空 items 的报告
    """
    if not req.code or not req.code.strip():
        raise HTTPException(status_code=400, detail="code 不能为空")

    logger.info(
        f"[review/code] 收到评审请求：file={req.file_path}, "
        f"lang={req.language}, len={len(req.code)}"
    )
    result = run_review(
        code=req.code,
        file_path=req.file_path,
        language=req.language,
        persist_feedback=req.persist_feedback,
    )
    return ReviewResponse(**result)


# ============================================================
# POST /review/code/batch
# ============================================================
class BatchReviewRequest(BaseModel):
    items: List[ReviewRequest] = Field(..., min_length=1, max_length=50)


class BatchReviewResponse(BaseModel):
    results: List[ReviewResponse]
    total: int
    elapsed_ms: float


@router.post(
    "/code/batch",
    response_model=BatchReviewResponse,
    summary="批量代码评审（串行）",
)
async def review_code_batch(req: BatchReviewRequest) -> BatchReviewResponse:
    """批量评审：按顺序串行跑每条，避免 LLM 并发限流."""
    import time

    t0 = time.time()
    results: List[ReviewResponse] = []
    for i, item in enumerate(req.items, 1):
        logger.info(f"[batch {i}/{len(req.items)}] 评审 {item.file_path or 'inline'}")
        try:
            r = run_review(
                code=item.code,
                file_path=item.file_path,
                language=item.language,
                persist_feedback=item.persist_feedback,
            )
            results.append(ReviewResponse(**r))
        except Exception as e:
            logger.error(f"[batch {i}] 评审失败：{e}")
            results.append(ReviewResponse(error=str(e)))
    elapsed_ms = (time.time() - t0) * 1000
    return BatchReviewResponse(results=results, total=len(results), elapsed_ms=elapsed_ms)


# ============================================================
# POST /review/git
# ============================================================
@router.post(
    "/git",
    response_model=GitReviewResponse,
    summary="Git 评审（pre-commit 钩子 / CloudCode）",
    description="接收 diff 文本 / commit 范围 / repo_path，走多文件 LangGraph 评审；返回是否应拦截 commit",
)
async def review_git(req: GitReviewRequest) -> GitReviewResponse:
    """Git 评审入口：4 种模式（按优先级）
    1. diff_text 非空：直接解析 diff 文本
    2. commit_range 非空：拉指定 commit 范围的 diff
    3. files 非空：直接逐文件评审（兼容老调用方）
    4. 仅 repo_path：取暂存区 diff
    """
    logger.info(
        f"[review/git] 收到请求：diff_len={len(req.diff_text or '')}, "
        f"range={req.commit_range}, repo={req.repo_path}, "
        f"files={len(req.files) if req.files else 0}"
    )

    # 模式 3：直接给 files（CloudCode / IDE 插件场景）
    if req.files and not req.diff_text and not req.commit_range:
        import time

        from app.agents.workflow import _review_single_file
        from app.core.git_ops import ChangedFile

        t0 = time.time()
        results = []
        for f in req.files:
            cf = ChangedFile(
                file_path=f.file_path,
                status="M",
                language=f.language or "unknown",
                is_binary=False,
            )
            # 给 _review_single_file 用的 duck-typed 对象
            class _FileShim:
                def __init__(self, path, lang, code):
                    self.file_path = path
                    self.language = lang
                    self._code = code
                    self.is_binary = False
                    self.status = "M"

                @property
                def added_code(self):
                    return self._code

            shim = _FileShim(f.file_path, f.language or "unknown", f.code)
            r = _review_single_file(shim, persist_feedback=req.persist_feedback)  # type: ignore
            r.line_range = f.line_range
            results.append(r)
        blocking = sum((it.review_report.blocking_count if it.review_report else 0) for it in results)
        warning = sum((it.review_report.warning_count if it.review_report else 0) for it in results)
        info = sum((it.review_report.info_count if it.review_report else 0) for it in results)
        return GitReviewResponse(
            results=results,
            total=len(results),
            blocking_count=blocking,
            warning_count=warning,
            info_count=info,
            has_blocking=blocking > 0,
            elapsed_ms=(time.time() - t0) * 1000,
            should_block_commit=req.fail_on_blocking and blocking > 0,
        )

    return run_git_review(
        repo_path=req.repo_path,
        diff_text=req.diff_text,
        commit_range=req.commit_range,
        persist_feedback=req.persist_feedback,
        fail_on_blocking=req.fail_on_blocking,
    )


# ============================================================
# GET /review/health
# ============================================================
@router.get("/health", summary="评审服务健康检查")
async def review_health() -> dict:
    """检查依赖是否就绪：embedding / redis / llm."""
    from app.clients.embedding_client import embedding_client
    from app.clients.llm_client import llm_client
    from app.clients.redis_client import redis_vector_client

    info = {
        "llm": {
            "model": llm_client.model_name,
            "initialized": llm_client._initialized,
        },
        "embedding": {
            "backend": "dashscope" if embedding_client._initialized else "lazy",
        },
        "redis": {
            "index": redis_vector_client._index_name,
            "initialized": redis_vector_client._initialized,
        },
    }
    return {"ok": True, "info": info}


# ============================================================
# GET /review/stats（阶段4 · 业务统计）
# ============================================================
@router.get("/stats", summary="评审服务业务统计")
async def review_stats() -> dict:
    """业务统计：总请求数 / LLM 耗时 / 评审拦截率."""
    from app.api.deps import usage_stats
    from app.repositories.knowledge_repo import knowledge_repo

    # 业务指标
    snapshot = usage_stats.snapshot()
    # 知识库规模
    try:
        knowledge_repo.init()
        kb_stats = knowledge_repo.stats()
    except Exception as e:
        kb_stats = {"error": str(e)}
    return {
        "ok": True,
        "usage": snapshot,
        "knowledge_base": kb_stats,
        "config": {
            "api_key_required": False,  # 从 settings 读
            "embedding_backend": "dashscope",
        },
    }