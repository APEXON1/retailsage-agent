"""总界面（Primary Interface）：统一登录页 + 知识库入口页。

这是截图里「智能问答门户」两个页面的单文件独立版本：
- 不 import `portal` 包，也不依赖 templates/ 与 static/，页面与样式全部内置；
- 仍然复用项目共享的账号库 `accounts/accounts.db`（可用 PRIMARY_DB 覆盖），
  所以登录态（rs_session Cookie）与 7860 / 8501 子应用保持互通；
- 应用卡片优先读 `portal/apps.json`，文件不存在时使用内置默认值。

运行：
    .venv\\Scripts\\python.exe "Primary Interface.py"
    .venv\\Scripts\\python.exe "Primary Interface.py" --port 8001
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import smtplib
import socket
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("PRIMARY_DB", str(ROOT / "accounts" / "accounts.db")))
APPS_FILE = ROOT / "portal" / "apps.json"

COOKIE = "rs_session"
TTL_HOURS = 12
MAX_AGE = TTL_HOURS * 3600

# 与 my-rag/config.py、my-rag/auth.py 保持一致，否则存量账号密码校验不过
LEGACY_LOGIN_DOMAIN = "demo.local"
PBKDF2_ITERATIONS = 120_000
PASSWORD_MIN_LENGTH = 6
VERIFICATION_CODE_TTL_SECONDS = 300
VERIFICATION_CODE_COOLDOWN_SECONDS = 60

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _load_env_file(path: Path) -> None:
    """极简 .env 加载器：仅补全尚未存在的环境变量（SMTP 配置与 HR 知识库共用一份）。"""
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

DEFAULT_APPS: list[dict] = [
    {
        "id": "insight",
        "name": "RetailSage 数据洞察",
        "icon": "📊",
        "url": "http://127.0.0.1:7860/",
        "desc": "用自然语言查询销售数据，自动完成归因分析、趋势分析与图表报告",
        "tags": ["订单 / 客户 / 门店", "NL2SQL", "图表与报告"],
    },
    {
        "id": "kb",
        "name": "智能 HR 知识库",
        "icon": "👔",
        "url": "http://127.0.0.1:8501/",
        "desc": "简历上传解析、标签检索、RAG 问答与 JD 匹配，人才库按账号隔离",
        "tags": ["简历解析", "RAG 问答", "JD 匹配"],
    },
]

CSS = """
:root {
  --bg: #f6f7f9;
  --bg-elev: #ffffff;
  --border: #e4e6eb;
  --border-strong: #d3d6dd;
  --text: #1a1d21;
  --text-dim: #6b7280;
  --text-faint: #9aa1ac;
  --accent: #4f46e5;
  --accent-hover: #4338ca;
  --accent-soft: #eef2ff;
  --ok: #0f9d58;
  --ok-soft: #e6f4ea;
  --warn: #b45309;
  --warn-soft: #fef3c7;
  --danger: #d93025;
  --danger-soft: #fce8e6;
  --shadow: 0 1px 2px rgba(16, 24, 40, .04), 0 8px 24px -12px rgba(16, 24, 40, .12);
  --shadow-lift: 0 2px 4px rgba(16, 24, 40, .05), 0 16px 32px -16px rgba(16, 24, 40, .22);
}

:root[data-theme="dark"] {
  --bg: #0f1115;
  --bg-elev: #171a20;
  --border: #262b33;
  --border-strong: #333a45;
  --text: #e8eaed;
  --text-dim: #9aa1ac;
  --text-faint: #6b7280;
  --accent: #818cf8;
  --accent-hover: #a5b4fc;
  --accent-soft: #1e2235;
  --ok: #4ade80;
  --ok-soft: #14261c;
  --warn: #fbbf24;
  --warn-soft: #2a2110;
  --danger: #f87171;
  --danger-soft: #2a1618;
  --shadow: 0 1px 2px rgba(0, 0, 0, .4), 0 8px 24px -12px rgba(0, 0, 0, .6);
  --shadow-lift: 0 2px 4px rgba(0, 0, 0, .5), 0 16px 32px -16px rgba(0, 0, 0, .8);
}

