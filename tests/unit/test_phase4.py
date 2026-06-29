"""阶段4 · API Key 鉴权 + request_id 中间件 + 业务统计 + SSE 单元测试."""

from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

from app.api.deps import UsageStats, _parse_api_keys, usage_stats
from app.api.stream_api import _sse


# ============================================================
# API Key 解析
# ============================================================
class TestParseApiKeys(unittest.TestCase):
    def test_empty(self):
        with patch("app.api.deps.settings") as s:
            s.api_keys = ""
            self.assertEqual(_parse_api_keys(), set())

    def test_single(self):
        with patch("app.api.deps.settings") as s:
            s.api_keys = "key1"
            self.assertEqual(_parse_api_keys(), {"key1"})

    def test_multiple(self):
        with patch("app.api.deps.settings") as s:
            s.api_keys = "key1,key2,key3"
            self.assertEqual(_parse_api_keys(), {"key1", "key2", "key3"})

    def test_whitespace(self):
        with patch("app.api.deps.settings") as s:
            s.api_keys = " key1 , key2 , "
            self.assertEqual(_parse_api_keys(), {"key1", "key2"})


# ============================================================
# API Key 鉴权依赖
# ============================================================
class TestRequireApiKey(unittest.TestCase):
    def test_anonymous_dev_mode(self):
        """未启用 + 未配置 keys → 跳过（开发模式）."""
        from app.api.deps import require_api_key

        async def run():
            from fastapi import Request

            req = Request(scope={"type": "http", "headers": []})
            return await require_api_key(req)

        with patch("app.api.deps.settings") as s:
            s.api_key_required = False
            s.api_keys = ""
            result = asyncio.run(run())
            self.assertEqual(result, "anonymous")

    def test_missing_key_when_required(self):
        from app.api.deps import require_api_key
        from fastapi import HTTPException, Request

        async def run():
            req = Request(scope={"type": "http", "headers": []})
            return await require_api_key(req)

        with patch("app.api.deps.settings") as s:
            s.api_key_required = True
            s.api_keys = "valid-key"
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(run())
            self.assertEqual(ctx.exception.status_code, 401)

    def test_wrong_key(self):
        from app.api.deps import require_api_key
        from fastapi import HTTPException, Request

        async def run():
            req = Request(scope={
                "type": "http",
                "headers": [(b"x-api-key", b"wrong-key")],
            })
            return await require_api_key(req)

        with patch("app.api.deps.settings") as s:
            s.api_key_required = True
            s.api_keys = "valid-key"
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(run())
            self.assertEqual(ctx.exception.status_code, 403)

    def test_correct_key(self):
        from app.api.deps import require_api_key
        from fastapi import Request

        async def run():
            req = Request(scope={
                "type": "http",
                "headers": [(b"x-api-key", b"valid-key")],
            })
            return await require_api_key(req)

        with patch("app.api.deps.settings") as s:
            s.api_key_required = True
            s.api_keys = "valid-key"
            result = asyncio.run(run())
            self.assertEqual(result, "valid-key")


# ============================================================
# 业务统计
# ============================================================
class TestUsageStats(unittest.TestCase):
    def setUp(self):
        self.stats = UsageStats()

    def test_record_request(self):
        self.stats.record_request("/a", 200, 100.0)
        self.stats.record_request("/a", 200, 50.0)
        self.stats.record_request("/b", 500, 200.0)
        snap = self.stats.snapshot()
        self.assertEqual(snap["total_requests"], 3)
        self.assertEqual(snap["by_endpoint"]["/a"], 2)
        self.assertEqual(snap["by_status"][200], 2)
        self.assertEqual(snap["avg_request_ms"], 116.7)  # (100+50+200)/3

    def test_record_review(self):
        self.stats.record_review(blocking=2, warning=1, info=0)
        self.stats.record_review(blocking=0, warning=1, info=0)
        self.stats.record_review(blocking=0, warning=0, info=0)
        snap = self.stats.snapshot()
        self.assertEqual(snap["reviews"]["total"], 3)
        self.assertEqual(snap["reviews"]["blocking"], 1)
        self.assertEqual(snap["reviews"]["warning_only"], 1)
        self.assertEqual(snap["reviews"]["clean"], 1)
        self.assertAlmostEqual(snap["reviews"]["blocking_rate"], 0.333, places=2)

    def test_record_llm(self):
        self.stats.record_llm(1000.0)
        self.stats.record_llm(3000.0)
        snap = self.stats.snapshot()
        self.assertEqual(snap["llm"]["calls"], 2)
        self.assertEqual(snap["llm"]["avg_ms"], 2000.0)


# ============================================================
# SSE 工具
# ============================================================
class TestSSEFormat(unittest.TestCase):
    def test_sse_basic(self):
        out = _sse("test", {"key": "value", "num": 42})
        text = out.decode("utf-8")
        self.assertIn("event: test", text)
        self.assertIn('"key": "value"', text)
        self.assertIn('"num": 42', text)
        self.assertTrue(text.endswith("\n\n"))

    def test_sse_unicode(self):
        out = _sse("data", {"msg": "中文"})
        text = out.decode("utf-8")
        self.assertIn("中文", text)


# ============================================================
# /knowledge/add 端到端（mock 知识库）
# ============================================================
class TestKnowledgeAddAPI(unittest.TestCase):
    def test_request_validation(self):
        from app.api.knowledge_add_api import KnowledgeAddRequest
        # 合法请求
        req = KnowledgeAddRequest(
            title="测试",
            code="let x = 1;",
            asset_type="review_case",
        )
        self.assertEqual(req.title, "测试")
        self.assertEqual(req.asset_type.value, "review_case")

    def test_feedback_requires_opinion(self):
        """feedback 类型必填 review_opinion（在端点层校验）."""
        from app.api.knowledge_add_api import KnowledgeAddRequest

        # Pydantic 不强制，端点强制
        req = KnowledgeAddRequest(
            title="test",
            code="x",
            asset_type="feedback",
        )
        self.assertIsNone(req.review_opinion)


if __name__ == "__main__":
    unittest.main()