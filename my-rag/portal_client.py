"""门户单点登录的客户端校验：只读共享账号库里的 sessions 表。

刻意不 import portal 包 —— 它属于另一个虚拟环境（依赖 fastapi/jinja2），
而且把项目根目录塞进 sys.path 会污染 my-rag 的裸模块导入。
这里只用标准库 + 本目录的 config。

独立运行（不起门户）时，库里没有 sessions 表，verify_session 静默返回 None，
应用自动回落到原有的登录 / 注册 / 找回密码页面。
"""

import hashlib
import os
import sqlite3
from datetime import datetime

from config import DB_PATH

# 与 portal/sessions.py 保持一致
COOKIE_NAME = "rs_session"
PORTAL_URL = os.environ.get("PORTAL_URL", "http://127.0.0.1:8000").rstrip("/")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_session(token: str):
    """校验门户会话 token，返回邮箱；无效/过期/无会话表都返回 None。"""
    if not token:
        return None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    except sqlite3.Error:
        return None

    try:
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            row = conn.execute(
                "SELECT email, expires_at FROM sessions WHERE token_hash = ?", (_hash(token),)
            ).fetchone()
        except sqlite3.OperationalError:
            return None  # 未启用门户 / 表尚未创建
    except sqlite3.Error:
        return None
    finally:
        conn.close()

    if not row:
        return None
    try:
        if datetime.fromisoformat(row[1]) < datetime.now():
            return None
    except (ValueError, TypeError):
        return None
    return row[0]
