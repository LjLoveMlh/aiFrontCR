"""文档 & 资产类型实体."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AssetType(str, Enum):
    """知识库资产类型（单库多类型架构，依靠此字段区分）."""

    SPEC = "spec"               # 编码规范（CLAUDE.md / 团队规范）
    REVIEW_CASE = "review_case" # 历史评审记录
    FEEDBACK = "feedback"       # 评审反馈沉淀
    UNKNOWN = "unknown"


class SourceType(str, Enum):
    """文档来源."""

    UPLOAD = "upload"              # 本地文件上传
    URL_FEISHU = "url:feishu"      # 飞书公开链接
    URL_PUBLIC_MD = "url:public_md"  # 公共 MD 链接
    BOOTSTRAP = "bootstrap"        # 内置样例
    FEEDBACK_FORM = "feedback_form"  # Web 反馈表单
    INGEST_CLI = "ingest_cli"      # CLI 批量入库
    AUTO_REVIEW = "auto_review"    # 阶段2 LangGraph 自动沉淀
    CLOUDCODE = "cloudcode"        # 阶段4 CloudCode 编辑器入库
    FEISHU_AGENT = "feishu_agent"  # 飞书私域文档 agent 解析入库


class Document(BaseModel):
    """知识库文档元信息（向量库 metadata 字段）."""

    id: str = Field(..., description="UUID，全库唯一")
    title: str = Field(..., description="文档标题（用户可见）")
    source: SourceType = Field(..., description="来源类型")
    url: Optional[str] = Field(None, description="原始 URL（在线导入时填）")
    asset_type: AssetType = Field(AssetType.UNKNOWN, description="资产类型")
    tags: List[str] = Field(default_factory=list, description="语言/框架标签：ts/vue/react/js")
    level: Optional[str] = Field(None, description="级别：必须/禁止/建议（仅 SPEC 有）")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    chunk_count: int = Field(0, description="切分出的 chunk 数量")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展元数据")


class ChunkMeta(BaseModel):
    """单个 chunk 的元信息（向量库 metadata + 检索返回 score）."""

    doc_id: str
    chunk_id: str  # 一般为 doc_id + ":" + chunk_index
    chunk_index: int
    asset_type: AssetType
    tags: List[str] = Field(default_factory=list)
    level: Optional[str] = None
    title: str
    source: SourceType
    url: Optional[str] = None
    # 检索结果时填充
    score: Optional[float] = None  # 向量距离 / 重排分数
    text_preview: Optional[str] = None  # 前 200 字预览
    text: Optional[str] = None  # 完整文本（仅检索时按需返回）

    def to_redis_metadata(self) -> Dict[str, Any]:
        """转 RediSearch metadata（TAG 字段需逗号分隔字符串）."""
        meta = {
            "doc_id": self.doc_id,
            "chunk_id": self.chunk_id,
            "chunk_index": self.chunk_index,
            "asset_type": self.asset_type.value,
            "tags": ",".join(self.tags) if self.tags else "",
            "level": self.level or "",
            "title": self.title,
            "source": self.source.value,
            "url": self.url or "",
        }
        return meta
