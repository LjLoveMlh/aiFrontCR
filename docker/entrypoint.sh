#!/usr/bin/env bash
# aiFrontCR · Docker 容器启动脚本
#
# 职责：
# 1. 等待 Redis 启动（健康检查通过）
# 2. 首次启动：初始化 Redis 索引 + 灌入 bootstrap 样例
# 3. 启动 uvicorn（生产模式：4 workers）

set -e

REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
APP_PORT="${APP_PORT:-8000}"
APP_HOST="${APP_HOST:-0.0.0.0}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
# uvicorn 要求小写
LOG_LEVEL_LOWER=$(echo "$LOG_LEVEL" | tr '[:upper:]' '[:lower:]')
WORKERS="${WORKERS:-2}"
SEED_KB="${SEED_KB:-false}"  # 是否首次启动灌入样例
INIT_INDEX="${INIT_INDEX:-true}"  # 是否初始化索引

echo "=========================================="
echo "🤖  aiFrontCR 容器启动"
echo "=========================================="
echo "  REDIS_URL: $REDIS_URL"
echo "  APP_PORT: $APP_PORT"
echo "  WORKERS: $WORKERS"
echo "  SEED_KB: $SEED_KB"
echo "=========================================="

# ---------- 1. 等待 Redis ----------
echo "⏳ 等待 Redis 就绪..."
for i in {1..30}; do
    if python -c "
import redis
r = redis.Redis.from_url('$REDIS_URL')
r.ping()
print('  ✅ Redis ping OK')
" 2>/dev/null; then
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ Redis 等待超时（30s）"
        exit 1
    fi
    sleep 1
done

# ---------- 2. 初始化索引（仅一次） ----------
if [ "$INIT_INDEX" = "true" ]; then
    echo "🔧 初始化 Redis 向量索引..."
    python -m app.scripts.init_redis_index 2>&1 | tail -5
fi

# ---------- 3. 灌入 bootstrap 样例（仅首次） ----------
if [ "$SEED_KB" = "true" ]; then
    echo "🌱 灌入 bootstrap 知识样例..."
    python -m app.scripts.seed_knowledge 2>&1 | tail -5
fi

# ---------- 3.5 配置 lark-cli（私域飞书 CR 文档解析 agent 需要） ----------
# 容器内没有 macOS Keychain，需显式注入 appSecret
if [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ]; then
    LARK_CONFIG_DIR="$HOME/.lark-cli"
    mkdir -p "$LARK_CONFIG_DIR"
    if [ ! -f "$LARK_CONFIG_DIR/config.json" ]; then
        echo "🔐 初始化 lark-cli config (app_id=$FEISHU_APP_ID)..."
        echo -n "$FEISHU_APP_SECRET" | lark-cli config init \
            --app-id "$FEISHU_APP_ID" \
            --app-secret-stdin \
            --brand "${FEISHU_BRAND:-feishu}" 2>&1 | tail -3 || \
            echo "  ⚠️ lark-cli config init 失败,私域飞书功能不可用"
    else
        echo "🔐 lark-cli config.json 已存在,跳过初始化"
    fi
else
    echo "ℹ️  未配置 FEISHU_APP_ID/FEISHU_APP_SECRET,跳过 lark-cli（私域飞书功能不可用）"
fi

# ---------- 4. 启动 uvicorn ----------
echo "🚀 启动 uvicorn (workers=$WORKERS)..."
exec uvicorn app.main:app \
    --host "$APP_HOST" \
    --port "$APP_PORT" \
    --workers "$WORKERS" \
    --log-level "$LOG_LEVEL_LOWER" \
    --access-log \
    --proxy-headers \
    --forwarded-allow-ips='*'