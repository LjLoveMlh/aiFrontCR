"""阶段2 · LangGraph 评审工作流单元测试.

覆盖：
- 节点纯函数（不需要 LLM / Redis）
- Prompt 模板构造
- Workflow 编排结构
- JSON 解析鲁棒性
- LLM mock 后的端到端串联
"""

from __future__ import annotations

import json
import unittest
from typing import Any, Dict
from unittest.mock import MagicMock, patch

from app.agents.nodes import _parse_llm_json
from app.agents.prompts import _infer_lang, build_human_prompt
from app.agents.state import ReviewState
from app.agents.workflow import build_workflow, run_review
from app.entities.review import ReviewItem, ReviewReport, Severity


# ============================================================
# JSON 解析
# ============================================================
class TestParseLLMJson(unittest.TestCase):
    def test_pure_json(self):
        raw = json.dumps({"summary": "ok", "items": []}, ensure_ascii=False)
        self.assertEqual(_parse_llm_json(raw)["summary"], "ok")

    def test_json_fence(self):
        raw = '```json\n{"summary": "x", "items": []}\n```'
        self.assertEqual(_parse_llm_json(raw)["summary"], "x")

    def test_json_fence_no_lang(self):
        raw = '```\n{"summary": "y", "items": []}\n```'
        self.assertEqual(_parse_llm_json(raw)["summary"], "y")

    def test_garbage_around(self):
        raw = '以下是结果：\n{"summary": "z", "items": []}\n以上。'
        self.assertEqual(_parse_llm_json(raw)["summary"], "z")

    def test_invalid_returns_none(self):
        self.assertIsNone(_parse_llm_json(""))
        self.assertIsNone(_parse_llm_json("not a json at all"))


# ============================================================
# Prompt 构造
# ============================================================
class TestBuildHumanPrompt(unittest.TestCase):
    def test_no_context(self):
        p = build_human_prompt("let x = 1;", None, "javascript", "", "")
        self.assertIn("let x = 1;", p)
        self.assertIn("javascript", p)
        self.assertNotIn("团队编码规范", p)
        self.assertNotIn("历史评审案例", p)

    def test_with_spec(self):
        p = build_human_prompt(
            "let x = 1;", "a.ts", None, "## 规则1\n禁止使用 any", ""
        )
        self.assertIn("团队编码规范", p)
        self.assertIn("禁止使用 any", p)

    def test_with_cases(self):
        p = build_human_prompt(
            "let x = 1;", "a.ts", None, "", "## 案例\n之前被指出..."
        )
        self.assertIn("历史评审案例", p)
        self.assertIn("之前被指出", p)

    def test_infer_lang_ts(self):
        self.assertEqual(_infer_lang("foo.ts", ""), "typescript")
        self.assertEqual(_infer_lang("foo.tsx", ""), "typescript")
        self.assertEqual(_infer_lang("foo.vue", ""), "vue")
        self.assertEqual(_infer_lang("foo.js", ""), "javascript")
        self.assertEqual(_infer_lang("foo.py", ""), "python")


