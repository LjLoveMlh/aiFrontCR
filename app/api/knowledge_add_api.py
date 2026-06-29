"""阶段4 · /knowledge/add 统一增量入库接口（CloudCode 友好）.

CloudCode 编辑器选中代码后，一键入库：
POST /knowledge/add

请求体（CloudCode 视角）：
{
  "title": "勾选代码的简短描述",
  "code": "用户选中的代码",
  "file_path": "src/foo.ts",
  "line_range": "12-25",
  "asset_type": "review_case",   // spec / review_case / feedback
  "level": "必须",                // 可选：必须/禁止/建议
  "tags": ["typescript", "react"],
  "rule_id": "RULE-007",          // 可选
  "review_opinion": "评审意见（可选，feedback 必填）",
  "code_good": "推荐写法（可选）"
}

返回：
{
  "ok": true,
  "doc_id": "uuid",
  "chunk_count": 3,
  "asset_type": "review_case"
}

vs 现有 /knowledge/api/feedback：
- /knowledge/api/feedback 仅限 feedback 类型
- /knowledge/add 支持 spec / review_case / feedback 统一入口
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.api.deps import require_api_key
from app.entities.document import AssetType
from app.repositories.knowledge_repo import knowledge_repo

router = APIRouter(prefix="/knowledge", tags=["knowledge-ingest"])


# ============================================================
# 请求 / 响应
# ============================================================
class KnowledgeAddRequest(BaseModel):
    """CloudCode 增量入库请求（统一入口）."""

    title: str = Field(..., min_length=1, max_length=200, description="知识条目标题")
    code: str = Field(..., min_length=1, description="代码片段（待入库的原始内容）")
    file_path: Optional[str] = Field(None, description="代码所在文件路径")
    line_range: Optional[str] = Field(None, description="代码行号范围，如 '12-25'")
    asset_type: AssetType = Field(
        AssetType.REVIEW_CASE, description="资产类型：spec / review_case / feedback"
    )
    level: Optional[str] = Field(None, description="级别：必须 / 禁止 / 建议（仅 spec 有意义）")
    tags: List[str] = Field(default_factory=list, description="标签：ts / vue / react / js 等")
    rule_id: Optional[str] = Field(None, description="关联规范编号，如 RULE-007")
    review_opinion: Optional[str] = Field(None, description="评审意见（feedback 必填）")
    code_good: Optional[str] = Field(None, description="推荐写法（可选）")
    source: Optional[str] = Field(
        None, description="来源标签：cloudcode / web / cli；默认 cloudcode"
    )


class KnowledgeAddResponse(BaseModel):
    ok: bool
    doc_id: str
    chunk_count: int
    asset_type: AssetType
    title: str


# ============================================================
# 端点
# ============================================================
@router.post(
    "/add",
    response_model=KnowledgeAddResponse,
    summary="知识库增量入库（CloudCode 友好）",
    description="统一入库入口：spec / review_case / feedback 三种资产类型；支持评审意见 + 推荐写法",
    dependencies=[Depends(require_api_key)],
)
async def knowledge_add(req: KnowledgeAddRequest) -> KnowledgeAddResponse:
    """CloudCode / 业务系统调用入口.

    内部流程：
    1. 校验 asset_type 与字段匹配（feedback 必填 review_opinion）
    2. 按资产类型拼接入库文本（结构化 markdown）
    3. 调 knowledge_repo.add_text 入库（自动切片 + 向量化）
    """
    # 1. 校验
    if req.asset_type == AssetType.FEEDBACK and not req.review_opinion:
        raise HTTPException(
            status_code=422,
            detail="asset_type=feedback 时 review_opinion 必填",
        )

    # 2. 拼接文本
    text = _format_for_kb(req)
    source_label = req.source or "cloudcode"
    source_map = {
        "cloudcode": "cloudcode",
        "web": "feedback_form",
        "cli": "ingest_cli",
    }
    source_value = source_map.get(source_label, "cloudcode")

    # 3. 入库
    try:
        knowledge_repo.init()
        # 把 source_value 映射到 SourceType 枚举（防 source 错填时给默认值）
        from app.entities.document import SourceType as _ST
        try:
            source_enum = _ST(source_value)
        except ValueError:
            source_enum = _ST.CLOUDCODE
        doc = knowledge_repo.add_text(
            title=req.title,
            text=text,
            asset_type=req.asset_type,
            source=source_enum,
            tags=req.tags or None,
            level=req.level,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"knowledge_add 入库失败：{e}")
        raise HTTPException(status_code=500, detail=f"入库失败：{e}")

    return KnowledgeAddResponse(
        ok=True,
        doc_id=doc.id,
        chunk_count=doc.chunk_count,
        asset_type=req.asset_type,
        title=doc.title,
    )


# ============================================================
# 辅助
# ============================================================
def _format_for_kb(req: KnowledgeAddRequest) -> str:
    """把请求拼成结构化 markdown 文本（让 chunk 切出来语义完整）."""
    file_line = req.file_path or "（未提供）"
    if req.line_range:
        file_line += f"（{req.line_range}）"
    rule_line = f"（规则 {req.rule_id}）" if req.rule_id else ""
    good_section = ""
    if req.code_good:
        good_section = f"### 代码（正确）\n```\n{req.code_good}\n```\n"
    opinion_section = ""
    if req.review_opinion:
        opinion_section = f"### 评审意见\n{req.review_opinion}\n"

    return f"""## {req.title}

### 文件
{file_line}
{rule_line}

### 代码（错误）
```
{req.code}
```

{good_section}
{opinion_section}### 级别
{req.level or '建议'}
"""