* { box-sizing: border-box; }

html, body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
               "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  font-size: 15px;
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

a { color: inherit; text-decoration: none; }

/* ==================== 登录页 ==================== */
.auth-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}

.auth-card {
  position: relative;
  width: 100%;
  max-width: 400px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 36px 32px 28px;
  box-shadow: var(--shadow);
}

/* 日间 / 夜间外观切换 */
.theme-toggle {
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 1px solid var(--border-strong);
  border-radius: 50%;
  background: var(--bg-elev);
  color: var(--text-dim);
  cursor: pointer;
  transition: color .15s, border-color .15s, background .15s;
}

.theme-toggle:hover {
  color: var(--accent);
  border-color: var(--accent);
  background: var(--accent-soft);
}

.theme-toggle svg { width: 17px; height: 17px; }
.theme-toggle .icon-moon { display: none; }
:root[data-theme="dark"] .theme-toggle .icon-sun { display: none; }
:root[data-theme="dark"] .theme-toggle .icon-moon { display: block; }

.auth-theme {
  position: absolute;
  top: 18px;
  right: 18px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 26px;
}

.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 17px;
  flex: 0 0 auto;
}

.brand-text { font-weight: 600; font-size: 15px; letter-spacing: -.01em; }
.brand-sub { font-size: 12px; color: var(--text-faint); font-weight: 400; }

.auth-title {
  font-size: 22px;
  font-weight: 650;
  letter-spacing: -.02em;
  margin: 0 0 6px;
}

.auth-sub {
  font-size: 13px;
  color: var(--text-dim);
  margin: 0 0 24px;
}

.field { margin-bottom: 16px; }

.field label {
  display: block;
  font-size: 12.5px;
  font-weight: 550;
  color: var(--text-dim);
  margin-bottom: 7px;
}

.field input {
  width: 100%;
  height: 42px;
  padding: 0 13px;
  font-size: 14.5px;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border-strong);
  border-radius: 9px;
  outline: none;
  transition: border-color .15s, box-shadow .15s;
  font-family: inherit;
}

.field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.btn-primary {
  width: 100%;
  height: 44px;
  margin-top: 8px;
  border: none;
  border-radius: 10px;
  background: var(--accent);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: background .15s, transform .06s;
}

.btn-primary:hover { background: var(--accent-hover); }
.btn-primary:active { transform: translateY(1px); }

.alert {
  display: flex;
  gap: 8px;
  padding: 10px 13px;
  margin-bottom: 18px;
  font-size: 13.5px;
  border-radius: 9px;
  background: var(--danger-soft);
  color: var(--danger);
  border: 1px solid color-mix(in srgb, var(--danger) 25%, transparent);
}

.auth-foot {
  margin-top: 22px;
  padding-top: 18px;
  border-top: 1px solid var(--border);
  font-size: 12.5px;
  color: var(--text-faint);
  line-height: 1.7;
}

.auth-foot strong { color: var(--text-dim); font-weight: 550; }

/* ==================== 入口页 ==================== */
.shell {
  max-width: 980px;
  margin: 0 auto;
  padding: 0 24px 56px;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  height: 68px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 40px;
}

.userbox {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 13px;
  color: var(--text-dim);
}

.logout-link {
  font-size: 13px;
  color: var(--text-dim);
  padding: 5px 11px;
  border: 1px solid var(--border-strong);
  border-radius: 7px;
  transition: color .15s, border-color .15s, background .15s;
}

.logout-link:hover {
  color: var(--danger);
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
  background: var(--danger-soft);
}

.page-title {
  font-size: 27px;
  font-weight: 650;
  letter-spacing: -.025em;
  margin: 0 0 8px;
}

.page-sub {
  font-size: 14px;
  color: var(--text-dim);
  margin: 0 0 32px;
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(330px, 1fr));
  gap: 18px;
}

.app-card {
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 24px 24px 22px;
  box-shadow: var(--shadow);
  transition: border-color .18s, box-shadow .18s, transform .18s;
  overflow: hidden;
}

