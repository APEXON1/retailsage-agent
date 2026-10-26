# ==================== 🔐 用户认证模块（邮箱注册 / 登录 / 密码重置） ====================
"""本地演示版邮箱认证体系：
- 密码存储：PBKDF2-HMAC-SHA256 加盐哈希（标准库实现，不引入第三方依赖）；
- 验证码：6 位数字，有效期 5 分钟，数据库中仅存其 SHA-256 哈希；
- 发信双模式：配置 SMTP 环境变量时真实发送邮件；未配置 / 发送失败时自动降级为
  「演示模式」——验证码由调用方直接展示在页面上（适合无邮件服务的本地/演示环境）。

SMTP 环境变量（均可选，不配即演示模式）：
    SMTP_HOST   如 smtp.qq.com
    SMTP_PORT   465（SSL，默认）或 587（STARTTLS）
    SMTP_USER   发信账号
    SMTP_PASS   邮箱授权码（非登录密码，QQ/163 需在设置中开启 SMTP 并获取）
    SMTP_FROM   发件人地址，默认同 SMTP_USER
"""

import hashlib
import hmac
import logging
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

from config import (
    LEGACY_LOGIN_DOMAIN,
    PASSWORD_MIN_LENGTH,
    VERIFICATION_CODE_COOLDOWN_SECONDS,
    VERIFICATION_CODE_TTL_SECONDS,
)
from database import (
    create_user,
    delete_verification_codes,
    get_latest_verification_code,
    get_user_by_email,
    save_verification_code,
    update_user_password,
)

# 邮箱格式校验（注册用，要求合法公网域名）
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

# PBKDF2 迭代次数（OWASP 对 SHA-256 的最低建议量级）
_PBKDF2_ITERATIONS = 120_000


def _load_dotenv(path: str = ".env") -> None:
    """极简 .env 加载器（不引入 python-dotenv 依赖）：仅设置尚未存在的环境变量。"""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        logging.warning(f".env 加载失败（忽略，将使用系统环境变量）: {e}")


# .env 与本文件同目录：不随启动时的 CWD 漂移（从项目根目录拉起时也不会误读根 .env）
_load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


# ==================== 账号标识 ====================
def normalize_login_id(identifier: str) -> str:
    """登录标识归一化：含 @ 视为邮箱（转小写）；否则按旧账号自动补全域。"""
    identifier = (identifier or "").strip()
    if not identifier:
        return ""
    if "@" in identifier:
        return identifier.lower()
    return f"{identifier}@{LEGACY_LOGIN_DOMAIN}"


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match((email or "").strip()))


# ==================== 密码哈希 ====================
def hash_password(password: str, salt: bytes | None = None):
    """PBKDF2-HMAC-SHA256 加盐哈希。返回 (hash_hex, salt_hex)，salt 为空时随机生成。"""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return dk.hex(), salt.hex()


