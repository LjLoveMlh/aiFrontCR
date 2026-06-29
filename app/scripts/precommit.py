"""阶段3 · Pre-commit 钩子 CLI 入口.

用途：
    .git/hooks/pre-commit 调用本脚本，自动评审暂存区代码。

调用：
    python -m app.scripts.precommit                  # 默认：取当前 git 仓库
    python -m app.scripts.precommit --repo /path     # 指定仓库
    python -m app.scripts.precommit --no-color       # 关闭彩色
    python -m app.scripts.precommit --no-block       # 不拦截（仅展示）
    python -m app.scripts.precommit --no-persist     # 不沉淀到 KB
    python -m app.scripts.precommit --http           # 走 HTTP 接口（部署版）

退出码：
    0 - 无 blocking，放行
    1 - 有 blocking，拦截（可被 git commit 捕获）
    2 - 系统异常
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import List

# 允许从仓库根目录直接运行
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


# ============================================================
# ANSI 颜色（兼容 Windows Git Bash）
# ============================================================
class C:
    """终端颜色代码."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"

    @classmethod
    def disable(cls):
        for attr in dir(cls):
            if attr.isupper() and not attr.startswith("_"):
                setattr(cls, attr, "")


SEVERITY_STYLE = {
    "blocking": (C.RED + C.BOLD, "🚫"),
    "warning": (C.YELLOW + C.BOLD, "⚠️ "),
    "info": (C.CYAN, "ℹ️ "),
}


def colorize(text: str, color: str, enable: bool = True) -> str:
    if not enable:
        return text
    return f"{color}{text}{C.RESET}"


