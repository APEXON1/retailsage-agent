# ==================== 🗄️ SQLite 数据库管理模块（从 main.py 解耦拆分） ====================
"""简历元数据的持久化与基于标签的结构化检索，完全不依赖大模型 / RAG。

多账号体系（owner 隔离）：
- resumes 表主键为 (owner_email, filename)，各账号简历互不可见；
- 旧版单账号表（filename 单列主键、无 owner_email）在 init_db 时自动重建，
  存量数据整体归档到 DEFAULT_OWNER（与 migrate_multiuser.py 的文件搬迁对应）。
"""

import os
import sqlite3
import time
import logging
import hashlib
from typing import List

from config import DB_PATH, DEFAULT_OWNER


def init_db():
    """初始化 SQLite 数据库与全部表（上下文管理器自动提交/关闭，防锁库）"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        # —— 用户表：邮箱即账号，密码仅存 PBKDF2 哈希 + 盐 ——
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT
            )
        ''')
        # —— 验证码表：仅存 SHA-256 哈希，带用途（register/reset）与过期时间 ——
        c.execute('''
            CREATE TABLE IF NOT EXISTS verification_codes (
                email TEXT,
                code_hash TEXT,
                purpose TEXT,
                expires_at TEXT,
                created_at TEXT
            )
        ''')
        c.execute(_RESUMES_DDL)
        _migrate_resumes_add_owner(c)
        # 退出 with 块时自动 commit + close


_RESUMES_DDL = '''
    CREATE TABLE IF NOT EXISTS resumes (
        owner_email TEXT NOT NULL,
        filename TEXT NOT NULL,
        name TEXT,
        age TEXT,
        gender TEXT,
        university TEXT,
        major TEXT,
        birth_year TEXT,
        file_hash TEXT,
        tags TEXT,
        degree TEXT,
        work_years TEXT,
        is_problematic INTEGER DEFAULT 0,
        parse_status TEXT DEFAULT 'completed',
        PRIMARY KEY (owner_email, filename)
    )
'''


def _migrate_resumes_add_owner(conn):
    """旧版 resumes 表（filename 单列主键、无 owner_email 列）→ 重建为复合主键表。
    存量数据整体归档到 DEFAULT_OWNER，保证历史简历归属明确。幂等：已是新结构则跳过。"""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(resumes)")]
    if not cols or "owner_email" in cols:
        return  # 表不存在（刚由 DDL 新建）或已是新结构
    logging.info(f"检测到旧版 resumes 表结构，开始迁移：存量数据归档到 {DEFAULT_OWNER}")
    conn.execute("ALTER TABLE resumes RENAME TO resumes_old_backup")
    conn.execute(_RESUMES_DDL)
    old_cols = [c for c in cols if c != "owner_email"]
    col_list = ", ".join(old_cols)
    conn.execute(
        f"INSERT INTO resumes (owner_email, {col_list}) "
        f"SELECT ?, {col_list} FROM resumes_old_backup",
        (DEFAULT_OWNER,),
    )
    conn.execute("DROP TABLE resumes_old_backup")
    logging.info("resumes 表结构迁移完成")


# ==================== 👤 用户表 CRUD ====================
def create_user(email: str, password_hash: str, salt: str):
    """创建用户。返回 (ok, message)。"""
    email = (email or "").strip().lower()
    if not email:
        return False, "邮箱不能为空"
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            conn.execute(
                "INSERT INTO users (email, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (email, password_hash, salt, datetime_now_iso()),
            )
        return True, "账号创建成功"
    except sqlite3.IntegrityError:
        return False, "该邮箱已被注册"
    except Exception as e:
        logging.error(f"创建用户失败: {e}")
        return False, f"账号创建失败: {e}"


def get_user_by_email(email: str):
    """按邮箱查询用户，返回 dict（含 password_hash / salt）或 None。"""
    email = (email or "").strip().lower()
    if not email:
        return None
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT email, password_hash, salt, created_at FROM users WHERE email=?",
                (email,),
            )
            row = c.fetchone()
        if not row:
            return None
        return {"email": row[0], "password_hash": row[1], "salt": row[2], "created_at": row[3]}
    except Exception as e:
        logging.error(f"查询用户失败: {e}")
        return None


def update_user_password(email: str, password_hash: str, salt: str):
    """更新用户密码哈希。返回 (ok, message)。"""
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            conn.execute(
                "UPDATE users SET password_hash=?, salt=? WHERE email=?",
                (password_hash, salt, email),
            )
        return True, "密码已更新"
    except Exception as e:
        logging.error(f"更新密码失败: {e}")
        return False, f"密码更新失败: {e}"


def datetime_now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


# ==================== ✉️ 验证码表 CRUD ====================
def save_verification_code(email: str, code_hash: str, purpose: str, expires_at: str):
    """存储验证码（哈希）。同一 email+purpose 的旧码先清除，保证只有最新码有效。"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.execute("DELETE FROM verification_codes WHERE email=? AND purpose=?", (email, purpose))
        conn.execute(
            "INSERT INTO verification_codes (email, code_hash, purpose, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, code_hash, purpose, expires_at, datetime_now_iso()),
        )


