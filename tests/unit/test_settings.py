"""配置加载单测."""

from __future__ import annotations

from pathlib import Path

from app.conf.settings import Settings, get_settings


def test_settings_defaults(fake_env):
    """测试默认值与 .env 覆盖."""
    # 清掉 lru_cache，确保读到 monkeypatch 的 env
    get_settings.cache_clear()
    s = Settings()
    assert s.dashscope_api_key == "sk-fake-key-for-test"
    assert s.qwen_chat_model == "qwen-test-model"
    assert s.app_name == "aiFrontCR"
    assert s.qwen_temperature == 0.1
    assert s.qwen_max_tokens == 4096
    # log_level 来自 env
    assert s.log_level == "DEBUG"


def test_get_settings_singleton(fake_env):
    """测试 get_settings() 缓存单例."""
    get_settings.cache_clear()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_log_dir_abs_creates_dir(fake_env, tmp_path):
    """log_dir_abs 应创建目录并返回绝对路径."""
    get_settings.cache_clear()
    s = Settings(log_dir=str(tmp_path / "test_logs"))
    log_dir = s.log_dir_abs
    assert log_dir.is_absolute()
    assert log_dir.exists()
    assert log_dir.is_dir()


def test_project_root():
    """project_root 指向项目根（含 app/ 子目录；容器内是 /app，宿主机是 aiFrontCR/）."""
    get_settings.cache_clear()
    s = Settings(dashscope_api_key="sk-test")
    # 必须能定位到 app/ 子目录（不限定名称，兼容容器 /app 和宿主机 aiFrontCR/）
    assert (s.project_root / "app").is_dir()
    assert (s.project_root / "requirements.txt").is_file()