# ============================================================
# 节点纯函数（不依赖外部 IO）
# ============================================================
class TestNodes(unittest.TestCase):
    def test_receive_code_empty(self):
        from app.agents.nodes import node_receive_code

        out = node_receive_code({"code": ""})  # type: ignore
        self.assertEqual(out["error"], "code 不能为空")

    def test_receive_code_whitespace(self):
        from app.agents.nodes import node_receive_code

        out = node_receive_code({"code": "   \n  "})  # type: ignore
        self.assertEqual(out["error"], "code 不能为空")

    def test_receive_code_normal(self):
        from app.agents.nodes import node_receive_code

        out = node_receive_code(  # type: ignore
            {"code": "  let x = 1;  ", "file_path": "a.ts", "language": "typescript"}
        )
        self.assertEqual(out["code"], "let x = 1;")
        self.assertEqual(out["file_path"], "a.ts")
        self.assertTrue(out["persist_feedback"])

    def test_classify_severity(self):
        from app.agents.nodes import node_classify_severity

        report = ReviewReport(
            summary="x",
            items=[
                ReviewItem(severity=Severity.BLOCKING, title="a", review_opinion="b", code_bad="c"),
                ReviewItem(severity=Severity.WARNING, title="d", review_opinion="e", code_bad="f"),
                ReviewItem(severity=Severity.WARNING, title="g", review_opinion="h", code_bad="i"),
                ReviewItem(severity=Severity.INFO, title="j", review_opinion="k", code_bad="l"),
            ],
        )
        out = node_classify_severity({"review_report": report})  # type: ignore
        r = out["review_report"]
        self.assertEqual(r.blocking_count, 1)
        self.assertEqual(r.warning_count, 2)
        self.assertEqual(r.info_count, 1)
        self.assertEqual(r.total, 4)
        self.assertTrue(r.has_blocking)


# ============================================================
# Workflow 编排结构
# ============================================================
class TestWorkflowStructure(unittest.TestCase):
    def test_nodes_registered(self):
        wf = build_workflow()
        names = list(wf.nodes.keys())
        # 5 个业务节点 + 入口 __start__
        self.assertIn("receive", names)
        self.assertIn("rag", names)
        self.assertIn("llm_review", names)
        self.assertIn("classify", names)
        self.assertIn("persist", names)


# ============================================================
# 端到端 mock：mock LLM + mock RAG 跑完整 5 节点
# ============================================================
class TestEndToEndMocked(unittest.TestCase):
    """端到端串联：用 mock 替换 llm_client.chat 和 knowledge_repo.search."""

    def _mock_llm_json(self) -> str:
        return json.dumps(
            {
                "summary": "mock 评审",
                "items": [
                    {
                        "severity": "blocking",
                        "title": "mock 阻断",
                        "rule_id": "RULE-001",
                        "code_bad": "let x: any = 1",
                        "code_good": "let x: number = 1",
                        "review_opinion": "应避免使用 any",
                    }
                ],
            },
            ensure_ascii=False,
        )

    def test_run_review_with_mocks(self):
        mock_rag_result = MagicMock()
        mock_rag_result.results = []

        with patch("app.agents.nodes.knowledge_repo") as mock_kr, patch(
            "app.agents.nodes.llm_client"
        ) as mock_lc:
            mock_kr.search.return_value = mock_rag_result
            mock_lc.init.return_value = None
            mock_lc.chat.return_value = self._mock_llm_json()
            mock_kr.init.return_value = None
            mock_kr.add_text.return_value = MagicMock(id="mock-doc-id")

            result = run_review(
                code="let x: any = 1",
                file_path="test.ts",
                language="typescript",
                persist_feedback=True,
            )

        self.assertIsNotNone(result["review_report"])
        report = result["review_report"]
        self.assertEqual(report["summary"], "mock 评审")
        self.assertEqual(report["blocking_count"], 1)
        self.assertEqual(len(report["items"]), 1)
        self.assertEqual(report["items"][0]["severity"], "blocking")
        self.assertEqual(result["feedback_doc_id"], "mock-doc-id")
        self.assertGreater(result["elapsed_ms"], 0)

    def test_run_review_no_persist(self):
        with patch("app.agents.nodes.knowledge_repo") as mock_kr, patch(
            "app.agents.nodes.llm_client"
        ) as mock_lc:
            mock_kr.search.return_value = MagicMock(results=[])
            mock_lc.init.return_value = None
            mock_lc.chat.return_value = self._mock_llm_json()

            result = run_review(
                code="let x: any = 1",
                file_path="test.ts",
                persist_feedback=False,
            )
        self.assertIsNone(result["feedback_doc_id"])
        # 即便不开 persist，knowledge_repo.add_text 也不该被调
        mock_kr.add_text.assert_not_called()


if __name__ == "__main__":
    unittest.main()