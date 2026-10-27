"""总集门户（8000）：统一登录页 + 应用入口卡片页。

约定（全局一致，改一处就要改全部）：
- 所有 URL 一律写 127.0.0.1，**不能写 localhost** —— 两者是不同 host，
  Cookie 不互通，且会让子应用的 fetch/WebSocket 变成跨站请求。
- 本进程绝不 import 任何一个业务应用（不加载 gradio / streamlit）。
"""

from __future__ import annotations

import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from .accounts import (
    authenticate,
    register_user,
    request_verification_code,
    reset_password,
)
from .dbi import DB_PATH, migrate
from .sessions import COOKIE, MAX_AGE, TTL_HOURS, issue, revoke, user_of

BASE = Path(__file__).resolve().parent
APPS: list[dict] = json.loads((BASE / "apps.json").read_text(encoding="utf-8"))


def load_apps() -> list[dict]:
    """每次请求都重读，改 apps.json 不用重启（加第三个知识库只需改这个文件）。"""
    try:
        return json.loads((BASE / "apps.json").read_text(encoding="utf-8"))
    except Exception:
        return APPS


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for note in migrate():
        print(f"[portal] 迁移: {note}")
    print(f"[portal] 账号库就绪: {DB_PATH}")
    print("[portal] 门户已启动: http://127.0.0.1:8000/")
    yield


app = FastAPI(title="RetailSage 门户", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


# ==================== 工具 ====================
def _safe_next(raw: str | None) -> str:
    """防开放重定向：只允许站内相对路径，或 apps.json 里登记过的应用地址。"""
    if not raw:
        return "/"
    raw = raw.strip()
    if raw.startswith("/") and not raw.startswith("//"):
        return raw
    for a in load_apps():
        base = str(a.get("url", "")).rstrip("/")
        if base and (raw == base or raw.startswith(base + "/") or raw.startswith(base + "?")):
            return raw
    return "/"


def _probe(url: str, timeout: float = 0.25) -> bool:
    """快速探测子应用端口是否在监听（本机连接，亚毫秒级）。"""
    try:
        p = urlparse(url)
        host = p.hostname or "127.0.0.1"
        port = p.port or 80
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _cards() -> list[dict]:
    return [dict(a, online=_probe(str(a.get("url", "")))) for a in load_apps()]


def _mask(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    if not domain:
        return email or ""
    if len(name) <= 2:
        return f"{name[0]}***@{domain}" if name else email
    return f"{name[:2]}***@{domain}"


def _set_session(resp: RedirectResponse, token: str) -> RedirectResponse:
    """登录 / 注册成功后统一种 Cookie，保证三处行为一致。"""
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


# ==================== 页面 ====================
@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "portal"}


@app.get("/login")
def login_page(request: Request, next: str = "/"):
    nxt = _safe_next(next)
    email = user_of(request.cookies.get(COOKIE))
    if email:
        return RedirectResponse(nxt, status_code=302)
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": nxt, "error": None, "identifier": "", "notice": None},
    )


async def _read_form(request: Request) -> dict[str, str]:
    raw = (await request.body()).decode("utf-8", "replace")
    return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}


@app.post("/login")
async def login_submit(request: Request):
    form = await _read_form(request)
    identifier = form.get("identifier", "")
    password = form.get("password", "")
    nxt = _safe_next(form.get("next"))

    ok, msg, email = await run_in_threadpool(authenticate, identifier, password)
    if not ok:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": nxt, "error": msg, "identifier": identifier, "notice": None},
        )

    token = await run_in_threadpool(issue, email)
    return _set_session(RedirectResponse(nxt, status_code=303), token)


@app.get("/register")
def register_page(request: Request):
    if user_of(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "register.html", {"error": None, "email": "", "code": ""}
    )


@app.post("/register")
async def register_submit(request: Request):
    form = await _read_form(request)
    email = form.get("email", "").strip().lower()
    code = form.get("code", "").strip()
    ok, msg = await run_in_threadpool(
        register_user, email, form.get("password", ""), form.get("confirm", ""), code
    )
    if not ok:
        return templates.TemplateResponse(
            request, "register.html", {"error": msg, "email": email, "code": code}
        )

    token = await run_in_threadpool(issue, email)
    return _set_session(RedirectResponse("/", status_code=303), token)


@app.post("/register/code")
async def register_code(request: Request):
    form = await _read_form(request)
    ok, msg, demo = await run_in_threadpool(
        request_verification_code, form.get("email", ""), "register"
    )
    return JSONResponse({"ok": ok, "message": msg, "demo_code": demo})


@app.get("/reset")
def reset_page(request: Request):
    if user_of(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "reset.html", {"error": None, "email": "", "code": ""}
    )


@app.post("/reset")
async def reset_submit(request: Request):
    form = await _read_form(request)
    email = form.get("email", "").strip().lower()
    code = form.get("code", "").strip()
    ok, msg = await run_in_threadpool(
        reset_password, email, code, form.get("password", ""), form.get("confirm", "")
    )
    if not ok:
        return templates.TemplateResponse(
            request, "reset.html", {"error": msg, "email": email, "code": code}
        )
    return templates.TemplateResponse(
        request,
        "login.html",
        {"next": "/", "error": None, "identifier": email, "notice": f"{msg}，请使用新密码登录。"},
    )


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


@app.get("/")
def home(request: Request):
    email = user_of(request.cookies.get(COOKIE))
    if not email:
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "email": email,
            "masked": _mask(email),
            "cards": _cards(),
            "portal": "http://127.0.0.1:8000",
            "ttl_hours": TTL_HOURS,
        },
    )
