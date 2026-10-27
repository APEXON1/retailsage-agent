"""账号体系：登录校验 + 邮箱注册 / 找回密码。

与 my-rag 完全同一套算法、同一张库（accounts/accounts.db）：
- PBKDF2 参数必须与 my-rag/auth.py 保持一致，否则存量账号密码会校验不过；
- 验证码表 verification_codes 与 my-rag/database.py 结构一致，两个入口互通；
- 发信复用 my-rag/.env 的 SMTP 配置，未配置或发送失败自动降级演示模式。

刻意不 import my-rag 的 auth.py —— 它会连带拉起 database.py / config.py，
并把 my-rag 目录塞进 sys.path，对门户进程是纯负担。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path

from .dbi import ROOT, connect

# 与 my-rag/config.py:17 一致：旧账号输入 hr03 自动补全为 hr03@demo.local
LEGACY_LOGIN_DOMAIN = "demo.local"

# 与 my-rag/auth.py:47 一致
PBKDF2_ITERATIONS = 120_000

# 与 my-rag/config.py 一致
PASSWORD_MIN_LENGTH = 6
VERIFICATION_CODE_TTL_SECONDS = 300
VERIFICATION_CODE_COOLDOWN_SECONDS = 60

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _load_env_file(path: Path) -> None:
    """极简 .env 加载器：仅补全尚未存在的环境变量（SMTP 与 HR 知识库共用一份）。"""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_load_env_file(ROOT / "my-rag" / ".env")


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


# ==================== 注册 / 找回密码 ====================
def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


def check_password_strength(password: str) -> tuple[bool, str]:
    if not password:
        return False, "密码不能为空"
    if len(password) < PASSWORD_MIN_LENGTH:
        return False, f"密码长度至少 {PASSWORD_MIN_LENGTH} 位"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "密码需同时包含字母和数字"
    return True, ""


def generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(str(code).encode("utf-8")).hexdigest()


def _save_verification_code(email: str, code_hash: str, purpose: str, expires_at: str) -> None:
    """同一邮箱同一用途只保留最新一条验证码。"""
    conn = connect()
    try:
        conn.execute("DELETE FROM verification_codes WHERE email=? AND purpose=?", (email, purpose))
        conn.execute(
            "INSERT INTO verification_codes (email, code_hash, purpose, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, code_hash, purpose, expires_at, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _latest_verification_code(email: str, purpose: str) -> dict | None:
    conn = connect(readonly=True)
    try:
        row = conn.execute(
            "SELECT email, code_hash, purpose, expires_at, created_at FROM verification_codes "
            "WHERE email=? AND purpose=? ORDER BY created_at DESC LIMIT 1",
            (email, purpose),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _delete_verification_codes(email: str, purpose: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM verification_codes WHERE email=? AND purpose=?", (email, purpose))
        conn.commit()
    finally:
        conn.close()


def verify_verification_code(email: str, code: str, purpose: str) -> tuple[bool, str]:
    latest = _latest_verification_code(email, purpose)
    if not latest:
        return False, "请先获取验证码"
    try:
        if datetime.fromisoformat(latest["expires_at"]) < datetime.now():
            _delete_verification_codes(email, purpose)
            return False, "验证码已过期，请重新获取"
    except (ValueError, TypeError):
        return False, "验证码数据异常，请重新获取"
    if not hmac.compare_digest(latest["code_hash"], _hash_code(code or "")):
        return False, "验证码错误"
    _delete_verification_codes(email, purpose)
    return True, "验证码校验通过"


def create_user(email: str, password_hash: str, salt: str) -> tuple[bool, str]:
    try:
        conn = connect()
        try:
            conn.execute(
                "INSERT INTO users (email, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
                (email, password_hash, salt, datetime.now().isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        return True, "账号创建成功"
    except Exception:
        return False, "该邮箱已被注册，请直接登录"


def update_user_password(email: str, password_hash: str, salt: str) -> tuple[bool, str]:
    try:
        conn = connect()
        try:
            conn.execute(
                "UPDATE users SET password_hash=?, salt=? WHERE email=?",
                (password_hash, salt, email),
            )
            conn.commit()
        finally:
            conn.close()
        return True, "密码已更新"
    except Exception:
        return False, "密码更新失败，请稍后再试"


def register_user(email: str, password: str, confirm: str, code: str) -> tuple[bool, str]:
    email = (email or "").strip().lower()
    if not is_valid_email(email):
        return False, "邮箱格式不正确"
    ok, msg = check_password_strength(password)
    if not ok:
        return False, msg
    if password != confirm:
        return False, "两次输入的密码不一致"
    ok, msg = verify_verification_code(email, code, "register")
    if not ok:
        return False, msg
    if get_user(email):
        return False, "该邮箱已被注册，请直接登录"
    pwd_hash, salt = hash_password(password)
    return create_user(email, pwd_hash, salt)


def reset_password(email: str, code: str, new_password: str, confirm: str) -> tuple[bool, str]:
    email = (email or "").strip().lower()
    if not get_user(email):
        return False, "该邮箱尚未注册"
    ok, msg = check_password_strength(new_password)
    if not ok:
        return False, msg
    if new_password != confirm:
        return False, "两次输入的新密码不一致"
    ok, msg = verify_verification_code(email, code, "reset")
    if not ok:
        return False, msg
    pwd_hash, salt = hash_password(new_password)
    return update_user_password(email, pwd_hash, salt)


# ==================== 验证码邮件（SMTP，未配置则降级演示模式） ====================
def _smtp_config() -> dict:
    user = os.environ.get("SMTP_USER", "").strip()
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "465") or 465),
        "user": user,
        "password": os.environ.get("SMTP_PASS", "").strip(),
        "from": os.environ.get("SMTP_FROM", "").strip() or user,
    }


def smtp_configured() -> bool:
    cfg = _smtp_config()
    return bool(cfg["host"] and cfg["user"] and cfg["password"])


def _send_code_email(to_email: str, code: str, purpose: str) -> bool:
    """未配置 SMTP 或发送失败返回 False（调用方降级演示模式）。"""
    cfg = _smtp_config()
    if not (cfg["host"] and cfg["user"] and cfg["password"]):
        return False

    action = "注册" if purpose == "register" else "重置密码"
    subject = f"【智能问答门户】{action}验证码"
    body = (
        f"您好，\n\n您正在进行{action}操作，本次验证码为：\n\n"
        f"    {code}\n\n"
        f"验证码 {VERIFICATION_CODE_TTL_SECONDS // 60} 分钟内有效，请勿向他人泄露。\n"
        f"如非本人操作，请忽略本邮件。\n\n"
        f"—— 智能问答门户（演示环境）"
    )
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("智能问答门户", cfg["from"]))
        msg["To"] = to_email
        if cfg["port"] == 465:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
            server.starttls()
        with server:
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from"], [to_email], msg.as_string())
        return True
    except Exception:
        return False


def request_verification_code(email: str, purpose: str) -> tuple[bool, str, str | None]:
    """生成并发送验证码，返回 (ok, message, demo_code)。"""
    email = (email or "").strip().lower()
    if not is_valid_email(email):
        return False, "邮箱格式不正确", None

    latest = _latest_verification_code(email, purpose)
    if latest:
        try:
            last_at = datetime.fromisoformat(latest["created_at"])
            remain = VERIFICATION_CODE_COOLDOWN_SECONDS - (datetime.now() - last_at).total_seconds()
            if remain > 0:
                return False, f"发送太频繁，请 {int(remain)} 秒后再试", None
        except (ValueError, TypeError):
            pass

    code = generate_verification_code()
    expires_at = (datetime.now() + timedelta(seconds=VERIFICATION_CODE_TTL_SECONDS)).isoformat()
    _save_verification_code(email, _hash_code(code), purpose, expires_at)

    if _send_code_email(email, code, purpose):
        return True, f"验证码已发送至 {email}，请查收（含垃圾邮件箱）", None
    return True, "当前环境未配置 SMTP，验证码为演示模式", code
