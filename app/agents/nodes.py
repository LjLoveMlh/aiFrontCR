"""阶段2 · LangGraph 评审工作流的 5 个节点.

节点顺序：
    1. node_receive_code      → 标准化输入
    2. node_rag_retrieve      → 双路召回（spec + review_case）
    3. node_llm_review        → 调 LLM + JSON 解析为 ReviewReport
    4. node_classify_severity → 统计 blocking/warning/info
    5. node_persist_feedback  → 把本次评审结果作为新案例自动入库

每个节点都是 (state) -> partial_state 函数；LangGraph 自动 merge.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Dict, List

from loguru import logger

from app.agents.prompts import SYSTEM_PROMPT, build_human_prompt
from app.agents.state import ReviewState
from app.clients.llm_client import llm_client
from app.conf.settings import settings
from app.entities.document import AssetType, SourceType
from app.entities.review import ReviewItem, ReviewReport, Severity
from app.entities.search import SearchRequest, SearchResult
from app.repositories.knowledge_repo import knowledge_repo


# ============================================================
# 节点 1：接收代码（标准化 + 推断）
# ============================================================
def node_receive_code(state: ReviewState) -> Dict[str, Any]:
    """接收并标准化代码输入."""
    code = (state.get("code") or "").strip()
    if not code:
        return {"error": "code 不能为空"}
    return {
        "code": code,
        "file_path": state.get("file_path"),
        "language": state.get("language"),
        "persist_feedback": bool(state.get("persist_feedback", True)),
    }


# ============================================================
# 节点 2：RAG 召回（spec + review_case 双路）
# ============================================================
def node_rag_retrieve(state: ReviewState) -> Dict[str, Any]:
    """调阶段1 的多路召回器，分别拉规范和历史案例."""
    code = state.get("code", "")
    if not code:
        return {"rag_specs": [], "rag_cases": [], "rag_spec_text": "", "rag_case_text": ""}

    # 用代码 + 文件名做 query（提升召回精度）
    query = code[:500]
    if state.get("file_path"):
        query = f"{state['file_path']}\n{query}"

    specs: List[SearchResult] = []
    cases: List[SearchResult] = []
    try:
        # 规范召回：只查 SPEC
        req_spec = SearchRequest(
            query=query,
            top_k=5,
            asset_types=[AssetType.SPEC],
            use_rerank=True,
            use_keyword=True,
        )
        resp_spec = knowledge_repo.search(req_spec)
        specs = resp_spec.results

        # 案例召回：只查 REVIEW_CASE
        req_case = SearchRequest(
            query=query,
            top_k=5,
            asset_types=[AssetType.REVIEW_CASE],
            use_rerank=True,
            use_keyword=True,
        )
        resp_case = knowledge_repo.search(req_case)
        cases = resp_case.results
    except Exception as e:
        logger.warning(f"RAG 召回失败（不阻断评审）：{e}")

    # 拼成喂给 LLM 的文本
    spec_text = _format_specs(specs)
    case_text = _format_cases(cases)

    logger.info(f"RAG 召回：specs={len(specs)}, cases={len(cases)}")
    return {
        "rag_specs": specs,
        "rag_cases": cases,
        "rag_spec_text": spec_text,
        "rag_case_text": case_text,
    }


def _format_specs(specs: List[SearchResult]) -> str:
    if not specs:
        return ""
    parts = []
    for i, s in enumerate(specs, 1):
        # ChunkMeta 没有 rule_id 字段，用 level（必须/禁止/建议）作为标签
        level = (s.chunk.level or "").strip()
        score = ""
        if s.rerank_score is not None:
            score = f"[rerank={s.rerank_score:.3f}]"
        elif s.vector_score is not None:
            score = f"[vec={s.vector_score:.3f}]"
        tag = f" {level} {score}".strip() if (level or score) else ""
        parts.append(f"--- 规范 {i}{tag} ---\n{s.text.strip()}")
    return "\n\n".join(parts)


def _format_cases(cases: List[SearchResult]) -> str:
    if not cases:
        return ""
    parts = []
    for i, c in enumerate(cases, 1):
        parts.append(f"--- 案例 {i} ---\n{c.text.strip()}")
    return "\n\n".join(parts)


# ============================================================
# 节点 3：LLM 评审（调 Qwen3-Max，解析 JSON）
# ============================================================
def node_llm_review(state: ReviewState) -> Dict[str, Any]:
    """调 LLM，把输出解析为 ReviewReport."""
    code = state.get("code", "")
    if not code:
        return {"llm_error": "code 为空", "review_report": None}

    # 拼 prompt
    human_msg = build_human_prompt(
        code=code,
        file_path=state.get("file_path"),
        language=state.get("language"),
        rag_spec_text=state.get("rag_spec_text", ""),
        rag_case_text=state.get("rag_case_text", ""),
    )

    t0 = time.time()
    try:
        llm_client.init()
        raw = llm_client.chat(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": human_msg},
            ],
            temperature=0.1,
        )
    except Exception as e:
        logger.error(f"LLM 调用失败：{e}")
        return {
            "llm_raw_output": "",
            "llm_error": str(e),
            "review_report": None,
            "error": f"LLM 调用失败：{e}",
        }
    elapsed = (time.time() - t0) * 1000
    logger.info(f"LLM 评审完成：{elapsed:.0f}ms, raw_len={len(raw)}")

    # 解析 JSON（兼容模型把 JSON 裹在 ```json ... ``` 里）
    parsed = _parse_llm_json(raw)
    if parsed is None:
        logger.warning(f"LLM 输出无法解析为 JSON：{raw[:300]}")
        # 兜底：构造一个 INFO 级报告
        report = ReviewReport(
            summary=f"LLM 输出格式异常，未能解析（{len(raw)} 字符）",
            items=[],
        )
    else:
        try:
            items = []
            for it in parsed.get("items") or []:
                try:
                    items.append(
                        ReviewItem(
                            severity=Severity(it.get("severity", "info")),
                            title=it.get("title", "（无标题）"),
                            rule_id=it.get("rule_id"),
                            code_bad=it.get("code_bad", ""),
                            code_good=it.get("code_good"),
                            review_opinion=it.get("review_opinion", ""),
                        )
                    )
                except Exception as e:
                    logger.warning(f"跳过一条 ReviewItem 解析失败：{e}")
            report = ReviewReport(
                summary=parsed.get("summary", ""),
                items=items,
                language=state.get("language"),
                file_path=state.get("file_path"),
            )
        except Exception as e:
            logger.error(f"ReviewReport 构造失败：{e}")
            report = ReviewReport(
                summary=f"评审结果解析失败：{e}",
                items=[],
            )

    report.rag_spec_count = len(state.get("rag_specs") or [])
    report.rag_case_count = len(state.get("rag_cases") or [])
    return {
        "llm_raw_output": raw,
        "review_report": report,
    }


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_llm_json(raw: str) -> Dict[str, Any] | None:
    """从 LLM 输出中提取 JSON 对象.

    兼容：
    - 纯 JSON
    - ```json ... ``` 包裹
    - 前置/后置有杂文本
    """
    if not raw:
        return None
    raw = raw.strip()
    # 1) 纯 JSON
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except Exception:
            pass
    # 2) ```json ... ```
    m = _JSON_FENCE_RE.search(raw)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 3) 找第一个 { 到最后一个 } 的子串
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except Exception:
            pass
    return None


# ============================================================
# 节点 4：分级统计
# ============================================================
def node_classify_severity(state: ReviewState) -> Dict[str, Any]:
    """根据 items 重算 blocking/warning/info 计数."""
    report = state.get("review_report")
    if not report:
        return {"review_report": None}
    report.recompute_counts()
    return {"review_report": report}


# ============================================================
# 节点 5：自动沉淀（feedback 入库）
# ============================================================
def node_persist_feedback(state: ReviewState) -> Dict[str, Any]:
    """把本次评审结果作为新 feedback 案例入库.

    仅当有 items 且 persist_feedback=True 时入库。
    """
    report = state.get("review_report")
    if not report or not report.items:
        return {"feedback_doc_id": None}
    if not state.get("persist_feedback", True):
        return {"feedback_doc_id": None}

    # 拼接入库文本
    title = (
        f"自动评审·{report.summary[:40]}"
        if report.summary
        else f"自动评审·{state.get('file_path') or 'unknown'}"
    )
    file_line = state.get("file_path") or "（未提供）"
    code_bad_section = ""
    code_good_section = ""
    opinions = []
    for i, it in enumerate(report.items, 1):
        code_bad_section += f"\n#### 问题 {i}（{it.severity.value}）\n```\n{it.code_bad}\n```\n"
        if it.code_good:
            code_good_section += f"\n#### 推荐 {i}\n```\n{it.code_good}\n```\n"
        opinions.append(f"- **{it.title}** ({it.severity.value})：{it.review_opinion}")

    chunk_text = f"""## {title}

### 文件
{file_line}

### 代码（错误）
```
{state.get('code', '')}
```

{code_bad_section}
{code_good_section}

### 评审意见
{chr(10).join(opinions)}
"""
    try:
        knowledge_repo.init()
        doc = knowledge_repo.add_text(
            title=title,
            text=chunk_text,
            asset_type=AssetType.FEEDBACK,
            source=SourceType.AUTO_REVIEW,
            tags=[state.get("language") or "auto"],
            level=(
                "必须" if report.has_blocking else ("建议" if report.warning_count else None)
            ),
        )
        logger.info(
            f"自动沉淀评审结果：doc_id={doc.id}, items={len(report.items)}, "
            f"blocking={report.blocking_count}"
        )
        return {"feedback_doc_id": doc.id}
    except Exception as e:
        logger.warning(f"自动沉淀失败（不阻断评审返回）：{e}")
        return {"feedback_doc_id": None, "error": f"沉淀失败：{e}"}