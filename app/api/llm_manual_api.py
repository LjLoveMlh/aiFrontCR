"""给其他大模型看的使用说明页面.

提供两个公开端点（不需要鉴权）：

- ``GET /llm-manual.md``     - Markdown 格式（推荐）
- ``GET /for-llm.txt``       - 纯文本格式（兜底，markdown 渲染失败时使用）
- ``GET /llm-manual``        - 同上但不带后缀，content-type 仍为 text/markdown
- ``GET /AGENTS.md``         - 兼容一些 Agent 框架习惯的入口

其他大模型工具（Cursor / CloudCode / Trae / Claude Code / Aider / Continue 等）
只要能访问 http://localhost:8000/llm-manual.md 拉一次本页内容，
就能知道 aiFrontCR 是什么、能用什么 API、怎么调用。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["llm-manual"], include_in_schema=False)

# ---------------------------------------------------------------------------
# 手册正文（Markdown）
# ---------------------------------------------------------------------------
# 这份文档的目标读者是「另一个大语言模型」，不是人类开发者。
# 因此写作风格：
#   1. 用祈使句 / 表格 / JSON Schema，少讲故事
#   2. 所有示例都附可执行的 curl
#   3. 字段含义逐个说明
#   4. 错误码 / 边界条件必须写清
# ---------------------------------------------------------------------------

MANUAL_MD = r"""# aiFrontCR · 给其他大模型的使用手册

> 这是一份「机器可读」手册，给 Cursor / CloudCode / Trae / Claude Code /
> Aider / Continue / 任何支持 function calling 或 HTTP 调用的 LLM 工具使用。
>
> 你（另一个大模型）只要把这段文字读进来，就能知道：
>   1. aiFrontCR 是什么、能为你做什么
>   2. 它暴露了哪些 HTTP API，路径、入参、出参、错误码
>   3. 如何在你的工具里集成（发送请求 / 解析结果 / 决定是否阻断）
>   4. 它是同步还是流式、是否需要鉴权
>
> 文档版本：v1.0 · 与 aiFrontCR 阶段 6 版本配套
> 在线地址（本服务的同源副本，永远是最新版）：
>   - http://localhost:8000/llm-manual.md  （主入口，Markdown）
>   - http://localhost:8000/for-llm.txt    （纯文本兜底）
>   - http://localhost:8000/AGENTS.md       （兼容 Agent 框架）

---

## 0. 一句话定位

**aiFrontCR = 前端代码评审 Agent（本地服务）**。

- 接收一段前端代码（Vue / React / TS / JS），返回结构化评审报告
- 基于阿里通义千问 Qwen3-Max + Redis 向量知识库 + LangGraph 工作流
- 知识库 = 团队规范（spec） + 历史 CR 案例（review_case） + 评审反馈（feedback）
- 默认部署在 `http://localhost:8000`，所有 API 走 JSON over HTTP
- 也提供 SSE 流式版本，事件协议见 §6

适合你的场景：
- 用户在编辑器里写了一坨新代码 → 调 `/review/code` → 把评审结果展示给用户
- 用户写完一个 feature 准备 git commit → 调 `/review/git` → 有 blocking 就拒绝
- 用户想沉淀一条评审意见到知识库 → 调 `/knowledge/add`
- 用户想检索知识库 → 调 `/knowledge/api/search`

---

## 1. 服务地址 & 鉴权

```
Base URL:   http://localhost:8000
API Doc:    http://localhost:8000/docs          (Swagger UI，可在线调试)
Health:     http://localhost:8000/health
Stats:      http://localhost:8000/review/stats
```

### 1.1 鉴权规则

| 端点类型 | 是否需要鉴权 | 鉴权方式 |
|---|---|---|
| `/llm-manual.md`、`/for-llm.txt`、`/AGENTS.md` | ❌ 公开 | 无 |
| `/health`、`/review/stats`、`/docs`、`/openapi.json` | ❌ 公开 | 无 |
| `/review/code`、`/review/code/stream`、`/review/code/batch` | ⚠️ 看配置 | 详见下 |
| `/review/git`、`/review/git/stream` | ⚠️ 看配置 | 详见下 |
| `/knowledge/add`、`/knowledge/api/search` | ⚠️ 看配置 | 详见下 |
| `/knowledge/admin/*`（Web 后台） | ✅ 必需 | 走 Session 密码（`ADMIN_PASSWORD`） |

