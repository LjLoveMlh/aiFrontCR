#!/usr/bin/env bash
# =============================================================================
# aiFrontCR · 阶段6 端到端联调脚本
#
# 模拟真实用户全链路使用：
#   1. 健康检查 + 服务状态
#   2. CloudCode 手动评审（POST /review/code）
#   3. SSE 流式评审（POST /review/code/stream）
#   4. 知识库新增案例（POST /knowledge/add）
#   5. 用相同 query 再次检索，验证新案例被命中
#   6. Git 评审接口（POST /review/git）
#   7. Web 知识库后台登录 + Dashboard
#   8. 业务统计（GET /review/stats）
#   9. 跨重启持久化（停 API → 验证 Redis 数据仍在 → 启 API → 验证 stats 不变）
#
# 用法：
#   ./scripts/e2e_demo.sh                    # 跑完整 9 步
#   ./scripts/e2e_demo.sh --skip-restart     # 跳过第 9 步（需要 sudo 操作 docker）
#   API_BASE=http://host:8000 ./scripts/...  # 改 API 地址
#
# 退出码：
#   0 = 全部通过
#   非 0 = 至少一步失败（会打印失败的步骤编号）
# =============================================================================

set -e

# ---------- 配置 ----------
API_BASE="${API_BASE:-http://localhost:8000}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKIP_RESTART=false
for arg in "$@"; do
  case "$arg" in
    --skip-restart) SKIP_RESTART=true ;;
    *) echo "未知参数: $arg（支持 --skip-restart）"; exit 2 ;;
  esac
done

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

PASS_COUNT=0
FAIL_COUNT=0
FAILED_STEPS=()

step() {
  echo ""
  echo -e "${CYAN}${BOLD}▶ $1${RESET}"
}

pass() {
  echo -e "  ${GREEN}✅ $1${RESET}"
  PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
  echo -e "  ${RED}❌ $1${RESET}"
  FAIL_COUNT=$((FAIL_COUNT + 1))
  FAILED_STEPS+=("$1")
}

# ---------- 1. 健康检查 + 服务状态 ----------
step "1/9 健康检查 + 服务状态"
HEALTH=$(curl -s --max-time 10 "$API_BASE/health")
if echo "$HEALTH" | grep -q '"status":"ok"'; then
  APP_NAME=$(echo "$HEALTH" | python -c "import json,sys; print(json.load(sys.stdin)['app'])" 2>/dev/null || echo "?")
  VERSION=$(echo "$HEALTH" | python -c "import json,sys; print(json.load(sys.stdin)['version'])" 2>/dev/null || echo "?")
  pass "/health OK（$APP_NAME v$VERSION）"
else
  fail "/health 返回异常：$HEALTH"
fi

# ---------- 2. CloudCode 手动评审 ----------
step "2/9 CloudCode 手动评审（POST /review/code）"
REVIEW=$(curl -s --max-time 90 -X POST "$API_BASE/review/code" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "let x: any = 1;\nfunction add(a, b) { return a + b; }",
    "file_path": "demo.ts",
    "language": "typescript",
    "persist_feedback": false
  }')
ITEMS=$(echo "$REVIEW" | python -c "import json,sys; print(json.load(sys.stdin)['review_report']['total'])" 2>/dev/null || echo 0)
BLOCKING=$(echo "$REVIEW" | python -c "import json,sys; print(json.load(sys.stdin)['review_report']['blocking_count'])" 2>/dev/null || echo 0)
RAG_SPEC=$(echo "$REVIEW" | python -c "import json,sys; print(json.load(sys.stdin)['review_report']['rag_spec_count'])" 2>/dev/null || echo 0)
RAG_CASE=$(echo "$REVIEW" | python -c "import json,sys; print(json.load(sys.stdin)['review_report']['rag_case_count'])" 2>/dev/null || echo 0)
ELAPSED=$(echo "$REVIEW" | python -c "import json,sys; d=json.load(sys.stdin); print(f'{d[\"review_report\"][\"elapsed_ms\"]/1000:.1f}')" 2>/dev/null || echo "?")

