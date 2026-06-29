# aiFrontCR · 前端代码评审 Agent

> 基于 LangChain + LangGraph + Redis Vector + 通义千问 Qwen3-Max 的私有化前端代码评审 Agent。
> 提交前自动预检，贴合团队 CLAUDE.md 规范与历史 CR 评审习惯，彻底规避 CR 重复问题。

## 项目定位

- **场景**：前端开发提交代码（Vue / React / TS / JS）前自动评审
- **核心能力**：双知识库 RAG（团队规范 + 历史 CR 案例）+ Git diff 差异化评审 + 分级结果（阻断/优化）
- **大模型**：阿里通义千问 **Qwen3-Max**（国内直连、稳定、中文评审话术贴合）
- **Agent 编排**：LangGraph 图式工作流
- **向量库**：Redis Stack（Vector Search）
- **服务**：FastAPI + Docker Compose
- **参考教程**：[AI Agents From Zero](https://didilili.github.io/ai-agents-from-zero/#/)

## 快速开始

完整手册见 **[USER_GUIDE.md](./USER_GUIDE.md)**，3 步起服务：

```bash
cp .env.example .env                       # 1. 配 DASHSCOPE_API_KEY
make up                                    # 2. 一键起 Docker
SEED_KB=true docker compose up -d          # 3. 首次灌入 bootstrap
make health                                # 验证
```

服务地址：

| 服务 | 地址 | 用途 |
|---|---|---|
| **API** | http://localhost:8000 | 主 API（评审 / SSE / 知识库） |
| **Swagger UI** | http://localhost:8000/docs | 接口文档 + 在线调试 |
| **知识库后台** | http://localhost:8000/knowledge/admin | 文档管理 / 检索调试 / 反馈（密码 `admin123`） |
| **RedisInsight** | http://localhost:8001 | Redis Web UI |
| **LLM 使用手册** | http://localhost:8000/llm-manual.md | 给其他 LLM 工具读的机器可读手册（Markdown） |
| **LLM 手册（纯文本）** | http://localhost:8000/for-llm.txt | 兜底版（去掉 Markdown 标记） |

> 🤖 **如果你是另一个 LLM 工具**（Cursor / CloudCode / Trae / Claude Code / Aider / Continue …）：
> 只需 `GET http://localhost:8000/llm-manual.md`，里面写全了 aiFrontCR 的所有 API、入参、出参、鉴权、错误码和集成示例。

### 验证千问联通（不依赖 Docker）

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.scripts.verify_qwen
```

### 跑单测

```bash
make test               # 容器内
pytest tests/unit -v    # 宿主机
```

## 目录结构

```
aiFrontCR/
├── app/                    # 源码主包（教程用 app/ 替代 src/）
│   ├── api/                # FastAPI 路由（含 /llm-manual.md 给 LLM 看）
│   ├── clients/            # 外部依赖单例（llm / embedding / redis）
│   ├── conf/               # 配置加载（pydantic-settings）
│   ├── core/               # 基础设施（日志 / git 操作）
│   ├── agent/              # LangGraph 图 / 节点 / 状态
│   ├── entities/           # Pydantic 业务实体
│   ├── models/             # 数据模型
│   ├── prompt/             # Prompt 加载工具
│   ├── repositories/       # Redis / 文件存储
│   ├── scripts/            # 一次性脚本（verify_qwen / precommit / ingest_file）
│   ├── services/           # 业务串联层
│   ├── web/                # 知识库管理后台（FastAPI + Jinja2）
│   └── main.py             # FastAPI 入口
├── conf/                   # 静态 YAML（日志、路径）
├── prompts/                # Prompt 模板
├── logs/                   # 运行日志
├── data/                   # 本地数据
└── tests/                  # 单测 / 集成测试
```

## 设计决策

- **大模型选型**：通义千问 Qwen3-Max（解决 Claude 国内访问问题）
- **配置加载**：env 优先 + pydantic-settings（少量静态配置走 `conf/app_config.yaml`）
- **客户端模式**：对齐教程 `*ClientManager` 单例 + `init()` 懒加载
- **依赖管理**：纯 `pip + requirements.txt`（未引入 poetry/uv 以贴合教程）
- **LLM 友好**：暴露 `/llm-manual.md` 让其他大模型工具无需阅读源码就能直接对接 API

