# aiFrontCR · Docker 部署指南

## 快速开始

```bash
# 1. 准备 .env（必填 DASHSCOPE_API_KEY）
cp .env.example .env
# 编辑 .env，把 DASHSCOPE_API_KEY 改成你的真实 key

# 2. 一键拉起整套服务
make up

# 3. 首次启动：灌入 bootstrap 样例（可选）
SEED_KB=true docker compose up -d

# 4. 验证
make health
```

服务地址：

| 服务 | 地址 | 用途 |
|---|---|---|
| **FastAPI** | http://localhost:8000 | 主 API（评审 / SSE / 知识库） |
| **Swagger UI** | http://localhost:8000/docs | 接口文档 + 在线调试 |
| **Redis** | localhost:6379 | 向量库（应用内连接） |
| **RedisInsight** | http://localhost:8001 | Redis Web UI（可选，可视化看向量） |

## 常用命令

```bash
make help          # 看所有命令
make up            # 启动
make down          # 停止
make logs-api      # 实时看 API 日志
make shell-api     # 进入 API 容器
make seed          # 灌入 bootstrap 知识样例
make test          # 跑单测
make health        # 健康检查
make version       # 查看版本
```

## 关键路径

| 路径 | 用途 | 容器内 |
|---|---|---|
| `redis_data` volume | Redis 持久化数据 | `/data` |
| `api_logs` volume | FastAPI 日志 | `/app/logs` |
| `api_data` volume | 业务数据（uploads / 知识库元信息） | `/app/data` |
| `api_cache` volume | HuggingFace 模型缓存 | `/app/.cache` |

容器重启 → 数据保留。`make down-all` 才会清空（慎用）。

## 端到端验收

```bash
# 1. 启动
make up

# 2. 等 30s 让 Redis 健康
sleep 30

# 3. 健康检查
curl -s http://localhost:8000/health | python -m json.tool

# 4. 跑一次评审
curl -s -X POST http://localhost:8000/review/code \
  -H "Content-Type: application/json" \
  -d '{
    "code": "let x: any = 1;",
    "file_path": "a.ts",
    "language": "typescript",
    "persist_feedback": false
  }' | python -m json.tool

# 5. 看 SSE 流式（实时显示评审进度）
curl -N -X POST http://localhost:8000/review/code/stream \
  -H "Content-Type: application/json" \
  -d '{"code": "var unused = 1;", "language": "typescript", "persist_feedback": false}'

# 6. 知识库增量入库
curl -s -X POST http://localhost:8000/knowledge/add \
  -H "Content-Type: application/json" \
  -d '{
    "title": "test",
    "code": "let x: any = 1;",
    "asset_type": "review_case",
    "review_opinion": "禁止使用 any"
  }' | python -m json.tool

# 7. 业务统计
curl -s http://localhost:8000/review/stats | python -m json.tool
```

## 常见问题

### Q1: 启动报 "DASHSCOPE_API_KEY 未设置"

```bash
# 在 .env 里加上
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxx
```

### Q2: 向量检索召回为空

```bash
# 灌入 bootstrap 样例
SEED_KB=true docker compose up -d
# 或在容器内手动跑
make seed
```

### Q3: 容器内 LLM 调用超时

检查 `.env` 的 `DASHSCOPE_TIMEOUT`（默认 30s）。Qwen3-Max 大评审可能要 60-120s，可调到 120。

### Q4: 端口 8000 被占用

修改 `docker-compose.yml` 里 `ports: - "8000:8000"` → `"8888:8000"`。

### Q5: 数据迁移 / 备份

```bash
# 备份知识库到 JSON
make backup

# 恢复
make restore
```

### Q6: 完全重置（清空所有数据）

```bash
make down-all  # 警告：清空所有 volume
make up
```

## 生产部署清单

部署到生产环境前，确认：

- [ ] `DASHSCOPE_API_KEY` 已填真实 key
- [ ] `SESSION_SECRET` 用 `openssl rand -hex 32` 生成
- [ ] `ADMIN_PASSWORD` 改成强密码
- [ ] `API_KEY_REQUIRED=true` + `API_KEYS=...` 启用 API Key 鉴权
- [ ] Redis 数据卷挂载到独立磁盘（防止容器漂移丢失）
- [ ] 防火墙只暴露 8000 端口；8001（RedisInsight）改为内网访问
- [ ] 日志接入 ELK / Loki（`api_logs` volume）
- [ ] 健康检查接入 K8s livenessProbe / readinessProbe
- [ ] `WORKERS` 调到 CPU 核数（默认 2）

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Compose                           │
│                                                             │
│  ┌──────────────────┐    ┌──────────────────────────┐     │
│  │  redis           │    │  api                     │     │
│  │  redis-stack     │◀──▶│  aifrontcr/api           │     │
│  │  port: 6379      │    │  port: 8000              │     │
│  │  +insight: 8001  │    │  workers: 2              │     │
│  │                  │    │  depends_on: healthy     │     │
│  │  Volume:         │    │                          │     │
│  │  - redis_data    │    │  Volumes:                │     │
│  │    (持久化)      │    │  - api_logs              │     │
│  └──────────────────┘    │  - api_data              │     │
│                          │  - api_cache             │     │
│                          │                          │     │
│                          │  Entrypoint:             │     │
│                          │  - 等待 Redis            │     │
│                          │  - init_redis_index      │     │
│                          │  - seed_knowledge        │     │
│                          │  - uvicorn               │     │
│                          └──────────────────────────┘     │
│                                                             │
│  Network: aifrontcr-net (bridge)                           │
└─────────────────────────────────────────────────────────────┘
```
