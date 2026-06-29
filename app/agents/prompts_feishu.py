"""飞书 CR 文档解析 Prompt 模板.

目标:把飞书会议纪要 / 评审记录(无结构化代码)重写为项目标准的 CR 格式:
    ## 评审点 N - <标题>
    ### 文件
    <文件路径或"未明确指定">
    ### 代码（错误）
    ```<lang>
    <示意性错误代码>
    ```
    ### 代码（正确）
    ```<lang>
    <示意性正确代码>
    ```
    ### 评审意见
    <整合"问题描述+解决方案"的话术>
    ### 级别
    必须/禁止/建议

实测样例输入:飞书"智能会议纪要"——纯自然语言的"问题描述/解决方案"对,无代码。
LLM 必须主动从自然语言推断示意性代码,填入 ### 代码（错误/正确）字段。
"""

from __future__ import annotations

from typing import Optional

# ============================================================
# System Prompt
# ============================================================
SYSTEM_PROMPT_FEISHU = """你是 aiFrontCR 团队的飞书 CR 文档解析 Agent。

【任务背景】
- 输入是飞书"智能会议纪要"或评审记录,通常是纯自然语言的"问题描述 / 解决方案"对
- 这些内容没有结构化代码块,直接切片入库后 RAG 召回质量差
- 你的工作:把它重写为项目标准的 CR 格式,让后续评审召回能直接命中

【输出格式 · 严格 markdown】
你必须按以下结构输出,**只输出 markdown,不要 JSON、不要任何解释、注释、前后缀**:

每个评审点用 `## 评审点 N - <标题>` 开头(N 从 1 开始),包含以下子节(必须全部出现,顺序固定):

### 文件
<文件路径或组件名;若原文档未明确,写"未明确指定">

### 代码（错误）
```<lang>
<示意性错误代码片段,5-30 行,基于原文档"问题描述"推断>
```

### 代码（正确）
```<lang>
<示意性正确代码片段,5-30 行,基于原文档"解决方案"推断>
```

### 评审意见
<2-4 句话,整合原文档的"问题描述 + 解决方案",说明为什么这样改>

### 级别
必须 / 禁止 / 建议（基于问题严重程度,选一个）

【强约束】
1. 每一个"问题-解决方案"对必须独立成一个 `## 评审点 N` 段
2. 代码块必须用三反引号 ``` 包裹,并标注语言(ts/tsx/vue/js/python)
3. 若原文档是纯行政内容(无技术问题),输出单段 `## 评审点 1 - 非技术议题`,代码字段可留空
4. 不要捏造原文档没有的"问题"——只把已有的整理成结构化格式
5. 不要输出"以下是整理后的内容:"等引导语
6. 不要输出 JSON 包裹

【示例】
输入:张三点出 PTUI 富文本高度仅 300px,只读时操作栏置灰,无法全屏。
输出:
## 评审点 1 - PTUI 富文本只读模式需支持全屏

### 文件
src/components/PtuiRichText/index.tsx

### 代码（错误）
```tsx
<PtuiRichText
  value={content}
  disabled={isReadonly}
  onChange={setContent}
/>
```

### 代码（正确）
```tsx
<PtuiRichText
  value={content}
  readOnly={isReadonly}
  onChange={setContent}
  fullscreenOnReadonly
  hidePlaceholderOnReadonly
/>
```

### 评审意见
只读模式下 PTUI 富文本默认高度 300px,操作栏置灰,用户无法放大查看。修复方案:增加 `readOnly` 属性支持,禁用默认提示语;若仍需操作,允许全屏查看。

### 级别
必须
"""


def build_human_prompt_feishu(cleaned_md: str, max_chars: int = 8000) -> str:
    """构造 user 消息:把飞书清洗后的 markdown 拼成单条 prompt.

    Args:
        cleaned_md: 飞书清洗后的 markdown
        max_chars: 超过此长度则截断(保留前 6/8 + 后 2/8 + 省略号),防超长爆 token
    """
    truncated = _truncate(cleaned_md, max_chars)
    return f"""以下是飞书 CR 文档(已清洗飞书标签和导航噪音),请重写为项目 CR 格式:

{truncated}

请输出重写后的 markdown:"""


def _truncate(md: str, max_chars: int) -> str:
    """超长 markdown 截断(保留前 6/8 + 后 2/8)."""
    if len(md) <= max_chars:
        return md
    head_chars = int(max_chars * 6 / 8)
    tail_chars = max_chars - head_chars
    return md[:head_chars] + "\n\n...(中间内容省略,已截断)...\n\n" + md[-tail_chars:]


# 占位常量,避免 lint 警告
_ = Optional
