"""门户会话：签发 / 校验 / 撤销。

设计要点：
- Cookie 名固定 rs_session，作用域是 host（**不含端口**），所以
  127.0.0.1:8000 种下的会话会被浏览器自动带到 :7860 和 :8501 — 这就是单点登录的全部机制。
- 库里只存 sha256(token)，即使库泄漏也拿不到可用 token。
- 不设 Secure（本地是 http，设了浏览器会静默丢弃），HttpOnly + SameSite=Lax。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from .dbi import connect

COOKIE = "rs_session"
TTL_HOURS = 12
MAX_AGE = TTL_HOURS * 3600


def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(email: str) -> str:
    """签发新会话，顺便清掉已过期的行。"""
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_h(token), email, now.isoformat(), (now + timedelta(hours=TTL_HOURS)).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def user_of(token: str | None) -> str | None:
    """校验 token，返回邮箱；无效/过期/表不存在都返回 None。"""
    if not token:
        return None
    try:
        conn = connect(readonly=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT email, expires_at FROM sessions WHERE token_hash = ?", (_h(token),)
        ).fetchone()
    except sqlite3.Error:
        return None  # 独立运行时库里还没有 sessions 表
    finally:
        conn.close()

    if not row:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) < datetime.now():
            return None
    except (ValueError, TypeError):
        return None
    return row["email"]


def revoke(token: str | None) -> None:
    if not token:
        return
    try:
        conn = connect()
    except sqlite3.Error:
        return
    try:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_h(token),))
        conn.commit()
    finally:
        conn.close()