if [ "$ITEMS" -gt 0 ] && [ "$RAG_SPEC" -gt 0 ]; then
  pass "评审返回 $ITEMS 条建议（$BLOCKING 阻断），召回 specs=$RAG_SPEC cases=$RAG_CASE，耗时 ${ELAPSED}s"
else
  fail "评审未返回有效结果：items=$ITEMS spec=$RAG_SPEC"
fi

# ---------- 3. SSE 流式评审 ----------
step "3/9 SSE 流式评审（POST /review/code/stream）"
SSE_FILE=$(mktemp)
curl -sN --max-time 60 -X POST "$API_BASE/review/code/stream" \
  -H "Content-Type: application/json" \
  -d '{"code": "var x = 1;", "language": "typescript", "persist_feedback": false}' \
  > "$SSE_FILE" 2>&1 || true
SSE_EVENTS=$(grep -c "^event:" "$SSE_FILE" 2>/dev/null || echo 0)
SSE_TYPES=$(grep "^event:" "$SSE_FILE" 2>/dev/null | sort -u | tr '\n' ',' | sed 's/event: //g; s/,$//')

if [ "$SSE_EVENTS" -ge 6 ]; then
  pass "SSE 收到 $SSE_EVENTS 个事件（$SSE_TYPES）"
else
  fail "SSE 事件数不足（$SSE_EVENTS 个），期望 ≥ 6"
  echo "    内容：$(head -3 "$SSE_FILE")"
fi
rm -f "$SSE_FILE"

# ---------- 4. 知识库新增案例 ----------
step "4/9 知识库新增案例（POST /knowledge/add）"
DOCS_BEFORE=$(curl -s --max-time 10 "$API_BASE/review/stats" | python -c "import json,sys; print(json.load(sys.stdin)['knowledge_base']['document_count'])" 2>/dev/null || echo 0)
TEST_TITLE="E2E-Demo-$(date +%s)"
ADD=$(curl -s --max-time 30 -X POST "$API_BASE/knowledge/add" \
  -H "Content-Type: application/json" \
  -d "{
    \"title\": \"$TEST_TITLE\",
    \"code\": \"function f() { return null; }\",
    \"language\": \"javascript\",
    \"asset_type\": \"review_case\",
    \"review_opinion\": \"函数 f 没有显式返回值，应改为 void 或返回明确类型\"
  }")
DOC_ID=$(echo "$ADD" | python -c "import json,sys; print(json.load(sys.stdin).get('doc_id',''))" 2>/dev/null || echo "")
CHUNK_COUNT=$(echo "$ADD" | python -c "import json,sys; print(json.load(sys.stdin).get('chunk_count',''))" 2>/dev/null || echo "")

if [ -n "$DOC_ID" ] && [ "$CHUNK_COUNT" -gt 0 ]; then
  pass "新增评审案例：doc_id=${DOC_ID:0:8}... chunks=$CHUNK_COUNT title=$TEST_TITLE"
else
  fail "知识库新增失败：$ADD"
fi

# ---------- 5. 验证新案例被检索命中（需先登录拿 session cookie） ----------
step "5/9 检索验证：用相同 query 检索，新案例被命中"
# 登录（/knowledge/api/search 要求 admin 鉴权）
SEARCH_COOKIE=$(mktemp)
curl -s -c "$SEARCH_COOKIE" -X POST "$API_BASE/knowledge/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "password=admin123" -o /dev/null --max-time 10
sleep 1  # 给一点时间让 embedding 入库完成
SEARCH=$(curl -s -b "$SEARCH_COOKIE" --max-time 30 -X POST "$API_BASE/knowledge/api/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "function f() { return null; }",
    "top_k": 5
  }' 2>/dev/null)