.app-card::after {
  content: "";
  position: absolute;
  inset: 0 0 auto 0;
  height: 2px;
  background: var(--accent);
  opacity: 0;
  transition: opacity .18s;
}

.app-card:hover {
  border-color: var(--border-strong);
  box-shadow: var(--shadow-lift);
  transform: translateY(-2px);
}

.app-card:hover::after { opacity: 1; }

.app-head {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 14px;
}

.app-icon {
  width: 44px;
  height: 44px;
  flex: 0 0 auto;
  border-radius: 12px;
  background: var(--accent-soft);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
}

.app-name {
  font-size: 16.5px;
  font-weight: 620;
  letter-spacing: -.01em;
  margin: 0 0 5px;
}

.pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  font-weight: 550;
  padding: 2px 8px;
  border-radius: 20px;
  background: var(--ok-soft);
  color: var(--ok);
}

.pill.off { background: var(--warn-soft); color: var(--warn); }

.pill .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}

.app-desc {
  font-size: 13.5px;
  color: var(--text-dim);
  margin: 0 0 18px;
  flex: 1;
}

.tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 18px;
}

.tag {
  font-size: 11.5px;
  color: var(--text-faint);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 2px 8px;
  background: var(--bg);
}

.enter {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--accent);
}

.enter .arrow { transition: transform .18s; }
.app-card:hover .enter .arrow { transform: translateX(3px); }

.page-foot {
  margin-top: 40px;
  padding-top: 20px;
  border-top: 1px solid var(--border);
  font-size: 12.5px;
  color: var(--text-faint);
  display: flex;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 10px;
}

/* ==================== 登录 / 注册 / 重置 ==================== */
.auth-links {
  display: flex;
  justify-content: flex-start;
  margin: 2px 0 14px;
  font-size: 13px;
}

.auth-links a {
  color: var(--accent);
  font-weight: 550;
}

.auth-links a:hover { text-decoration: underline; }

.btn-row {
  display: flex;
  gap: 10px;
  margin-top: 8px;
}

.btn-row > * { flex: 1; }
.btn-row .btn-primary { margin-top: 0; }

.btn-secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 44px;
  border: 1px solid var(--accent);
  border-radius: 10px;
  background: transparent;
  color: var(--accent);
  font-size: 15px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  transition: background .15s, color .15s;
}

.btn-secondary:hover { background: var(--accent-soft); }

.hint {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-faint);
}

.code-row { display: flex; gap: 8px; }
.code-row input { flex: 1; min-width: 0; }

.btn-ghost {
  height: 42px;
  padding: 0 14px;
  border: 1px solid var(--border-strong);
  border-radius: 9px;
  background: var(--bg);
  color: var(--text-dim);
  font-size: 13px;
  font-weight: 550;
  font-family: inherit;
  white-space: nowrap;
  cursor: pointer;
  transition: border-color .15s, color .15s;
}

.btn-ghost:hover { border-color: var(--accent); color: var(--accent); }

.alert.info {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: color-mix(in srgb, var(--accent) 25%, transparent);
}

.alert.ok {
  background: var(--ok-soft);
  color: var(--ok);
  border-color: color-mix(in srgb, var(--ok) 25%, transparent);
}
"""


_THEME_SCRIPT = """<script>
  function readThemeCookie() {
    var m = document.cookie.match(/(?:^|;\\s*)rs_theme=(dark|light)/);
    return m ? m[1] : null;
  }
  (function () {
    var saved = localStorage.getItem('rs-theme') || readThemeCookie();
    var dark = saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    var theme = dark ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', theme);
    document.cookie = 'rs_theme=' + theme + '; path=/; max-age=31536000; samesite=lax';
  })();
  function syncAppLinks(theme) {
    var links = document.querySelectorAll('a.app-card');
    for (var i = 0; i < links.length; i++) {
      try {
        var u = new URL(links[i].href, window.location.href);
        u.searchParams.set('__theme', theme);
        links[i].href = u.toString();
      } catch (e) {}
    }
  }
  function toggleTheme() {
    var root = document.documentElement;
    var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    localStorage.setItem('rs-theme', next);
    document.cookie = 'rs_theme=' + next + '; path=/; max-age=31536000; samesite=lax';
    syncAppLinks(next);
  }
  document.addEventListener('DOMContentLoaded', function () {
    syncAppLinks(document.documentElement.getAttribute('data-theme') || 'light');
  });
