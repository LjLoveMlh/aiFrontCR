"""阶段2 · LangGraph 工作流编排.

把 5 个节点串成线性 StateGraph，编译产物 `review_app` 供 API 层 invoke.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger
from langgraph.graph import END, StateGraph

from app.agents.nodes import (
    node_classify_severity,
    node_llm_review,
    node_persist_feedback,
    node_rag_retrieve,
    node_receive_code,
)
from app.agents.state import ReviewState
from app.core.git_ops import ChangedFile, GitOps, parse_diff_text
from app.entities.review import GitReviewItemResult, GitReviewResponse, ReviewReport


def build_workflow() -> StateGraph:
    """构造 StateGraph（线性：receive → rag → llm_review → classify → persist → END）."""
    workflow = StateGraph(ReviewState)

    workflow.add_node("receive", node_receive_code)
    workflow.add_node("rag", node_rag_retrieve)
    workflow.add_node("llm_review", node_llm_review)
    workflow.add_node("classify", node_classify_severity)
    workflow.add_node("persist", node_persist_feedback)

    workflow.set_entry_point("receive")
    workflow.add_edge("receive", "rag")
    workflow.add_edge("rag", "llm_review")
    workflow.add_edge("llm_review", "classify")
    workflow.add_edge("classify", "persist")
    workflow.add_edge("persist", END)

    return workflow


# 模块级 compile 产物（API 层直接 invoke）
review_app = build_workflow().compile()


def run_review(
    code: str,
    file_path: str | None = None,
    language: str | None = None,
    persist_feedback: bool = True,
) -> Dict[str, Any]:
    """便捷调用入口：传入参数 → invoke → 返回最终 state 字典.

    Returns:
        dict: 含 review_report / feedback_doc_id / elapsed_ms 等字段
    """
    initial: ReviewState = {
        "code": code,
        "file_path": file_path,
        "language": language,
        "persist_feedback": persist_feedback,
    }
    t0 = time.time()
    final_state = review_app.invoke(initial)
    elapsed_ms = (time.time() - t0) * 1000

    # 把耗时回填到 report（如果存在）
    report = final_state.get("review_report")
    if report is not None:
        try:
            report.elapsed_ms = elapsed_ms
        except Exception:
            pass

    logger.info(
        f"评审完成：items={getattr(report, 'total', 0)}, "
        f"blocking={getattr(report, 'blocking_count', 0)}, "
        f"elapsed={elapsed_ms:.0f}ms"
    )

    # 转 dict（Pydantic v2 用 model_dump）
    out: Dict[str, Any] = {
        "review_report": report.model_dump() if report is not None else None,
        "feedback_doc_id": final_state.get("feedback_doc_id"),
        "error": final_state.get("error"),
        "llm_error": final_state.get("llm_error"),
        "llm_raw_output": final_state.get("llm_raw_output"),
        "elapsed_ms": elapsed_ms,
    }
    return out


# ============================================================
# 阶段3 · 多文件 Git 评审
# ============================================================
def _review_single_file(
    file: ChangedFile,
    persist_feedback: bool = True,
) -> GitReviewItemResult:
    """对单个变更文件跑 LangGraph 评审."""
    t0 = time.time()
    code = file.added_code
    if not code.strip():
        return GitReviewItemResult(
            file_path=file.file_path,
            language=file.language,
            review_report=ReviewReport(
                summary="（空 hunk，跳过评审）",
                items=[],
                language=file.language,
                file_path=file.file_path,
            ),
            elapsed_ms=0.0,
        )
    try:
        result = run_review(
            code=code,
            file_path=file.file_path,
            language=file.language,
            persist_feedback=persist_feedback,
        )
        elapsed = (time.time() - t0) * 1000
        report_data = result.get("review_report") or {}
        report = ReviewReport(**report_data) if report_data else None
        return GitReviewItemResult(
            file_path=file.file_path,
            language=file.language,
            review_report=report,
            error=result.get("error"),
            elapsed_ms=elapsed,
        )
    except Exception as e:
        logger.error(f"评审文件失败 {file.file_path}: {e}")
        return GitReviewItemResult(
            file_path=file.file_path,
            language=file.language,
            error=str(e),
            elapsed_ms=(time.time() - t0) * 1000,
        )


def run_git_review(
    repo_path: Optional[str] = None,
    diff_text: Optional[str] = None,
    commit_range: Optional[str] = None,
    persist_feedback: bool = True,
    fail_on_blocking: bool = True,
) -> GitReviewResponse:
    """多文件 Git 评审入口.

    三种调用模式（按优先级）：
    1. 传 diff_text → 解析后逐文件评审
    2. 传 commit_range + repo_path → 调 git CLI 拉 diff 后逐文件评审
    3. 只传 repo_path → 取暂存区 diff 后逐文件评审
    """
    t0 = time.time()
    files: List[ChangedFile] = []

    # 模式 1：直接给 diff
    if diff_text:
        files = parse_diff_text(diff_text)
        logger.info(f"Git 评审模式=diff：解析到 {len(files)} 个文件")
    else:
        # 模式 2/3：git CLI
        ops = GitOps(repo_path or ".")
        if not ops.is_valid_repo:
            return GitReviewResponse(
                results=[],
                total=0,
                blocking_count=0,
                warning_count=0,
                info_count=0,
                has_blocking=False,
                elapsed_ms=0.0,
                should_block_commit=False,
            )
        if commit_range:
            files = ops.get_changed_files(commit_range)
            logger.info(f"Git 评审模式=range({commit_range})：解析到 {len(files)} 个文件")
        else:
            files = ops.get_staged_hunks()
            logger.info(f"Git 评审模式=staged：解析到 {len(files)} 个文件")

    # 过滤：跳过二进制 / 删除 / 空文件
    reviewable = [f for f in files if not f.is_binary and f.status != "D" and f.added_code.strip()]
    skipped = [f for f in files if f not in reviewable]
    if skipped:
        logger.info(f"跳过 {len(skipped)} 个不可评审文件：{[f.file_path for f in skipped]}")

    # 逐文件评审
    results: List[GitReviewItemResult] = []
    for f in reviewable:
        item = _review_single_file(f, persist_feedback=persist_feedback)
        results.append(item)
        if item.review_report:
            logger.info(
                f"[{f.file_path}] items={item.review_report.total}, "
                f"blocking={item.review_report.blocking_count}, "
                f"warning={item.review_report.warning_count}"
            )

    # 汇总
    blocking = sum(
        (it.review_report.blocking_count if it.review_report else 0) for it in results
    )
    warning = sum(
        (it.review_report.warning_count if it.review_report else 0) for it in results
    )
    info = sum(
        (it.review_report.info_count if it.review_report else 0) for it in results
    )
    has_blocking = blocking > 0
    elapsed = (time.time() - t0) * 1000
    should_block = fail_on_blocking and has_blocking

    return GitReviewResponse(
        results=results,
        total=len(results),
        blocking_count=blocking,
        warning_count=warning,
        info_count=info,
        has_blocking=has_blocking,
        elapsed_ms=elapsed,
        should_block_commit=should_block,
    )