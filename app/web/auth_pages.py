"""Web 登录页路由."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.conf.settings import settings
from app.core.security import check_password
from app.web.deps import is_admin

router = APIRouter(tags=["web-auth"])

# 模板在 main.py 启动时挂载；这里仅声明变量，由 include_router 时注入
templates: Jinja2Templates = None  # type: ignore


def set_templates(t: Jinja2Templates) -> None:
    global templates
    templates = t


@router.get("/knowledge/login", response_class=HTMLResponse, summary="登录页")
async def login_page(request: Request, msg: str = ""):
    """登录页（GET）."""
    if is_admin(request):
        return RedirectResponse("/knowledge/admin", status_code=302)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"app_name": settings.app_name, "error": msg},
    )


@router.post("/knowledge/login", summary="提交登录")
async def login_submit(request: Request, password: str = Form(...)):
    """登录提交（POST）."""
    if check_password(password, settings.admin_password):
        request.session["is_admin"] = True
        return RedirectResponse("/knowledge/admin", status_code=302)
    return RedirectResponse("/knowledge/login?msg=密码错误", status_code=302)


@router.post("/knowledge/logout", summary="登出")
async def logout(request: Request):
    """登出."""
    request.session.clear()
    return RedirectResponse("/knowledge/login", status_code=302)
