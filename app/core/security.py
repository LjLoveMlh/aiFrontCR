"""安全相关：密码比对 / Cookie 签名 / Token 生成."""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256


def constant_time_eq(a: str, b: str) -> bool:
    """常时间字符串比较，防时序攻击."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def check_password(input_password: str, expected_password: str) -> bool:
    """校验密码（阶段1 简易版，明文比较，生产应换 bcrypt / argon2）."""
    return constant_time_eq(input_password, expected_password)


def gen_session_secret() -> str:
    """生成 32 字节十六进制 session 密钥（用于首次启动初始化）."""
    return secrets.token_hex(32)


def short_hash(text: str) -> str:
    """短哈希（8 位），用于生成 doc_id / chunk_id 的可读后缀."""
    return sha256(text.encode("utf-8")).hexdigest()[:8]
