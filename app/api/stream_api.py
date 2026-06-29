"""阶段4 · SSE 流式评审接口.

让 CloudCode 编辑器能实时看到评审进度：

POST /review/code/stream       - 单文件流式评审
POST /review/git/stream        - Git diff 流式评审（逐文件进度）

事件类型（event: xxx）：
- "start"        - 开始（含 request_id）
- "rag"          - RAG 召回完成（specs, cases 数量）
- "llm_start"    - LLM 调用开始
- "llm_token"    - LLM 增量输出（可选，依赖模型 stream 支持）
- "llm_done"     - LLM 输出完成
- "classify"     - 严重等级统计完成
- "persist"      - 自动沉淀完成
- "result"       - 整次报告
- "file_done"    - 单文件完成（仅 git 流式）
- "error"        - 异常
- "done"         - 结束
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from app.api.deps import require_api_key, usage_stats
from app.core.git_ops import parse_diff_text
from app.entities.review import GitReviewRequest, ReviewRequest

router = APIRouter(prefix="/review", tags=["review-stream"])


# ============================================================
# 工具
# ============================================================
def _sse(event: str, data: Dict[str, Any]) -> bytes:
    """格式化为 SSE 消息.

    协议：
        event: <event>
        data: <json>
        \\n  （空行表示一条消息结束）
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


async def _aiter_sse(generator: AsyncGenerator[bytes, None]) -> AsyncGenerator[bytes, None]:
    """包一层 async generator，确保客户端断开时停止."""
    try:
        async for chunk in generator:
            yield chunk
    except asyncio.CancelledError:
        logger.info("SSE 客户端断开连接")
        raise