</script>"""

_THEME_ICONS = """<svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
      <circle cx="12" cy="12" r="4"></circle>
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path>
    </svg>
    <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
    </svg>"""


def _theme_button(extra_class: str = "") -> str:
    cls = f"theme-toggle {extra_class}".strip()
    return (
        f'<button class="{cls}" type="button" onclick="toggleTheme()" '
        f'title="切换日间 / 夜间外观" aria-label="切换日间 / 夜间外观">\n    {_THEME_ICONS}\n  </button>'
    )


# ==================== 应用卡片 ====================
def load_apps() -> list[dict]:
    """优先读 portal/apps.json（每次请求重读，改配置不用重启），缺失时用内置默认值。"""
    try:
        data = json.loads(APPS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else DEFAULT_APPS
    except Exception:
        return DEFAULT_APPS


def _safe_next(raw: str | None) -> str:
    """防开放重定向：只允许站内相对路径，或 apps.json 里登记过的应用地址。"""
    if not raw:
        return "/"
    raw = raw.strip()
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    for app in load_apps():
        base = str(app.get("url", "")).rstrip("/")
        if base and (raw == base or raw.startswith(base + "/") or raw.startswith(base + "?")):
            return raw
    return "/"


def _probe(url: str, timeout: float = 0.25) -> bool:
    """快速探测子应用端口是否在监听（本机连接，亚毫秒级）。"""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _cards() -> list[dict]:
    return [dict(app, online=_probe(str(app.get("url", "")))) for app in load_apps()]


def _mask(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    if not domain:
        return email or ""
    if len(name) <= 2:
        return f"{name[0]}***@{domain}" if name else email
    return f"{name[:2]}***@{domain}"


# ==================== 账号库（与 portal/dbi.py 同一张表） ====================
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

# 与 my-rag/database.py 同一张表：验证码只存 SHA-256，带用途与过期时间
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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    if readonly:
        # 不用 mode=ro：WAL 下只读连接需要访问 -shm/-wal，mode=ro 会打不开库
        conn.execute("PRAGMA query_only=ON")
    return conn


def migrate() -> list[str]:
    """幂等迁移：可重复执行，返回本次实际做了什么的说明列表。"""
    notes: list[str] = []
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=10000")
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
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


# ==================== 登录校验（与 portal/accounts.py 同算法） ====================
def normalize_login_id(identifier: str) -> str:
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


# ==================== 注册 / 找回密码（移植自 my-rag/auth.py） ====================
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
    except sqlite3.Error:
        row = None
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
    except sqlite3.IntegrityError:
        return False, "该邮箱已被注册，请直接登录"
    except Exception as exc:
        return False, f"账号创建失败: {exc}"


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
    except Exception as exc:
        return False, f"密码更新失败: {exc}"


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


# ==================== 会话（与 portal/sessions.py 同一个 Cookie） ====================
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(email: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    conn = connect()
    try:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_token_hash(token), email, now.isoformat(),
             (now + timedelta(hours=TTL_HOURS)).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def user_of(token: str | None) -> str | None:
    if not token:
        return None
    try:
        conn = connect(readonly=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT email, expires_at FROM sessions WHERE token_hash = ?", (_token_hash(token),)
        ).fetchone()
    except sqlite3.Error:
        return None
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
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()
    finally:
        conn.close()


# ==================== 页面渲染 ====================
def _page(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>{CSS}</style>
  {_THEME_SCRIPT}
</head>
<body>
{body}
</body>
</html>"""


