"""Web 后台管理页面路由."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.conf.settings import settings
from app.entities.document import AssetType
from app.entities.feedback import FeedbackRequest
from app.entities.search import SearchRequest
from app.repositories.knowledge_repo import knowledge_repo
from app.services.url_fetcher import fetch_url
from app.web.auth_pages import is_admin
from app.web.deps import require_admin

router = APIRouter(prefix="/knowledge/admin", tags=["web-admin"], dependencies=[Depends(require_admin)])

# 模板在 main.py 启动时挂载
templates: Jinja2Templates = None  # type: ignore


def set_templates(t: Jinja2Templates) -> None:
    global templates
    templates = t


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------
@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """概览页."""
    knowledge_repo.init()
    stats = knowledge_repo.stats()
    return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
            "app_name": settings.app_name,
            "stats": stats,
            "is_admin": is_admin(request),
        },
    )


# ----------------------------------------------------------------------
# 文档总览
# ----------------------------------------------------------------------
@router.get("/documents", response_class=HTMLResponse)
async def documents_list(request: Request, msg: str = ""):
    docs = knowledge_repo.list_documents()
    return templates.TemplateResponse(
            request,
            "documents.html",
            {
            "app_name": settings.app_name,
            "documents": docs,
            "is_admin": is_admin(request),
            "msg": msg,
        },
    )


@router.get("/documents/{doc_id}", response_class=HTMLResponse)
async def document_detail(request: Request, doc_id: str):
    doc = knowledge_repo.get_document(doc_id)
    if not doc:
        return RedirectResponse("/knowledge/admin/documents?msg=文档不存在", status_code=302)
    return templates.TemplateResponse(
            request,
            "document_detail.html",
            {
            "app_name": settings.app_name,
            "doc": doc,
            "is_admin": is_admin(request),
        },
    )


# ----------------------------------------------------------------------
# 本地上传
# ----------------------------------------------------------------------
@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, msg: str = ""):
    return templates.TemplateResponse(
            request,
            "upload.html",
            {
            "app_name": settings.app_name,
            "asset_types": [t.value for t in AssetType],
            "is_admin": is_admin(request),
            "msg": msg,
        },
    )


@router.post("/upload")
async def upload_submit(
    request: Request,
    file: UploadFile = File(...),
    asset_type: str = Form("spec"),
    tags: str = Form(""),
    level: str = Form(""),
):
    try:
        # 文件类型校验
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in [".md", ".markdown", ".txt", ".json"]:
            return RedirectResponse(
                f"/knowledge/admin/upload?msg=不支持的文件类型 {suffix}", status_code=302
            )
        # 保存到 uploads
        upload_path = settings.uploads_dir / file.filename
        upload_path.parent.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        upload_path.write_bytes(content)
        # 入库
        knowledge_repo.init()
        try:
            at = AssetType(asset_type)
        except ValueError:
            at = AssetType.SPEC
        doc = knowledge_repo.add_file(
            file_path=str(upload_path),
            title=Path(file.filename).stem,
            asset_type=at,
            tags=[t.strip() for t in tags.split(",") if t.strip()],
            level=level or None,
            source="upload",
        )
        msg = f"上传成功：{file.filename}，{doc.chunk_count} chunks"
    except Exception as e:
        msg = f"上传失败：{e}"
    return RedirectResponse(f"/knowledge/admin/upload?msg={msg}", status_code=302)


# ----------------------------------------------------------------------
# 在线链接导入
# ----------------------------------------------------------------------
@router.get("/import-url", response_class=HTMLResponse)
async def import_url_page(request: Request, msg: str = ""):
    return templates.TemplateResponse(
            request,
            "import_url.html",
            {
            "app_name": settings.app_name,
            "asset_types": [t.value for t in AssetType],
            "is_admin": is_admin(request),
            "msg": msg,
        },
    )


@router.post("/import-url")
async def import_url_submit(
    request: Request,
    url: str = Form(...),
    title: str = Form(""),
    asset_type: str = Form("spec"),
    tags: str = Form(""),
):
    try:
        knowledge_repo.init()
        fetched = await fetch_url(url)
        doc = knowledge_repo.add_url(
            title=title or fetched.title,
            url=url,
            text=fetched.content,
            content_type=fetched.content_type,
            source_label=fetched.source,
            asset_type=AssetType(asset_type),
            tags=[t.strip() for t in tags.split(",") if t.strip()],
        )
        msg = f"导入成功：{doc.title}，{doc.chunk_count} chunks（来源：{fetched.source}）"
    except Exception as e:
        msg = f"导入失败：{e}"
    return RedirectResponse(f"/knowledge/admin/import-url?msg={msg}", status_code=302)


# ----------------------------------------------------------------------
# 检索调试
# ----------------------------------------------------------------------
@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = ""):
    results = None
    if q:
        knowledge_repo.init()
        try:
            req = SearchRequest(query=q, top_k=settings.retrieval_top_k)
            resp = knowledge_repo.search(req)
            results = resp
        except Exception as e:
            results = {"error": str(e)}
    return templates.TemplateResponse(
            request,
            "search.html",
            {
            "app_name": settings.app_name,
            "asset_types": [t.value for t in AssetType],
            "q": q,
            "results": results,
            "is_admin": is_admin(request),
        },
    )


# ----------------------------------------------------------------------
# 评审反馈
# ----------------------------------------------------------------------
@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(request: Request, msg: str = ""):
    return templates.TemplateResponse(
            request,
            "feedback.html",
            {
            "app_name": settings.app_name,
            "is_admin": is_admin(request),
            "msg": msg,
        },
    )


@router.post("/feedback")
async def feedback_submit(
    request: Request,
    title: str = Form(...),
    code_bad: str = Form(...),
    code_good: str = Form(""),
    review_opinion: str = Form(...),
    file_path: str = Form(""),
    line_range: str = Form(""),
    rule_id: str = Form(""),
    severity: str = Form("warning"),
    tags: str = Form(""),
    level: str = Form(""),
):
    try:
        knowledge_repo.init()
        # 拼装 chunk 文本
        code_lang = "ts" if "ts" in tags.lower() else "js"
        file_line = file_path or "（未提供）"
        if line_range:
            file_line += f"（{line_range}）"
        rule_note = f"（规则 {rule_id}）" if rule_id else ""
        good_section = ""
        if code_good:
            good_section = f"### 代码（正确）\n```\n{code_good}\n```\n"
        chunk_text = f"""## {title}

### 文件
{file_line}
{rule_note}

### 代码（错误）
```{code_lang}
{code_bad}
```

{good_section}

### 评审意见
{review_opinion}

### 级别
{level or '建议'}
"""
        doc = knowledge_repo.add_text(
            title=title,
            text=chunk_text,
            asset_type=AssetType.FEEDBACK,
            source="feedback_form",
            tags=[t.strip() for t in tags.split(",") if t.strip()],
            level=level or None,
        )
        msg = f"反馈已沉淀：{doc.title}，{doc.chunk_count} chunks"
    except Exception as e:
        msg = f"提交失败：{e}"
    return RedirectResponse(f"/knowledge/admin/feedback?msg={msg}", status_code=302)
