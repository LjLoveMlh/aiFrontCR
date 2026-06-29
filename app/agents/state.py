"""阶段2 · LangGraph 评审工作流 State 定义.

ReviewState: TypedDict 风格的全局状态，在 5 个节点间流转.
所有字段都标注了所在节点写入/读取，方便调试.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from app.entities.review import ReviewReport, Severity
from app.entities.search import SearchResult


class ReviewState(TypedDict, total=False):
    """LangGraph 工作流的全局状态.

    节点写入顺序：
        node_receive_code -> node_rag_retrieve -> node_llm_review
        -> node_classify_severity -> node_persist_feedback
    """

    # ============ 输入（node_receive_code 写入）============
    code: str                          # 待评审代码
    file_path: Optional[str]           # 文件路径
    language: Optional[str]            # 代码语言
    persist_feedback: bool             # 是否自动沉淀

    # ============ 中间产物（node_rag_retrieve 写入）============
    rag_specs: List[SearchResult]       # 召回的规范 chunks
    rag_cases: List[SearchResult]       # 召回的历史 CR 案例 chunks
    rag_spec_text: str                  # 拼好的规范文本（喂给 LLM）
    rag_case_text: str                  # 拼好的案例文本（喂给 LLM）

    # ============ LLM 输出（node_llm_review 写入）============
    llm_raw_output: str                 # LLM 原始 JSON 文本（debug 用）
    llm_error: Optional[str]            # LLM 调用异常

    # ============ 最终输出（node_classify / node_persist 写入）============
    review_report: Optional[ReviewReport]   # 结构化报告
    feedback_doc_id: Optional[str]          # 沉淀的 feedback 文档 ID
    error: Optional[str]                    # 整个工作流的异常信息