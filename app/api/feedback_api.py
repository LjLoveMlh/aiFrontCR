"""Web 后台 JSON API 路由 - 反馈沉淀."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.entities.document import AssetType
from app.entities.feedback import FeedbackRequest
from app.repositories.knowledge_repo import knowledge_repo
from app.web.deps import require_admin

router = APIRouter(prefix="/knowledge/api/feedback", tags=["web-api"], dependencies=[Depends(require_admin)])


@router.post("", summary="评审反馈沉淀（自动入库为新案例）")
async def feedback(req: FeedbackRequest):
    """把评审结论作为新案例增量入库.

    供阶段2 LangGraph Agent 自动调用 / 阶段4 业务系统手动调用。
    """
    knowledge_repo.init()
    file_line = req.file_path or "（未提供）"
    if req.line_range:
        file_line += f"（{req.line_range}）"
    rule_note = f"（规则 {req.rule_id}）" if req.rule_id else ""
    good_section = ""
    if req.code_good:
        good_section = f"### 代码（正确）\n```\n{req.code_good}\n```\n"
    chunk_text = f"""## {req.title}

### 文件
{file_line}
{rule_note}

### 代码（错误）
```
{req.code_bad}
```

{good_section}

### 评审意见
{req.review_opinion}

### 级别
{req.level or '建议'}
"""
    doc = knowledge_repo.add_text(
        title=req.title,
        text=chunk_text,
        asset_type=req.asset_type,
        source="feedback_form",
        tags=req.tags,
        level=req.level,
    )
    return {"ok": True, "doc_id": doc.id, "chunks": doc.chunk_count}
