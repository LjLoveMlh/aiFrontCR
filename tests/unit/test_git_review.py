"""阶段3 · Git 评审 workflow + API 单元测试（mock LLM）."""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from app.agents.workflow import run_git_review
from app.entities.review import GitReviewRequest, GitReviewResponse


SAMPLE_DIFF = """diff --git a/src/bad.ts b/src/bad.ts
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/bad.ts
@@ -0,0 +1,3 @@
+function add(a, b) { return a + b; }
+add(1, 2);
+var unused = 1;
"""


def _mock_llm_json() -> str:
    return json.dumps(
        {
            "summary": "mock 评审",
            "items": [
                {
                    "severity": "blocking",
                    "title": "mock 阻断",
                    "rule_id": None,
                    "code_bad": "let x: any = 1",
                    "code_good": "let x: number = 1",
                    "review_opinion": "应避免使用 any",
                },
                {
                    "severity": "warning",
                    "title": "mock 警告",
                    "rule_id": None,
                    "code_bad": "var x = 1",
                    "code_good": "const x = 1",
                    "review_opinion": "不要用 var",
                },
            ],
        },
        ensure_ascii=False,
    )


class TestRunGitReview(unittest.TestCase):
    def test_diff_text_mode(self):
        with patch("app.agents.nodes.knowledge_repo") as mock_kr, patch(
            "app.agents.nodes.llm_client"
        ) as mock_lc:
            mock_kr.search.return_value = MagicMock(results=[])
            mock_lc.init.return_value = None
            mock_lc.chat.return_value = _mock_llm_json()
            mock_kr.init.return_value = None
            mock_kr.add_text.return_value = MagicMock(id="mock-doc-id")

            result = run_git_review(
                diff_text=SAMPLE_DIFF,
                persist_feedback=True,
                fail_on_blocking=True,
            )

        self.assertIsInstance(result, GitReviewResponse)
        self.assertEqual(result.total, 1)  # 1 file
        self.assertEqual(result.blocking_count, 1)
        self.assertEqual(result.warning_count, 1)
        self.assertTrue(result.has_blocking)
        self.assertTrue(result.should_block_commit)

    def test_fail_on_blocking_false(self):
        with patch("app.agents.nodes.knowledge_repo") as mock_kr, patch(
            "app.agents.nodes.llm_client"
        ) as mock_lc:
            mock_kr.search.return_value = MagicMock(results=[])
            mock_lc.init.return_value = None
            mock_lc.chat.return_value = _mock_llm_json()
            mock_kr.add_text.return_value = MagicMock(id="mock")

            result = run_git_review(
                diff_text=SAMPLE_DIFF,
                persist_feedback=False,
                fail_on_blocking=False,
            )

        self.assertTrue(result.has_blocking)
        self.assertFalse(result.should_block_commit)  # 配置不拦截

    def test_empty_diff(self):
        result = run_git_review(diff_text="", persist_feedback=False)
        self.assertEqual(result.total, 0)
        self.assertFalse(result.has_blocking)
        self.assertFalse(result.should_block_commit)

    def test_multiple_files(self):
        multi = SAMPLE_DIFF + """
diff --git a/src/ok.ts b/src/ok.ts
new file mode 100644
--- /dev/null
+++ b/src/ok.ts
@@ -0,0 +1,2 @@
+export const ok = 1;
+export const also = 2;
"""
        with patch("app.agents.nodes.knowledge_repo") as mock_kr, patch(
            "app.agents.nodes.llm_client"
        ) as mock_lc:
            mock_kr.search.return_value = MagicMock(results=[])
            mock_lc.init.return_value = None
            mock_lc.chat.return_value = _mock_llm_json()
            mock_kr.add_text.return_value = MagicMock(id="mock")

            result = run_git_review(diff_text=multi, persist_feedback=False)

        self.assertEqual(result.total, 2)
        # 2 个文件 * mock 评审的 1 blocking + 1 warning = 2 blocking + 2 warning
        self.assertEqual(result.blocking_count, 2)
        self.assertEqual(result.warning_count, 2)


class TestGitReviewRequestSchema(unittest.TestCase):
    def test_diff_text_mode(self):
        req = GitReviewRequest(
            diff_text="diff --git a b",
            fail_on_blocking=True,
        )
        self.assertEqual(req.fail_on_blocking, True)
        self.assertIsNone(req.repo_path)
        self.assertIsNone(req.files)

    def test_files_mode(self):
        from app.entities.review import GitReviewFile

        req = GitReviewRequest(
            files=[
                GitReviewFile(file_path="a.ts", code="let x = 1;", language="typescript")
            ],
        )
        self.assertEqual(len(req.files), 1)
        self.assertEqual(req.files[0].file_path, "a.ts")


if __name__ == "__main__":
    unittest.main()