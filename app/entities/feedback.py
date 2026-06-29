"""Web 反馈表单 & 评审沉淀实体."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.entities.document import AssetType


class FeedbackRequest(BaseModel):
    """Web 反馈表单：评审完成后正向沉淀新案例."""

    title: str = Field(..., min_length=1, max_length=200, description="评审标题")
    code_bad: str = Field(..., min_length=1, description="错误代码片段")
    code_good: Optional[str] = Field(None, description="正确代码片段（可选）")
    review_opinion: str = Field(..., min_length=1, description="评审意见 / 整改建议")
    file_path: Optional[str] = Field(None, description="涉及文件路径（如 src/api/user.ts）")
    line_range: Optional[str] = Field(None, description="行号区间，如 'L42-L58'")
    rule_id: Optional[str] = Field(None, description="关联的规范 ID（如 RULE-001）")
    severity: str = Field("warning", description="blocker / warning / suggestion")
    tags: List[str] = Field(default_factory=list, description="标签：ts/vue/react/js")
    level: Optional[str] = Field(None, description="规范级别：必须/禁止/建议")
    asset_type: AssetType = Field(AssetType.FEEDBACK, description="资产类型，默认 feedback")
    extra: dict = Field(default_factory=dict, description="扩展字段")