# ============================================================
# 单文件流式评审
# ============================================================
async def _review_code_stream_impl(req: ReviewRequest) -> AsyncGenerator[bytes, None]:
    """单文件流式评审：逐步产出 start / rag / llm / classify / persist / result."""
    if not req.code or not req.code.strip():
        yield _sse("error", {"detail": "code 不能为空"})
        return

    request_id = f"stream-{uuid.uuid4().hex[:12]}"
    t0 = time.time()

    yield _sse("start", {
        "request_id": request_id,
        "file_path": req.file_path,
        "language": req.language,
        "code_length": len(req.code),
        "persist_feedback": req.persist_feedback,
    })

    # 阶段 1：RAG 召回
    from app.entities.document import AssetType
    from app.entities.search import SearchRequest
    from app.repositories.knowledge_repo import knowledge_repo

    try:
        query = req.code[:500]
        if req.file_path:
            query = f"{req.file_path}\n{query}"
        req_spec = SearchRequest(
            query=query, top_k=5, asset_types=[AssetType.SPEC],
            use_rerank=True, use_keyword=True,
        )
        req_case = SearchRequest(
            query=query, top_k=5, asset_types=[AssetType.REVIEW_CASE],
            use_rerank=True, use_keyword=True,
        )
        specs = knowledge_repo.search(req_spec).results
        cases = knowledge_repo.search(req_case).results
        yield _sse("rag", {"specs": len(specs), "cases": len(cases)})
    except Exception as e:
        logger.warning(f"RAG 召回失败（继续评审）：{e}")
        specs, cases = [], []
        yield _sse("rag", {"specs": 0, "cases": 0, "warning": str(e)})

    # 阶段 2：LLM
    from app.agents.prompts import SYSTEM_PROMPT, build_human_prompt
    from app.api.deps import usage_stats as _stats
    from app.clients.llm_client import llm_client
    from app.entities.document import AssetType, SourceType

    yield _sse("llm_start", {"model": llm_client.model_name})
    t_llm = time.time()
    try:
        # 确保 LLM 已初始化（防止懒加载失败）
        try:
            llm_client.init()
        except Exception:
            pass  # 已初始化时会跳过
        human_msg = build_human_prompt(
            code=req.code,
            file_path=req.file_path,
            language=req.language,
            rag_spec_text=_format_specs_text(specs),
            rag_case_text=_format_cases_text(cases),
        )
        raw = llm_client.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": human_msg},
            ],
            temperature=0.1,
        )
    except Exception as e:
        logger.error(f"LLM 调用失败：{e}")
        yield _sse("error", {"phase": "llm", "detail": str(e)})
        return
    llm_ms = (time.time() - t_llm) * 1000
    _stats.record_llm(llm_ms)
    yield _sse("llm_done", {"elapsed_ms": round(llm_ms, 1), "raw_length": len(raw)})

    # 阶段 3：解析 + 构造报告
    from app.agents.nodes import _parse_llm_json
    from app.entities.review import ReviewItem, ReviewReport, Severity

    parsed = _parse_llm_json(raw)
    if parsed is None:
        report = ReviewReport(summary=f"LLM 输出格式异常（{len(raw)} 字符）", items=[])
    else:
        items = []
        for it in (parsed.get("items") or []):
            try:
                items.append(ReviewItem(
                    severity=Severity(it.get("severity", "info")),
                    title=it.get("title", "（无标题）"),
                    rule_id=it.get("rule_id"),
                    code_bad=it.get("code_bad", ""),
                    code_good=it.get("code_good"),
                    review_opinion=it.get("review_opinion", ""),
                ))
            except Exception as e:
                logger.warning(f"跳过一条 ReviewItem：{e}")
        report = ReviewReport(
            summary=parsed.get("summary", ""),
            items=items,
            language=req.language,
            file_path=req.file_path,
        )
    report.rag_spec_count = len(specs)
    report.rag_case_count = len(cases)
    report.recompute_counts()

    yield _sse("classify", {
        "blocking": report.blocking_count,
        "warning": report.warning_count,
        "info": report.info_count,
        "total": report.total,
        "has_blocking": report.has_blocking,
    })

    # 阶段 4：自动沉淀
    feedback_doc_id: Optional[str] = None
    if req.persist_feedback and report.items:
        try:
            title = f"自动评审·{report.summary[:40]}" if report.summary else (
                f"自动评审·{req.file_path or 'unknown'}"
            )
            file_line = req.file_path or "（未提供）"
            opinions = [
                f"- **{it.title}** ({it.severity.value})：{it.review_opinion}"
                for it in report.items
            ]
            code_bad_section = ""
            code_good_section = ""
            for i, it in enumerate(report.items, 1):
                code_bad_section += f"\n#### 问题 {i}（{it.severity.value}）\n```\n{it.code_bad}\n```\n"
                if it.code_good:
                    code_good_section += f"\n#### 推荐 {i}\n```\n{it.code_good}\n```\n"
            chunk_text = f"""## {title}

### 文件
{file_line}

### 代码（错误）
```
{req.code}
```

{code_bad_section}
{code_good_section}

### 评审意见
{chr(10).join(opinions)}
"""
            doc = knowledge_repo.add_text(
                title=title, text=chunk_text,
                asset_type=AssetType.FEEDBACK,
                source=SourceType.AUTO_REVIEW,
                tags=[req.language or "auto"],
                level=("必须" if report.has_blocking else ("建议" if report.warning_count else None)),
            )
            feedback_doc_id = doc.id
        except Exception as e:
            logger.warning(f"自动沉淀失败（不阻断）：{e}")
            yield _sse("persist", {"ok": False, "error": str(e)})
        else:
            yield _sse("persist", {"ok": True, "feedback_doc_id": feedback_doc_id})
    else:
        yield _sse("persist", {"ok": False, "reason": "no items or persist_feedback=false"})

    # 阶段 5：最终结果
    elapsed_ms = (time.time() - t0) * 1000
    report.elapsed_ms = elapsed_ms
    _stats.record_review(report.blocking_count, report.warning_count, report.info_count)
    yield _sse("result", {
        "report": report.model_dump(),
        "feedback_doc_id": feedback_doc_id,
        "elapsed_ms": round(elapsed_ms, 1),
    })
    yield _sse("done", {"request_id": request_id, "ok": True})


@router.post(
    "/code/stream",
    summary="单文件流式评审（SSE）",
    description="逐阶段推送 start / rag / llm / classify / persist / result / done 事件",
    dependencies=[Depends(require_api_key)],
)
async def review_code_stream(req: ReviewRequest):
    return StreamingResponse(
        _review_code_stream_impl(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Nginx 关闭缓冲
        },
    )