def get_latest_verification_code(email: str, purpose: str):
    """查询某邮箱某用途的最新验证码记录，返回 dict 或 None。"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT email, code_hash, purpose, expires_at, created_at "
            "FROM verification_codes WHERE email=? AND purpose=? ORDER BY created_at DESC LIMIT 1",
            (email, purpose),
        )
        row = c.fetchone()
    if not row:
        return None
    return {"email": row[0], "code_hash": row[1], "purpose": row[2],
            "expires_at": row[3], "created_at": row[4]}


def delete_verification_codes(email: str, purpose: str):
    """消费（删除）某邮箱某用途的全部验证码。"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        conn.execute("DELETE FROM verification_codes WHERE email=? AND purpose=?", (email, purpose))


# ==================== 📄 简历表 CRUD（按 owner 隔离） ====================
def insert_or_update_resume_db(data: dict):
    """插入或更新简历解析数据（上下文管理器自动提交/关闭，防锁库）。
    data 必须包含 owner_email 与 filename（复合主键）。"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        # tags 支持 list / tuple / str，统一转为逗号分隔字符串存储，便于后续 LIKE 结构化查询
        tags = data.get("tags", "")
        if isinstance(tags, (list, tuple)):
            tags = ",".join(str(t).strip() for t in tags if str(t).strip())
        else:
            tags = str(tags) if tags else ""
        c.execute('''
            INSERT OR REPLACE INTO resumes
            (owner_email, filename, name, age, gender, university, major, birth_year, file_hash, tags, degree, work_years, parse_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data.get("owner_email"), data.get("filename"), data.get("name"), data.get("age"),
              data.get("gender"), data.get("university"), data.get("major"),
              data.get("birth_year", "未知"), data.get("file_hash"), tags,
              data.get("degree", "未知"), data.get("work_years", "未知"),
              data.get("parse_status", "completed")))
        # 退出 with 块时自动 commit + close


def delete_resume_from_db(filename: str, owner_email: str):
    """从数据库中删除指定用户的指定简历（上下文管理器自动提交/关闭，防锁库）"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM resumes WHERE filename=? AND owner_email=?", (filename, owner_email))


def update_resume_problem_status(filename: str, status: bool, owner_email: str):
    """更新指定简历的「解析异常标记」状态（上下文管理器自动提交/关闭，防锁库）"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute("UPDATE resumes SET is_problematic=? WHERE filename=? AND owner_email=?",
                  (1 if status else 0, filename, owner_email))


def update_resume_status(filename: str, status: str, owner_email: str):
    """单独更新某份简历的解析状态（pending / completed / failed）。"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute("UPDATE resumes SET parse_status=? WHERE filename=? AND owner_email=?",
                  (status, filename, owner_email))