FOUND=$(echo "$SEARCH" | python -c "
import json, sys
try:
    d = json.load(sys.stdin)
    hits = [r for r in d.get('results', []) if '$DOC_ID' in r.get('chunk', {}).get('doc_id', '')]
    print(len(hits))
except Exception:
    print(0)
" 2>/dev/null || echo 0)

if [ "$FOUND" -gt 0 ]; then
  pass "新案例被检索到（$FOUND 个 chunk 命中）"
else
  fail "新案例未被检索到（FOUND=$FOUND）"
  echo "    检索响应：$(echo "$SEARCH" | head -c 200)"
fi
rm -f "$SEARCH_COOKIE"

# ---------- 6. Git 评审接口 ----------
step "6/9 Git 评审接口（POST /review/git）"
GIT_DIFF='--- a/src/demo.ts
+++ b/src/demo.ts
@@ -1,3 +1,4 @@
 function add(a: number, b: number) {
   return a + b;
 }
+var unused = 1;
'
GIT_REVIEW=$(curl -s --max-time 60 -X POST "$API_BASE/review/git" \
  -H "Content-Type: application/json" \
  -d "{
    \"diff_text\": $(echo "$GIT_DIFF" | python -c "import json,sys; print(json.dumps(sys.stdin.read()))"),
    \"files\": [{
      \"file_path\": \"src/demo.ts\",
      \"language\": \"typescript\",
      \"code\": \"function add(a: number, b: number) {\\n  return a + b;\\n}\\nvar unused = 1;\\n\",
      \"additions\": 1,
      \"deletions\": 0
    }],
    \"persist_feedback\": false
  }")
GIT_OK=$(echo "$GIT_REVIEW" | python -c "import json,sys; d=json.load(sys.stdin); print('1' if 'results' in d else '0')" 2>/dev/null || echo 0)
GIT_TOTAL=$(echo "$GIT_REVIEW" | python -c "import json,sys; d=json.load(sys.stdin); results=d.get('results', []); print(sum(r.get('review_report', {}).get('total', 0) if r.get('review_report') else 0 for r in results))" 2>/dev/null || echo 0)

if [ "$GIT_OK" = "1" ]; then
  pass "Git 评审接口返回有效响应（命中 $GIT_TOTAL 条问题）"
else
  fail "Git 评审失败：$(echo "$GIT_REVIEW" | head -c 200)"
fi

