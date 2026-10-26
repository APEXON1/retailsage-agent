# ==================== 🔧 一次性迁移脚本：单账号 → 多账号体系 ====================
"""将旧版「静态账号 + 共享数据」的项目迁移到「邮箱注册 + 按账号隔离」体系。

执行内容（幂等，可重复运行，不会重复搬迁或重复建号）：
1. 调用 init_db()：创建 users / verification_codes 表；旧版 resumes 表
   （filename 单列主键、无 owner_email）自动重建为 (owner_email, filename)
   复合主键，存量记录整体归档到 DEFAULT_OWNER（hr03@demo.local）；
2. 将旧账号 hr01 / hr02 / hr03 写入 users 表（密码与原静态配置一致，
   仅改为 PBKDF2 加盐哈希存储，不明文落库）；
3. 将 data/ 根目录下的存量简历文件搬迁到 data/hr03@demo.local/；
4. 备份 resumes.db 为 resumes.db.bak-migrate 后执行上述数据库变更；
5. 删除旧版无 owner 元数据的索引（faiss_index/ 与 bm25_index.pkl），
   下次启动 app.py 时按 owner 自动重建（需 Ollama 已启动且已拉取模型）。

用法：
    uv run python migrate_multiuser.py
"""

import os
import shutil
import sqlite3
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from config import BM25_PKL, DATA_DIR, DB_PATH, DEFAULT_OWNER, INDEX_DIR, LEGACY_LOGIN_DOMAIN
from database import init_db, create_user, get_user_by_email
from auth import hash_password

# 旧版 app.py 中的静态账号密码：**不写进仓库**（避免凭据泄漏）。
# 需要重跑迁移时用环境变量传入，格式 "账号:密码,账号:密码"：
#   set LEGACY_ACCOUNTS=hr01:xxxx,hr02:xxxx,hr03:xxxx
#   python migrate_multiuser.py
# 不传则不做建号动作，只执行建表 / 文件搬迁 / 清理旧索引。
def _load_legacy_accounts() -> dict:
    raw = os.getenv("LEGACY_ACCOUNTS", "").strip()
    accounts = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        username, sep, password = item.partition(":")
        if sep and username.strip() and password.strip():
            accounts[username.strip()] = password.strip()
    return accounts


LEGACY_ACCOUNTS = _load_legacy_accounts()


def backup_database():
    """迁移前备份 resumes.db，避免意外丢失。"""
    if not os.path.exists(DB_PATH):
        logging.info("未发现 resumes.db，跳过备份（视为全新环境）")
        return
    backup = DB_PATH + ".bak-migrate"
    shutil.copy2(DB_PATH, backup)
    logging.info(f"已备份数据库 → {backup}")


def seed_legacy_users():
    """将旧账号写入 users 表（占位邮箱 hrXX@demo.local），已存在则跳过。"""
    if not LEGACY_ACCOUNTS:
        logging.info("未提供 LEGACY_ACCOUNTS 环境变量，跳过旧账号建号")
        return
    for username, password in LEGACY_ACCOUNTS.items():
        email = f"{username}@{LEGACY_LOGIN_DOMAIN}"
        if get_user_by_email(email):
            logging.info(f"账号已存在，跳过：{email}")
            continue
        pwd_hash, salt = hash_password(password)
        ok, msg = create_user(email, pwd_hash, salt)
        logging.info(f"迁移账号 {email}：{'成功' if ok else msg}")


def move_legacy_files():
    """将 data/ 根目录下的存量文件搬迁到默认账号目录（已存在则跳过）。"""
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR, exist_ok=True)
        logging.info("data/ 目录不存在，已创建")
        return
    target_dir = os.path.join(DATA_DIR, DEFAULT_OWNER)
    os.makedirs(target_dir, exist_ok=True)
    moved, skipped = 0, 0
    for name in os.listdir(DATA_DIR):
        src = os.path.join(DATA_DIR, name)
        if not os.path.isfile(src):
            continue  # 跳过子目录（如各账号目录）
        dst = os.path.join(target_dir, name)
        if os.path.exists(dst):
            skipped += 1
            continue
        shutil.move(src, dst)
        moved += 1
    logging.info(f"文件搬迁完成：移动 {moved} 个，跳过 {skipped} 个（目标已存在）→ {target_dir}")


def clear_legacy_indexes():
    """删除旧版无 owner 元数据的索引，等待 app 启动时按 owner 重建。"""
    removed = []
    for path in (INDEX_DIR, BM25_PKL):
        if os.path.isdir(path):
            shutil.rmtree(path)
            removed.append(path)
        elif os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    if removed:
        logging.info(f"已删除旧索引（无 owner 元数据，将自动重建）：{', '.join(removed)}")
    else:
        logging.info("未发现旧索引文件，无需删除")


def main():
    print("=" * 60)
    print(" 多账号体系迁移脚本")
    print("=" * 60)
    backup_database()
    init_db()  # 建新表 + 旧 resumes 表自动归档到 DEFAULT_OWNER
    seed_legacy_users()
    move_legacy_files()
    clear_legacy_indexes()
    print("-" * 60)
    print("迁移完成！后续步骤：")
    print("  1. 确认 Ollama 已启动（ollama serve）且已拉取模型：")
    print("     ollama pull deepseek-r1:8b / qwen2.5:7b / nomic-embed-text")
    print("  2. 启动应用：uv run streamlit run app.py")
    print("  3. 用 hr01 / hr02 / hr03（原密码）登录，或在注册页用邮箱注册新账号")
    print("  4. 存量简历已归属 hr03@demo.local，即输入 hr03 登录即可看到")


if __name__ == "__main__":
    main()
