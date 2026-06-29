"""阶段2 · 代码评审结果数据模型.

由 LangGraph 工作流产出：
- Severity: 严重等级（blocking 阻断提交 / warning 建议 / info 提示）
- ReviewItem: 单条评审点
- ReviewReport: 整次评审的结构化报告

Pydantic v2 风格。所有字段都为中文友好 + 前端可序列化。
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class Severity(str, Enum):
    """评审问题严重等级.

    - BLOCKING: 阻断性问题，必须修改后才能提交（pre-commit 会拒绝）
    - WARNING: 优化建议，建议修改但不阻断
    - INFO: 仅提示，不影响提交流程
    """

    BLOCKING = "blocking"
    WARNING = "warning"
    INFO = "info"


class ReviewItem(BaseModel):
    """单条评审点.

    一条评审点对应一处代码问题，模仿历史 CR 评审人的写作风格：
    - title: 短描述（如「头像缺少 onError」）
    - rule_id: 关联的规范编号（如 RULE-007），可选
    - code_bad: 错误代码片段
    - code_good: 推荐写法，可选
    - review_opinion: 评审意见（自由文本，模仿人类评审口吻）
    - file_path / line_range: 定位信息（阶段3 由 git diff 填）
    - reference_case_id: 引用的历史 CR 文档 ID
    """

    severity: Severity = Field(..., description="严重等级")
    title: str = Field(..., description="问题标题（短描述）")
    rule_id: Optional[str] = Field(None, description="关联规范编号，如 RULE-007")
    file_path: Optional[str] = Field(None, description="文件路径（Git 阶段填）")
    line_range: Optional[str] = Field(None, description="行号范围，如 12-18")
    code_bad: str = Field(..., description="问题代码片段")
    code_good: Optional[str] = Field(None, description="推荐写法")
    review_opinion: str = Field(..., description="评审意见（自由文本）")
    reference_case_id: Optional[str] = Field(None, description="参考的历史 CR 文档 ID")


class ReviewReport(BaseModel):
    """整次评审的结构化报告.

    由 LangGraph LLM 节点产出，最终返回给调用方（CloudCode / Git 钩子）。
    """

    summary: str = Field(..., description="评审摘要（一句话总结）")
    items: List[ReviewItem] = Field(default_factory=list, description="所有评审点")
    language: Optional[str] = Field(None, description="代码语言（ts/js/vue/react）")
    file_path: Optional[str] = Field(None, description="文件路径")
    blocking_count: int = Field(0, description="阻断性问题数")
    warning_count: int = Field(0, description="建议修改数")
    info_count: int = Field(0, description="提示数")
    total: int = Field(0, description="总评审点数")
    elapsed_ms: float = Field(0.0, description="总耗时（毫秒）")
    has_blocking: bool = Field(False, description="是否有阻断性问题（pre-commit 用）")
    rag_spec_count: int = Field(0, description="召回的规范 chunk 数")
    rag_case_count: int = Field(0, description="召回的历史 CR chunk 数")
    feedback_doc_id: Optional[str] = Field(
        None, description="自动沉淀的 feedback 文档 ID"
    )

    def recompute_counts(self) -> "ReviewReport":
        """根据 items 重算各项计数."""
        self.total = len(self.items)
        self.blocking_count = sum(1 for x in self.items if x.severity == Severity.BLOCKING)
        self.warning_count = sum(1 for x in self.items if x.severity == Severity.WARNING)
        self.info_count = sum(1 for x in self.items if x.severity == Severity.INFO)
        self.has_blocking = self.blocking_count > 0
        return self


class ReviewRequest(BaseModel):
    """评审请求（API 入参）."""

    code: str = Field(..., description="待评审代码片段", min_length=1)
    file_path: Optional[str] = Field(None, description="文件路径（可选）")
    language: Optional[str] = Field(None, description="代码语言（可选，自动推断）")
    persist_feedback: bool = Field(
        True, description="评审完成后是否自动沉淀到 feedback 知识库"
    )


class GitReviewRequest(BaseModel):
    """Git 评审请求（pre-commit 钩子 / CloudCode 调用）.

    两种模式：
    1. 直接给 unified diff 文本（mode="diff"）：解析后逐文件评审
    2. 逐文件传 file_path + code（mode="files"，兼容老调用方）
    """

    repo_path: Optional[str] = Field(None, description="Git 仓库根路径（钩子场景自动探测）")
    diff_text: Optional[str] = Field(None, description="unified diff 文本（mode=diff 时用）")
    commit_range: Optional[str] = Field(None, description="commit 范围，如 HEAD~1..HEAD（mode=range 时用）")
    files: Optional[List["GitReviewFile"]] = Field(None, description="逐文件入参（mode=files 时用）")
    persist_feedback: bool = Field(
        True, description="评审完成后是否自动沉淀到 feedback 知识库"
    )
    fail_on_blocking: bool = Field(
        True, description="存在 blocking 时钩子是否失败（默认 True，pre-commit 拦截）"
    )


class GitReviewFile(BaseModel):
    """Git 评审的单个文件入参."""

    file_path: str = Field(..., description="文件路径")
    code: str = Field(..., description="新增/修改后的代码（hunk 拼接）", min_length=1)
    language: Optional[str] = Field(None, description="代码语言")
    line_range: Optional[str] = Field(None, description="行号范围，如 12-18")


class GitReviewItemResult(BaseModel):
    """单个文件的评审结果（Git 评审专用）."""

    file_path: str
    language: Optional[str] = None
    line_range: Optional[str] = None
    review_report: Optional[ReviewReport] = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0


class GitReviewResponse(BaseModel):
    """Git 评审响应：所有文件的评审结果 + 汇总."""

    results: List[GitReviewItemResult]
    total: int
    blocking_count: int
    warning_count: int
    info_count: int
    has_blocking: bool
    elapsed_ms: float
    should_block_commit: bool = Field(
        ..., description="钩子是否应拦截本次提交（fail_on_blocking + has_blocking）"
    )