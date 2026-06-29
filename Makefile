# =============================================================================
# aiFrontCR · Makefile
# 一键命令：make up / down / build / logs / seed / test / shell
# =============================================================================

.PHONY: help build up down restart logs logs-api logs-redis shell shell-api \
        shell-redis seed init-index test clean prune ps status health version

# ---------- 颜色输出 ----------
GREEN  := \033[0;32m
YELLOW := \033[0;33m
RESET  := \033[0m

help:  ## 显示帮助
	@echo "$(GREEN)aiFrontCR · Docker 命令速查$(RESET)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	    awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-15s$(RESET) %s\n", $$1, $$2}'
	@echo ""

# ---------- 构建 / 启动 ----------
build:  ## 构建 Docker 镜像
	docker compose build

up:  ## 启动所有服务（后台）
	docker compose up -d
	@echo "$(GREEN)✅ 服务已启动$(RESET)"
	@echo "  API:    http://localhost:8000"
	@echo "  Redis:  localhost:6379"
	@echo "  Web UI: http://localhost:8001 (RedisInsight)"

down:  ## 停止所有服务
	docker compose down
	@echo "$(GREEN)✅ 服务已停止$(RESET)"

down-all:  ## 停止服务 + 清理数据卷（清空所有数据！慎用）
	docker compose down -v
	@echo "$(YELLOW)⚠️  所有数据已清理$(RESET)"

restart:  ## 重启所有服务
	docker compose restart

# ---------- 日志 ----------
logs:  ## 查看所有服务日志
	docker compose logs -f

logs-api:  ## 查看 API 容器日志
	docker compose logs -f api

logs-redis:  ## 查看 Redis 容器日志
	docker compose logs -f redis

# ---------- 进入容器 ----------
shell: shell-api  ## 进入 API 容器 shell（默认）

shell-api:  ## 进入 API 容器
	docker compose exec api bash

shell-redis:  ## 进入 Redis 容器
	docker compose exec redis redis-cli

# ---------- 初始化 ----------
init-index:  ## 初始化 Redis 向量索引
	docker compose exec api python -m app.scripts.init_redis_index

seed:  ## 灌入 bootstrap 样例
	docker compose exec api python -m app.scripts.seed_knowledge

# ---------- 单测 ----------
test:  ## 跑单测（在 API 容器内）
	docker compose exec api python -m pytest tests/unit/ -v

# ---------- 状态查询 ----------
ps:  ## 查看服务状态
	docker compose ps

status: ps  ## 同 ps

health:  ## 检查 API 健康
	@curl -s http://localhost:8000/health | python -m json.tool

version:  ## 查看镜像 / 容器版本
	@docker compose version
	@docker version --format 'Docker: {{.Server.Version}}'
	@docker compose exec -T api python -c "from app import __version__; print('aiFrontCR:', __version__)"

# ---------- 清理 ----------
clean:  ## 清理悬空镜像 / 容器
	docker image prune -f
	docker container prune -f

prune:  ## 深度清理（会询问）
	docker system prune

# ---------- 备份 / 恢复 ----------
backup:  ## 备份知识库到 JSON
	docker compose exec api python -m app.scripts.backup_redis

restore:  ## 从 JSON 恢复知识库
	docker compose exec api python -m app.scripts.restore_redis

# ---------- 联调 ----------
e2e:  ## 端到端 9 步联调（默认含跨重启持久化）
	bash scripts/e2e_demo.sh

e2e-fast:  ## 端到端联调（跳过跨重启步骤）
	bash scripts/e2e_demo.sh --skip-restart