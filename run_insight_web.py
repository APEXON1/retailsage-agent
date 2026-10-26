"""RetailSage 洞察应用的 Web 网关（默认 7860）。

作用：把原来是 `python app.py` 直接 launch 的 Gradio 应用，包进一个 FastAPI，
在前面加一道「门户会话校验」的门。

- 未登录的页面请求 → 302 回门户登录页（保留 next，登录后原路返回）
- 未登录的接口/WebSocket 请求 → 401 / 关闭连接
- 已登录 → 放行，并把账号通过 auth_dependency 交给 Gradio，
  于是事件处理函数里的 `gr.Request().username` 就是当前账号

两层校验都必须有：
  中间件负责「体验」（302 跳转），auth_dependency 负责「功能」
  （Gradio 内部 /login_check 与 request.username 都走它，去掉就拿不到身份）。

单机无认证运行仍然走 `python app.py`，与本文件互不影响。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)                       # 先钉死 CWD：.env / 相对路径全部依赖它
sys.path.insert(0, str(ROOT))        # 再扩 sys.path

os.environ.setdefault("no_proxy", "localhost,127.0.0.1,0.0.0.0")
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,0.0.0.0")

# 注意：这里不要再 load_dotenv()。app.py 自己在导入 config 前会按文件位置加载根 .env，
# 重复加载没有意义，还容易掩盖路径问题。
from app import CUSTOM_CSS, build_demo  # noqa: E402  （重活都在这里：gradio + data_insight.config 建目录）

import gradio as gr  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.responses import JSONResponse, RedirectResponse  # noqa: E402

from portal.sessions import COOKIE, user_of  # noqa: E402

HOST = os.getenv("INSIGHT_HOST", "127.0.0.1")
PORT = int(os.getenv("INSIGHT_PORT", "7860"))
PORTAL_URL = os.getenv("PORTAL_URL", "http://127.0.0.1:8000").rstrip("/")
SELF_URL = f"http://{HOST}:{PORT}/"

# 这些前缀是 Gradio 自己的资源/接口，未登录时不应该跳 HTML，直接 401 更清晰
_API_PREFIXES = ("/gradio_api", "/static", "/favicon", "/assets")


def _cookie(scope, name: str) -> str | None:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"cookie":
            for part in value.decode("latin-1").split(";"):
                k, _, v = part.strip().partition("=")
                if k == name:
                    return unquote(v)
    return None


def _wants_html(scope) -> bool:
    for key, value in scope.get("headers") or []:
        if key.lower() == b"accept":
            return "text/html" in value.decode("latin-1").lower()
    return False


class Gate:
    """纯 ASGI 中间件（不用 BaseHTTPMiddleware，避免它给 WebSocket 添麻烦）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        user = user_of(_cookie(scope, COOKIE))
        if user:
            scope.setdefault("state", {})["rs_user"] = user
            return await self.app(scope, receive, send)

        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return

        path = scope.get("path", "/")
        if _wants_html(scope) and not path.startswith(_API_PREFIXES):
            target = f"{PORTAL_URL}/login?next={quote(SELF_URL, safe='')}"
            resp = RedirectResponse(target, status_code=302)
        else:
            resp = JSONResponse(
                {"detail": "Not authenticated", "login": f"{PORTAL_URL}/login"}, status_code=401
            )
        await resp(scope, receive, send)


def create_app() -> FastAPI:
    demo = build_demo()
    application = FastAPI(title="RetailSage 洞察", docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(Gate)   # 必须在 mount 之前加，才能覆盖到挂载进来的路由
    gr.mount_gradio_app(
        application,
        demo,
        path="/",
        auth_dependency=lambda request: user_of(request.cookies.get(COOKIE)),
        css=CUSTOM_CSS,
    )
    return application


app = create_app()


if __name__ == "__main__":
    print(f"✅ RetailSage 洞察已启动：{SELF_URL}（需先从门户 {PORTAL_URL} 登录）")
    # 必须传 app 对象而不是 "module:app" 字符串 —— 传字符串会二次导入，
    # build_demo() 会跑两遍（内存翻倍 + 端口冲突）
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