_CODE_SCRIPT = """<script>
async function sendCode(purpose, btn) {
  const email = document.getElementById('email').value.trim();
  const box = document.getElementById('codeMsg');
  if (!email) {
    box.style.display = 'block';
    box.className = 'alert';
    box.textContent = '请先填写邮箱';
    return;
  }
  box.style.display = 'block';
  box.className = 'alert info';
  box.textContent = '正在发送…';
  if (btn) btn.disabled = true;
  try {
    const resp = await fetch('/' + purpose + '/code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ email: email })
    });
    const data = await resp.json();
    if (data.ok) {
      box.className = 'alert info';
      box.textContent = data.message + (data.demo_code ? '　验证码：' + data.demo_code : '');
    } else {
      box.className = 'alert';
      box.textContent = data.message || '发送失败，请稍后再试';
    }
  } catch (e) {
    box.className = 'alert';
    box.textContent = '网络异常，请稍后再试';
  } finally {
    if (btn) btn.disabled = false;
  }
}
</script>"""


def _auth_shell(
    title: str,
    sub: str,
    form_html: str,
    foot: str,
    error: str | None = None,
    notice: str | None = None,
) -> str:
    alerts = ""
    if error:
        alerts += f'      <div class="alert"><span>⚠</span><span>{html.escape(error)}</span></div>\n'
    if notice:
        alerts += f'      <div class="alert ok"><span>✓</span><span>{html.escape(notice)}</span></div>\n'

    body = f"""  <div class="auth-wrap">
    <div class="auth-card">
      {_theme_button("auth-theme")}
      <div class="brand">
        <div class="brand-mark">◆</div>
        <div>
          <div class="brand-text">智能问答门户</div>
          <div class="brand-sub">统一入口 · 多知识库</div>
        </div>
      </div>

      <h1 class="auth-title">{title}</h1>
      <p class="auth-sub">{sub}</p>

{alerts}{form_html}

      <div class="auth-foot">
        {foot}
      </div>
    </div>
  </div>"""
    return _page(f"{title} · 智能问答门户", body)


def render_login(error: str | None = None, identifier: str = "", nxt: str = "/",
                 notice: str | None = None) -> str:
    form = f"""      <form method="post" action="/login" autocomplete="on">
        <input type="hidden" name="next" value="{html.escape(nxt, quote=True)}">
        <div class="field">
          <label for="identifier">账号</label>
          <input id="identifier" name="identifier" type="text"
                 value="{html.escape(identifier, quote=True)}"
                 placeholder="邮箱，或旧账号如 hr03" autocomplete="username" autofocus required>
        </div>
        <div class="field">
          <label for="password">密码</label>
          <input id="password" name="password" type="password"
                 placeholder="请输入密码" autocomplete="current-password" required>
        </div>
        <div class="auth-links">
          <a href="/reset">忘记密码？</a>
        </div>
        <div class="btn-row">
          <button class="btn-primary" type="submit">登录</button>
          <a class="btn-secondary" href="/register">注册账号</a>
        </div>
      </form>"""
    foot = '<strong>演示账号</strong>：demo@demo.local / demo123456'
    return _auth_shell(
        "登录", "登录后可进入全部问答应用，各账号数据相互隔离。",
        form, foot, error, notice,
    )


def render_register(error: str | None = None, email: str = "", code: str = "",
                    notice: str | None = None) -> str:
    form = f"""      <form method="post" action="/register" autocomplete="on">
        <div class="field">
          <label for="email">邮箱</label>
          <input id="email" name="email" type="email"
                 value="{html.escape(email, quote=True)}"
                 placeholder="you@example.com" autocomplete="email" autofocus required>
          <div class="hint">注册后以此邮箱登录，简历等数据按账号隔离。</div>
        </div>
        <div class="field">
          <label for="code">邮箱验证码</label>
          <div class="code-row">
            <input id="code" name="code" type="text" inputmode="numeric" maxlength="6"
                   value="{html.escape(code, quote=True)}"
                   placeholder="6 位数字" autocomplete="one-time-code" required>
            <button class="btn-ghost" type="button" onclick="sendCode('register', this)">发送验证码</button>
          </div>
          <div id="codeMsg" class="alert info" style="display:none;margin-top:10px"></div>
        </div>
        <div class="field">
          <label for="password">密码</label>
          <input id="password" name="password" type="password"
                 placeholder="至少 6 位，需含字母和数字" autocomplete="new-password" required>
        </div>
        <div class="field">
          <label for="confirm">确认密码</label>
          <input id="confirm" name="confirm" type="password"
                 placeholder="再次输入密码" autocomplete="new-password" required>
        </div>
        <div class="btn-row">
          <button class="btn-primary" type="submit">注册并登录</button>
          <a class="btn-secondary" href="/login">返回登录</a>
        </div>
      </form>
{_CODE_SCRIPT}"""
    foot = (
        '验证码 5 分钟内有效，同一邮箱 60 秒内只能发送一次。<br>'
        '注册成功会自动登录并回到知识库入口。'
    )
    return _auth_shell(
        "注册账号", "注册即创建属于您的账号，三个应用共用同一套账密。",
        form, foot, error, notice,
    )


