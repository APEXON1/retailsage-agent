# ==================== 🎯 演示账号种子数据模块 ====================
"""为「一键体验」提供开箱即用的演示账号与合成简历。

设计目标：面试官 / HR 扫码进入后**零注册、零等待**，直接看到一份有内容的
人才库（上传 → 自动解析 → 标签检索 → RAG 问答 → JD 匹配全流程可玩）。

特性：
1. 幂等：每次启动调用都安全，不会重复建号、不会覆盖已上传的文件；
2. 自愈：Streamlit Cloud 重启后 SQLite 与 data/ 会重置，重新启动即自动重建；
3. 隔离：演示账号与真实账号（hr03 等）走同一套 owner 隔离逻辑，互不可见；
4. 合规：种子数据全部为虚构合成简历（见 data_demo/*.txt），不含真实个人信息。
"""

import logging
import os
import shutil

from config import DATA_DIR, DEMO_EMAIL, DEMO_PASSWORD, DEMO_SEED_DIR


def ensure_demo_account() -> dict:
    """确保演示账号存在且种子简历已就位。返回统计信息 dict。

    - 账号不存在：创建（密码 PBKDF2 哈希存储，与普通注册用户同一套逻辑）
    - 种子文件缺失：从 data_demo/ 复制到 data/<DEMO_EMAIL>/（已存在则跳过）
    - DB 记录缺失：补一条 pending 记录交后台队列解析（保证看板不为空）
    - 不会删除用户自行上传的文件
    """
    # 延迟导入：避免 seed 模块被脚本单独引用时拉起整个 auth 依赖树
    from database import get_user_by_email, create_user, get_all_resumes_from_db, insert_or_update_resume_db
    from auth import hash_password

    stats = {"account_created": False, "files_copied": 0, "files_present": 0, "records_queued": 0}

    if not get_user_by_email(DEMO_EMAIL):
        pwd_hash, salt = hash_password(DEMO_PASSWORD)
        ok, msg = create_user(DEMO_EMAIL, pwd_hash, salt)
        if ok:
            stats["account_created"] = True
            logging.info(f"[演示账号] 已创建 {DEMO_EMAIL}")
        else:
            logging.error(f"[演示账号] 创建失败: {msg}")
            return stats
    else:
        logging.info(f"[演示账号] {DEMO_EMAIL} 已存在，跳过创建")

    target_dir = os.path.join(DATA_DIR, DEMO_EMAIL)
    os.makedirs(target_dir, exist_ok=True)

    if not os.path.isdir(DEMO_SEED_DIR):
        logging.warning(f"[演示账号] 种子目录 {DEMO_SEED_DIR} 不存在，跳过复制")
        return stats

    for name in sorted(os.listdir(DEMO_SEED_DIR)):
        src = os.path.join(DEMO_SEED_DIR, name)
        dst = os.path.join(target_dir, name)
        if not os.path.isfile(src):
            continue
        if os.path.exists(dst):
            stats["files_present"] += 1
            continue
        shutil.copy2(src, dst)
        stats["files_copied"] += 1
        logging.info(f"[演示账号] 已复制种子简历: {name}")

    # 记录对齐：目录里有文件但 DB 没有记录时（云端重启 DB 被重置 / 手工删过记录），
    # 补一条 pending 记录交后台队列解析，避免 HR 进来看到空看板
    try:
        existing = {r.get("源文件") for r in get_all_resumes_from_db(DEMO_EMAIL)}
        for name in sorted(os.listdir(target_dir)):
            if name in existing:
                continue
            insert_or_update_resume_db({
                "owner_email": DEMO_EMAIL,
                "filename": name,
                "name": "排队解析中…", "age": "未知", "gender": "未知",
                "university": "排队解析中…", "major": "排队解析中…", "birth_year": "未知",
                "tags": "", "degree": "未知", "work_years": "未知",
                "parse_status": "pending",
            })
            stats["records_queued"] += 1
            logging.info(f"[演示账号] 已加入后台解析队列: {name}")
    except Exception as e:
        logging.warning(f"[演示账号] 记录对齐失败（不影响文件本身）: {e}")

    return stats


def reset_demo_account() -> dict:
    """清空演示账号已上传的文件并重新灌入种子数据（用于演示前恢复初始状态）。
    注意：只影响演示账号，不触碰其他账号。"""
    from database import get_user_by_email

    target_dir = os.path.join(DATA_DIR, DEMO_EMAIL)
    if os.path.isdir(target_dir):
        for name in os.listdir(target_dir):
            path = os.path.join(target_dir, name)
            if os.path.isfile(path):
                os.remove(path)
    if not get_user_by_email(DEMO_EMAIL):
        ensure_demo_account()
        return {"reset": True, "recreated": True}
    return ensure_demo_account()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(ensure_demo_account())
