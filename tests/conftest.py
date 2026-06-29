"""pytest 公共 fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def fake_env(monkeypatch):
    """提供假环境变量，避免单测依赖真实 .env."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-fake-key-for-test")
    monkeypatch.setenv("QWEN_CHAT_MODEL", "qwen-test-model")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    return monkeypatch