def render_reset(error: str | None = None, email: str = "", code: str = "",
                 notice: str | None = None) -> str:
    form = f"""      <form method="post" action="/reset" autocomplete="on">
        <div class="field">
          <label for="email">注册邮箱</label>
          <input id="email" name="email" type="email"
                 value="{html.escape(email, quote=True)}"
                 placeholder="you@example.com" autocomplete="email" autofocus required>
        </div>
        <div class="field">
          <label for="code">邮箱验证码</label>
          <div class="code-row">
            <input id="code" name="code" type="text" inputmode="numeric" maxlength="6"
                   value="{html.escape(code, quote=True)}"
                   placeholder="6 位数字" autocomplete="one-time-code" required>
            <button class="btn-ghost" type="button" onclick="sendCode('reset', this)">发送验证码</button>
          </div>
          <div id="codeMsg" class="alert info" style="display:none;margin-top:10px"></div>
        </div>
        <div class="field">
          <label for="password">新密码</label>
          <input id="password" name="password" type="password"
                 placeholder="至少 6 位，需含字母和数字" autocomplete="new-password" required>
        </div>
        <div class="field">
          <label for="confirm">确认新密码</label>
          <input id="confirm" name="confirm" type="password"
                 placeholder="再次输入新密码" autocomplete="new-password" required>
        </div>
        <div class="btn-row">
          <button class="btn-primary" type="submit">重置密码</button>
          <a class="btn-secondary" href="/login">返回登录</a>
        </div>
      </form>
{_CODE_SCRIPT}"""
    foot = '通过注册邮箱的验证码重置密码，重置后请使用新密码登录。'
    return _auth_shell(
        "忘记密码", "验证码校验通过后即可设置新密码。",
        form, foot, error, notice,
    )


def _card_html(app: dict) -> str:
    tags = "".join(
        f'<span class="tag">{html.escape(str(t))}</span>' for t in (app.get("tags") or [])
    )
    tags_block = f'        <div class="tags">{tags}</div>\n' if tags else ""
    pill = (
        '<span class="pill"><span class="dot"></span>运行中</span>'
        if app.get("online")
        else '<span class="pill off"><span class="dot"></span>未启动</span>'
    )
    url = html.escape(str(app.get("url", "")), quote=True)
    return f"""      <a class="app-card" href="{url}">
        <div class="app-head">
          <div class="app-icon">{html.escape(str(app.get("icon", "")))}</div>
          <div>
            <div class="app-name">{html.escape(str(app.get("name", "")))}</div>
            {pill}
          </div>
        </div>
        <p class="app-desc">{html.escape(str(app.get("desc", "")))}</p>
{tags_block}        <span class="enter">进入应用 <span class="arrow">→</span></span>
      </a>"""


