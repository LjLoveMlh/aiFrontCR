"""阶段3 · Git diff 解析 + GitOps 单元测试."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.core.git_ops import (
    ChangedFile,
    DiffHunk,
    GitOps,
    _infer_lang,
    parse_diff_text,
)


# ============================================================
# 纯函数：parse_diff_text
# ============================================================
class TestParseDiffText(unittest.TestCase):
    SAMPLE = """diff --git a/src/foo.ts b/src/foo.ts
index 1234567..abcdef0 100644
--- a/src/foo.ts
+++ b/src/foo.ts
@@ -1,5 +1,7 @@
 line1
-old line
+new line
+another new
 line3
 line4
@@ -10,2 +12,3 @@
 a
 b
+c
diff --git a/src/bar.tsx b/src/bar.tsx
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/bar.tsx
@@ -0,0 +1,4 @@
+export const Foo = () => {
+  return null;
+};
+
diff --git a/src/deleted.ts b/src/deleted.ts
deleted file mode 100644
index 1234567..0000000
--- a/src/deleted.ts
+++ /dev/null
@@ -1,3 +0,0 @@
-line a
-line b
-line c
"""

    def test_parse_count(self):
        files = parse_diff_text(self.SAMPLE)
        self.assertEqual(len(files), 3)

    def test_parse_modified(self):
        f = parse_diff_text(self.SAMPLE)[0]
        self.assertEqual(f.file_path, "src/foo.ts")
        self.assertEqual(f.status, "M")
        self.assertEqual(f.language, "typescript")
        self.assertEqual(len(f.hunks), 2)

    def test_parse_added(self):
        f = parse_diff_text(self.SAMPLE)[1]
        self.assertEqual(f.file_path, "src/bar.tsx")
        self.assertEqual(f.status, "A")
        self.assertEqual(len(f.hunks), 1)
        self.assertEqual(f.hunks[0].new_count, 4)

    def test_parse_deleted(self):
        f = parse_diff_text(self.SAMPLE)[2]
        self.assertEqual(f.file_path, "src/deleted.ts")
        self.assertEqual(f.status, "D")

    def test_added_code_property(self):
        f = parse_diff_text(self.SAMPLE)[0]
        code = f.added_code
        # new line 和 another new 都应在
        self.assertIn("new line", code)
        self.assertIn("another new", code)
        # old line 不该在
        self.assertNotIn("old line", code)

    def test_empty_input(self):
        self.assertEqual(parse_diff_text(""), [])
        self.assertEqual(parse_diff_text("   \n\n"), [])

    def test_infer_lang(self):
        self.assertEqual(_infer_lang("a.ts"), "typescript")
        self.assertEqual(_infer_lang("a.tsx"), "typescript")
        self.assertEqual(_infer_lang("a.vue"), "vue")
        self.assertEqual(_infer_lang("a.py"), "python")
        self.assertEqual(_infer_lang("a.unknown"), "unknown")


# ============================================================
# GitOps：基于真实 git 仓库
# ============================================================
class TestGitOpsReal(unittest.TestCase):
    """用一个临时 git 仓库跑真实 diff 流程."""

    def setUp(self):
        # 建临时目录 + git init
        self.tmpdir = tempfile.mkdtemp(prefix="aifrontcr-test-")
        self.repo_path = self.tmpdir
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_path, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@local"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=self.repo_path, check=True)
        # 写一个初始文件并 commit
        f1 = Path(self.repo_path) / "src" / "foo.ts"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_path, check=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_staged_diff(self):
        # 改文件 + 暂存
        f1 = Path(self.repo_path) / "src" / "foo.ts"
        f1.write_text("line1\nline2 modified\nline3\nline4 added\nline5\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo_path, check=True)

        ops = GitOps(self.repo_path)
        self.assertTrue(ops.is_valid_repo)
        files = ops.get_staged_hunks()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].file_path, "src/foo.ts")
        self.assertEqual(files[0].status, "M")
        self.assertGreater(len(files[0].hunks), 0)
        code = files[0].added_code
        self.assertIn("line2 modified", code)
        self.assertIn("line4 added", code)

    def test_added_file(self):
        # 新增文件
        f2 = Path(self.repo_path) / "src" / "bar.tsx"
        f2.write_text("export const Bar = 1;\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo_path, check=True)

        ops = GitOps(self.repo_path)
        files = ops.get_staged_hunks()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].status, "A")
        self.assertIn("export const Bar", files[0].added_code)

    def test_no_diff(self):
        ops = GitOps(self.repo_path)
        files = ops.get_staged_hunks()
        self.assertEqual(len(files), 0)

    def test_invalid_repo(self):
        # 临时空目录
        bad = tempfile.mkdtemp(prefix="aifrontcr-bad-")
        try:
            ops = GitOps(bad)
            self.assertFalse(ops.is_valid_repo)
            self.assertEqual(ops.get_staged_hunks(), [])
        finally:
            import shutil

            shutil.rmtree(bad, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()