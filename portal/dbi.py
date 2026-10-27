"""共享账号库（accounts/accounts.db）的连接封装与幂等迁移。

背景：门户（8000）、RetailSage 洞察（7860）、知识库问答（8501）三个进程
都要读这套账号与会话，因此库放在项目根目录，且必须开 WAL 才能并发读写。

只依赖标准库，任何一方的虚拟环境都能引用。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "accounts" / "accounts.db"

# 与 my-rag/database.py::init_db 保持完全一致的表结构，
# 这样全新目录（没有存量 resumes.db）也能直接跑起来。
_USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT
)
"""

_SESSIONS_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
)
"""

# 与 my-rag/database.py 同一张表：验证码只存 SHA-256，带用途（register/reset）与过期时间
_VERIFICATION_DDL = """
CREATE TABLE IF NOT EXISTS verification_codes (
    email TEXT,
    code_hash TEXT,
    purpose TEXT,
    expires_at TEXT,
    created_at TEXT
)
"""


def connect(readonly: bool = False, timeout: float = 5.0) -> sqlite3.Connection:
    """短连接：用完即 close，绝不跨请求持有。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if readonly:
        # 注意：不要用 mode=ro 的 URI 连接 —— WAL 下只读连接需要访问 -shm/-wal，
        # mode=ro 会报 "unable to open database file"。用 query_only 表达只读语义。
        conn.execute("PRAGMA query_only=ON")
    return conn


def migrate() -> list[str]:
    """幂等迁移：可重复执行。返回本次实际做了什么的说明列表。"""
    notes: list[str] = []
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # isolation_level=None（自动提交）才能安全执行 PRAGMA journal_mode
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=10000")

        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            # WAL 持久化在库文件里，设一次后续所有进程都受益
            got = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            notes.append(f"journal_mode: {mode} -> {got}")

        conn.execute(_USERS_DDL)
        conn.execute(_SESSIONS_DDL)
        conn.execute(_VERIFICATION_DDL)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_exp ON sessions(expires_at)")

        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'user'")
            notes.append("users.role 列已添加")
        conn.execute("UPDATE users SET role='user' WHERE role IS NULL OR role=''")
    finally:
        conn.close()
    return notes


if __name__ == "__main__":
    for n in migrate() or ["无变更（已是最新结构）"]:
        print(n)
    conn = connect(readonly=True)
    try:
        print("数据库:", DB_PATH)
        print("表:", [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")])
        for row in conn.execute("SELECT email, role FROM users ORDER BY email"):
            print(f"  {row['email']:<20} role={row['role']}")
    finally:
        conn.close()
