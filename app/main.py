"""FastAPI 应用入口（阶段0 + 阶段1）."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.api.auth import router as auth_api_router
from app.api.deps import RequestContextMiddleware
from app.api.documents_api import router as documents_api_router
from app.api.feedback_api import router as feedback_api_router
from app.api.health import router as health_router
from app.api.knowledge_add_api import router as knowledge_add_router
from app.api.review_api import router as review_router
from app.api.search_api import router as search_api_router
from app.api.stream_api import router as stream_router
from app.conf.settings import settings
from app.core.log import logger
from app.web import admin_pages, auth_pages

# 模板目录
TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"
STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动/关闭钩子."""
    logger.info(f"=== {settings.app_name} v{__version__} 启动 ===")
    logger.info(f"LLM 模型：{settings.qwen_chat_model}")
    logger.info(f"Redis 索引：{settings.redis_index_name}")
    logger.info(f"Embedding：{settings.embedding_model_id}")
    logger.info(f"Web 后台：/knowledge/admin（密码鉴权）")
    logger.info(f"日志目录：{settings.log_dir_abs}")
    yield
    logger.info(f"=== {settings.app_name} 关闭 ===")


def create_app() -> FastAPI:
    """工厂方法：创建 FastAPI 实例."""
    app = FastAPI(
        title=settings.app_name,
        description="aiFrontCR · 前端代码评审 Agent（通义千问 + LangGraph + Redis Vector）",
        version=__version__,
        debug=settings.app_debug,
        lifespan=lifespan,
    )

    # Session（Web 后台鉴权）
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="aiFrontCR_session",
        max_age=60 * 60 * 24 * 7,  # 7 天
        same_site="lax",
    )

    # 阶段4：request_id + 统计中间件（必须在外层）
    app.add_middleware(RequestContextMiddleware)

    # CORS（阶段4 CloudCode 联调时放开）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 静态文件（CSS / JS / 未来图片）
    app.mount(
        "/knowledge/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="knowledge-static",
    )

    # 模板（注入到 web 路由）
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    auth_pages.set_templates(templates)
    admin_pages.set_templates(templates)

    # 路由注册
    app.include_router(health_router, tags=["health"])
    app.include_router(auth_pages.router, tags=["web-auth"])
    app.include_router(admin_pages.router, tags=["web-admin"])
    # JSON API（鉴权由 router 级 Depends 注入）
    app.include_router(auth_api_router, tags=["web-api"])
    app.include_router(documents_api_router, tags=["web-api"])
    app.include_router(search_api_router, tags=["web-api"])
    app.include_router(feedback_api_router, tags=["web-api"])
    # 阶段2：代码评审 LangGraph
    app.include_router(review_router, tags=["review"])
    # 阶段4：SSE 流式
    app.include_router(stream_router, tags=["review-stream"])
    # 阶段4：知识库增量入库（CloudCode 友好）
    app.include_router(knowledge_add_router, tags=["knowledge-ingest"])

    # 阶段3/4/5 预留：
    # app.include_router(git_router, prefix="/git", tags=["git"])
    # app.include_router(business_router, prefix="/business", tags=["business"])

    return app


app = create_app()
