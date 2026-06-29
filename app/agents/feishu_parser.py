"""飞书 CR 文档解析 Agent.

调用 LLM 把飞书会议纪要 / 评审记录(纯自然语言)重写为项目标准 CR 格式。
复用 app/agents/prompts_feishu.py 中的 prompt 模板。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from loguru import logger

from app.agents.prompts_feishu import (
    SYSTEM_PROMPT_FEISHU,
    build_human_prompt_feishu,
)
from app.clients.llm_client import llm_client
from app.conf.settings import settings


# ============================================================
# 数据类
# ============================================================
@dataclass
class RewriteResult:
    """重写结果."""

    rewritten_md: str                # 重写后的 markdown(入库用)
    points_count: int                # 评审点数量(0 表示 fallback)
    fallback_used: bool              # 是否降级为原文(LLM 失败或格式异常)
    raw_output: str = ""             # LLM 原始输出(用于调试)
    error: str | None = None         # 失败原因


# ============================================================
# FeishuParserAgent
# ============================================================
class FeishuParserAgent:
    """飞书 CR 文档解析 Agent(LLM 驱动)."""

    def __init__(self) -> None:
        self._initialized = False

    def _ensure_ready(self) -> None:
        if not self._initialized:
            llm_client.init()
            self._initialized = True

    def rewrite(self, cleaned_md: str) -> RewriteResult:
        """把飞书清洗后的 markdown 重写为项目 CR 格式.

        Args:
            cleaned_md: lark_fetcher.clean_feishu_markdown 的输出

        Returns:
            RewriteResult

        异常:不抛;LLM 失败时返回 fallback_used=True
        """
        if not cleaned_md or not cleaned_md.strip():
            return RewriteResult(
                rewritten_md="",
                points_count=0,
                fallback_used=True,
                error="输入为空",
            )

        self._ensure_ready()
        max_chars = settings.feishu_llm_max_input_chars
        prompt = build_human_prompt_feishu(cleaned_md, max_chars=max_chars)

        t0 = time.time()
        try:
            raw = llm_client.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_FEISHU},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )
        except Exception as e:
            logger.error(f"[feishu-parser] LLM 调用失败: {e}")
            return RewriteResult(
                rewritten_md=cleaned_md,
                points_count=0,
                fallback_used=True,
                error=f"LLM 调用失败: {e}",
            )

        elapsed = (time.time() - t0) * 1000
        logger.info(f"[feishu-parser] LLM 完成：{elapsed:.0f}ms, raw_len={len(raw)}")

        # 解析输出：校验 H2 评审点结构
        points = _parse_review_points(raw)
        if points == 0:
            logger.warning(
                f"[feishu-parser] LLM 输出无有效评审点（fallback 原文入库）: {raw[:200]}"
            )
            return RewriteResult(
                rewritten_md=cleaned_md,
                points_count=0,
                fallback_used=True,
                raw_output=raw,
                error="LLM 输出无 ## 评审点 标题",
            )

        # 清洗 LLM 输出：去掉前后 ```markdown 包裹 / 多余解释
        cleaned = _clean_llm_output(raw)
        points_after = _parse_review_points(cleaned)
        logger.info(f"[feishu-parser] 解析成功：{points_after} 个评审点, {len(cleaned)} 字符")
        return RewriteResult(
            rewritten_md=cleaned,
            points_count=points_after,
            fallback_used=False,
            raw_output=raw,
        )


# ============================================================
# 解析工具
# ============================================================
_H2_RE = re.compile(r"^##\s+评审点\s+\d+", re.MULTILINE)


def _parse_review_points(md: str) -> int:
    """统计 `## 评审点 N` 数量."""
    if not md:
        return 0
    return len(_H2_RE.findall(md))


def _clean_llm_output(raw: str) -> str:
    """清洗 LLM 输出：去掉 ```markdown 包裹、引导语、空行."""
    if not raw:
        return raw
    text = raw.strip()

    # 1) 去掉 ```markdown ... ``` 包裹
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1).strip()

    # 2) 去掉首行引导语（如 "以下是重写后的内容:" / "重写结果如下:"）
    lines = text.split("\n")
    while lines and lines[0].strip():
        first = lines[0].strip()
        if first.startswith("## 评审点") or first.startswith("### "):
            break
        # 是引导语，去掉
        if first.startswith(("以下是", "下面是", "重写", "整理", "输出")):
            lines.pop(0)
            continue
        # 首行就是内容,跳出
        break

    text = "\n".join(lines).strip()
    return text