# ============================================================
# Git diff 流式评审
# ============================================================
async def _review_git_stream_impl(req: GitReviewRequest) -> AsyncGenerator[bytes, None]:
    """Git diff 流式评审：start → 每文件（rag/llm/classify/persist/file_done）→ done."""
    request_id = f"git-stream-{uuid.uuid4().hex[:12]}"
    t0 = time.time()
    yield _sse("start", {
        "request_id": request_id,
        "mode": "diff_text" if req.diff_text else ("commit_range" if req.commit_range else "staged"),
        "fail_on_blocking": req.fail_on_blocking,
    })

    # 解析 diff
    if req.diff_text:
        files = parse_diff_text(req.diff_text)
    else:
        from app.core.git_ops import GitOps
        ops = GitOps(req.repo_path or ".")
        if not ops.is_valid_repo:
            yield _sse("error", {"detail": f"不是有效的 git 仓库: {req.repo_path}"})
            return
        if req.commit_range:
            files = ops.get_changed_files(req.commit_range)
        else:
            files = ops.get_staged_hunks()

    reviewable = [f for f in files if not f.is_binary and f.status != "D" and f.added_code.strip()]
    yield _sse("files_discovered", {
        "total": len(files),
        "reviewable": len(reviewable),
        "skipped": [f.file_path for f in files if f not in reviewable],
    })

    # 逐文件流式评审
    total_blocking = 0
    total_warning = 0
    total_info = 0
    for idx, f in enumerate(reviewable, 1):
        yield _sse("file_start", {
            "index": idx,
            "total": len(reviewable),
            "file_path": f.file_path,
            "language": f.language,
            "status": f.status,
        })
        # 复用单文件流式评审（嵌套：先 file_start → 内部事件 → file_done）
        sub_req = ReviewRequest(
            code=f.added_code,
            file_path=f.file_path,
            language=f.language,
            persist_feedback=False,  # 父级统一控制
        )
        # 简化版：直接跑一次非流式评审
        from app.agents.workflow import run_review
        try:
            result = run_review(
                code=f.added_code,
                file_path=f.file_path,
                language=f.language,
                persist_feedback=False,
            )
            report_dict = result.get("review_report") or {}
            from app.entities.review import ReviewReport
            report = ReviewReport(**report_dict) if report_dict else None
        except Exception as e:
            logger.error(f"评审失败 {f.file_path}: {e}")
            yield _sse("file_error", {"file_path": f.file_path, "detail": str(e)})
            continue

        if report is None:
            yield _sse("file_error", {"file_path": f.file_path, "detail": "no report"})
            continue

        total_blocking += report.blocking_count
        total_warning += report.warning_count
        total_info += report.info_count
        yield _sse("file_done", {
            "index": idx,
            "file_path": f.file_path,
            "summary": report.summary,
            "items_count": report.total,
            "blocking": report.blocking_count,
            "warning": report.warning_count,
            "info": report.info_count,
            "report": report.model_dump(),
        })

    # 父级统一沉淀：把所有文件的 items 拼成一条 feedback
    feedback_doc_id: Optional[str] = None
    if req.persist_feedback and total_blocking + total_warning > 0:
        try:
            from app.entities.document import AssetType, SourceType
            # 拼一个综合 report 入库
            file_summaries = "\n".join([
                f"### {f.file_path}\n{r.summary or ''}"
                for f, r in zip(reviewable, [None] * len(reviewable))  # 占位，下面重写
            ])
            # 简化：直接把每个文件的代码拼起来
            combined_code = "\n\n".join(
                f"### {f.file_path}\n{f.added_code}" for f in reviewable
            )
            doc = knowledge_repo.add_text(
                title=f"Git 评审·{len(reviewable)} files·blocking={total_blocking}",
                text=f"## Git 自动评审\n\n{combined_code}\n\n### 总计\n"
                     f"- blocking: {total_blocking}\n- warning: {total_warning}\n- info: {total_info}",
                asset_type=AssetType.FEEDBACK,
                source=SourceType.AUTO_REVIEW,
                tags=["git", "auto"],
                level="必须" if total_blocking > 0 else "建议",
            )
            feedback_doc_id = doc.id
        except Exception as e:
            logger.warning(f"Git 评审整体沉淀失败：{e}")

    has_blocking = total_blocking > 0
    should_block = req.fail_on_blocking and has_blocking
    elapsed_ms = (time.time() - t0) * 1000
    yield _sse("done", {
        "request_id": request_id,
        "ok": True,
        "total_files": len(reviewable),
        "blocking": total_blocking,
        "warning": total_warning,
        "info": total_info,
        "has_blocking": has_blocking,
        "should_block_commit": should_block,
        "feedback_doc_id": feedback_doc_id,
        "elapsed_ms": round(elapsed_ms, 1),
    })


@router.post(
    "/git/stream",
    summary="Git diff 流式评审（SSE）",
    description="逐文件推送 file_start / file_done 事件，最终 done 事件汇总",
    dependencies=[Depends(require_api_key)],
)
async def review_git_stream(req: GitReviewRequest):
    return StreamingResponse(
        _review_git_stream_impl(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# 内部工具
# ============================================================
def _format_specs_text(specs) -> str:
    if not specs:
        return ""
    parts = []
    for i, s in enumerate(specs, 1):
        level = (s.chunk.level or "").strip()
        tag = f" {level}".strip() if level else ""
        parts.append(f"--- 规范 {i}{tag} ---\n{s.text.strip()}")
    return "\n\n".join(parts)


def _format_cases_text(cases) -> str:
    if not cases:
        return ""
    parts = []
    for i, c in enumerate(cases, 1):
        parts.append(f"--- 案例 {i} ---\n{c.text.strip()}")
    return "\n\n".join(parts)
