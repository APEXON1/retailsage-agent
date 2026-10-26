"""账号校验：与 my-rag 完全同一套算法与同一张表。

刻意不 import my-rag 的 auth.py —— 它会连带拉起 database.py / config.py，
并把 my-rag 目录塞进 sys.path，对门户进程是纯负担。
这里只复刻必要的几个函数（PBKDF2 参数必须与 my-rag/auth.py:47,87-101 保持一致，
否则存量账号密码会校验不过）。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .dbi import connect

# 与 my-rag/config.py:17 一致：旧账号输入 hr03 自动补全为 hr03@demo.local
LEGACY_LOGIN_DOMAIN = "demo.local"

# 与 my-rag/auth.py:47 一致
PBKDF2_ITERATIONS = 120_000


def normalize_login_id(identifier: str) -> str:
    """含 @ 视为邮箱（转小写）；否则按旧账号规则补全域。"""
    identifier = (identifier or "").strip()
    if not identifier:
        return ""
    if "@" in identifier:
        return identifier.lower()
    return f"{identifier}@{LEGACY_LOGIN_DOMAIN}"


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    """恒定时间比较；任何异常都视为失败。"""
    try:
        calc, _ = hash_password(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(calc, password_hash or "")
    except Exception:
        return False


def get_user(email: str) -> dict | None:
    conn = connect(readonly=True)
    try:
        row = conn.execute(
            "SELECT email, password_hash, salt, role FROM users WHERE email = ?", (email,)
        ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()
    return dict(row) if row else None


def authenticate(identifier: str, password: str) -> tuple[bool, str, str | None]:
    """返回 (ok, message, email)。email 为归一化后的账号标识。"""
    email = normalize_login_id(identifier)
    if not email:
        return False, "请输入账号", None
    if not password:
        return False, "请输入密码", None

    user = get_user(email)
    if not user:
        return False, "账号不存在，请检查邮箱是否正确", None
    if not verify_password(password, user["password_hash"], user["salt"]):
        return False, "密码错误", None
    return True, "登录成功", email
