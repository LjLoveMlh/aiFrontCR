"""FeishuParserAgent 单测（不调真实 LLM,只测 prompt 渲染/输出清洗/parse 工具）."""

from __future__ import annotations

from app.agents.feishu_parser import (
    _clean_llm_output,
    _parse_review_points,
)
from app.agents.prompts_feishu import (
    SYSTEM_PROMPT_FEISHU,
    _truncate,
    build_human_prompt_feishu,
)


# ============================================================
# build_human_prompt_feishu
# ============================================================
def test_build_human_prompt_basic():
    md = "# 总结\n\nPTUI 富文本问题。"
    prompt = build_human_prompt_feishu(md)
    assert "PTUI 富文本问题" in prompt
    assert "飞书 CR 文档" in prompt
    assert "重写" in prompt


def test_build_human_prompt_truncation():
    md = "x" * 10_000
    prompt = build_human_prompt_feishu(md, max_chars=2000)
    # 截断后应包含省略号
    assert "省略" in prompt or "截断" in prompt
    # 截断后字符数应明显小于原 md
    assert len(prompt) < 4000


def test_build_human_prompt_no_truncate_when_short():
    md = "短文本"
    prompt = build_human_prompt_feishu(md, max_chars=10000)
    assert "省略" not in prompt
    assert "截断" not in prompt
    assert "短文本" in prompt


# ============================================================
# _truncate
# ============================================================
def test_truncate_short():
    md = "hello"
    out = _truncate(md, 1000)
    assert out == md


def test_truncate_long():
    md = "a" * 1000
    out = _truncate(md, 200)
    assert "省略" in out
    assert len(out) <= 300  # 留点 buffer 给"省略"和"截断"提示


# ============================================================
# _parse_review_points
# ============================================================
def test_parse_review_points_multiple():
    md = """## 评审点 1 - 问题 A

### 文件
a.ts

## 评审点 2 - 问题 B

### 文件
b.ts

## 评审点 3 - 问题 C

### 文件
c.ts
"""
    assert _parse_review_points(md) == 3


def test_parse_review_points_zero():
    md = "# 普通标题\n\n无评审点。\n"
    assert _parse_review_points(md) == 0


def test_parse_review_points_must_strict():
    """必须严格匹配 `## 评审点 N` 格式,`### 评审点` 或 `#### 评审点` 不算."""
    md = """### 评审点 1 - 子标题

#### 评审点 2 - 子子标题

## 评审点 1 - 真正的评审点
"""
    # 只匹配 ## 级别
    assert _parse_review_points(md) == 1


# ============================================================
# _clean_llm_output
# ============================================================
def test_clean_llm_output_strips_markdown_fence():
    raw = """```markdown
## 评审点 1 - x

### 文件
a.ts
```"""
    cleaned = _clean_llm_output(raw)
    assert not cleaned.startswith("```")
    assert cleaned.startswith("## 评审点")


def test_clean_llm_output_strips_intro():
    raw = """以下是重写后的内容：

## 评审点 1 - x

### 文件
a.ts
"""
    cleaned = _clean_llm_output(raw)
    assert "以下是" not in cleaned
    assert cleaned.startswith("## 评审点")


def test_clean_llm_output_keeps_content():
    raw = """## 评审点 1 - x

### 文件
a.ts
"""
    cleaned = _clean_llm_output(raw)
    assert "## 评审点 1 - x" in cleaned
    assert "### 文件" in cleaned


# ============================================================
# SYSTEM_PROMPT_FEISHU sanity
# ============================================================
def test_system_prompt_contains_key_constraints():
    assert "## 评审点" in SYSTEM_PROMPT_FEISHU
    assert "### 文件" in SYSTEM_PROMPT_FEISHU
    assert "### 代码（错误）" in SYSTEM_PROMPT_FEISHU
    assert "### 代码（正确）" in SYSTEM_PROMPT_FEISHU
    assert "### 评审意见" in SYSTEM_PROMPT_FEISHU
    assert "### 级别" in SYSTEM_PROMPT_FEISHU
    assert "必须" in SYSTEM_PROMPT_FEISHU
    assert "禁止" in SYSTEM_PROMPT_FEISHU
    assert "建议" in SYSTEM_PROMPT_FEISHU