def render_home(email: str, masked: str, cards: list[dict]) -> str:
    cards_html = "\n".join(_card_html(app) for app in cards)
    body = f"""  <div class="shell">
    <div class="topbar">
      <div class="brand" style="margin-bottom:0">
        <div class="brand-mark">◆</div>
        <div>
          <div class="brand-text">智能问答门户</div>
          <div class="brand-sub">统一入口 · 多知识库</div>
        </div>
      </div>
      <div class="userbox">
        {_theme_button()}
        <span title="{html.escape(email, quote=True)}">{html.escape(masked)}</span>
        <a class="logout-link" href="/logout">退出</a>
      </div>
    </div>

    <h1 class="page-title">选择一个知识库</h1>
    <p class="page-sub">当前账号 {html.escape(email)} 已登录，点开即用，无需再次输入密码。</p>

    <div class="grid">
{cards_html}
    </div>

    <div class="page-foot">
      <span>会话有效期 {TTL_HOURS} 小时 · 退出后需重新登录</span>
      <span>入口配置：portal/apps.json</span>
    </div>
  </div>"""
    return _page("智能问答门户", body)


# ==================== 应用 ====================
@asynccontextmanager
async def lifespan(_app: FastAPI):
    for note in migrate():
        print(f"[primary] 迁移: {note}")
    print(f"[primary] 账号库就绪: {DB_PATH}")
    print("[primary] 总界面已就绪（监听地址见上方 uvicorn 输出）")
    yield


app = FastAPI(
    title="RetailSage 总界面",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True, "service": "primary-interface"})


async def _read_form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


def _with_session(resp: RedirectResponse, token: str) -> RedirectResponse:
    resp.set_cookie(
        COOKIE,
        token,
        max_age=MAX_AGE,
        path="/",
        httponly=True,
        samesite="lax",
        secure=False,  # 本地 http，设了浏览器会直接丢弃
    )
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    nxt = _safe_next(next)
    email = user_of(request.cookies.get(COOKIE))
    if email:
        return RedirectResponse(nxt, status_code=302)
    return HTMLResponse(render_login(None, "", nxt))


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await _read_form(request)
    identifier = form.get("identifier", "")
    password = form.get("password", "")
    nxt = _safe_next(form.get("next"))

    ok, msg, email = await run_in_threadpool(authenticate, identifier, password)
    if not ok:
        return HTMLResponse(render_login(msg, identifier, nxt))

    token = await run_in_threadpool(issue, email)
    return _with_session(RedirectResponse(nxt, status_code=303), token)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if user_of(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(render_register())


@app.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request):
    form = await _read_form(request)
    email = form.get("email", "").strip().lower()
    code = form.get("code", "").strip()
    ok, msg = await run_in_threadpool(
        register_user, email, form.get("password", ""), form.get("confirm", ""), code
    )
    if not ok:
        return HTMLResponse(render_register(msg, email, code))

    token = await run_in_threadpool(issue, email)
    return _with_session(RedirectResponse("/", status_code=303), token)


@app.post("/register/code")
async def register_code(request: Request):
    form = await _read_form(request)
    ok, msg, demo = await run_in_threadpool(
        request_verification_code, form.get("email", ""), "register"
    )
    return JSONResponse({"ok": ok, "message": msg, "demo_code": demo})


@app.get("/reset", response_class=HTMLResponse)
def reset_page(request: Request):
    if user_of(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=302)
    return HTMLResponse(render_reset())


@app.post("/reset", response_class=HTMLResponse)
async def reset_submit(request: Request):
    form = await _read_form(request)
    email = form.get("email", "").strip().lower()
    code = form.get("code", "").strip()
    ok, msg = await run_in_threadpool(
        reset_password, email, code, form.get("password", ""), form.get("confirm", "")
    )
    if not ok:
        return HTMLResponse(render_reset(msg, email, code))
    return HTMLResponse(render_login(notice=f"{msg}，请使用新密码登录。"))


@app.post("/reset/code")
async def reset_code(request: Request):
    form = await _read_form(request)
    ok, msg, demo = await run_in_threadpool(
        request_verification_code, form.get("email", ""), "reset"
    )
    return JSONResponse({"ok": ok, "message": msg, "demo_code": demo})


@app.get("/logout")
def logout(request: Request):
    revoke(request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(COOKIE, path="/")
    return resp


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    email = user_of(request.cookies.get(COOKIE))
    if not email:
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse(render_home(email, _mask(email), _cards()))


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="总界面：统一登录 + 知识库入口")
    parser.add_argument("--host", default=os.getenv("PRIMARY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PRIMARY_PORT", "8000")))
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
