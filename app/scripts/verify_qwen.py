"""Qwen3-Max 联通验证脚本（阶段0 核心验收点）.

执行：
    python -m app.scripts.verify_qwen

行为：
    1. 读取 .env 配置
    2. 初始化 LLMClient
    3. 发一条中文测试消息，打印模型回复 + 用时
    4. 退出码：成功 0 / 失败 1
"""

from __future__ import annotations

import sys
import time

from app.clients.llm_client import llm_client
from app.conf.settings import settings
from app.core.log import logger


TEST_PROMPT = "用一句话介绍你自己，并说明你能为 aiFrontCR 前端代码评审 Agent 做什么。"
TEST_SYSTEM = "你是一位资深的阿里通义千问模型，正在参与一个 AI Agent 项目的阶段0联通测试。"


def main() -> int:
    print("=" * 70)
    print(f"aiFrontCR · Qwen 联通验证")
    print("=" * 70)
    print(f"模型：{settings.qwen_chat_model}")
    print(f"Base URL：{settings.dashscope_base_url}")
    print(f"Temperature：{settings.qwen_temperature}")
    print(f"Max Tokens：{settings.qwen_max_tokens}")
    print(f"Timeout：{settings.dashscope_timeout}s")
    print("-" * 70)

    # 1. 初始化
    try:
        llm_client.init()
    except Exception as e:
        print(f"\n[FAIL] LLMClient 初始化失败：{e}")
        logger.exception("LLMClient 初始化失败")
        return 1

    print(f"Backend：{llm_client.backend}")
    print("-" * 70)

    # 2. 调用
    print(f"\n[PROMPT]\n{TEST_PROMPT}\n")
    print("[REPLY] (loading...)", flush=True)
    start = time.time()
    try:
        reply = llm_client.quick_chat(prompt=TEST_PROMPT, system=TEST_SYSTEM)
    except Exception as e:
        elapsed = time.time() - start
        print(f"\n[FAIL] 调用失败（耗时 {elapsed:.2f}s）：{e}")
        logger.exception("Qwen 调用失败")
        return 1
    elapsed = time.time() - start

    # 3. 输出
    print(f"\n{reply}\n")
    print("-" * 70)
    print(f"[OK] 调用成功")
    print(f"     耗时：{elapsed:.2f}s")
    print(f"     回复长度：{len(reply)} 字符")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