### 1.2 外部 API 的鉴权细节

`/review/*` 和 `/knowledge/add` 走 `X-API-Key` header：

```bash
# 启用了 API Key 时调用必须带 header
curl -H "X-API-Key: YOUR_KEY" http://localhost:8000/review/code ...

# 开发模式（默认，.env 里 API_KEY_REQUIRED=false 且没配 API_KEYS）
# → 任何来源都不需要 key，可以直接调
```

服务器端逻辑（看 `app/api/deps.py::require_api_key`）：

- 若 `.env` 里 `API_KEYS=` 为空 **且** `API_KEY_REQUIRED=false` → 完全开放（dev）
- 否则 → 必须带 `X-API-Key: <合法 key>`，否则 401 / 403
- 多个 key 用英文逗号分隔：`API_KEYS=key1,key2,key3`

**给你的建议**：
- 集成前先 `curl http://localhost:8000/health` 探活，能 200 就是好的
- 如果用户说「API 调不通」→ 提示用户检查 `.env` 的 `API_KEY_REQUIRED` 和 `API_KEYS`

---

## 2. 核心 API：单文件代码评审

### 2.1 `POST /review/code`

**用途**：最常用，给你一段代码，返回结构化评审报告。

**鉴权**：默认不需要；生产环境可能需要 `X-API-Key`。

**请求体**（JSON）：

```json
{
  "code": "let x: any = 1;\nconsole.log(x);",
  "file_path": "src/utils.ts",          // 可选，影响 RAG 召回
  "language": "typescript",             // 可选，auto / typescript / javascript / vue / react
  "persist_feedback": true              // 是否把这次评审沉淀到知识库（默认 true）
}
```

**响应**（JSON）：

```json
{
  "review_report": {
    "summary": "代码存在类型安全问题：变量滥用 any 类型...",
    "items": [
      {
        "severity": "blocking",          // blocking | warning | info
        "title": "变量 x 不应使用 any 类型",
        "rule_id": "RULE-005",            // 关联的规范编号（可选）
        "code_bad": "let x: any = 1;",
        "code_good": "const x: number = 1;",
        "review_opinion": "x 初始值为数字 1，应直接声明 number 类型..."
      }
    ],
    "language": "typescript",
    "file_path": "src/utils.ts",
    "blocking_count": 1,
    "warning_count": 0,
    "info_count": 0,
    "total": 1,
    "elapsed_ms": 4213.7,
    "has_blocking": true,
    "rag_spec_count": 5,
    "rag_case_count": 5,
    "feedback_doc_id": "uuid-xxx"        // 若 persist_feedback=true 才有
  },
  "feedback_doc_id": "uuid-xxx",
  "error": null,
  "llm_error": null,
  "elapsed_ms": 4213.7
}
```

**严重等级语义**（关键！）：

| 值 | 含义 | 你应该怎么用 |
|---|---|---|
| `blocking` | 阻断性问题，必须修改 | 提示用户「不修改不能 commit」 |
| `warning` | 优化建议 | 提示用户「建议改」 |
| `info` | 仅提示 | 提示用户「可以参考」 |

**完整可执行示例**：

```bash
curl -X POST http://localhost:8000/review/code \
  -H "Content-Type: application/json" \
  -d '{
    "code": "var x = 1;",
    "file_path": "src/utils.ts",
    "language": "typescript"
  }'
```

**错误码**：

| HTTP | 原因 | 你应该怎么提示 |
|---|---|---|
| 400 | `code` 字段为空 | 「代码片段不能为空」 |
| 401 | 缺 `X-API-Key` | 「需要 API Key」 |
| 403 | `X-API-Key` 不合法 | 「API Key 错误」 |
| 500 | LLM 调用失败 / Redis 异常 | 「评审服务异常，请重试」 |
| 200 + `error` | 工作流内部异常（不阻断） | 报告里 `items=[]`，告诉用户「未发现明显问题」 |

