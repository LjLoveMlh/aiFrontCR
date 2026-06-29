"""loguru 单例日志.

- 控制台彩色输出
- 文件滚动（按大小 20MB / 保留 30 天）
- 预留 request_id 注入（阶段4 FastAPI 中间件用）
"""

from __future__ import annotations

import sys
from contextvars import ContextVar
from typing import Optional

from loguru import logger as _loguru_logger

from app.conf.settings import settings

# 请求上下文（阶段4 中间件注入，本阶段不实现）
request_id_ctx: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


class AppLogger:
    """loguru 包装：单例 + 初始化时绑定 sink."""

    def __init__(self) -> None:
        self._initialized = False

    def init(self) -> None:
        """初始化 sink（幂等，多次调用安全）."""
        if self._initialized:
            return
        # 移除默认 sink
        _loguru_logger.remove()
        # 控制台
        _loguru_logger.add(
            sys.stderr,
            level=settings.log_level,
            colorize=True,
            format=(
                "<g>{time:YYYY-MM-DD HH:mm:ss}</g> | "
                "<lvl>{level: <7}</lvl> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<lvl>{message}</lvl>"
            ),
            backtrace=True,
            diagnose=False,
        )
        # 文件（按大小滚动）
        log_file = settings.log_dir_abs / "app.log"
        _loguru_logger.add(
            str(log_file),
            level=settings.log_level,
            rotation="20 MB",
            retention="30 days",
            encoding="utf-8",
            enqueue=True,  # 异步安全
            backtrace=True,
            diagnose=False,
        )
        self._initialized = True

    def __getattr__(self, name):
        # 转发到 loguru 全局 logger
        return getattr(_loguru_logger, name)


# 单例
logger = AppLogger()
logger.init()


# 便捷函数：阶段4 中间件用
def set_request_id(rid: str) -> None:
    request_id_ctx.set(rid)


def clear_request_id() -> None:
    request_id_ctx.set(None)
