"""Web 后台鉴权依赖."""

from __future__ import annotations

from fastapi import HTTPException, Request, status


def require_admin(request: Request) -> bool:
    """页面鉴权依赖：未登录跳 /knowledge/login."""
    if not request.session.get("is_admin"):
        # 重定向到登录页
        raise HTTPException(
            status_code=status.HTTP_302_FOUND,
            headers={"Location": "/knowledge/login"},
        )
    return True


def is_admin(request: Request) -> bool:
    """软判断：当前请求是否已登录（用于 base.html 模板展示登录态）."""
    return bool(request.session.get("is_admin"))