### 2.2 `POST /review/code/batch`

**用途**：批量评审多条代码片段，**串行**执行避免打爆 LLM 限流。

**请求体**：

```json
{
  "items": [
    {"code": "var x = 1;", "language": "javascript"},
    {"code": "let y: any = 2;", "language": "typescript"}
  ]
}
```

**限制**：`items` 数量 `[1, 50]`，超过会 422。

**响应**：

```json
{
  "results": [/* 数组，每条形如 /review/code 的响应 */],
  "total": 2,
  "elapsed_ms": 8421.3
}
```

### 2.3 `POST /review/git`

**用途**：把整个 git diff 一次性评审；专为 pre-commit 钩子和 IDE 插件设计。

**4 种模式**（按优先级）：

| 模式 | 触发条件 | 用途 |
|---|---|---|
| `diff_text` | `diff_text` 非空 | 直接给 unified diff 文本 |
| `commit_range` | `commit_range` 非空（如 `HEAD~1..HEAD`） | 服务端去拉 diff |
| `files` | `files` 数组非空 | 逐文件入参（老接口兼容） |
| `staged` | 都不传 | 读暂存区（pre-commit 钩子场景） |

**请求体示例（diff 模式）**：

```json
{
  "diff_text": "diff --git a/src/utils.ts b/src/utils.ts\n@@ -1,3 +1,3 @@\n-var x = 1;\n+const x: number = 1;",
  "repo_path": "/Users/me/proj",       // 可选，不传则用当前进程工作目录
  "persist_feedback": true,
  "fail_on_blocking": true            // 有 blocking 时返回 should_block_commit=true
}
```

**响应**：

```json
{
  "results": [
    {
      "file_path": "src/utils.ts",
      "language": "typescript",
      "line_range": "1-1",
      "review_report": { /* 同 /review/code 的 review_report */ },
      "error": null,
      "elapsed_ms": 4213.7
    }
  ],
  "total": 1,
  "blocking_count": 0,
  "warning_count": 0,
  "info_count": 0,
  "has_blocking": false,
  "elapsed_ms": 4213.7,
  "should_block_commit": false         // ← pre-commit 钩子看这个字段
}
```

**给你做集成时的关键提示**：
- `should_block_commit == true` → 拒绝用户的 commit
- `has_blocking == true` → UI 上红色高亮
- 单个文件失败不影响其他文件，`results[i].error` 单独标记

---

## 3. 知识库 API

### 3.1 `POST /knowledge/add`（增量入库）

**用途**：把一条评审案例 / 规范 / 反馈塞进知识库，让后续评审参考。

**鉴权**：默认不需要；生产环境可能需要 `X-API-Key`。

**请求体**：

```json
{
  "title": "PR #123 - 禁止 var 声明",
  "code": "var x = 1;",
  "file_path": "src/utils.js",          // 可选
  "line_range": "12-25",                // 可选
  "asset_type": "review_case",          // 必须：spec / review_case / feedback
  "level": "禁止",                       // 可选：必须/禁止/建议
  "tags": ["javascript", "var"],
  "rule_id": "RULE-005",                // 可选
  "review_opinion": "禁止使用 var，统一 const/let",
  "code_good": "const x = 1;",
  "source": "cloudcode"                 // cloudcode / web / cli
}
```

**约束**：
- `asset_type=feedback` 时 `review_opinion` 必填
- `title` 长度 `[1, 200]`

**响应**：

```json
{
  "ok": true,
  "doc_id": "uuid-xxx",
  "chunk_count": 3,
  "asset_type": "review_case",
  "title": "PR #123 - 禁止 var 声明"
}
```

**三种 asset_type 怎么选**（重要）：

| asset_type | 用途 | 典型字段 |
|---|---|---|
| `spec` | 团队编码规范（CLAUDE.md 类） | title + review_opinion + level + tags |
| `review_case` | 历史 PR 的 CR 记录 | code + review_opinion + code_good |
| `feedback` | Agent 评审后的人工反馈 | code + review_opinion |

### 3.2 `POST /knowledge/api/search`（检索）

