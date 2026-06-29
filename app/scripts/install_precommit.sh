#!/usr/bin/env bash
# aiFrontCR · Pre-commit 钩子安装脚本
#
# 用法：
#   bash app/scripts/install_precommit.sh                # 安装到当前仓库
#   bash app/scripts/install_precommit.sh /path/to/repo  # 安装到指定仓库
#   bash app/scripts/install_precommit.sh --no-block     # 安装但不拦截（仅展示）
#   bash app/scripts/install_precommit.sh --http http://localhost:8000  # 走 HTTP

set -euo pipefail

# 默认参数
REPO_PATH="${1:-.}"
USE_HTTP=""
NO_BLOCK=""

# 解析后续参数
shift || true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --http)
            USE_HTTP="$2"
            shift 2
            ;;
        --no-block)
            NO_BLOCK="--no-block"
            shift
            ;;
        *)
            echo "[WARN] 未知参数: $1" >&2
            shift
            ;;
    esac
done

# 路径解析
REPO_PATH="$(cd "$REPO_PATH" && pwd)"
HOOK_FILE="$REPO_PATH/.git/hooks/pre-commit"
AI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "🔧 aiFrontCR · Pre-commit 钩子安装"
echo "   仓库：$REPO_PATH"
echo "   aiFrontCR 根：$AI_ROOT"

# 检查 .git 目录
if [[ ! -d "$REPO_PATH/.git" ]]; then
    echo "❌ 错误：$REPO_PATH 不是 git 仓库" >&2
    exit 1
fi

# 备份已有钩子
if [[ -f "$HOOK_FILE" ]]; then
    BACKUP="$HOOK_FILE.backup.$(date +%Y%m%d%H%M%S)"
    echo "📦 备份已有钩子到 $BACKUP"
    mv "$HOOK_FILE" "$BACKUP"
fi

# 拼钩子命令
if [[ -n "$USE_HTTP" ]]; then
    HOOK_CMD="python -m app.scripts.precommit --repo '$REPO_PATH' --http '$USE_HTTP' $NO_BLOCK"
else
    HOOK_CMD="cd '$AI_ROOT' && source .venv/bin/activate 2>/dev/null || true; cd '$REPO_PATH' && PYTHONPATH='$AI_ROOT' python -m app.scripts.precommit --repo '$REPO_PATH' $NO_BLOCK"
fi

# 写钩子
cat > "$HOOK_FILE" <<EOF
#!/usr/bin/env bash
# aiFrontCR 自动评审钩子（由 install_precommit.sh 生成）
# 删除此文件即可卸载

set -e
$HOOK_CMD
exit \$?
EOF

chmod +x "$HOOK_FILE"

echo "✅ Pre-commit 钩子已安装：$HOOK_FILE"
echo ""
echo "🧪 测试钩子（不实际提交）："
echo "   cd '$REPO_PATH' && git diff --cached --quiet || bash '$HOOK_FILE' || true"
echo ""
echo "🗑️  卸载：rm '$HOOK_FILE'"