def get_pending_resumes():
    """查询所有处于 'pending' 状态的简历（含归属人），供后台守护线程跨账号消费。
    返回 [{'filename': ..., 'owner_email': ...}, ...]。"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute("SELECT filename, owner_email FROM resumes WHERE parse_status='pending'")
        return [{"filename": r[0], "owner_email": r[1]} for r in c.fetchall()]


def clear_user_resumes_db(owner_email: str):
    """清空指定用户的全部简历记录（就地 DELETE + 可选 VACUUM），不影响其他账号数据。"""
    # isolation_level=None → 自动提交模式，DELETE 立即生效，不会出现"事务中执行 VACUUM"的冲突
    with sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None) as conn:
        conn.execute("DELETE FROM resumes WHERE owner_email=?", (owner_email,))
        # VACUUM 仅用于回收空间；若此刻被其他连接锁定则跳过，不影响数据已清空的结果
        try:
            conn.execute("VACUUM")
        except sqlite3.OperationalError as e:
            logging.warning(f"VACUUM 空间回收失败（不影响数据已清空）: {e}")


def get_all_resumes_from_db(owner_email: str):
    """获取指定用户的简历看板数据，供前端展示（上下文管理器自动关闭）"""
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute(
            "SELECT filename, name, age, gender, university, major, birth_year, tags, degree, work_years, is_problematic, parse_status "
            "FROM resumes WHERE owner_email=?",
            (owner_email,),
        )
        rows = c.fetchall()
    return [{"源文件": r[0], "姓名": r[1], "年龄": r[2] or "未知", "出生年月": r[6] or "未知",
             "性别": r[3] or "未知", "毕业院校": r[4] or "未知", "专业": r[5] or "未知",
             "学历层次": r[8] or "未知", "工作年限": r[9] or "未知",
             "标签": r[7] or "", "标记问题": bool(r[10] or 0),
             "解析状态": r[11] or "completed"} for r in rows]


# ==================== 🏷️ 基于标签的结构化检索（不依赖大模型 / RAG） ====================
def _escape_like(s: str) -> str:
    """转义 LIKE 通配符（\\、%、_），避免标签内容干扰匹配。"""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_resumes_by_tags(required_tags: List[str], fuzzy: bool = True, owner_email: str = None):
    # 1. 规整输入
    required = []
    seen = set()
    for t in required_tags or []:
        s = str(t).strip()
        if s and s not in seen:
            seen.add(s)
            required.append(s)
    if not required:
        logging.info("标签检索：未提供任何标签，返回空结果。")
        return []
    if not owner_email:
        logging.warning("标签检索：未提供 owner_email，返回空结果（数据隔离约束）。")
        return []

    # 2. 构造参数化 SQL（始终带 owner_email 条件，保证账号间数据隔离）
    if fuzzy:
        # 模糊匹配：允许子串包含
        placeholders = " AND ".join(["tags LIKE ? ESCAPE '\\'"] * len(required))
        params = [f"%{_escape_like(t)}%" for t in required]
    else:
        # 精确匹配：用逗号锚定精确 token 匹配，避免 'Java' 误中 'JavaScript'
        placeholders = " AND ".join(["',' || tags || ',' LIKE ? ESCAPE '\\'"] * len(required))
        params = [f"%,{_escape_like(t)},%" for t in required]

    sql = f"SELECT filename, name, tags FROM resumes WHERE owner_email=? AND {placeholders}"
    params = [owner_email] + params

    start = time.perf_counter()
    with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
        c = conn.cursor()
        c.execute(sql, params)
        rows = c.fetchall()
    elapsed_ms = (time.perf_counter() - start) * 1000

    # 3. 组装返回结果，并计算每条命中的标签
    results = []
    for fname, name, tags_str in rows:
        resume_tags = [t.strip() for t in (tags_str or "").split(",") if t.strip()]
        matched = []
        if fuzzy:
            for rt in resume_tags:
                for req in required:
                    if req.lower() in rt.lower() and rt not in matched:
                        matched.append(rt)
        else:
            matched = [t for t in required if t in resume_tags]

        results.append({
            "filename": fname,
            "name": name or "未知",
            "matched_tags": matched,
            "tags": resume_tags,
        })

    count = len(results)
    logging.info(f"SQL 标签过滤命中 {count} 条（owner={owner_email}, 查询标签={required}, fuzzy={fuzzy}）")
    return results


# ==================== 🗄️ 文件 IO 辅助（从 main.py 迁入，供索引/去重复用） ====================
def safe_remove(path: str):
    """安全删除文件：文件不存在时静默忽略，避免 FileNotFoundError 导致程序崩溃。"""
    try:
        if os.path.exists(path):
            os.remove(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning(f"删除文件失败（已忽略）: {path}；异常: {e}")


def calculate_file_md5(filepath: str) -> str | None:
    """计算文件 MD5 哈希值（分块读取，适合大文件）；读取失败返回 None。"""
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logging.warning(f"计算文件 MD5 失败（视为无 hash）: {filepath}；异常: {e}")
        return None


def is_file_hash_exists(file_hash: str, owner_email: str = None) -> bool:
    """查询指定用户是否已存在该 file_hash（用于去重，避免重复解析相同内容）。
    去重范围限定在同一账号内，不同账号上传相同内容互不影响。"""
    if not file_hash or not owner_email:
        return False
    try:
        with sqlite3.connect(DB_PATH, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM resumes WHERE file_hash=? AND owner_email=?",
                      (file_hash, owner_email))
            return c.fetchone() is not None
    except Exception as e:
        logging.warning(f"查询 file_hash 失败（视为不存在）: {file_hash}；异常: {e}")
        return False