def verify_password(password: str, password_hash: str, salt_hex: str) -> bool:
    """恒定时间比较，防时序攻击；任何异常均视为校验失败。"""
    try:
        calc, _ = hash_password(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(calc, password_hash or "")
    except Exception:
        return False


def check_password_strength(password: str):
    """返回 (ok, message)。"""
    if not password:
        return False, "密码不能为空"
    if len(password) < PASSWORD_MIN_LENGTH:
        return False, f"密码长度至少 {PASSWORD_MIN_LENGTH} 位"
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "密码需同时包含字母和数字"
    return True, ""


# ==================== 验证码 ====================
def generate_verification_code() -> str:
    """生成 6 位数字验证码（secrets 防预测）。"""
    return f"{secrets.randbelow(1_000_000):06d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(str(code).encode("utf-8")).hexdigest()


def request_verification_code(email: str, purpose: str = "register"):
    """生成并存储验证码，尝试发送邮件。
    返回 (ok, message, demo_code)：
        ok=True 且 demo_code=None → 已真实发送到邮箱；
        ok=True 且 demo_code非空  → 演示模式，调用方需将 demo_code 展示给用户。
    """
    email = (email or "").strip().lower()
    if not is_valid_email(email):
        return False, "邮箱格式不正确", None

    # 冷却期检查：距上一条不足 60s 则拒绝，防止验证码轰炸
    latest = get_latest_verification_code(email, purpose)
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
    save_verification_code(email, _hash_code(code), purpose, expires_at)

    if _send_code_email(email, code, purpose):
        return True, f"验证码已发送至 {email}，请查收（含垃圾邮件箱）", None
    # 未配置 SMTP 或发送失败 → 自动降级演示模式
    logging.warning("SMTP 未配置或发送失败，降级为演示模式（验证码不真实发送）")
    return True, "演示模式：当前环境未配置 SMTP，验证码不会真实发送", code


def verify_verification_code(email: str, code: str, purpose: str = "register"):
    """校验并消费（删除）验证码。返回 (ok, message)。"""
    email = (email or "").strip().lower()
    latest = get_latest_verification_code(email, purpose)
    if not latest:
        return False, "请先获取验证码"
    try:
        if datetime.fromisoformat(latest["expires_at"]) < datetime.now():
            delete_verification_codes(email, purpose)
            return False, "验证码已过期，请重新获取"
    except (ValueError, TypeError):
        return False, "验证码数据异常，请重新获取"
    if not hmac.compare_digest(latest["code_hash"], _hash_code(code or "")):
        return False, "验证码错误"
    delete_verification_codes(email, purpose)
    return True, "验证码校验通过"


# ==================== 注册 / 登录 / 重置 ====================
def register_user(email: str, password: str, confirm_password: str, code: str):
    """邮箱 + 验证码注册。返回 (ok, message)。"""
    email = (email or "").strip().lower()
    if not is_valid_email(email):
        return False, "邮箱格式不正确"
    ok, msg = check_password_strength(password)
    if not ok:
        return False, msg
    if password != confirm_password:
        return False, "两次输入的密码不一致"
    ok, msg = verify_verification_code(email, code, "register")
    if not ok:
        return False, msg
    if get_user_by_email(email):
        return False, "该邮箱已被注册，请直接登录"
    pwd_hash, salt = hash_password(password)
    ok, msg = create_user(email, pwd_hash, salt)
    if not ok:
        return False, msg
    logging.info(f"新用户注册成功: {email}")
    return True, "注册成功，已自动登录"


def authenticate(identifier: str, password: str):
    """登录校验。返回 (ok, message, email)；email 为归一化后的账号标识。"""
    email = normalize_login_id(identifier)
    if not email:
        return False, "请输入账号", None
    user = get_user_by_email(email)
    if not user:
        return False, "账号不存在，请先注册", None
    if not verify_password(password, user["password_hash"], user["salt"]):
        return False, "密码错误", None
    return True, "登录成功", email


def reset_password(email: str, code: str, new_password: str, confirm_password: str):
    """验证码校验通过后重置密码。返回 (ok, message)。"""
    email = (email or "").strip().lower()
    if not get_user_by_email(email):
        return False, "该邮箱尚未注册"
    ok, msg = check_password_strength(new_password)
    if not ok:
        return False, msg
    if new_password != confirm_password:
        return False, "两次输入的新密码不一致"
    ok, msg = verify_verification_code(email, code, "reset")
    if not ok:
        return False, msg
    pwd_hash, salt = hash_password(new_password)
    ok, msg = update_user_password(email, pwd_hash, salt)
    if not ok:
        return False, msg
    return True, "密码重置成功，请返回登录"


# ==================== SMTP 发信 ====================
def _smtp_config() -> dict:
    user = os.environ.get("SMTP_USER", "").strip()
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "465") or 465),
        "user": user,
        "pass": os.environ.get("SMTP_PASS", "").strip(),
        "from": os.environ.get("SMTP_FROM", "").strip() or user,
    }


def smtp_configured() -> bool:
    """是否配置了可用的 SMTP（决定注册页是否提示真实发信）。"""
    cfg = _smtp_config()
    return bool(cfg["host"] and cfg["user"] and cfg["pass"])


def _send_code_email(to_email: str, code: str, purpose: str) -> bool:
    """通过 SMTP 发送验证码邮件；未配置或发送失败返回 False（调用方降级演示模式）。"""
    cfg = _smtp_config()
    if not (cfg["host"] and cfg["user"] and cfg["pass"]):
        return False

    action = "注册" if purpose == "register" else "重置密码"
    subject = f"【智能 HR 人才库】{action}验证码"
    body = (
        f"您好，\n\n您正在进行{action}操作，本次验证码为：\n\n"
        f"    {code}\n\n"
        f"验证码 {VERIFICATION_CODE_TTL_SECONDS // 60} 分钟内有效，请勿向他人泄露。\n"
        f"如非本人操作，请忽略本邮件。\n\n"
        f"—— 智能 HR 简历管理与问答系统（演示环境）"
    )
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = formataddr(("智能HR人才库", cfg["from"]))
        msg["To"] = to_email
        if cfg["port"] == 465:
            server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
        else:
            server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
            server.starttls()
        with server:
            server.login(cfg["user"], cfg["pass"])
            server.sendmail(cfg["from"], [to_email], msg.as_string())
        logging.info(f"验证码邮件已发送至 {to_email}（purpose={purpose}）")
        return True
    except Exception as e:
        logging.warning(f"验证码邮件发送失败（降级演示模式）: {e}")
        return False