**鉴权**：走 session 密码（Web 后台专用）。

**给非 Web 场景的替代**：直接调内部的检索端点请走 Web 端；如果你需要程序化检索，建议先把结果用 `/knowledge/add` 入库，再走 `/review/code` 间接验证。

### 3.3 `GET /knowledge/api/documents`（文档列表）

需要 Web 后台 session，鉴权同上。

---

## 4. SSE 流式评审协议（章节 6 摘要）

`POST /review/code/stream` 和 `POST /review/git/stream` 返回 `text/event-stream`，
事件类型如下：

| event | 含义 | data 字段 |
|---|---|---|
| `start` | 评审开始 | `{request_id, file_path, language, code_length, persist_feedback}` |
| `rag` | RAG 召回完成 | `{specs, cases}` |
| `llm_start` | LLM 调用开始 | `{model}` |
| `llm_done` | LLM 输出完成 | `{elapsed_ms, raw_length}` |
| `classify` | 严重等级统计 | `{blocking, warning, info, total, has_blocking}` |
| `persist` | 自动沉淀完成 | `{ok, feedback_doc_id, error?}` |
| `result` | 整次报告 | `{report, feedback_doc_id, elapsed_ms}` |
| `file_done` | Git 模式单文件完成 | `{file_path, blocking, warning, info, report}` |
| `error` | 异常 | `{phase?, detail}` |
| `done` | 结束 | `{request_id, ok, has_blocking, should_block_commit?, ...}` |

**客户端解析示例**（Python）：

```python
import httpx
import json

with httpx.stream(
    "POST",
    "http://localhost:8000/review/code/stream",
    json={"code": "var x = 1;", "language": "javascript"},
    headers={"Accept": "text/event-stream"},
) as r:
    current_event = None
    for line in r.iter_lines():
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            print(f"[{current_event}]", data)
        elif line == "":
            current_event = None
```

---

## 5. 内部信息：知识库管理

如果你要让用户「管理」知识库（上传 / 删除 / 重向量化 / 看统计），
不要直接调这些 API（要 session 鉴权），而是引导用户去
**Web 后台** `http://localhost:8000/knowledge/admin`（密码登录，默认 `admin123`）。

后台支持：
- 本地 `.md / .txt / .json` 上传
- 在线链接导入（飞书公开链接 / 公共 URL）
- 文档列表 / 删除 / 重向量化
- 检索调试面板
- 反馈录入

---

## 6. 业务统计 & 健康检查

### 6.1 `GET /review/stats`

```json
{
  "ok": true,
  "usage": {
    "uptime_seconds": 3600.0,
    "total_requests": 142,
    "by_endpoint": {"/review/code": 50, "/health": 92},
    "by_status": {"200": 140, "500": 2},
    "avg_request_ms": 234.5,
    "llm": {"calls": 50, "total_ms": 950000, "avg_ms": 19000},
    "reviews": {
      "total": 50, "blocking": 12, "warning_only": 30, "clean": 8,
      "blocking_rate": 0.24
    }
  },
  "knowledge_base": {
    "document_count": 47,
    "chunk_count": 312,
    "by_type": {"spec": 5, "review_case": 38, "feedback": 4}
  }
}
```

### 6.2 `GET /health`

```json
{"status": "ok", "app": "aiFrontCR", "model": "qwen3-max", "version": "0.6.0", "timestamp": "..."}
```

### 6.3 `GET /review/health`

```json
{
  "ok": true,
  "info": {
    "llm": {"model": "qwen3-max", "initialized": true},
    "embedding": {"backend": "dashscope"},
    "redis": {"index": "aiFrontCR_kb", "initialized": true}
  }
}
```

---

## 7. 集成模式（你最关心的部分）

### 模式 A：编辑器里选中代码 → 评审

```
用户操作：在 IDE 里选中一段代码 → 右键 / 快捷键 / 命令面板
你的工具动作：
  1. POST /review/code，body = {code, file_path, language}
  2. 解析 review_report.items
  3. 把每条 item 渲染成 IDE 的 inline 标注（红线 / 黄线 / 灰线）
  4. has_blocking=true 时顶部加一条「存在 X 个阻断问题」红条
```

