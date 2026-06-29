"""飞书 CR 文档解析入库 API · POST /knowledge/import_feishu.

解决阶段1 url_fetcher 的两个痛点:
1. 私域飞书链接读不到 → 通过 `lark-cli docs +fetch --as bot` (tenant token 鉴权)
2. 飞书会议纪要格式与项目 CR 格式不匹配 → FeishuParserAgent 用 LLM 重写

链路:
    POST /knowledge/import_feishu
        → LarkFetcher.fetch_lark_doc (lark-cli 抓取 + markdown 清洗)
        → FeishuParserAgent.rewrite (LLM 重写为 ## 评审点 N 结构)
        → knowledge_repo.add_text (按 REVIEW_CASE 切片 + 向量化入库)
        → 返回 {ok, doc_id, points_count, chunk_count, ...}
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.agents.feishu_parser import FeishuParserAgent
from app.api.deps import require_api_key
from app.entities.document import AssetType, SourceType
from app.repositories.knowledge_repo import knowledge_repo
from app.services.lark_fetcher import (
    LarkAuthError,
    LarkDocNotFoundError,
    LarkFetcherError,
    LarkInvalidResponseError,
    LarkNotInstalledError,
    fetch_lark_doc,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge-feishu"])


# ============================================================
# 请求 / 响应
# ============================================================
class FeishuImportRequest(BaseModel):
    """飞书文档入库请求."""

    url: str = Field(..., description="飞书文档 URL（docx / wiki,私域也支持）")
    title: Optional[str] = Field(None, description="自定义标题，默认用飞书文档原标题")
    asset_type: AssetType = Field(
        AssetType.REVIEW_CASE,
        description="资产类型：spec / review_case / feedback,默认 review_case",
    )
    tags: List[str] = Field(default_factory=list, description="标签：ts/vue/react/js")
    level: Optional[str] = Field(None, description="级别：必须/禁止/建议,默认 '建议'")
    dry_run: bool = Field(
        False,
        description="True 则只返回 LLM 重写结果,不真入库（用于人工核对效果）",
    )


class FeishuImportResponse(BaseModel):
    """飞书文档入库响应."""

    ok: bool
    dry_run: bool
    doc_id: Optional[str] = None
    title: str
    url: str
    original_chars: int
    cleaned_chars: int
    rewritten_chars: int
    points_count: int
    chunk_count: int
    fallback_used: bool
    warning: Optional[str] = None
    rewritten_md_preview: Optional[str] = Field(
        None,
        description="重写后 markdown 前 1000 字符(dry_run=True 时必填)",
    )


# ============================================================
# 端点
# ============================================================
@router.post(
    "/import_feishu",
    response_model=FeishuImportResponse,
    summary="飞书 CR 文档解析入库",
    description=(
        "通过 lark-cli 抓取飞书私域文档,用 LLM 重写为项目 CR 格式 "
        "（## 评审点 N - / ### 文件 / ### 代码（错误）/ ### 评审意见）,"
        "按 REVIEW_CASE 资产类型入库。dry_run=True 时不真入库,只返回重写结果。"
    ),
    dependencies=[Depends(require_api_key)],
)
async def import_feishu(req: FeishuImportRequest) -> FeishuImportResponse:
    """飞书 CR 文档入库主入口."""
    # 1) 抓取 + 清洗飞书 markdown
    try:
        lark_doc = await fetch_lark_doc(req.url)
    except LarkNotInstalledError as e:
        logger.error(f"[feishu-import] lark-cli 未安装: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"lark-cli 未安装，请先执行: npm i -g lark-cli ({e})",
        )
    except LarkAuthError as e:
        logger.error(f"[feishu-import] lark-cli 鉴权失败: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"lark-cli 鉴权失败（请检查 ~/.lark-cli/config.json 和 bot scope）: {e}",
        )
    except LarkDocNotFoundError as e:
        logger.error(f"[feishu-import] 飞书文档读不到: {e}")
        raise HTTPException(
            status_code=502,
            detail=f"飞书文档读不到（权限不足或链接错误）: {e}",
        )
    except (LarkInvalidResponseError, LarkFetcherError) as e:
        logger.error(f"[feishu-import] 飞书抓取失败: {e}")
        raise HTTPException(status_code=502, detail=f"飞书抓取失败: {e}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # 2) LLM 重写为 CR 格式
    parser = FeishuParserAgent()
    rewrite_result = parser.rewrite(lark_doc.cleaned_markdown)
    title = req.title or lark_doc.title
    warning = rewrite_result.error if rewrite_result.fallback_used else None

    # 3) dry_run：不入库,只返回重写结果
    if req.dry_run:
        preview = rewrite_result.rewritten_md[:1000]
        logger.info(
            f"[feishu-import] dry_run ok: title={title[:30]}, "
            f"points={rewrite_result.points_count}, fallback={rewrite_result.fallback_used}"
        )
        return FeishuImportResponse(
            ok=True,
            dry_run=True,
            doc_id=None,
            title=title,
            url=req.url,
            original_chars=lark_doc.raw_length,
            cleaned_chars=lark_doc.cleaned_length,
            rewritten_chars=len(rewrite_result.rewritten_md),
            points_count=rewrite_result.points_count,
            chunk_count=0,
            fallback_used=rewrite_result.fallback_used,
            warning=warning,
            rewritten_md_preview=preview,
        )

    # 4) 真正入库
    try:
        knowledge_repo.init()
        doc = knowledge_repo.add_text(
            title=title,
            text=rewrite_result.rewritten_md,
            asset_type=req.asset_type,
            source=SourceType.FEISHU_AGENT,
            url=req.url,
            tags=req.tags or None,
            level=req.level or "建议",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"[feishu-import] 入库失败: {e}")
        raise HTTPException(status_code=500, detail=f"入库失败: {e}")

    logger.info(
        f"[feishu-import] ok: doc_id={doc.id}, title={title[:30]}, "
        f"points={rewrite_result.points_count}, chunks={doc.chunk_count}, "
        f"fallback={rewrite_result.fallback_used}"
    )
    return FeishuImportResponse(
        ok=True,
        dry_run=False,
        doc_id=doc.id,
        title=title,
        url=req.url,
        original_chars=lark_doc.raw_length,
        cleaned_chars=lark_doc.cleaned_length,
        rewritten_chars=len(rewrite_result.rewritten_md),
        points_count=rewrite_result.points_count,
        chunk_count=doc.chunk_count,
        fallback_used=rewrite_result.fallback_used,
        warning=warning,
        rewritten_md_preview=None,
    )
