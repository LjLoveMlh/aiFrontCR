"""Git 操作封装（阶段3 核心模块）.

提供：
- GitOps 类：用 gitpython 封装仓库操作
- DiffHunk / ChangedFile 数据结构：把 diff 解析为结构化
- parse_diff() 纯函数：从 unified diff 文本解析出 hunk 列表

典型用法：
    ops = GitOps("/path/to/repo")
    files = ops.get_staged_files()      # 暂存区变更文件
    for f in files:
        hunks = ops.get_staged_hunks(f) # 每个文件的 diff hunks
        for h in hunks:
            print(h.new_start, h.new_count, h.code)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

try:
    import git
    from git.diff import Diff

    HAS_GITPYTHON = True
except ImportError:  # 极端兜底
    HAS_GITPYTHON = False
    git = None  # type: ignore
    Diff = None  # type: ignore


# ============================================================
# 数据结构
# ============================================================
@dataclass
class DiffHunk:
    """单个 diff hunk（含起止行号 + 新侧代码）."""

    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str  # "@@ -10,5 +12,7 @@ ..."
    # 新侧代码（去掉 + 号）
    code: str
    # 原始新增行（含 + 号），用于审阅高亮
    added_lines: List[str] = field(default_factory=list)


@dataclass
class ChangedFile:
    """变更文件（含路径 + 全部 hunks + 状态）."""

    file_path: str
    status: str  # "A" / "M" / "D" / "R" / "?"
    hunks: List[DiffHunk] = field(default_factory=list)
    language: str = "unknown"
    is_binary: bool = False

    @property
    def added_code(self) -> str:
        """把所有 hunk 的 code 拼成单段代码（用于 RAG 召回 + LLM 评审）."""
        if not self.hunks:
            return ""
        return "\n".join(h.code for h in self.hunks)

    @property
    def added_line_count(self) -> int:
        return sum(h.new_count for h in self.hunks)


# ============================================================
# 纯函数：解析 unified diff 文本
# ============================================================
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$"
)


def parse_diff_text(diff_text: str) -> List[ChangedFile]:
    """从 unified diff 文本解析为 ChangedFile 列表.

    兼容：
    - git diff 输出（含 diff --git / index / --- / +++）
    - 多个文件连续
    - 新增文件 / 修改文件 / 删除文件
    """
    if not diff_text or not diff_text.strip():
        return []

    files: List[ChangedFile] = []
    cur_file: Optional[ChangedFile] = None
    cur_hunk: Optional[DiffHunk] = None
    cur_old_line = 0
    cur_new_line = 0

    lines = diff_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # 文件头：diff --git a/xxx b/xxx
        if line.startswith("diff --git "):
            if cur_file is not None:
                if cur_hunk is not None:
                    cur_file.hunks.append(cur_hunk)
                    cur_hunk = None
                files.append(cur_file)
            # 提取路径
            m = re.search(r"^diff --git a/(.+?) b/(.+?)$", line)
            path = m.group(2) if m else ""
            cur_file = ChangedFile(file_path=path, status="M")
            cur_hunk = None
            i += 1
            continue

        # 模式：A/M/D/R
        if line.startswith("new file"):
            if cur_file:
                cur_file.status = "A"
            i += 1
            continue
        if line.startswith("deleted file"):
            if cur_file:
                cur_file.status = "D"
            i += 1
            continue
        if line.startswith("rename from"):
            if cur_file:
                cur_file.status = "R"
            i += 1
            continue
        if line.startswith("Binary files"):
            if cur_file:
                cur_file.is_binary = True
            i += 1
            continue

        # --- / +++ 跳过
        if line.startswith("--- ") or line.startswith("+++ "):
            i += 1
            continue

        # hunk 头
        m = _HUNK_HEADER_RE.match(line)
        if m and cur_file is not None:
            # 收尾上一个 hunk
            if cur_hunk is not None:
                cur_file.hunks.append(cur_hunk)
            old_start = int(m.group(1))
            old_count = int(m.group(2) or 1)
            new_start = int(m.group(3))
            new_count = int(m.group(4) or 1)
            header = m.group(5).strip()
            cur_hunk = DiffHunk(
                file_path=cur_file.file_path,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=header,
                code="",
                added_lines=[],
            )
            cur_old_line = old_start
            cur_new_line = new_start
            i += 1
            continue

        # hunk body
        if cur_hunk is not None:
            if line.startswith("+"):
                # 新增行
                cur_hunk.added_lines.append(line)
                cur_hunk.code += line[1:] + "\n"
                cur_new_line += 1
            elif line.startswith("-"):
                # 删除行（不算新增）
                cur_old_line += 1
            elif line.startswith(" "):
                # 上下文行
                cur_hunk.code += line[1:] + "\n"
                cur_old_line += 1
                cur_new_line += 1
            elif line.startswith("\\"):
                # "\ No newline at end of file"
                pass
            else:
                # 未知行（如空行 / 段尾）→ 直接略过
                pass

        i += 1

    # 收尾
    if cur_hunk is not None and cur_file is not None:
        cur_file.hunks.append(cur_hunk)
    if cur_file is not None and cur_file not in files:
        files.append(cur_file)

    # 给每个文件打语言标签
    for f in files:
        f.language = _infer_lang(f.file_path)

    return files


# ============================================================
# GitOps：基于 gitpython 的仓库操作
# ============================================================
class GitOps:
    """Git 仓库操作封装."""

    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self._repo: Optional["git.Repo"] = None
        if HAS_GITPYTHON:
            try:
                self._repo = git.Repo(self.repo_path)
            except Exception as e:
                logger.warning(f"无法初始化 git 仓库 {self.repo_path}: {e}")

    @property
    def is_valid_repo(self) -> bool:
        return self._repo is not None and not self._repo.bare

    def _ensure_repo(self) -> "git.Repo":
        if self._repo is None:
            raise RuntimeError(f"不是有效的 git 仓库: {self.repo_path}")
        return self._repo

    # ------------------------------------------------------------------
    # 暂存区
    # ------------------------------------------------------------------
    def get_staged_files(self) -> List[ChangedFile]:
        """获取暂存区变更文件列表（含未跟踪 + 修改 + 删除）."""
        if not self.is_valid_repo:
            return []
        repo = self._ensure_repo()
        # diff_index(None, HEAD) 返回相对 HEAD 的所有暂存变更
        try:
            diffs = repo.index.diff(repo.head.commit, R=True)
        except Exception:
            diffs = repo.index.diff(None, R=True)
        files: List[ChangedFile] = []
        for d in diffs:
            files.append(_diff_to_changed_file(d))
        # 未跟踪文件：手动 add intent
        try:
            untracked = repo.index.diff(None, untracked_files=True)
            for d in untracked:
                files.append(_diff_to_changed_file(d, default_status="A"))
        except Exception:
            pass
        return files

    def get_staged_diff_text(self) -> str:
        """获取暂存区 unified diff 文本（用 git CLI 拉，更可靠）."""
        if not self.is_valid_repo:
            return ""
        import subprocess

        try:
            r = subprocess.run(
                ["git", "diff", "--cached", "--no-color"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.stdout or ""
        except Exception as e:
            logger.warning(f"git diff --cached 失败: {e}")
            return ""

    def get_staged_hunks(self) -> List[ChangedFile]:
        """从暂存区拉 diff 文本并解析."""
        return parse_diff_text(self.get_staged_diff_text())

    # ------------------------------------------------------------------
    # 历史范围
    # ------------------------------------------------------------------
    def get_diff(self, commit_range: str = "HEAD~1..HEAD") -> str:
        """获取 commit 范围的 diff 文本."""
        if not self.is_valid_repo:
            return ""
        import subprocess

        try:
            r = subprocess.run(
                ["git", "diff", "--no-color", commit_range],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return r.stdout or ""
        except Exception as e:
            logger.warning(f"git diff {commit_range} 失败: {e}")
            return ""

    def get_changed_files(self, commit_range: str = "HEAD~1..HEAD") -> List[ChangedFile]:
        return parse_diff_text(self.get_diff(commit_range))

    # ------------------------------------------------------------------
    # 文件内容
    # ------------------------------------------------------------------
    def get_file_content(self, file_path: str, ref: str = "HEAD") -> str:
        """获取指定 ref 下的文件内容（diff 上下文用）."""
        if not self.is_valid_repo:
            return ""
        try:
            return self._ensure_repo().git.show(f"{ref}:{file_path}")
        except Exception:
            return ""


# ============================================================
# 内部：gitpython Diff → ChangedFile
# ============================================================
def _diff_to_changed_file(d: "Diff", default_status: str = "M") -> ChangedFile:
    path = d.b_path or d.a_path or "?"
    status = default_status
    if d.new_file:
        status = "A"
    elif d.deleted_file:
        status = "D"
    elif d.renamed:
        status = "R"
    f = ChangedFile(file_path=path, status=status, language=_infer_lang(path))
    return f


# ============================================================
# 工具：语言推断
# ============================================================
_LANG_MAP = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".vue": "vue",
    ".py": "python",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".html": "html",
    ".json": "json",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".sh": "shell",
}


def _infer_lang(file_path: str) -> str:
    p = file_path.lower()
    for ext, lang in _LANG_MAP.items():
        if p.endswith(ext):
            return lang
    return "unknown"
