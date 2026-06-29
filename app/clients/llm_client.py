"""通义千问 Qwen3-Max Chat 客户端（阶段0 核心）.

设计：
- 单例 + init() 懒加载，避免启动即建连
- 优先使用 langchain_community.chat_models.ChatDashScope
  （若库不可用，降级为 dashscope.Generation 原生 SDK）
- 暴露 chat(messages) 统一接口，给阶段2 LangGraph 节点复用
- 模块级 llm_client 实例
"""

from __future__ import annotations

import os
import time
from typing import Any, List, Optional, Union

from loguru import logger

from app.conf.settings import settings

# LangChain AIMessage / HumanMessage
try:
    from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

    HAS_LANGCHAIN_MSG = True
except ImportError:  # 极端兜底
    HAS_LANGCHAIN_MSG = False
    BaseMessage = Any  # type: ignore

# ChatDashScope（langchain-community）
ChatDashScope: Optional[type] = None
try:
    from langchain_community.chat_models.tongyi import ChatTongyi as _ChatTongyi

    ChatDashScope = _ChatTongyi
except ImportError:
    try:
        from langchain_community.chat_models import ChatDashScope as _ChatDashScope

        ChatDashScope = _ChatDashScope
    except ImportError:
        ChatDashScope = None

# dashscope 原生 SDK
try:
    import dashscope
    from dashscope import Generation

    HAS_DASHSCOPE = True
except ImportError:
    HAS_DASHSCOPE = False


class LLMClient:
    """通义千问 LLM 客户端（单例 + 懒加载）."""

    def __init__(self) -> None:
        self._initialized = False
        self._backend: str = "none"  # "langchain" | "dashscope" | "none"
        self._chat_model: Any = None

    def init(self) -> "LLMClient":
        """初始化客户端（幂等）.

        Returns:
            self，支持链式调用
        """
        if self._initialized:
            return self

        # 1. 注入环境变量（dashscope SDK 自动读 DASHSCOPE_API_KEY）
        os.environ.setdefault("DASHSCOPE_API_KEY", settings.dashscope_api_key)

        # 2. 优先尝试 LangChain ChatDashScope
        if ChatDashScope is not None:
            try:
                self._chat_model = ChatDashScope(
                    model=settings.qwen_chat_model,
                    dashscope_api_key=settings.dashscope_api_key,
                    temperature=settings.qwen_temperature,
                    max_tokens=settings.qwen_max_tokens,
                    timeout=settings.dashscope_timeout,
                )
                self._backend = "langchain"
                logger.info(
                    f"LLMClient 初始化成功 [backend=langchain, model={settings.qwen_chat_model}]"
                )
                self._initialized = True
                return self
            except Exception as e:
                logger.warning(f"LangChain ChatDashScope 初始化失败，降级到 dashscope 原生 SDK: {e}")

        # 3. 降级到 dashscope 原生 SDK
        if HAS_DASHSCOPE:
            dashscope.api_key = settings.dashscope_api_key
            self._backend = "dashscope"
            logger.info(
                f"LLMClient 初始化成功 [backend=dashscope, model={settings.qwen_chat_model}]"
            )
            self._initialized = True
            return self

        raise RuntimeError(
            "LLMClient 初始化失败：未安装 langchain-community 或 dashscope，"
            "请检查 requirements.txt 是否安装完整"
        )

    def _ensure_ready(self) -> None:
        if not self._initialized:
            raise RuntimeError("LLMClient 未初始化，请先调用 llm_client.init()")

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def model_name(self) -> str:
        return settings.qwen_chat_model

    # ------------------------------------------------------------------
    # 统一 chat 接口：输入 List[BaseMessage] 或 List[dict]
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: List[Union[BaseMessage, dict]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """调用千问对话，返回文本回复.

        Args:
            messages: 消息列表，支持 LangChain BaseMessage 或 {"role": ..., "content": ...}
            temperature: 覆盖默认温度
            max_tokens: 覆盖默认最大 token

        Returns:
            模型回复文本
        """
        self._ensure_ready()

        # 统一为 dashscope 风格 dict
        dashscope_msgs = self._normalize_messages(messages)

        if self._backend == "langchain":
            return self._chat_langchain(dashscope_msgs, temperature, max_tokens)
        elif self._backend == "dashscope":
            return self._chat_dashscope(dashscope_msgs, temperature, max_tokens)
        else:
            raise RuntimeError(f"未知 backend: {self._backend}")

    def _normalize_messages(
        self, messages: List[Union[BaseMessage, dict]]
    ) -> List[dict]:
        """统一消息为 dashscope 风格 dict."""
        out: List[dict] = []
        for m in messages:
            if isinstance(m, dict):
                out.append({"role": m.get("role", "user"), "content": m.get("content", "")})
            elif HAS_LANGCHAIN_MSG and isinstance(m, BaseMessage):
                if isinstance(m, HumanMessage):
                    role = "user"
                elif isinstance(m, AIMessage):
                    role = "assistant"
                elif isinstance(m, SystemMessage):
                    role = "system"
                else:
                    role = "user"
                content = m.content if isinstance(m.content, str) else str(m.content)
                out.append({"role": role, "content": content})
            else:
                out.append({"role": "user", "content": str(m)})
        return out

    def _chat_langchain(
        self,
        messages: List[dict],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        """走 LangChain ChatDashScope."""
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        lc_msgs = []
        for m in messages:
            if m["role"] == "user":
                lc_msgs.append(HumanMessage(content=m["content"]))
            elif m["role"] == "assistant":
                lc_msgs.append(AIMessage(content=m["content"]))
            elif m["role"] == "system":
                lc_msgs.append(SystemMessage(content=m["content"]))
            else:
                lc_msgs.append(HumanMessage(content=m["content"]))

        kwargs: dict = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        resp = self._chat_model.invoke(lc_msgs, **kwargs)
        # resp 是 AIMessage，content 可能是 str 或 list
        content = resp.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # 多模态场景，拼接文本块
            parts = []
            for blk in content:
                if isinstance(blk, dict) and "text" in blk:
                    parts.append(blk["text"])
                else:
                    parts.append(str(blk))
            return "\n".join(parts)
        return str(content)

    def _chat_dashscope(
        self,
        messages: List[dict],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> str:
        """走 dashscope 原生 SDK."""
        kwargs: dict = {
            "model": settings.qwen_chat_model,
            "messages": messages,
            "result_format": "message",
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        else:
            kwargs["temperature"] = settings.qwen_temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = settings.qwen_max_tokens

        resp = Generation.call(**kwargs)
        if getattr(resp, "status_code", 0) != 200:
            raise RuntimeError(
                f"dashscope 调用失败: status_code={resp.status_code}, "
                f"code={getattr(resp, 'code', '')}, message={getattr(resp, 'message', '')}"
            )
        return resp.output.choices[0].message.content

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    def quick_chat(self, prompt: str, system: Optional[str] = None) -> str:
        """单轮对话快捷入口."""
        messages: List[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return self.chat(messages)


# 模块级单例
llm_client = LLMClient()
