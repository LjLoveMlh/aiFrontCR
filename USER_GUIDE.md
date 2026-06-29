# aiFrontCR · 用户手册

> aiFrontCR 是**前端专属 AI 代码评审 Agent**。本手册面向开发者本人，按"装好 → 用起来 → 出问题怎么办"三段式组织。

---

## 目录

- [一、5 分钟跑起来](#一5-分钟跑起来)
- [二、知识库管理（核心）](#二知识库管理核心)
- [三、日常开发工作流](#三日常开发工作流)
- [四、RAG 调优指南](#四rag-调优指南)
- [五、运维与监控](#五运维与监控)
- [六、常见问题排查](#六常见问题排查)
- [七、架构与原理速览](#七架构与原理速览)

---

## 一、5 分钟跑起来

### 1.1 前置条件

| 软件 | 最低版本 | 用途 |
|---|---|---|
| Docker Desktop | 4.x | 跑容器 |
| DashScope API Key | - | 阿里通义千问调用（[申请地址](https://dashscope.console.aliyun.com/apiKey)） |
| 端口 8000 / 6379 / 8001 | - | FastAPI / Redis / RedisInsight |

> 不需要装 Python、不需要下模型（走 DashScope API）。

### 1.2 三步启动

```bash
# 1. 准备 .env（必填 DASHSCOPE_API_KEY）
cp .env.example .env
# 编辑 .env，把 DASHSCOPE_API_KEY 改成你的真实 key

# 2. 一键拉起整套服务
make up

# 3. 验证
make health
```

服务地址：

| 服务 | 地址 | 用途 |
|---|---|---|
| **FastAPI** | http://localhost:8000 | 主 API（评审 / SSE / 知识库） |
| **Swagger UI** | http://localhost:8000/docs | 接口文档 + 在线调试 |
| **Redis** | localhost:6379 | 向量库（应用内连接） |
| **RedisInsight** | http://localhost:8001 | Redis Web UI（可视化看向量） |
| **知识库后台** | http://localhost:8000/knowledge/admin | 文档管理 / 检索调试 / 反馈 |

### 1.3 首次启动：灌入 bootstrap 样例

```bash
# 让 aiFrontCR 自带 4 篇示例（1 规范 + 3 评审案例），用来验证整条链路
SEED_KB=true docker compose up -d
```

或者随时手动灌入：

```bash
make seed
```

### 1.4 停止 / 重启

```bash
make down            # 停止（数据保留）
make restart         # 重启
make down-all        # 停止 + 清空所有数据卷（慎用）
```

---

## 二、知识库管理（核心）

aiFrontCR 的灵魂是**知识库**。代码评审的准确度完全取决于知识库质量。

### 2.1 知识库分三类

| 资产类型 | asset_type | 用途 | 来源 |
|---|---|---|---|
| 编码规范 | `spec` | CLAUDE.md / 团队规范 | 一次性灌入 |
| 评审案例 | `review_case` | 历史 PR 的 CR 记录 | 持续累积 |
| 反馈沉淀 | `feedback` | Agent 自动评审后入库 | 自动 |

三类**存同一个 Redis 向量库**，靠 metadata 区分。

### 2.2 三种入库方式

#### 方式 A：Web 后台上传（最直观）

```bash
# 浏览器打开
open http://localhost:8000/knowledge/admin
# 默认密码 admin123（.env 里改 ADMIN_PASSWORD）
```

页面支持：
- 本地 `.md / .txt / .json` 上传
- 在线链接导入（飞书公开链接 / 公共 MD URL）
- 文档列表 / 删除 / 重向量化
- 检索调试面板（输入代码片段看召回效果）
- 反馈录入（提交代码 + 评审意见 → 入库）

#### 方式 B：HTTP 接口（自动化友好）

```bash
# 新增评审案例
curl -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{
    "title": "PR #123 - 禁止 var 声明",
    "code": "var x = 1;",
    "language": "javascript",
    "asset_type": "review_case",
    "review_opinion": "禁止使用 var，统一 const/let"
  }'

# 新增规范（spec）
curl -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{
    "title": "TS 命名规范",
    "content": "类型用 PascalCase，变量用 camelCase，常量用 UPPER_SNAKE...",
    "asset_type": "spec",
    "tags": ["typescript"]
  }'

# 反馈沉淀（Agent 自动入库时也用这个）
curl -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Agent 评审反馈 - 2026-06-24",
    "code": "function add(a, b) { return a + b; }",
    "language": "javascript",
    "asset_type": "feedback",
    "review_opinion": "函数参数缺类型注解，TS 项目必填"
  }'
```

> 启用 API Key 鉴权（生产）后需带 `X-API-Key` header，详见 [§3.4](#34-api-key-鉴权生产模式)。

#### 方式 C：批量本地文件

把 `.md` 文件放进 `data/knowledge_base/specs/` 或 `data/knowledge_base/reviews/`，然后：

```bash
# 命令行批量导入
docker compose exec api python -m app.scripts.ingest_file \
  --dir data/knowledge_base/specs \
  --asset-type spec
```

或者通过 `seed_knowledge` 脚本：

```bash
make seed
```

### 2.3 知识库操作清单

| 操作 | 命令 | 何时用 |
|---|---|---|
| 灌入 bootstrap | `make seed` | 首次跑通链路 |
| 灌入指定目录 | `python -m app.scripts.ingest_file --dir <dir>` | 批量导入历史 CR |
| 单条 API 入库 | `curl POST /knowledge/add` | CI 自动沉淀 |
| Web 上传 | http://localhost:8000/knowledge/admin/upload | 人工录入 |
| 删除文档 | `DELETE /knowledge/api/documents/{id}` 或 Web | 误录 / 修正 |
| 重向量化 | `POST /knowledge/api/documents/{id}/reembed` | 切换 Embedding 后 |
| 备份 | `make backup` | 升级前 |
| 恢复 | `make restore` | 灾难恢复 |
| 看统计 | `GET /review/stats` | 监控 KB 规模 |

### 2.4 录入建议

✅ **DO**：
- **规范**：每条规范独立成段，标题清晰（`## 规则 X`），含「反例/正例/级别」
- **评审案例**：每条历史 PR 单独成文件，含「错误代码 / 正确代码 / 评审意见」
- **feedback**：完整保留"代码 + 评审意见"二元组，便于后续向量检索时同时匹配

❌ **DON'T**：
- 一坨不规范格式的长文（切片器会切碎，召回时缺上下文）
- 纯 PDF 截图（需先 OCR 提取文本）
- 重复灌入相同内容（占用向量库空间，影响 recall 质量）

---

## 三、日常开发工作流

### 3.1 方式一：CloudCode / 编辑器手动评审

打开编辑器，选中代码片段 → 调用 HTTP：

```bash
curl -X POST http://localhost:8000/review/code \
  -H "Content-Type: application/json" \
  -d '{
    "code": "let x: any = 1;",
    "file_path": "src/utils.ts",
    "language": "typescript"
  }'
```

返回结构化 JSON：

```json
{
  "review_report": {
    "summary": "代码存在类型安全问题：变量滥用 any 类型...",
    "items": [
      {
        "severity": "blocking",
        "title": "变量 x 不应使用 any 类型",
        "rule_id": "RULE-005",
        "code_bad": "let x: any = 1;",
        "code_good": "const x: number = 1;",
        "review_opinion": "x 初始值为数字 1..."
      }
    ],
    "blocking_count": 1,
    "rag_spec_count": 5,
    "rag_case_count": 5
  }
}
```

**SSE 流式版本**（实时显示评审进度）：

```bash
curl -N -X POST http://localhost:8000/review/code/stream \
  -H "Content-Type: application/json" \
  -d '{"code": "var x = 1;", "language": "typescript"}'
```

事件流：`start → rag → llm_start → llm_done → classify → persist → result → done`

### 3.2 方式二：Git pre-commit 钩子（自动拦截）

#### 安装

```bash
# 一键安装到当前 git 仓库的 .git/hooks/pre-commit
bash app/scripts/install_precommit.sh
```

默认走 `python app/scripts/precommit.py`，需：
- 在系统 Python 装依赖（`pip install -r requirements.txt`）
- `DASHSCOPE_API_KEY` 在环境变量里

#### Docker 模式（推荐）

```bash
# 走 HTTP 调用 Docker 容器内的 API（无需本地装 Python）
bash app/scripts/install_precommit.sh --http http://localhost:8000
```

#### 拦截效果

```bash
$ git commit -m "add new feature"
🔍 aiFrontCR · 正在评审本次变更...

📄 src/utils.ts
   阻断: 1  警告: 0  提示: 0
   ❌ 变量 x 不应使用 any 类型
      let x: any = 1;
      → const x: number = 1;

❌ 阻断 1 条，提交被拦截
修复后再 commit。--no-verify 跳过（不推荐）
```

支持参数：
- `--no-color` 关彩色
- `--no-block` 仅警告不拦截
- `--no-persist` 不自动沉淀
- `--http URL` 走 HTTP 而非本地 CLI

### 3.3 方式三：Web 后台调试

适合：调 prompt、看召回、试规范、debug 误判。

打开 http://localhost:8000/knowledge/admin

- **Dashboard**：向量总量、文档数、最近评审
- **Documents**：所有文档总览，可看 chunks、可删、可重向量化
- **Search**：输入 query 看 top-5 召回 + 分数
- **Feedback**：录评审反馈
- **Upload**：上传本地 .md

### 3.4 API Key 鉴权（生产模式）

```bash
# .env
API_KEYS=key1-for-cloudcode,key2-for-cli
API_KEY_REQUIRED=true
```

调用时带 header：

```bash
curl -H "X-API-Key: key1-for-cloudcode" \
  -X POST http://localhost:8000/review/code \
  ...
```

Web 后台（`/knowledge/admin`）走**独立 session 鉴权**（`ADMIN_PASSWORD`），不受 API Key 影响。

### 3.5 评审反馈沉淀（自动 / 手动）

`/review/code` 接口默认会把这次评审当 feedback 入库（`persist_feedback: true`）。
每天的评审记录就自动成了知识库增量。

Web 端：进 `/knowledge/admin/feedback` 手动录入（适合人工 CR 后补录）。

---

## 四、RAG 调优指南

### 4.1 调参速查

`.env` 里所有 RAG 相关参数：

| 参数 | 默认值 | 含义 | 调优建议 |
|---|---|---|---|
| `RETRIEVAL_TOP_K` | 5 | 最终返回条数 | 越多越全但越慢，建议 3-10 |
| `VECTOR_TOP_K` | 30 | 向量召回数 | 建议 20-50 |
| `KEYWORD_TOP_K` | 20 | 关键词召回数 | 建议 10-30 |
| `MIN_RERANK_SCORE` | 0.0 | rerank 分数下限 | **0 不过滤；0.3-0.5 过滤低质召回** |
| `CHUNK_SIZE` | 600 | 切片字符数 | 长文档调大（800-1000），短文档调小（300-400） |
| `CHUNK_OVERLAP` | 120 | 切片重叠 | 建议 size 的 15-25% |
| `EMBEDDING_MODEL_ID` | `text-embedding-v3` | Embedding 模型 | 切 v2 (1536维) 需同步改 `EMBEDDING_DIM` |
| `RERANK_MODEL_ID` | `gte-rerank` | Rerank 模型 | 切本地 BGE 需 `RERANK_BACKEND=local` |

### 4.2 调优实战

**症状 1：召回里混了不相关内容**

```bash
# 调高 MIN_RERANK_SCORE 过滤低分
MIN_RERANK_SCORE=0.4
```

**症状 2：规范召回太少**

```bash
# 加大切片 + 调大向量召回数
CHUNK_SIZE=800
VECTOR_TOP_K=50
```

**症状 3：评审太慢**

```bash
# 减少 rerank 前的召回数
VECTOR_TOP_K=20
KEYWORD_TOP_K=10
```

**症状 4：评审风格不像团队**

往知识库多灌历史评审记录（`review_case`），LLM 会学口吻。

### 4.3 三路召回原理

```
query
  ↓
┌─→ 向量召回 (DashScope text-embedding-v3)    → top-30
│
├─→ 关键词召回 (RediSearch FT.SEARCH MATCH)   → top-20
│
└─→ 合并去重
       ↓
   BGE Rerank (gte-rerank)                     → top-5
       ↓
   MIN_RERANK_SCORE 过滤                       → final
```

---

## 五、运维与监控

### 5.1 业务统计

```bash
curl -s http://localhost:8000/review/stats | python -m json.tool
```

返回：

```json
{
  "usage": {
    "total_requests": 142,
    "by_endpoint": {"/review/code": 50, "/health": 92},
    "by_status": {"200": 140, "500": 2},
    "llm": {"calls": 50, "total_ms": 950000, "avg_ms": 19000}
  },
  "knowledge_base": {
    "document_count": 47,
    "chunk_count": 312,
    "by_type": {"spec": 5, "review_case": 38, "feedback": 4}
  }
}
```

### 5.2 健康检查

```bash
# 接入 K8s livenessProbe
curl -f http://localhost:8000/health

# Docker 自动 healthcheck（每 30s 探一次）
docker compose ps  # 看 STATUS 列 healthy / starting / unhealthy
```

### 5.3 日志

容器内 `/app/logs` 挂到 `api_logs` 数据卷：

```bash
make logs-api    # 实时跟
```

### 5.4 备份 / 恢复

```bash
make backup      # 导出到 /app/data/backup/aiFrontCR_kb_<timestamp>.json
make restore     # 从 latest 恢复
```

### 5.5 升级流程

```bash
# 1. 备份
make backup

# 2. 拉新代码
git pull

# 3. 重新构建镜像
docker compose build

# 4. 重启
docker compose up -d
```

---

## 六、常见问题排查

### Q1: 启动报 "DASHSCOPE_API_KEY 未设置"

```bash
# 在 .env 里加上（注意等号后无空格、引号非必须）
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxx
```

### Q2: 评审返回 0 条建议

可能原因：
1. **知识库为空** → `make seed` 灌入 bootstrap，或 `/knowledge/admin/upload` 上传
2. **召回被过滤** → 把 `MIN_RERANK_SCORE` 调到 0 看是否被过滤
3. **网络问题** → 看 `make logs-api` 有没有 DashScope 报错

### Q3: 容器内 LLM 调用超时

```bash
# .env 调到 120s（Qwen3-Max 大评审可能要 60-120s）
DASHSCOPE_TIMEOUT=120
docker compose restart api
```

### Q4: 端口 8000 被占用

修改 `docker-compose.yml`：

```yaml
ports:
  - "8888:8000"   # 宿主机:容器
```

### Q5: 数据迁移 / 备份

```bash
make backup      # 备份到容器内 /app/data/backup
make restore     # 恢复
# 文件可拷到 host：docker compose cp api:/app/data/backup ./backups
```

### Q6: 完全重置（清空所有数据）

```bash
make down-all    # 警告：清空所有 volume
make up
```

### Q7: 怎么切换本地 BGE 模型（不用 DashScope）

```bash
# .env
EMBEDDING_BACKEND=local
EMBEDDING_MODEL_ID=BAAI/bge-m3
RERANK_BACKEND=local
RERANK_MODEL_ID=BAAI/bge-reranker-v2-m3
# 首次启动会自动下载 ~2.3GB
```

### Q8: 怎么在公网部署

参考 `DEPLOY.md`「生产部署清单」一节，核心是：
- 改 `ADMIN_PASSWORD` / `SESSION_SECRET` 强密码
- 设 `API_KEY_REQUIRED=true` 启用 API Key
- `DASHSCOPE_API_KEY` 走 secrets manager
- 防火墙只暴露 8000

### Q9: 评审结果和团队真实风格不一致

**根因：知识库里历史评审案例不够**。

```bash
# 1. 灌历史 PR 的 CR 记录（越多越好，至少 30 条）
# 2. 看 LLM 是否在用正确的口吻
curl -X POST http://localhost:8000/knowledge/api/search \
  -H "Cookie: session=..." -d '{"query": "你的真实问题代码"}'
# 3. 调 prompt（在 app/agents/prompts.py 的 SYSTEM_PROMPT）
```

### Q10: 端到端验证脚本失败

```bash
# 一键诊断
bash scripts/e2e_demo.sh
# 跑完会打印哪一步失败 + 原因
```

---

## 七、架构与原理速览

```
┌─────────────────────────────────────────────────────────────┐
│  CloudCode  /  Git pre-commit  /  Web 后台  /  CLI          │
└────────────┬──────────────────┬──────────────────┬──────────┘
             │ HTTP             │                  │
             ▼                  ▼                  ▼
        ┌─────────────────────────────────────────────────┐
        │  FastAPI（app/main.py）                         │
        │  · /review/code        - 评审                  │
        │  · /review/code/stream - SSE                   │
        │  · /review/git         - Git diff 评审         │
        │  · /knowledge/add      - 入库                  │
        │  · /knowledge/admin/*  - Web 后台               │
        └────────────────┬────────────────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────────────────┐
        │  LangGraph 工作流（5 节点）                       │
        │  receive → rag → llm_review → classify → persist│
        └──────┬───────────────────┬──────────────────────┘
               │                   │
               ▼                   ▼
        ┌──────────────┐    ┌─────────────────┐
        │  RAG 多路召回│    │  Qwen3-Max      │
        │  · 向量     │    │  (DashScope)    │
        │  · 关键词   │    └─────────────────┘
        │  · Rerank   │
        └──────┬───────┘
               │
               ▼
        ┌──────────────────────────────────────┐
        │  Redis Stack（向量库 + 全文索引）      │
        │  · aiFrontCR_kb（单库多类型）         │
        │  · 持久化卷 aifrontcr_redis_data     │
        └──────────────────────────────────────┘
```

### 模块对应

| 阶段 | 模块 | 路径 |
|---|---|---|
| 阶段 0 | 配置 / LLM 客户端 | `app/conf/` `app/clients/llm_client.py` |
| 阶段 1 | 知识库 / RAG / Web | `app/clients/redis_client.py` `app/services/retriever.py` `app/web/` |
| 阶段 2 | LangGraph 工作流 | `app/agents/` |
| 阶段 3 | Git pre-commit | `app/core/git_ops.py` `app/scripts/precommit.py` |
| 阶段 4 | SSE / API Key / 统计 | `app/api/stream_api.py` `app/api/deps.py` |
| 阶段 5 | Docker 编排 | `Dockerfile` `docker-compose.yml` `Makefile` |
| 阶段 6 | 调优 / 手册 | `app/agents/prompts.py` `app/conf/settings.py` `USER_GUIDE.md` |

### 关键文件

| 用途 | 路径 |
|---|---|
| 配置 | `.env` + `conf/app_config.yaml` |
| 评审 Prompt | `app/agents/prompts.py` |
| 工作流 | `app/agents/workflow.py` |
| 多路召回 | `app/services/retriever.py` |
| Embedding | `app/clients/embedding_client.py` |
| Redis 向量 | `app/clients/redis_client.py` |
| Git 解析 | `app/core/git_ops.py` |
| 端到端验证 | `scripts/e2e_demo.sh` |

---

## 附录：命令速查

```bash
# 服务
make up              # 启动
make down            # 停止（保留数据）
make down-all        # 停止 + 清空
make restart         # 重启
make ps              # 看状态
make health          # 健康检查
make logs-api        # 实时日志
make shell-api       # 进 API 容器
make shell-redis     # 进 Redis 容器

# 知识库
make seed            # 灌入 bootstrap
make init-index      # 初始化向量索引
make backup          # 备份
make restore         # 恢复

# 验证
make test            # 跑 61 个单测
make version         # 看版本
bash scripts/e2e_demo.sh   # 端到端联调 9 步
```