### 模式 B：pre-commit 钩子

```
用户操作：git commit
钩子动作（在你的 hook 脚本里）：
  1. git diff --cached → 拿 diff_text
  2. POST /review/git，body = {diff_text, fail_on_blocking: true}
  3. if response.should_block_commit: exit 1
  4. else: commit 成功
官方 hook 脚本：app/scripts/precommit.py（已内置）
```

### 模式 C：批量评审

```
场景：用户改了 10 个文件想一次过
动作：POST /review/code/batch，body = {items: [...]}
用途：拿到所有文件的报告做总览
```

### 模式 D：沉淀反馈

```
场景：评审结果不够好，人工补一句「这里应该用 forEach 不要用 map」
动作：POST /knowledge/add，body = {asset_type: "feedback", code, review_opinion, ...}
用途：下次评审这条 LLM 会学到
```

---

## 8. 字段 / 枚举速查

### Severity（严重等级）

| 值 | 含义 |
|---|---|
| `blocking` | 阻断（必须改） |
| `warning` | 建议（应该改） |
| `info` | 提示（可参考） |

### AssetType（资产类型）

| 值 | 用途 |
|---|---|
| `spec` | 编码规范 |
| `review_case` | 历史 CR 案例 |
| `feedback` | 评审反馈 |

### 通用约定

- 所有时间字段单位 **毫秒（ms）**
- 所有 ID 字段为 **UUID 字符串**
- `*_count` 字段一定 ≥ 0 整数
- `error` 字段为 `null` 表示成功；非空字符串表示有异常（但 HTTP 仍可能 200）
- 文件路径统一用 POSIX 风格（`src/utils.ts` 不是 `src\\utils.ts`）

---

## 9. 限流 / 超时 / 错误

| 现象 | 可能原因 | 你的处理 |
|---|---|---|
| HTTP 504 / 超时 | Qwen3-Max 大评审要 60-120s | 把客户端超时设到 120s+，失败时给用户「评审中，请稍候」 |
| `llm_error` 非空 | DashScope 限流 / 网络 | 提示用户稍后重试 |
| `error` 非空 + items=[] | 内部异常 | 告诉用户「未发现明显问题」（不报错） |
| 返回 `blocking_count` 远高于预期 | 知识库里有大量误判规范 | 让用户去 `/knowledge/admin` 删脏数据 |

---

## 10. 一句话工作流

```
        ┌──────────────┐
        │  用户编辑器   │
        └──────┬───────┘
               │ 选中代码 / git diff
               ▼
        ┌──────────────┐
        │  你的 LLM 工具 │  ← 你现在在的地方
        └──────┬───────┘
               │ POST /review/code
               ▼
        ┌──────────────┐
        │  aiFrontCR    │  ← 你要调的服务
        │  (FastAPI)    │
        └──────┬───────┘
               │ JSON 报告
               ▼
        ┌──────────────┐
        │  你的 LLM 工具 │  解析 + 渲染给用户
        └──────┬───────┘
               │ 标注 / 阻断
               ▼
        ┌──────────────┐
        │  用户编辑器   │
        └──────────────┘
```

---

## 11. 快速验证脚本（先跑通再集成）

```bash
# 1. 探活
curl -s http://localhost:8000/health

# 2. 单文件评审
curl -X POST http://localhost:8000/review/code \
  -H "Content-Type: application/json" \
  -d '{"code": "var x = 1;", "language": "javascript"}' | python -m json.tool

# 3. 入库一条规则
curl -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{
    "title": "禁止 var 声明",
    "code": "var x = 1;",
    "language": "javascript",
    "asset_type": "review_case",
    "review_opinion": "禁止使用 var，统一 const/let"
  }'

# 4. 再评审一次，看 LLM 是否学到了
curl -X POST http://localhost:8000/review/code \
  -H "Content-Type: application/json" \
  -d '{"code": "var y = 2;", "language": "javascript"}'

# 5. 看统计
curl -s http://localhost:8000/review/stats | python -m json.tool
```

---

## 12. 常见问题（FAQ）