# ============================================================
# 主流程
# ============================================================
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="aiFrontCR-precommit",
        description="aiFrontCR · Pre-commit 自动代码评审",
    )
    parser.add_argument("--repo", default=".", help="Git 仓库根路径（默认当前目录）")
    parser.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    parser.add_argument("--no-block", action="store_true", help="不拦截 commit（仅展示评审意见）")
    parser.add_argument("--no-persist", action="store_true", help="不沉淀到 feedback 知识库")
    parser.add_argument(
        "--http",
        default=None,
        help="走 HTTP 接口（默认 None=本地直跑；可配 http://localhost:8000）",
    )
    parser.add_argument("--timeout", type=int, default=120, help="单文件评审超时（秒）")
    args = parser.parse_args()

    if args.no_color:
        C.disable()
    use_color = not args.no_color

    # 头部
    print(colorize("=" * 70, C.BOLD, use_color))
    print(colorize("🤖  aiFrontCR · Pre-commit 评审", C.BOLD + C.CYAN, use_color))
    print(colorize("=" * 70, C.BOLD, use_color))

    # 1. 探测暂存区
    try:
        diff_text = _git_staged_diff(args.repo)
    except Exception as e:
        print(colorize(f"[ERROR] git 暂存区 diff 拉取失败：{e}", C.RED, use_color))
        return 2

    if not diff_text.strip():
        print(colorize("(空暂存区，跳过评审)", C.GRAY, use_color))
        return 0

    # 2. 调评审
    print(colorize(f"📁 仓库：{args.repo}", C.GRAY, use_color))
    print(
        colorize(
            f"🔍 暂存区变更：{_count_files_in_diff(diff_text)} 个文件，"
            f"{_count_lines(diff_text)} 行 diff",
            C.GRAY,
            use_color,
        )
    )
    print(colorize("-" * 70, C.BOLD, use_color))

    t0 = time.time()
    try:
        if args.http:
            result = _review_via_http(diff_text, args)
        else:
            result = _review_local(diff_text, args)
    except Exception as e:
        print(colorize(f"\n[ERROR] 评审异常：{e}", C.RED, use_color))
        return 2

    elapsed = time.time() - t0

    # 3. 渲染结果
    if not result["results"]:
        print(colorize("(无可评审文件)", C.GRAY, use_color))
        return 0

    blocking_total = result["blocking_count"]
    warning_total = result["warning_count"]
    info_total = result["info_count"]
    has_blocking = result["has_blocking"]

    # 逐文件展示
    for item in result["results"]:
        report = item.get("review_report")
        if not report:
            err = item.get("error")
            if err:
                print(
                    colorize(
                        f"\n❌ {item['file_path']} 评审失败：{err}",
                        C.RED,
                        use_color,
                    )
                )
            continue
        if report["total"] == 0:
            print(
                colorize(
                    f"\n✅ {item['file_path']}  ·  无问题",
                    C.GREEN,
                    use_color,
                )
            )
            continue

        file_color = C.RED if report["blocking_count"] > 0 else (
            C.YELLOW if report["warning_count"] > 0 else C.CYAN
        )
        print(
            colorize(
                f"\n📄 {item['file_path']}",
                C.BOLD + file_color,
                use_color,
            )
        )
        print(
            colorize(
                f"   {report['summary']}",
                C.DIM,
                use_color,
            )
        )
        for it in report["items"]:
            style, icon = SEVERITY_STYLE.get(it["severity"], (C.GRAY, "•"))
            print(
                colorize(
                    f"\n   {icon} [{it['severity'].upper()}] {it['title']}",
                    style,
                    use_color,
                )
            )
            if it.get("rule_id"):
                print(
                    colorize(f"      规则：{it['rule_id']}", C.GRAY, use_color)
                )
            if it.get("code_bad"):
                bad = it["code_bad"].strip().splitlines()
                print(colorize("      问题代码：", C.GRAY, use_color))
                for ln in bad[:8]:
                    print(colorize(f"        {ln}", C.RED, use_color))
                if len(bad) > 8:
                    print(colorize(f"        ... ({len(bad)-8} more)", C.GRAY, use_color))
            if it.get("code_good"):
                good = it["code_good"].strip().splitlines()
                print(colorize("      推荐写法：", C.GRAY, use_color))
                for ln in good[:8]:
                    print(colorize(f"        {ln}", C.GREEN, use_color))
                if len(good) > 8:
                    print(colorize(f"        ... ({len(good)-8} more)", C.GRAY, use_color))
            if it.get("review_opinion"):
                print(
                    colorize(
                        f"      💬 {it['review_opinion']}",
                        C.DIM,
                        use_color,
                    )
                )

    # 4. 总结
    print(colorize("\n" + "=" * 70, C.BOLD, use_color))
    summary = (
        f"📊 总计：{colorize(f'{blocking_total} blocking', C.RED + C.BOLD, use_color)}, "
        f"{colorize(f'{warning_total} warning', C.YELLOW + C.BOLD, use_color)}, "
        f"{colorize(f'{info_total} info', C.CYAN, use_color)}  "
        f"({len(result['results'])} 个文件，{elapsed:.1f}s)"
    )
    print(summary)

    if has_blocking and not args.no_block:
        print(colorize("🚫 COMMIT 已被拦截：存在 blocking 问题", C.RED + C.BOLD, use_color))
        print(
            colorize(
                "   修复后重试 commit；或加 --no-block 仅查看（不推荐）",
                C.GRAY,
                use_color,
            )
        )
        print(colorize("=" * 70, C.BOLD, use_color))
        return 1

    print(colorize("✅ COMMIT 放行", C.GREEN + C.BOLD, use_color))
    print(colorize("=" * 70, C.BOLD, use_color))
    return 0


# ============================================================
# 辅助函数
# ============================================================
def _git_staged_diff(repo_path: str) -> str:
    """从指定仓库拉暂存区 diff."""
    r = subprocess.run(
        ["git", "diff", "--cached", "--no-color"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.stdout or ""


def _count_files_in_diff(diff: str) -> int:
    return sum(1 for ln in diff.splitlines() if ln.startswith("diff --git "))


def _count_lines(diff: str) -> int:
    return sum(1 for ln in diff.splitlines() if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")))


def _review_local(diff_text: str, args) -> dict:
    """本地直接调 LangGraph 评审."""
    from app.agents.workflow import run_git_review

    resp = run_git_review(
        diff_text=diff_text,
        persist_feedback=not args.no_persist,
        fail_on_blocking=not args.no_block,
    )
    return resp.model_dump()


def _review_via_http(diff_text: str, args) -> dict:
    """通过 HTTP 接口评审（适合部署后从本机调）."""
    import json
    import urllib.request

    url = args.http.rstrip("/") + "/review/git"
    payload = json.dumps(
        {
            "diff_text": diff_text,
            "persist_feedback": not args.no_persist,
            "fail_on_blocking": not args.no_block,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=args.timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[ABORT] 用户中断")
        sys.exit(2)