# ---------- 7. Web 后台登录 ----------
step "7/9 Web 后台登录（POST /knowledge/login）"
COOKIE_FILE=$(mktemp)
LOGIN_HTTP=$(curl -s -c "$COOKIE_FILE" -X POST "$API_BASE/knowledge/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "password=admin123" -o /dev/null -w "%{http_code}" --max-time 10)
DASH_HTTP=$(curl -s -b "$COOKIE_FILE" "$API_BASE/knowledge/admin" \
  -o /dev/null -w "%{http_code}" --max-time 10)

if [ "$LOGIN_HTTP" = "302" ] && [ "$DASH_HTTP" = "200" ]; then
  pass "Web 登录 $LOGIN_HTTP + Dashboard $DASH_HTTP"
else
  fail "Web 登录失败（login=$LOGIN_HTTP dashboard=$DASH_HTTP）"
fi
rm -f "$COOKIE_FILE"

# ---------- 8. 业务统计 ----------
step "8/9 业务统计（GET /review/stats）"
STATS=$(curl -s --max-time 10 "$API_BASE/review/stats")
DOC_COUNT=$(echo "$STATS" | python -c "import json,sys; print(json.load(sys.stdin)['knowledge_base']['document_count'])" 2>/dev/null || echo 0)
CHUNK_COUNT=$(echo "$STATS" | python -c "import json,sys; print(json.load(sys.stdin)['knowledge_base']['chunk_count'])" 2>/dev/null || echo 0)
REVIEW_COUNT=$(echo "$STATS" | python -c "import json,sys; print(json.load(sys.stdin)['usage']['total_requests'])" 2>/dev/null || echo 0)

if [ "$DOC_COUNT" -gt 0 ] && [ "$CHUNK_COUNT" -gt 0 ]; then
  pass "统计正常：KB=$DOC_COUNT docs / $CHUNK_COUNT chunks；累计 $REVIEW_COUNT 次请求"
else
  fail "统计异常：$STATS"
fi

# ---------- 9. 跨重启持久化（可选） ----------
if [ "$SKIP_RESTART" = "true" ]; then
  step "9/9 跨重启持久化（已跳过：--skip-restart）"
  echo -e "  ${YELLOW}⏭ 跳过${RESET}"
elif [ -f "$PROJECT_ROOT/docker-compose.yml" ] && command -v docker >/dev/null 2>&1; then
  step "9/9 跨重启持久化（停 API → 验证 → 重启 → 验证）"
  cd "$PROJECT_ROOT"
  DOCS_BEFORE=$(curl -s --max-time 10 "$API_BASE/review/stats" | python -c "import json,sys; print(json.load(sys.stdin)['knowledge_base']['document_count'])" 2>/dev/null || echo 0)

  echo "  → 停止 API 容器..."
  docker compose stop api >/dev/null 2>&1

  echo "  → 验证 Redis 数据仍在..."
  sleep 2
  REDIS_COUNT=$(docker compose exec -T redis redis-cli FT.SEARCH aiFrontCR_kb "*" LIMIT 0 0 2>/dev/null | head -1 || echo 0)
  if [ "$REDIS_COUNT" -gt 0 ] 2>/dev/null; then
    pass "Redis 中仍有 $REDIS_COUNT 条记录"
  else
    fail "Redis 数据丢失：$REDIS_COUNT"
  fi

  echo "  → 启动 API 容器..."
  docker compose start api >/dev/null 2>&1

  echo "  → 等待健康..."
  RESTART_OK=false
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    HEALTH=$(curl -s --max-time 5 "$API_BASE/health" 2>/dev/null || true)
    if echo "$HEALTH" | grep -q '"status":"ok"' 2>/dev/null; then
      pass "重启后 API 健康（等待 $((i*3))s）"
      RESTART_OK=true
      break
    fi
    sleep 3
  done
  if [ "$RESTART_OK" != "true" ]; then
    fail "重启后 API 在 45s 内未健康"
  fi

  DOCS_AFTER=$(curl -s --max-time 10 "$API_BASE/review/stats" | python -c "import json,sys; print(json.load(sys.stdin)['knowledge_base']['document_count'])" 2>/dev/null || echo 0)
  if [ "$DOCS_BEFORE" = "$DOCS_AFTER" ] && [ "$DOCS_AFTER" -gt 0 ]; then
    pass "跨重启数据一致：$DOCS_BEFORE docs（重启前）= $DOCS_AFTER docs（重启后）"
  else
    fail "跨重启数据丢失：before=$DOCS_BEFORE after=$DOCS_AFTER"
  fi
else
  step "9/9 跨重启持久化（已跳过：未找到 docker-compose.yml）"
  echo -e "  ${YELLOW}⏭ 跳过${RESET}"
fi

# ---------- 汇总 ----------
echo ""
echo "════════════════════════════════════════════════════════"
echo -e "  ${BOLD}阶段6 端到端联调报告${RESET}"
echo "════════════════════════════════════════════════════════"
echo -e "  ${GREEN}通过：$PASS_COUNT${RESET}    ${RED}失败：$FAIL_COUNT${RESET}"
if [ ${#FAILED_STEPS[@]} -gt 0 ]; then
  echo ""
  echo -e "  ${RED}失败步骤：${RESET}"
  for s in "${FAILED_STEPS[@]}"; do
    echo -e "    ${RED}• $s${RESET}"
  done
  echo ""
  echo -e "  ${RED}❌ 联调失败${RESET}"
  exit 1
fi
echo ""
echo -e "  ${GREEN}✅ 全部通过，aiFrontCR 端到端可用${RESET}"
exit 0