**Q1：我调 `/review/code` 返回 `items=[]` 是 bug 吗？**
不是。可能是：(1) 知识库为空 → 让用户去 `/knowledge/admin` 灌数据；(2) LLM 觉得代码没问题；(3) 召回被 `MIN_RERANK_SCORE` 过滤。

**Q2：评审结果跟团队风格不一致？**
根因 = 知识库里 `review_case` 太少。灌 30+ 条历史 PR 的 CR 记录，LLM 会自动学口吻。

**Q3：怎么禁用 API Key 鉴权？**
`.env` 里设 `API_KEY_REQUIRED=false` 且不配 `API_KEYS`。

**Q4：怎么切换到本地 BGE 模型（不依赖 DashScope）？**
`.env` 里设 `EMBEDDING_BACKEND=local` + `RERANK_BACKEND=local`，首次启动会自动下载 ~2.3GB。

**Q5：能离线用吗？**
可以，但要满足：(1) 切到本地 LLM（如 vLLM 部署 Qwen）；(2) 切到本地 Embedding/Rerank。LLM 端不在 aiFrontCR 范围（它假设 LLM 服务可达）。

---

## 13. 版本 & 变更

- **v1.0** (2026-06-29)：初版，对应 aiFrontCR 阶段 6
- 任何 API 变更都会同步更新本文件，请始终 fetch 最新版

— END —
"""


# ---------------------------------------------------------------------------
# 纯文本版（去掉 markdown 标记，兜底用）
# ---------------------------------------------------------------------------
def _md_to_txt(md: str) -> str:
    """粗略的 md -> txt 转换，保留可读性."""
    lines: list[str] = []
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("# "):
            lines.append("")
            lines.append("=" * 60)
            lines.append(line[2:].strip())
            lines.append("=" * 60)
        elif line.startswith("## "):
            lines.append("")
            lines.append("-" * 60)
            lines.append(line[3:].strip())
            lines.append("-" * 60)
        elif line.startswith("### "):
            lines.append("")
            lines.append("【" + line[4:].strip() + "】")
        elif line.startswith("#### "):
            lines.append("  " + line[5:].strip())
        elif line.strip().startswith("```"):
            continue  # 跳代码块围栏
        elif line.lstrip().startswith("- "):
            lines.append("  " + line.strip())
        elif line.lstrip().startswith("|"):
            lines.append(line)
        else:
            lines.append(line)
    return "\n".join(lines).strip() + "\n"


MANUAL_TXT = _md_to_txt(MANUAL_MD)


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.get(
    "/llm-manual.md",
    response_class=PlainTextResponse,
    summary="给其他大模型的使用手册（Markdown）",
)
async def get_llm_manual_md() -> PlainTextResponse:
    """Markdown 格式的使用说明。

    Content-Type: text/markdown; charset=utf-8
    不需要鉴权，永远公开。
    """
    return PlainTextResponse(
        content=MANUAL_MD,
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/llm-manual",
    response_class=PlainTextResponse,
    summary="给其他大模型的使用手册（Markdown，无后缀）",
)
async def get_llm_manual_no_ext() -> PlainTextResponse:
    """不带 .md 后缀的别名，方便某些 LLM 工具直接 GET。"""
    return PlainTextResponse(
        content=MANUAL_MD,
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/AGENTS.md",
    response_class=PlainTextResponse,
    summary="AGENTS.md 兼容入口",
)
async def get_agents_md() -> PlainTextResponse:
    """兼容一些 Agent 框架（如 Aider / Continue）默认会读的 AGENTS.md 文件。"""
    return PlainTextResponse(
        content=MANUAL_MD,
        media_type="text/markdown; charset=utf-8",
    )


@router.get(
    "/for-llm.txt",
    response_class=PlainTextResponse,
    summary="给其他大模型的使用手册（纯文本兜底）",
)
async def get_llm_manual_txt() -> PlainTextResponse:
    """纯文本版（去掉 markdown 标记），当 markdown 渲染失败时使用。"""
    return PlainTextResponse(
        content=MANUAL_TXT,
        media_type="text/plain; charset=utf-8",
    )
