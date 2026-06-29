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

## 阶段路线

| 阶段 | 内容 | 状态 |
|------|------|------|
| 0 | 项目初始化 & 环境搭建（千问接入模板） | ✅ |
| 1 | 单知识库 RAG（Redis Vector + DashScope 嵌入 + Web 后台） | ✅ |
| 2 | LangGraph 评审工作流（5 节点状态机） | ✅ |
| 3 | Git diff 解析 + pre-commit 钩子 | ✅ |
| 4 | FastAPI 服务 + SSE + API Key 鉴权 + 业务统计 | ✅ |
| 5 | Docker Compose 容器化（多阶段构建 + 4 命名卷） | ✅ |
| 6 | RAG 调优 + 端到端联调 + 使用手册 | ✅ |

## 快速开始

完整手册见 **[USER_GUIDE.md](./USER_GUIDE.md)**，3 步起服务：

```bash
cp .env.example .env                       # 1. 配 DASHSCOPE_API_KEY
make up                                    # 2. 一键起 Docker
SEED_KB=true docker compose up -d          # 3. 首次灌入 bootstrap
make health                                # 验证
```

服务地址：
- API：http://localhost:8000（Swagger: `/docs`）
- 知识库后台：http://localhost:8000/knowledge/admin（默认密码 `admin123`）
- RedisInsight：http://localhost:8001

### 验证千问联通（不依赖 Docker）

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.scripts.verify_qwen
```

### 跑单测

```bash
make test               # 容器内：61 passed
pytest tests/unit -v    # 宿主机
```

## 目录结构

```
aiFrontCR/
├── app/                    # 源码主包（教程用 app/ 替代 src/）
│   ├── api/                # FastAPI 路由
│   ├── clients/            # 外部依赖单例（llm / embedding / redis）
│   ├── conf/               # 配置加载（pydantic-settings）
│   ├── core/               # 基础设施（日志 / git 操作）
│   ├── agent/              # LangGraph 图 / 节点 / 状态（阶段2）
│   ├── entities/           # Pydantic 业务实体（阶段2）
│   ├── models/             # 数据模型（阶段2）
│   ├── prompt/             # Prompt 加载工具（阶段2）
│   ├── repositories/       # Redis / 文件存储（阶段1）
│   ├── scripts/            # 一次性脚本（verify_qwen 等）
│   ├── services/           # 业务串联层（阶段2）
│   └── main.py             # FastAPI 入口
├── conf/                   # 静态 YAML（日志、路径）
├── prompts/                # Prompt 模板（阶段2）
├── logs/                   # 运行日志
├── data/                   # 本地数据
└── tests/                  # 单测 / 集成测试
```

## 设计决策

- **大模型选型**：通义千问 Qwen3-Max（解决 Claude 国内访问问题）
- **配置加载**：env 优先 + pydantic-settings（少量静态配置走 `conf/app_config.yaml`）
- **客户端模式**：对齐教程 `*ClientManager` 单例 + `init()` 懒加载
- **依赖管理**：纯 `pip + requirements.txt`（未引入 poetry/uv 以贴合教程）

## 下一步

完成阶段0 验收后，进入 **阶段1：双知识库 RAG 模块**——把 `CLAUDE.md` 团队规范与历史 CR 记录接入 Redis Vector。
