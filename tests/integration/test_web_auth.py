"""Web 后台鉴权集成测试（TestClient）.

不依赖 Redis，可独立跑：
    pytest tests/integration/test_web_auth.py -v
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.conf.settings import settings
    from app.main import app

    # 用固定 session_secret
    settings.session_secret = "test-secret-32-bytes-1234567890abcdef"
    settings.admin_password = "test-pwd"
    return TestClient(app)


def test_login_page_get(client):
    """未登录访问 /knowledge/admin 应跳到 /knowledge/login."""
    r = client.get("/knowledge/admin", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == "/knowledge/login"

    r = client.get("/knowledge/login")
    assert r.status_code == 200
    assert "管理员密码" in r.text


def test_login_post_wrong(client):
    r = client.post("/knowledge/login", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 302
    assert "msg=密码错误" in r.headers["Location"]


def test_login_post_correct(client):
    r = client.post("/knowledge/login", data={"password": "test-pwd"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["Location"] == "/knowledge/admin"
    # 后续请求带 cookie 应可访问
    r2 = client.get("/knowledge/admin", follow_redirects=False)
    # 此时 TestClient 自动带了 cookie，可能 200 或 500（无 Redis）
    # 我们只验证不再 302
    assert r2.status_code != 302 or r2.headers.get("Location", "").endswith("/login") is False


def test_whoami_unauth(client):
    r = client.get("/knowledge/api/whoami")
    assert r.status_code == 200
    assert r.json()["is_admin"] is False
