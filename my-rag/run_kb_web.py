"""RetailSage 智能 HR 知识库的 Web 网关（默认 8501）。

作用与洞察端 run_insight_web.py 完全对称：把 `python app.py` 直接 launch 的 Gradio
应用包进一个 FastAPI，在前面加一道「门户会话校验」的门。

- 未登录的页面请求 → 302 回门户登录页（保留 next，登录后原路返回）
- 未登录的接口/WebSocket 请求 → 401 / 关闭连接
- 已登录 → 放行，并把账号通过 auth_dependency 交给 Gradio，
  于是事件处理函数里的 `gr.Request().username` 就是当前账号

与洞察端唯一的实质差异：my-rag 是独立虚拟环境，刻意不 import portal 包
（那属于另一个 venv，且把项目根塞进 sys.path 会污染本目录的裸模块导入），
改用本目录 portal_client.py 的 COOKIE_NAME + verify_session 读共享账号库的 sessions 表。

单机无认证运行仍然走 `python app.py`（自动回落到应用内登录页），与本文件互不影响。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parent   # my-rag 目录
os.chdir(ROOT)                           # 先钉死 CWD：config/auth 的相对路径依赖它
sys.path.insert(0, str(ROOT))            # 再扩 sys.path，保证裸模块导入命中本目录

os.environ.setdefault("no_proxy", "localhost,127.0.0.1,0.0.0.0")
os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,0.0.0.0")

# 注意：不要在这里 load_dotenv()。auth.py 会按文件位置加载本目录 .env，
# config.py 的路径也全部锚定到文件位置，重复加载没有意义。
from app import KB_CSS, build_kb, build_theme  # noqa: E402  （重活都在这里：gradio + init_db + 后台解析队列）

import gradio as gr  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from starlette.responses import JSONResponse, RedirectResponse  # noqa: E402

import portal_client  # noqa: E402
from portal_client import COOKIE_NAME, verify_session  # noqa: E402

HOST = os.getenv("KB_HOST", "127.0.0.1")
PORT = int(os.getenv("KB_PORT", "8501"))
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

        user = verify_session(_cookie(scope, COOKIE_NAME))
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


# 跟随门户的日间/夜间选择：门户把选择写进 rs_theme Cookie（同 host，跨端口共享），
# 这里在 Gradio 初始化前把它转成 Gradio 认识的 ?__theme=dark|light。
THEME_HEAD = """
<script>
(function () {
  var m = document.cookie.match(/(?:^|;\\s*)rs_theme=(dark|light)/);
  if (!m) return;
  var url = new URL(window.location.href);
  if (url.searchParams.get('__theme') !== m[1]) {
    url.searchParams.set('__theme', m[1]);
    window.location.replace(url.toString());
  }
})();
</script>
"""


def create_app() -> FastAPI:
    demo = build_kb()
    application = FastAPI(title="RetailSage 智能 HR 知识库", docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(Gate)   # 必须在 mount 之前加，才能覆盖到挂载进来的路由
    gr.mount_gradio_app(
        application,
        demo,
        path="/",
        auth_dependency=lambda request: verify_session(request.cookies.get(COOKIE_NAME)),
        theme=build_theme(),   # Gradio 6：mount 不读 Blocks() 里的 theme，必须在此显式传入
        css=KB_CSS,
        head=THEME_HEAD,
    )
    return application


app = create_app()


if __name__ == "__main__":
    print(f"✅ RetailSage 智能 HR 知识库已启动：{SELF_URL}（需先从门户 {PORTAL_URL} 登录）")
    # 必须传 app 对象而不是 "module:app" 字符串 —— 传字符串会二次导入，
    # build_kb() 会跑两遍（后台队列/索引重复初始化 + 端口冲突）
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
