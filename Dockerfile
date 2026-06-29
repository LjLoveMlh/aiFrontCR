# =============================================================================
# aiFrontCR · 多阶段 Dockerfile
# 阶段1（builder）：装编译依赖（torch / sentence-transformers 需要）
# 阶段2（runtime）：精简运行时，复制 builder 的 site-packages
# =============================================================================

# ---------- 阶段 1：builder ----------
FROM python:3.10-slim AS builder

# 镜像元信息
LABEL maintainer="aiFrontCR" \
      version="0.1.0" \
      description="aiFrontCR · 前端代码评审 Agent"

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/.cache/huggingface

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libffi-dev \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# 工作目录
WORKDIR /app

# 先复制 requirements 利用 Docker 缓存
COPY requirements.txt .

# 装包（用 --user 隔离到 /install，最后整体复制）
RUN pip install --user --no-cache-dir -r requirements.txt

# ---------- 阶段 2：runtime ----------
FROM python:3.10-slim AS runtime

# 仅装运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    tini \
    git \
    && rm -rf /var/lib/apt/lists/*

# 用非 root 用户跑（安全）
RUN groupadd -r aifrontcr && useradd -r -g aifrontcr -m -d /app aifrontcr

# 工作目录
WORKDIR /app

# 复制 builder 装好的包
COPY --from=builder /root/.local /home/aifrontcr/.local
ENV PATH=/home/aifrontcr/.local/bin:$PATH \
    PYTHONPATH=/app:/home/aifrontcr/.local/lib/python3.10/site-packages \
    HF_HOME=/app/.cache/huggingface

# 复制项目代码
COPY --chown=aifrontcr:aifrontcr . /app

# 准备数据/日志/缓存目录
RUN mkdir -p /app/data /app/logs /app/.cache/huggingface /app/data/uploads \
    && chown -R aifrontcr:aifrontcr /app

# 切换到非 root
USER aifrontcr

# 暴露端口
EXPOSE 8000

# 健康检查（每 30s 探一次）
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://127.0.0.1:8000/health || exit 1

# 用 tini 跑 entrypoint（信号转发 + 僵尸进程回收）
ENTRYPOINT ["/usr/bin/tini", "--"]

# 默认命令
CMD ["bash", "/app/docker/entrypoint.sh"]
